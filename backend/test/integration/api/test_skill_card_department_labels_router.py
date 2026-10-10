"""普通用户技能卡片仅展示可见共享范围的部门名称。"""

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


async def test_non_admin_card_uses_visible_department_names_only(monkeypatch):
    """多部门共享返回真实名称，不附带不可见 Skill 的部门。"""
    visible = Skill(
        id=1,
        slug="credit-check",
        name="Credit Check",
        description="visible",
        source_type="upload",
        dir_path="shared/credit-check",
        enabled=True,
        created_by="owner",
        share_config={
            "version": 2,
            "read_scope": {"access_level": "department", "department_ids": [1, 2], "user_uids": []},
            "manage_scope": None,
        },
    )
    hidden = Skill(
        id=2,
        slug="secret",
        name="Secret",
        description="hidden",
        source_type="upload",
        dir_path="shared/secret",
        enabled=True,
        created_by="owner",
        share_config={
            "version": 2,
            "read_scope": {"access_level": "department", "department_ids": [3], "user_uids": []},
            "manage_scope": None,
        },
    )

    class FakeSkillRepository:
        def __init__(self, _db):
            pass

        async def list_all(self):
            return [visible, hidden]

    class FakeDepartmentRepository:
        def __init__(self, _db):
            pass

        async def list_departments(self):
            return [
                Department(id=1, name="科技部门"),
                Department(id=2, name="测试部门"),
                Department(id=3, name="保密部门"),
            ]

    async def no_personal_skills(_uid):
        return []

    monkeypatch.setattr(service, "SkillRepository", FakeSkillRepository)
    monkeypatch.setattr(service, "DepartmentRepository", FakeDepartmentRepository)
    monkeypatch.setattr(service, "list_personal_skills", no_personal_skills)

    app = FastAPI()
    app.include_router(user_skills, prefix="/api")
    user = User(uid="viewer", username="viewer", password_hash="x", role="user", department_id=1)

    async def provide_user():
        return user

    async def provide_db():
        yield object()

    app.dependency_overrides[get_required_user] = provide_user
    app.dependency_overrides[get_db] = provide_db
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/skills")

    assert response.status_code == 200, response.text
    assert [item["slug"] for item in response.json()["data"]] == ["credit-check"]
    assert response.json()["data"][0]["read_department_names"] == ["科技部门", "测试部门"]
    assert "保密部门" not in response.text


async def test_non_admin_card_uses_visible_shared_user_names_only(monkeypatch):
    """人员共享卡片返回实际用户名，不返回不可见 Skill 的成员。"""
    visible = Skill(
        id=1,
        slug="ppt-maker",
        name="Ppt Maker",
        description="visible",
        source_type="upload",
        dir_path="shared/ppt-maker",
        enabled=True,
        created_by="owner",
        share_config={
            "version": 2,
            "read_scope": {"access_level": "user", "department_ids": [], "user_uids": ["viewer", "colleague"]},
            "manage_scope": None,
        },
    )
    hidden = Skill(
        id=2,
        slug="secret",
        name="Secret",
        description="hidden",
        source_type="upload",
        dir_path="shared/secret",
        enabled=True,
        created_by="owner",
        share_config={
            "version": 2,
            "read_scope": {"access_level": "user", "department_ids": [], "user_uids": ["secret-user"]},
            "manage_scope": None,
        },
    )

    class FakeSkillRepository:
        def __init__(self, _db):
            pass

        async def list_all(self):
            return [visible, hidden]

    class FakeUserRepository:
        def __init__(self, _db):
            pass

        async def list_by_uids(self, uids):
            assert set(uids) == {"viewer", "colleague"}
            return [
                User(uid="viewer", username="张三", password_hash="x", role="user", is_deleted=0),
                User(uid="colleague", username="李四", password_hash="x", role="user", is_deleted=0),
            ]

    async def no_personal_skills(_uid):
        return []

    monkeypatch.setattr(service, "SkillRepository", FakeSkillRepository)
    monkeypatch.setattr(service, "UserRepository", FakeUserRepository)
    monkeypatch.setattr(service, "list_personal_skills", no_personal_skills)

    app = FastAPI()
    app.include_router(user_skills, prefix="/api")
    user = User(uid="viewer", username="张三", password_hash="x", role="user")

    async def provide_user():
        return user

    async def provide_db():
        yield object()

    app.dependency_overrides[get_required_user] = provide_user
    app.dependency_overrides[get_db] = provide_db
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/skills")

    assert response.status_code == 200, response.text
    assert [item["slug"] for item in response.json()["data"]] == ["ppt-maker"]
    assert response.json()["data"][0]["read_user_names"] == ["李四", "张三"]
    assert "secret-user" not in response.text
