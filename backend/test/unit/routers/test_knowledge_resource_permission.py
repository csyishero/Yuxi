from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from server.routers import knowledge_router
from server.utils.knowledge_permissions import (
    require_knowledge_base_delete_document,
    require_knowledge_base_edit,
    require_knowledge_base_manage,
    require_knowledge_base_read,
)
from server.utils.knowledge_response import serialize_knowledge_base
from yuxi.knowledge.read_models import KnowledgeBaseSummary
from yuxi.permissions import ResourcePermission


def test_serialize_knowledge_base_redacts_credentials_from_compatibility_fields():
    database = KnowledgeBaseSummary(
        kb_id="kb-1",
        name="知识库",
        description=None,
        kb_type="dify",
        embedding_model_spec=None,
        llm_model_spec=None,
        query_params={},
        additional_params={"dify_token": "secret", "chunk_size": 100},
        share_config={"version": 2, "read_scope": None, "manage_scope": None},
        created_by=None,
        created_at=None,
    )

    response = serialize_knowledge_base(database, redact_secrets=True)

    assert response["additional_params"]["chunk_size"] == 100
    assert response["metadata"]["chunk_size"] == 100
    assert "dify_token" not in response["additional_params"]
    assert "dify_token" not in response["metadata"]


def test_serialize_knowledge_base_exposes_edit_capability_separately_from_management():
    database = KnowledgeBaseSummary(
        kb_id="kb-1",
        name="知识库",
        description=None,
        kb_type="milvus",
        embedding_model_spec=None,
        llm_model_spec=None,
        query_params={},
        additional_params={},
        share_config={"version": 2, "read_scope": {"access_level": "global"}},
        created_by=None,
        created_at=None,
    )

    response = serialize_knowledge_base(database, permission=ResourcePermission.EDIT)

    assert response["effective_permission"] == "edit"
    assert response["can_edit"] is True
    assert response["can_manage"] is False


@pytest.mark.asyncio
async def test_department_admin_cannot_create_knowledge_base_for_another_department(monkeypatch):
    class FakeDepartmentRepository:
        def __init__(self, _db):
            pass

        async def get_by_id(self, _department_id):
            return SimpleNamespace(id=2)

    monkeypatch.setattr(knowledge_router, "DepartmentRepository", FakeDepartmentRepository)
    admin = SimpleNamespace(uid="admin-1", role="admin", department_id=1)

    with pytest.raises(HTTPException) as exc_info:
        await knowledge_router._resolve_database_owner_department_id(admin, 2, SimpleNamespace())

    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_superadmin_can_choose_existing_knowledge_base_department(monkeypatch):
    class FakeDepartmentRepository:
        def __init__(self, _db):
            pass

        async def get_by_id(self, department_id):
            return SimpleNamespace(id=department_id) if department_id == 2 else None

    monkeypatch.setattr(knowledge_router, "DepartmentRepository", FakeDepartmentRepository)
    superadmin = SimpleNamespace(uid="root", role="superadmin", department_id=1)

    assert (
        await knowledge_router._resolve_database_owner_department_id(superadmin, 2, SimpleNamespace())
        == 2
    )


@pytest.mark.asyncio
async def test_superadmin_cannot_fall_back_when_requested_department_is_invalid(monkeypatch):
    class FakeDepartmentRepository:
        def __init__(self, _db):
            pass

        async def get_by_id(self, _department_id):
            return None

    monkeypatch.setattr(knowledge_router, "DepartmentRepository", FakeDepartmentRepository)
    superadmin = SimpleNamespace(uid="root", role="superadmin", department_id=1)

    with pytest.raises(HTTPException) as exc_info:
        await knowledge_router._resolve_database_owner_department_id(superadmin, 0, SimpleNamespace())

    assert exc_info.value.status_code == 400


@pytest.mark.parametrize(
    ("path", "method"),
    [
        ("/knowledge/databases/{kb_id}/documents/batch", "DELETE"),
        ("/knowledge/databases/{kb_id}/documents/{doc_id}", "DELETE"),
    ],
)
def test_document_delete_routes_require_delete_document_capability(path, method):
    route = next(
        route
        for route in knowledge_router.knowledge.routes
        if getattr(route, "path", None) == path and method in getattr(route, "methods", set())
    )

    assert require_knowledge_base_delete_document in {
        dependency.call for dependency in route.dependant.dependencies
    }


@pytest.mark.asyncio
async def test_regular_contributor_cannot_upload_without_target_knowledge_base():
    regular_user = SimpleNamespace(uid="editor-1", role="user", department_id=2)
    admin = SimpleNamespace(uid="admin-1", role="admin", department_id=2)

    with pytest.raises(HTTPException) as exc_info:
        await knowledge_router._require_upload_permission_if_kb_id(None, regular_user)
    assert exc_info.value.status_code == 403

    assert await knowledge_router._require_upload_permission_if_kb_id(None, admin) is None


@pytest.mark.asyncio
async def test_upload_only_department_contributor_can_stage_file(monkeypatch):
    database = {
        "created_by": "owner",
        "share_config": {
            "version": 2,
            "read_scope": {"access_level": "department", "department_ids": [2]},
            "edit_scope": {"access_level": "department", "department_ids": [2]},
            "manage_scope": None,
            "capability_policy": {
                "read": ["view", "search"],
                "edit": ["upload"],
                "manage": [],
            },
        },
    }

    async def fake_get_database_info(_kb_id):
        return database

    monkeypatch.setattr(knowledge_router.knowledge_base, "get_database_info", fake_get_database_info)
    contributor = SimpleNamespace(uid="editor-1", role="user", department_id=2)

    assert await knowledge_router._require_upload_permission_if_kb_id("kb-1", contributor) is None


@pytest.mark.parametrize(("uid", "role", "can_read"), [("admin-1", "admin", True), ("other-user", "user", False)])
@pytest.mark.asyncio
async def test_non_manager_cannot_manage_global_read_knowledge_base(monkeypatch, uid, role, can_read):
    database = {
        "created_by": "owner",
        "share_config": {
            "version": 2,
            "read_scope": {"access_level": "global"},
            "manage_scope": None,
        },
    }

    async def fake_get_database_info(_kb_id):
        return database

    monkeypatch.setattr(knowledge_router.knowledge_base, "get_database_info", fake_get_database_info)
    user = SimpleNamespace(uid=uid, role=role, department_id=2)

    if can_read:
        assert await require_knowledge_base_read("kb-1", user) is user

    with pytest.raises(HTTPException) as exc_info:
        await require_knowledge_base_manage("kb-1", user)
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_query_parameter_routes_apply_knowledge_base_acl(monkeypatch):
    database = {
        "created_by": "owner",
        "share_config": {
            "version": 2,
            "read_scope": {"access_level": "user", "user_uids": ["admin-1"]},
            "manage_scope": None,
        },
    }

    async def fake_get_database_info(_kb_id):
        return database

    monkeypatch.setattr(knowledge_router.knowledge_base, "get_database_info", fake_get_database_info)
    readonly_admin = SimpleNamespace(uid="admin-1", role="admin", department_id=2)

    assert await require_knowledge_base_read("kb-1", readonly_admin) is readonly_admin

    with pytest.raises(HTTPException) as exc_info:
        await require_knowledge_base_read(
            "kb-1", SimpleNamespace(uid="admin-2", role="admin", department_id=2)
        )
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_authorized_regular_user_can_contribute_documents_but_cannot_manage(monkeypatch):
    database = {
        "created_by": "owner",
        "share_config": {
            "version": 2,
            "read_scope": {"access_level": "global"},
            "edit_scope": {"access_level": "user", "user_uids": ["editor-1"]},
            "manage_scope": None,
        },
    }

    async def fake_get_database_info(_kb_id):
        return database

    monkeypatch.setattr(knowledge_router.knowledge_base, "get_database_info", fake_get_database_info)
    editor = SimpleNamespace(uid="editor-1", role="user", department_id=2)
    reader = SimpleNamespace(uid="reader-1", role="user", department_id=2)

    assert await require_knowledge_base_edit("kb-1", editor) is editor

    with pytest.raises(HTTPException) as exc_info:
        await require_knowledge_base_edit("kb-1", reader)
    assert exc_info.value.status_code == 403

    with pytest.raises(HTTPException) as exc_info:
        await require_knowledge_base_manage("kb-1", editor)
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_explicit_regular_user_manager_can_manage_knowledge_base(monkeypatch):
    database = {
        "created_by": "owner",
        "share_config": {
            "version": 2,
            "read_scope": {"access_level": "global"},
            "manage_scope": {"access_level": "user", "user_uids": ["manager-1"]},
        },
    }

    async def fake_get_database_info(_kb_id):
        return database

    monkeypatch.setattr(knowledge_router.knowledge_base, "get_database_info", fake_get_database_info)
    manager = SimpleNamespace(uid="manager-1", role="user", department_id=2)

    assert await require_knowledge_base_manage("kb-1", manager) is manager


@pytest.mark.asyncio
async def test_delegated_manager_can_load_only_current_department_permission_options(monkeypatch):
    database = SimpleNamespace(
        owner_department_id=1,
        created_by="owner",
        share_config={
            "version": 2,
            "read_scope": {"access_level": "global"},
            "manage_scope": {"access_level": "user", "user_uids": ["manager-1"]},
        },
    )

    async def fake_get_database_info(_kb_id):
        return database

    class FakeDepartmentRepository:
        def __init__(self, _db):
            pass

        async def get_by_id(self, department_id):
            return SimpleNamespace(id=department_id, name="开发部")

    class FakeUserRepository:
        def __init__(self, _db):
            pass

        async def list_with_department(self, *, limit, department_id):
            assert limit == 1000
            assert department_id == 2
            user = SimpleNamespace(
                uid="manager-1",
                username="协管用户",
                role="user",
                department_id=2,
            )
            return [(user, "开发部")]

    monkeypatch.setattr(knowledge_router.knowledge_base, "get_database_info", fake_get_database_info)
    monkeypatch.setattr(knowledge_router, "DepartmentRepository", FakeDepartmentRepository)
    monkeypatch.setattr(knowledge_router, "UserRepository", FakeUserRepository)
    manager = SimpleNamespace(uid="manager-1", role="user", department_id=2)

    result = await knowledge_router.get_database_permission_options(
        "kb-1",
        current_user=manager,
        db=SimpleNamespace(),
    )

    assert result["departments"] == [{"id": 2, "name": "开发部"}]
    assert result["users"][0]["uid"] == "manager-1"
