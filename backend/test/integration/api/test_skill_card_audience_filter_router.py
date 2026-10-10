"""管理员按人员筛选时，共享 Skill 按目标人员授权范围收敛。"""

from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI

from server.routers.skill_router import user_skills
from server.utils.auth_middleware import get_db, get_required_user
from yuxi.agents.skills import service
from yuxi.storage.postgres.models_business import Department, Skill, User

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


@pytest.fixture(scope="session", autouse=True)
def ensure_live_api_schema():
    """本文件通过内存 ASGI 测试接口。"""


@pytest.fixture(scope="session", autouse=True)
def cleanup_test_knowledge_resources():
    """本文件没有知识库资源。"""
    yield


@pytest.fixture(scope="session", autouse=True)
def cleanup_test_sandboxes():
    """本文件没有沙盒资源。"""
    yield


def _skill(slug: str, access_level: str, *, department_ids=None, user_uids=None, source_type="upload") -> Skill:
    """构造不同读取范围的共享 Skill。"""
    return Skill(
        slug=slug,
        name=slug,
        description=slug,
        source_type=source_type,
        dir_path=f"shared/{slug}",
        enabled=True,
        created_by="owner",
        share_config={
            "version": 2,
            "read_scope": {
                "access_level": access_level,
                "department_ids": department_ids or [],
                "user_uids": user_uids or [],
            },
            "manage_scope": None,
        },
    )


async def test_person_search_filters_department_and_named_user_shares(monkeypatch):
    """命中用户只看到其部门和直接授权的共享卡片。"""
    items = [
        _skill("builtin", "global", source_type="builtin"),
        _skill("global-share", "global"),
        _skill("dept-one", "department", department_ids=[1]),
        _skill("dept-two", "department", department_ids=[2]),
        _skill("direct-one", "user", user_uids=["target"]),
        _skill("direct-two", "user", user_uids=["other"]),
    ]

    class FakeSkillRepository:
        def __init__(self, _db):
            pass

        async def list_all(self):
            return items

    class FakeUserRepository:
        def __init__(self, _db):
            pass

        async def list_skill_catalog_audience(self, *, search=None, department_id=None):
            assert department_id is None
            return [("target", 1)] if search == "test" else []

        async def list_by_uids(self, uids):
            assert uids == ["target"]
            return [User(uid="target", username="测试用户", password_hash="x", role="user", is_deleted=0)]

    class FakeDepartmentRepository:
        def __init__(self, _db):
            pass

        async def list_departments(self):
            return [Department(id=1, name="科技部门"), Department(id=2, name="开发部门")]

    async def no_personal_skills(_uid):
        return []

    monkeypatch.setattr(service, "SkillRepository", FakeSkillRepository)
    monkeypatch.setattr(service, "UserRepository", FakeUserRepository)
    monkeypatch.setattr(service, "DepartmentRepository", FakeDepartmentRepository)
    monkeypatch.setattr(service, "list_personal_skills", no_personal_skills)

    app = FastAPI()
    app.include_router(user_skills, prefix="/api")
    user = User(uid="admin", username="管理员", password_hash="x", role="superadmin")

    async def provide_user():
        return user

    async def provide_db():
        yield object()

    app.dependency_overrides[get_required_user] = provide_user
    app.dependency_overrides[get_db] = provide_db
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/skills?audience_search=test")
        no_match_response = await client.get("/api/skills?audience_search=missing")

    assert response.status_code == 200, response.text
    assert [item["slug"] for item in response.json()["data"]] == [
        "builtin", "global-share", "dept-one", "direct-one"
    ]
    assert response.json()["data"][-1]["read_user_names"] == ["测试用户"]
    assert [item["slug"] for item in no_match_response.json()["data"]] == ["builtin"]


async def test_non_admin_cannot_search_other_users_skills(monkeypatch):
    """普通用户不能利用人员筛选查询目标账号范围。"""
    class FakeSkillRepository:
        def __init__(self, _db):
            pass

        async def list_all(self):
            return []

    async def no_personal_skills(_uid):
        return []

    monkeypatch.setattr(service, "SkillRepository", FakeSkillRepository)
    monkeypatch.setattr(service, "list_personal_skills", no_personal_skills)

    app = FastAPI()
    app.include_router(user_skills, prefix="/api")
    user = User(uid="viewer", username="普通用户", password_hash="x", role="user")

    async def provide_user():
        return user

    async def provide_db():
        yield object()

    app.dependency_overrides[get_required_user] = provide_user
    app.dependency_overrides[get_db] = provide_db
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/skills?audience_search=test")

    assert response.status_code == 403


async def test_department_admin_cannot_search_another_department(monkeypatch):
    """部门管理员不能借人员筛选查询其他部门。"""
    class FakeSkillRepository:
        def __init__(self, _db):
            pass

        async def list_all(self):
            return []

    async def no_personal_skills(_uid):
        return []

    monkeypatch.setattr(service, "SkillRepository", FakeSkillRepository)
    monkeypatch.setattr(service, "list_personal_skills", no_personal_skills)

    app = FastAPI()
    app.include_router(user_skills, prefix="/api")
    user = User(uid="admin", username="部门管理员", password_hash="x", role="admin", department_id=1)

    async def provide_user():
        return user

    async def provide_db():
        yield object()

    app.dependency_overrides[get_required_user] = provide_user
    app.dependency_overrides[get_db] = provide_db
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/skills?audience_department_id=2")

    assert response.status_code == 403
