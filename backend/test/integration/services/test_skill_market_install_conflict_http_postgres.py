"""市场同名安装冲突的 HTTP 与专用 PostgreSQL 测试。"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from server.routers.skill_market_router import market_router
from server.utils.auth_middleware import get_db, get_required_user
from yuxi.agents.skills import service as skill_service
from yuxi.storage.postgres.models_business import (
    Base,
    Department,
    MarketSkill,
    MarketSkillInstall,
    MarketSkillLike,
    MarketSkillRelease,
    MCPServer,
    Skill,
    SkillShareRequest,
    User,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


@pytest.fixture(scope="session", autouse=True)
def ensure_live_api_schema():
    """只使用独立临时数据库，不检查线上 Schema。"""


@pytest.fixture(scope="session", autouse=True)
def cleanup_test_knowledge_resources():
    """本文件不创建知识库资源。"""
    yield


@pytest.fixture(scope="session", autouse=True)
def cleanup_test_sandboxes():
    """本文件不创建沙盒。"""
    yield


async def test_market_conflict_cards_and_install_guard_use_the_same_reason(monkeypatch, tmp_path: Path):
    """列表与详情预告冲突，安装拒绝同名技能且不写安装指针。"""
    database_url = os.getenv("YUXI_TEST_ISOLATED_POSTGRES_URL")
    if not database_url:
        pytest.skip("需要单独创建并设置 YUXI_TEST_ISOLATED_POSTGRES_URL")
    target = make_url(database_url)
    if target.host not in {"127.0.0.1", "localhost"} or not (target.database or "").startswith("yuxi_pytest_"):
        pytest.fail("测试数据库必须是本机 yuxi_pytest_ 前缀的独立数据库")

    schema = f"pytest_market_conflict_{uuid.uuid4().hex[:12]}"
    admin_engine = create_async_engine(database_url)
    async with admin_engine.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_async_engine(database_url, connect_args={"server_settings": {"search_path": schema}})
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    personal_root = tmp_path / "personal"
    skill_dir = personal_root / "viewer" / "personal-conflict"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: personal-conflict\ndescription: 个人版本\n---\n# Skill\n", encoding="utf-8"
    )
    monkeypatch.setattr(skill_service, "personal_skills_root", lambda uid: personal_root / uid)
    viewer = User(uid="viewer", username="Viewer", password_hash="x", role="user", department_id=1)
    app = FastAPI()
    app.include_router(market_router, prefix="/api")

    async def provide_user():
        """为测试 HTTP 请求提供当前用户。"""
        return viewer

    async def provide_db():
        """每次请求使用独立测试数据库会话。"""
        async with sessions() as db:
            yield db

    app.dependency_overrides[get_required_user] = provide_user
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
                        MarketSkillLike.__table__,
                        MCPServer.__table__,
                    ],
                )
            )
        async with sessions() as db:
            db.add(Department(id=1, name="研发部"))
            db.add_all([viewer, User(uid="author", username="Author", password_hash="x", role="user", department_id=1)])
            db.add_all(
                [
                    Skill(
                        slug=slug,
                        name="不可见共享技能名称",
                        description="PRIVATE SHARED DESCRIPTION",
                        source_type="personal_share",
                        dir_path=f"shared/{slug}",
                        share_config={"version": 2, "read_scope": None, "edit_scope": None, "manage_scope": None},
                        enabled=False,
                    )
                    for slug in ("personal-conflict", "reserved-conflict")
                ]
            )
            for slug in ("personal-conflict", "reserved-conflict", "free-skill"):
                db.add(
                    MarketSkill(
                        id=f"listing-{slug}",
                        slug=slug,
                        owner_uid="author",
                        personal_slug=slug,
                        scope="global",
                        latest_release_id=f"release-{slug}",
                    )
                )
                db.add(
                    MarketSkillRelease(
                        id=f"release-{slug}",
                        market_skill_id=f"listing-{slug}",
                        version=1,
                        name=slug,
                        description="公开市场技能",
                        category="other",
                        dir_path=f"market_releases/{slug}",
                        content_hash="hash",
                        tool_dependencies=[],
                        mcp_dependencies=[],
                        skill_dependencies=[],
                        request_id=f"request-{slug}",
                    )
                )
            await db.commit()

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            catalog = await client.get("/api/skill-market", params={"limit": 10})
            assert catalog.status_code == 200, catalog.text
            assert catalog.json()["total"] == 3
            cards = {card["slug"]: card for card in catalog.json()["data"]}
            expected = {"personal-conflict": "personal", "reserved-conflict": "reserved", "free-skill": None}
            assert {slug: card["install_conflict"] for slug, card in cards.items()} == expected
            assert "PRIVATE SHARED DESCRIPTION" not in catalog.text

            for slug, conflict in expected.items():
                detail = await client.get(f"/api/skill-market/{slug}")
                assert detail.status_code == 200, detail.text
                assert detail.json()["data"]["install_conflict"] == conflict
                assert "PRIVATE SHARED DESCRIPTION" not in detail.text

            for slug, reason in (
                ("personal-conflict", "已有同名个人技能，无法安装市场版"),
                ("reserved-conflict", "技能标识已被占用，无法安装市场版"),
            ):
                denied = await client.post(f"/api/skill-market/{slug}/install")
                assert denied.status_code == 400, denied.text
                assert denied.json()["detail"] == reason

            async with sessions() as db:
                assert (await db.scalars(select(MarketSkillInstall))).all() == []

            allowed = await client.post("/api/skill-market/free-skill/install")
            assert allowed.status_code == 200, allowed.text
            assert allowed.json()["data"]["installed_version"] == 1
            async with sessions() as db:
                installs = (await db.scalars(select(MarketSkillInstall))).all()
                assert [(item.uid, item.market_skill_id) for item in installs] == [("viewer", "listing-free-skill")]
    finally:
        await engine.dispose()
        async with admin_engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin_engine.dispose()
