"""分页搜索保持旧接口默认，并回填旧版知识库共享成员。"""

import importlib
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.routers import knowledge_router, workspace_router
from yuxi.permissions import KnowledgeBaseCapability

filesystem_router = importlib.import_module("server.routers.filesystem_router")


@pytest.mark.parametrize("kind", ["workspace", "viewer"])
def test_file_search_without_page_parameters_still_returns_first_100_results(monkeypatch, kind: str) -> None:
    app = FastAPI()
    app.dependency_overrides[workspace_router.get_required_user] = lambda: SimpleNamespace(uid="user-1")
    app.dependency_overrides[filesystem_router.get_db] = lambda: object()

    async def search_stub(**kwargs):
        return {
            "entries": [{"name": f"result-{index}"} for index in range(kwargs["limit"])],
            "has_more": True,
            "truncated": False,
        }

    if kind == "workspace":
        monkeypatch.setattr(workspace_router, "search_workspace_files", search_stub)
        app.include_router(workspace_router.workspace)
        url = "/workspace/search?query=report"
    else:
        monkeypatch.setattr(filesystem_router, "search_viewer_files", search_stub)
        app.include_router(filesystem_router.filesystem_router)
        url = "/viewer/filesystem/search?thread_id=thread-1&query=report"

    response = TestClient(app).get(url)

    assert response.status_code == 200
    assert len(response.json()["entries"]) == 100


@pytest.fixture
def permission_client(monkeypatch) -> TestClient:
    users = [
        SimpleNamespace(uid=f"user-{index}", username=f"User {index}", role="user", department_id=1, is_deleted=0)
        for index in range(1001)
    ]

    class FakeDepartmentRepository:
        def __init__(self, _db):
            pass

        async def list_departments(self):
            return []

    class FakeUserRepository:
        def __init__(self, _db):
            pass

        async def list_page_with_department(self, *, offset, limit, department_id, search):
            return [(user, "Dept") for user in users[offset : offset + limit]], len(users)

        async def list_by_uids(self, uids):
            return [user for user in users if user.uid in uids]

    async def get_database_info(_kb_id):
        return SimpleNamespace(
            share_config={"access_level": "user", "user_uids": ["user-1000"]},
            owner_department_id=1,
        )

    monkeypatch.setattr(knowledge_router.knowledge_base, "get_database_info", get_database_info)
    monkeypatch.setattr(
        knowledge_router,
        "resolve_knowledge_base_capabilities",
        lambda *_args: {KnowledgeBaseCapability.SHARE},
    )
    monkeypatch.setattr(knowledge_router, "DepartmentRepository", FakeDepartmentRepository)
    monkeypatch.setattr(knowledge_router, "UserRepository", FakeUserRepository)

    app = FastAPI()
    app.dependency_overrides[knowledge_router.get_required_user] = lambda: SimpleNamespace(
        uid="root", role="superadmin", department_id=None
    )
    app.dependency_overrides[knowledge_router.get_db] = lambda: object()
    app.include_router(knowledge_router.knowledge)
    return TestClient(app)


def test_permission_options_without_limit_preserves_first_1000_users(permission_client: TestClient) -> None:
    response = permission_client.get("/knowledge/databases/kb-1/permission-options")

    assert response.status_code == 200
    assert len(response.json()["users"]) == 1000
    assert response.json()["has_more"] is True


def test_permission_options_return_selected_user_from_legacy_top_level_scope(permission_client: TestClient) -> None:
    response = permission_client.get("/knowledge/databases/kb-1/permission-options?limit=50")

    assert response.status_code == 200
    assert [user["uid"] for user in response.json()["selected_users"]] == ["user-1000"]
