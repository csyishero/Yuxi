"""技能共享来源分类的 HTTP 分页行为。"""

from datetime import datetime

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from server.routers.skill_router import user_skills
from server.utils.auth_middleware import get_db, get_required_user
from yuxi.agents.skills import service as skill_service
from yuxi.storage.postgres.models_business import Base, Department, Skill, User

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


@pytest.fixture(scope="session", autouse=True)
def ensure_live_api_schema():
    """本例使用独立数据库，不依赖运行中的 API。"""


@pytest.fixture(scope="session", autouse=True)
def cleanup_test_knowledge_resources():
    """本例不创建知识库资源。"""
    yield


@pytest.fixture(scope="session", autouse=True)
def cleanup_test_sandboxes():
    """本例不创建沙盒。"""
    yield


async def test_shared_kind_preserves_visibility_and_pages_each_kind(monkeypatch):
    """普通用户不见跨部门共享，超级管理员按共享或内置分别取得准确页数。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    viewer = User(uid="viewer", username="Viewer", password_hash="x", role="user", department_id=1)
    root = User(uid="root", username="Root", password_hash="x", role="superadmin", department_id=1)
    active_user = {"value": viewer}
    app = FastAPI()
    app.include_router(user_skills, prefix="/api")

    async def provide_user():
        return active_user["value"]

    async def provide_db():
        async with sessions() as db:
            yield db

    async def no_personal_skills(_uid):
        return []

    async def no_personal_skill(_uid, _slug):
        raise ValueError("个人 Skill 不存在")

    app.dependency_overrides[get_required_user] = provide_user
    app.dependency_overrides[get_db] = provide_db
    monkeypatch.setattr(skill_service, "list_personal_skills", no_personal_skills)
    monkeypatch.setattr(skill_service, "get_personal_skill", no_personal_skill)

    try:
        async with engine.begin() as connection:
            await connection.run_sync(
                lambda conn: Base.metadata.create_all(
                    conn, tables=[Department.__table__, User.__table__, Skill.__table__]
                )
            )
        async with sessions() as db:
            db.add_all([Department(id=1, name="Product"), Department(id=2, name="Finance"), viewer, root])
            for index, (slug, source_type, department_id, enabled) in enumerate(
                [
                    ("shared-product", "upload", 1, True),
                    ("shared-finance", "upload", 2, True),
                    ("builtin-global", "builtin", None, True),
                    ("shared-disabled", "upload", 1, False),
                    ("image-gen", "builtin", None, True),
                    ("mysql-reporter", "builtin", None, True),
                    ("builtin-disabled", "builtin", None, False),
                ],
                start=1,
            ):
                read_scope = (
                    {"access_level": "global", "department_ids": [], "user_uids": []}
                    if department_id is None
                    else {"access_level": "department", "department_ids": [department_id], "user_uids": []}
                )
                db.add(
                    Skill(
                        slug=slug,
                        name=slug,
                        description="test",
                        source_type=source_type,
                        dir_path=f"shared/{slug}",
                        share_config={"version": 2, "read_scope": read_scope, "manage_scope": None},
                        enabled=enabled,
                        created_by="author",
                        updated_at=datetime(2026, 1, index),
                    )
                )
            await db.commit()

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            # 旧客户端没有 limit 时仍由实际共享授权决定哪些卡片可见。
            visible = (await client.get("/api/skills", params={"source": "shared", "shared_kind": "shared"})).json()
            assert visible["total"] == 1
            assert [item["slug"] for item in visible["data"]] == ["shared-product"]

            active_user["value"] = root
            first = (
                await client.get("/api/skills", params={"source": "shared", "shared_kind": "shared", "limit": 1})
            ).json()
            assert [item["slug"] for item in first["data"]] == ["shared-finance"]
            assert (first["total"], first["has_more"]) == (2, True)

            second = (
                await client.get(
                    "/api/skills", params={"source": "shared", "shared_kind": "shared", "limit": 1, "offset": 1}
                )
            ).json()
            assert [item["slug"] for item in second["data"]] == ["shared-product"]
            assert (second["total"], second["has_more"]) == (2, False)

            builtin = (
                await client.get("/api/skills", params={"source": "shared", "shared_kind": "builtin", "limit": 1})
            ).json()
            assert [item["slug"] for item in builtin["data"]] == ["builtin-global"]
            assert (builtin["total"], builtin["has_more"]) == (1, False)

            unpaged_builtin = (
                await client.get("/api/skills", params={"source": "shared", "shared_kind": "builtin"})
            ).json()
            assert [item["slug"] for item in unpaged_builtin["data"]] == ["builtin-global"]
            assert unpaged_builtin["total"] == 1

            governance = (await client.get("/api/skills", params={"source": "shared", "limit": 20})).json()
            assert governance["total"] == 7
            assert {item["slug"] for item in governance["data"]} >= {
                "shared-disabled",
                "builtin-disabled",
                "image-gen",
                "mysql-reporter",
            }

            filtered_governance = (
                await client.get(
                    "/api/skills",
                    params={"source": "shared", "limit": 20, "exclude_retired_builtin": "true"},
                )
            ).json()
            assert filtered_governance["total"] == 5
            assert {item["slug"] for item in filtered_governance["data"]} == {
                "shared-product",
                "shared-finance",
                "builtin-global",
                "shared-disabled",
                "builtin-disabled",
            }
    finally:
        await engine.dispose()
