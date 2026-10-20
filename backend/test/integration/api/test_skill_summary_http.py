"""个人空间精确总数的 HTTP 与来源组合验证。"""

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from server.routers.skill_market_router import market_router
from server.routers.skill_router import user_skills
from server.utils.auth_middleware import get_current_user, get_db, get_required_user
from yuxi.agents.skills import service as skill_service
from yuxi.services import skill_catalog_summary_service as summary_service
from yuxi.storage.postgres.models_business import (
    Base,
    Department,
    MarketSkill,
    MarketSkillInstall,
    MarketSkillRelease,
    Skill,
    SkillShareRequest,
    User,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


@pytest.fixture(scope="session", autouse=True)
def ensure_live_api_schema():
    """本例仅使用内存中的隔离业务数据库。"""


@pytest.fixture(scope="session", autouse=True)
def cleanup_test_knowledge_resources():
    """本例不创建知识库资源。"""
    yield


@pytest.fixture(scope="session", autouse=True)
def cleanup_test_sandboxes():
    """本例不创建沙盒。"""
    yield


async def test_skill_summary_matches_authorized_personal_install_and_shared_sources(monkeypatch, tmp_path):
    """HTTP 总数只统计本人的原件和安装、授权共享与系统技能。"""
    personal_root = tmp_path / "personal"
    owned = personal_root / "viewer" / "owned"
    owned.mkdir(parents=True)
    (owned / "SKILL.md").write_text("---\nname: owned\ndescription: budget reports\n---\n# Owned\n", encoding="utf-8")
    monkeypatch.setattr(skill_service, "personal_skills_root", lambda uid: personal_root / uid)

    class SharedPageForSqlite:
        """此 HTTP 组合测试用固定共享计数替代 PostgreSQL 专用窗口查询。"""

        def __init__(self, _db):
            pass

        async def page_visible(self, user, **kwargs):
            assert user.uid == "viewer"
            assert kwargs["catalog_all"] is True
            assert kwargs["shared_kind"] == "shared"
            return [], {"": 1, "budget": 1, "hidden": 0}[kwargs["search"]]

    monkeypatch.setattr(summary_service, "SkillRepository", SharedPageForSqlite)

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    viewer = User(uid="viewer", username="Viewer", password_hash="x", role="user", department_id=1)
    app = FastAPI()
    app.include_router(user_skills, prefix="/api")
    app.include_router(market_router, prefix="/api")

    async def provide_user():
        return viewer

    async def provide_db():
        async with sessions() as db:
            yield db

    app.dependency_overrides[get_required_user] = provide_user
    app.dependency_overrides[get_current_user] = provide_user
    app.dependency_overrides[get_db] = provide_db

    try:
        async with engine.begin() as connection:
            await connection.run_sync(
                lambda conn: Base.metadata.create_all(
                    conn,
                    tables=[
                        Department.__table__,
                        User.__table__,
                        Skill.__table__,
                        SkillShareRequest.__table__,
                        MarketSkill.__table__,
                        MarketSkillRelease.__table__,
                        MarketSkillInstall.__table__,
                    ],
                )
            )
        async with sessions() as db:
            db.add_all(
                [
                    Department(id=1, name="Product"),
                    Department(id=2, name="Finance"),
                    viewer,
                    User(uid="other", username="Other", password_hash="x", department_id=2),
                ]
            )
            for slug, description, scope in [
                ("shared-visible", "budget helper", "global"),
                ("shared-hidden", "budget secret", "department"),
                ("builtin-visible", "general help", "global"),
            ]:
                db.add(
                    Skill(
                        slug=slug,
                        name=slug,
                        description=description,
                        source_type="builtin" if slug.startswith("builtin") else "upload",
                        dir_path=f"shared/{slug}",
                        share_config={
                            "version": 2,
                            "read_scope": {
                                "access_level": scope,
                                "department_ids": [2] if scope == "department" else [],
                                "user_uids": [],
                            },
                            "manage_scope": None,
                        },
                        enabled=True,
                        created_by="other",
                    )
                )
            for slug, owner_uid, personal_slug, scope, department_id, description in [
                ("owned-market", "viewer", "owned", "global", None, "slides"),
                ("hidden-cost", "other", "source", "department", 2, "budget secret"),
            ]:
                db.add(
                    MarketSkill(
                        id=slug,
                        slug=slug,
                        owner_uid=owner_uid,
                        personal_slug=personal_slug,
                        scope=scope,
                        department_id=department_id,
                        latest_release_id=f"release-{slug}",
                    )
                )
                db.add(
                    MarketSkillRelease(
                        id=f"release-{slug}",
                        market_skill_id=slug,
                        version=1,
                        name=slug,
                        description=description,
                        dir_path=f"market_releases/{slug}",
                        content_hash="hash",
                        request_id=f"request-{slug}",
                    )
                )
                db.add(MarketSkillInstall(uid="viewer", market_skill_id=slug, release_id=f"release-{slug}"))
            await db.commit()

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            personal = (await client.get("/api/skills", params={"source": "personal", "limit": 30})).json()
            installs = (await client.get("/api/skill-market/-/installs", params={"limit": 30})).json()
            all_counts = (await client.get("/api/skills/summary")).json()
            budget_counts = (await client.get("/api/skills/summary", params={"search": "budget"})).json()
            hidden_counts = (await client.get("/api/skills/summary", params={"search": "hidden"})).json()

        assert [item["slug"] for item in personal["data"]] == ["owned"]
        assert {item["slug"] for item in installs["data"]} == {"owned-market", "hidden-cost"}
        assert all_counts["data"] == {"created_total": 1, "all_total": 4}
        assert budget_counts["data"] == {"created_total": 1, "all_total": 2}
        assert hidden_counts["data"] == {"created_total": 0, "all_total": 1}
    finally:
        await engine.dispose()
