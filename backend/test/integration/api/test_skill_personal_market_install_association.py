"""个人技能与本人市场安装跨分页的身份关联。"""

from datetime import datetime

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from server.routers.skill_market_router import market_router
from server.routers.skill_router import user_skills
from server.utils.auth_middleware import get_current_user, get_db, get_required_user
from yuxi.agents.skills import market as market_module
from yuxi.agents.skills import service as skill_service
from yuxi.storage.postgres.models_business import (
    Base,
    Department,
    MarketSkill,
    MarketSkillInstall,
    MarketSkillRelease,
    Skill,
    User,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


@pytest.fixture(scope="session", autouse=True)
def ensure_live_api_schema():
    """本例只使用独立 SQLite 数据库。"""


@pytest.fixture(scope="session", autouse=True)
def cleanup_test_knowledge_resources():
    """本例不创建知识库资源。"""
    yield


@pytest.fixture(scope="session", autouse=True)
def cleanup_test_sandboxes():
    """本例不创建沙盒。"""
    yield


async def test_personal_and_install_pages_link_only_the_same_owners_source(monkeypatch, tmp_path):
    """当前个人页与安装页可跨页识别同源条目，隐藏市场版本仍裁剪详情。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    viewer = User(uid="viewer", username="Viewer", password_hash="x", role="user", department_id=1)
    personal = [
        skill_service.ResolvedSkill(
            id=f"personal:owned-{index}",
            slug=f"owned-{index}",
            name=f"Owned {index}",
            description="Personal description",
            source_type="personal",
            source_scope="personal",
            source_dir=tmp_path / f"owned-{index}",
            enabled=True,
            created_by="viewer",
            share_config=None,
            tool_dependencies=[],
            mcp_dependencies=[],
            skill_dependencies=[],
        )
        for index in (1, 2)
    ]
    app = FastAPI()
    app.include_router(user_skills, prefix="/api")
    app.include_router(market_router, prefix="/api")

    async def provide_user():
        return viewer

    async def provide_db():
        async with sessions() as db:
            yield db

    async def personal_page(_uid: str, *, offset: int, limit: int, search: str = ""):
        assert search == ""
        return personal[offset : offset + limit], offset + limit < len(personal)

    async def personal_by_slug(uid: str, slug: str):
        assert uid == "viewer"
        for item in personal:
            if item.slug == slug:
                return item
        raise ValueError("个人 Skill 不存在")

    app.dependency_overrides[get_required_user] = provide_user
    app.dependency_overrides[get_current_user] = provide_user
    app.dependency_overrides[get_db] = provide_db
    monkeypatch.setattr(skill_service, "list_personal_skills_page", personal_page)
    monkeypatch.setattr(market_module, "get_personal_skill", personal_by_slug)

    try:
        async with engine.begin() as connection:
            await connection.run_sync(
                lambda conn: Base.metadata.create_all(
                    conn,
                    tables=[
                        Department.__table__,
                        User.__table__,
                        Skill.__table__,
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
                    User(uid="other", username="Other", password_hash="x", department_id=1),
                ]
            )
            for slug, owner_uid, personal_slug, department_id, day in [
                ("other-market", "other", "owned-1", None, 2),
                ("own-market", "viewer", "owned-2", 2, 1),
            ]:
                db.add(
                    MarketSkill(
                        id=slug,
                        slug=slug,
                        owner_uid=owner_uid,
                        personal_slug=personal_slug,
                        scope="global" if department_id is None else "department",
                        department_id=department_id,
                        latest_release_id=f"release-{slug}",
                    )
                )
                db.add(
                    MarketSkillRelease(
                        id=f"release-{slug}",
                        market_skill_id=slug,
                        version=2,
                        name=f"Secret {slug}",
                        description="Private release description",
                        category="data_analysis",
                        dir_path=f"market_releases/{slug}",
                        content_hash="hash",
                        request_id=f"request-{slug}",
                    )
                )
                db.add(
                    MarketSkillInstall(
                        uid="viewer",
                        market_skill_id=slug,
                        release_id=f"release-{slug}",
                        updated_at=datetime(2026, 1, day),
                    )
                )
            await db.commit()

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            first_personal = (await client.get("/api/skills", params={"source": "personal", "limit": 1})).json()
            assert first_personal["data"][0]["slug"] == "owned-1"
            assert first_personal["data"][0]["market_install"] is None

            second_personal = (
                await client.get("/api/skills", params={"source": "personal", "limit": 1, "offset": 1})
            ).json()
            attached = second_personal["data"][0]["market_install"]
            assert (attached["slug"], attached["installed_version"], attached["visible"]) == (
                "own-market",
                2,
                False,
            )
            assert attached["description"] == ""
            assert attached["owner_uid"] == "viewer"
            assert attached["own_personal_slug"] == "owned-2"

            first_install = (await client.get("/api/skill-market/-/installs", params={"limit": 1})).json()
            assert first_install["data"][0]["slug"] == "other-market"
            assert first_install["data"][0]["own_personal"] is None

            second_install = (await client.get("/api/skill-market/-/installs", params={"limit": 1, "offset": 1})).json()
            installed = second_install["data"][0]
            assert installed["slug"] == "own-market"
            assert installed["description"] == ""
            assert installed["own_personal"]["slug"] == "owned-2"
            assert installed["own_personal"]["name"] == "Owned 2"
    finally:
        await engine.dispose()
