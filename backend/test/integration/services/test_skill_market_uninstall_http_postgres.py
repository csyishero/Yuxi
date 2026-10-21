"""市场技能卸载的真实 HTTP、PostgreSQL 与用户投影边界。"""

from __future__ import annotations

import os
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from server.routers.skill_market_router import market_router
from server.utils.auth_middleware import get_current_user, get_db
from yuxi.agents.context import normalize_agent_context_config
from yuxi.agents.skills import market, service as skill_service
from yuxi.storage.postgres.manager import pg_manager
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
    """本文件只在隔离 Schema 中创建所需表。"""


@pytest.fixture(scope="session", autouse=True)
def cleanup_test_knowledge_resources():
    """本文件不创建知识库资源。"""
    yield


@pytest.fixture(scope="session", autouse=True)
def cleanup_test_sandboxes():
    """本文件不创建沙盒。"""
    yield


async def test_uninstall_removes_only_own_install_and_projection_even_after_scope_changes(monkeypatch, tmp_path: Path):
    """卸载只收回当前用户的安装，市场发布和他人的安装继续存在。"""
    schema = f"pytest_skill_uninstall_{uuid.uuid4().hex[:12]}"
    admin_engine = create_async_engine(os.environ["POSTGRES_URL"])
    async with admin_engine.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_async_engine(os.environ["POSTGRES_URL"], connect_args={"server_settings": {"search_path": schema}})
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    market_id = str(uuid.uuid4())
    release_id = str(uuid.uuid4())
    source = tmp_path / "market" / "report"
    source.mkdir(parents=True)
    (source / "SKILL.md").write_text("---\nname: report\ndescription: Shared report\n---\n# Report\n")

    async def no_personal_skills(_uid):
        """此例只验证市场来源。"""
        return []

    async def no_visible_mcp(_db, _user):
        """此例没有 MCP 引用。"""
        return set()

    @asynccontextmanager
    async def isolated_session():
        """投影刷新读取同一隔离 Schema。"""
        async with sessions() as db:
            yield db

    monkeypatch.setattr(skill_service, "list_personal_skills", no_personal_skills)
    monkeypatch.setattr(skill_service, "get_skill_projection_dir", lambda: tmp_path / "projections")
    monkeypatch.setattr(skill_service, "get_visible_mcp_reference_slugs", no_visible_mcp)
    monkeypatch.setattr(market, "get_skill_data_dir", lambda: tmp_path)
    monkeypatch.setattr(pg_manager, "get_async_session_context", isolated_session)

    users = {
        uid: User(uid=uid, username=uid, password_hash="x", role="user", department_id=1)
        for uid in ("member-a", "member-b", "other")
    }
    current = users["member-a"]
    app = FastAPI()
    app.include_router(market_router, prefix="/api")

    async def provide_user():
        """由测试切换当前已认证用户。"""
        return current

    async def provide_db():
        """每次请求都使用独立事务会话。"""
        async with sessions() as db:
            yield db

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
                        MarketSkillLike.__table__,
                        MCPServer.__table__,
                    ],
                )
            )
            await connection.execute(text("INSERT INTO departments (id, name) VALUES (1, 'Credit'), (2, 'Risk')"))
        async with sessions() as db:
            db.add_all(
                [
                    *users.values(),
                    MarketSkill(
                        id=market_id,
                        slug="report",
                        owner_uid="publisher",
                        personal_slug="report",
                        scope="department",
                        department_id=1,
                        latest_release_id=release_id,
                    ),
                    MarketSkillRelease(
                        id=release_id,
                        market_skill_id=market_id,
                        version=1,
                        name="Team report",
                        description="Shared report",
                        dir_path="market/report",
                        content_hash="report-hash",
                        request_id=str(uuid.uuid4()),
                    ),
                ]
            )
            await db.flush()
            db.add_all(
                [
                    MarketSkillInstall(uid="member-a", market_skill_id=market_id, release_id=release_id),
                    MarketSkillInstall(uid="member-b", market_skill_id=market_id, release_id=release_id),
                ]
            )
            await db.commit()

        projection_name = market.market_projection_slug(release_id)
        member_a_projection = tmp_path / "projections" / "member-a" / projection_name / "SKILL.md"
        member_b_projection = tmp_path / "projections" / "member-b" / projection_name / "SKILL.md"
        await skill_service.refresh_user_skill_projection_async("member-a")
        await skill_service.refresh_user_skill_projection_async("member-b")
        assert member_a_projection.is_file() and member_b_projection.is_file()
        fixed_agent = {
            "tools": [],
            "knowledges": [],
            "mcps": [],
            "skills": ["report"],
            "preload_skills": ["report"],
        }
        async with sessions() as db:
            assert await skill_service.select_default_skill_slugs(db, users["member-a"]) == ["report"]
            assert (await normalize_agent_context_config(fixed_agent, db=db, user=users["member-a"]))["skills"] == [
                "report"
            ]

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            current = None
            assert (await client.get("/api/skill-market/-/installs")).status_code == 401
            assert (await client.delete("/api/skill-market/report/install")).status_code == 401

            current = users["other"]
            assert (await client.get("/api/skill-market/-/installs")).json()["data"] == []
            denied = await client.delete("/api/skill-market/report/install")
            assert denied.status_code == 200
            assert denied.json()["data"] == {"slug": "report", "uninstalled": False}

            current = users["member-a"]
            own_installs = (await client.get("/api/skill-market/-/installs")).json()["data"]
            assert own_installs == [
                {
                    "slug": "report",
                    "name": "Team report",
                    "description": "Shared report",
                    "category": "other",
                    "installed_version": 1,
                    "owner_uid": "publisher",
                    "personal_slug": "report",
                    "own_personal_slug": None,
                    "visible": True,
                    "unavailable_reason": None,
                }
            ]
            removed = await client.delete("/api/skill-market/report/install")
            assert removed.status_code == 200, removed.text
            assert removed.json()["data"] == {"slug": "report", "uninstalled": True}
            assert (await client.get("/api/skill-market/-/installs")).json()["data"] == []
            assert not member_a_projection.exists()
            assert member_b_projection.is_file()
            assert (await client.get("/api/skill-market/report")).json()["data"]["install_count"] == 1

            async with sessions() as db:
                assert await skill_service.select_default_skill_slugs(db, users["member-a"]) == []
                assert await skill_service.select_default_skill_slugs(db, users["member-b"]) == ["report"]
                effective_fixed = await normalize_agent_context_config(fixed_agent, db=db, user=users["member-a"])
                assert effective_fixed["skills"] == []
                assert effective_fixed["preload_skills"] == []

            async with sessions() as db:
                listing = await db.get(MarketSkill, market_id)
                listing.department_id = 2
                newer_release = MarketSkillRelease(
                    id=str(uuid.uuid4()),
                    market_skill_id=market_id,
                    version=2,
                    name="Restricted update",
                    description="Newer restricted description",
                    dir_path="market/restricted",
                    content_hash="restricted-hash",
                    request_id=str(uuid.uuid4()),
                )
                db.add(newer_release)
                listing.latest_release_id = newer_release.id
                await db.commit()
            current = users["member-b"]
            assert (await client.get("/api/skill-market/report")).status_code == 404
            assert (await client.get("/api/skill-market?installed_only=true")).json()["data"] == []
            invisible_installs = (await client.get("/api/skill-market/-/installs")).json()["data"]
            assert invisible_installs == [
                {
                    "slug": "report",
                    "installed_version": 1,
                    "visible": False,
                    "unavailable_reason": "out_of_scope",
                }
            ]
            async with sessions() as db:
                assert await market.market_projection_sources(db, users["member-b"]) == {}
                assert [
                    item
                    for item in await skill_service.list_accessible_skills(db, users["member-b"])
                    if item.source_scope == "market"
                ] == []
            await skill_service.refresh_user_skill_projection_async("member-b")
            assert not member_b_projection.exists()
            async with sessions() as db:
                listing = await db.get(MarketSkill, market_id)
                listing.scope = "global"
                listing.department_id = None
                await db.commit()
            async with sessions() as db:
                assert set(await market.market_projection_sources(db, users["member-b"])) == {projection_name}
                assert [
                    item.slug
                    for item in await skill_service.list_accessible_skills(db, users["member-b"])
                    if item.source_scope == "market"
                ] == ["report"]
                assert (await market.list_user_market_installs(db, user=users["member-b"]))[0][
                    "unavailable_reason"
                ] is None
            await skill_service.refresh_user_skill_projection_async("member-b")
            assert member_b_projection.is_file()
            async with sessions() as db:
                listing = await db.get(MarketSkill, market_id)
                listing.is_published = False
                member_b = await db.scalar(select(User).where(User.uid == "member-b"))
                member_b.department_id = None
                await db.commit()
            users["member-b"].department_id = None
            assert (await client.get("/api/skill-market/report")).status_code == 400
            assert (await client.get("/api/skill-market/-/installs")).json()["data"] == [
                {"slug": "report", "installed_version": 1, "visible": False, "unavailable_reason": "unpublished"}
            ]
            invisible_removal = await client.delete("/api/skill-market/report/install")
            assert invisible_removal.status_code == 200, invisible_removal.text
            assert invisible_removal.json()["data"]["uninstalled"] is True
            assert (await client.get("/api/skill-market/-/installs")).json()["data"] == []
            assert not member_b_projection.exists()
            assert (await client.delete("/api/skill-market/report/install")).json()["data"]["uninstalled"] is False
            assert (await client.delete("/api/skill-market/missing/install")).json()["data"]["uninstalled"] is False

        async with sessions() as db:
            listing = await db.get(MarketSkill, market_id)
            listing.department_id = 1
            await db.commit()
            assert await db.get(MarketSkill, market_id) is not None
            assert await db.get(MarketSkillRelease, release_id) is not None
            assert (await db.scalars(select(MarketSkillInstall))).all() == []
            assert "report" not in await skill_service.select_default_skill_slugs(db, users["member-a"])
            assert "report" not in await skill_service.select_default_skill_slugs(db, users["member-b"])
            assert await market.market_projection_sources(db, users["member-a"]) == {}
    finally:
        await engine.dispose()
        async with admin_engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin_engine.dispose()


async def test_relisted_projection_excludes_previous_generation(monkeypatch, tmp_path: Path):
    """新批次确认后，实际用户目录只包含本批次的审核版本。"""
    schema = f"pytest_skill_relisted_{uuid.uuid4().hex[:12]}"
    admin_engine = create_async_engine(os.environ["POSTGRES_URL"])
    async with admin_engine.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_async_engine(os.environ["POSTGRES_URL"], connect_args={"server_settings": {"search_path": schema}})
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    market_id = str(uuid.uuid4())
    release_ids = {version: str(uuid.uuid4()) for version in (1, 2, 4)}

    async def no_personal_skills(_uid):
        return []

    monkeypatch.setattr(skill_service, "list_personal_skills", no_personal_skills)
    monkeypatch.setattr(skill_service, "get_skill_projection_dir", lambda: tmp_path / "projections")
    monkeypatch.setattr(skill_service, "get_skills_root_dir", lambda: tmp_path / "shared")
    monkeypatch.setattr(market, "get_skill_data_dir", lambda: tmp_path)
    (tmp_path / "shared").mkdir()
    for version in release_ids:
        source = tmp_path / "market" / f"v{version}"
        source.mkdir(parents=True)
        (source / "SKILL.md").write_text(f"---\nname: report\ndescription: v{version}\n---\n# V{version}\n")

    member = User(uid="member", username="Member", password_hash="x", role="user", department_id=1)
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
            await connection.execute(text("INSERT INTO departments (id, name) VALUES (1, 'Credit')"))
        async with sessions() as db:
            db.add(member)
            await db.flush()
            db.add_all(
                [
                    MarketSkill(
                        id=market_id,
                        slug="report",
                        owner_uid="publisher",
                        personal_slug="report",
                        scope="department",
                        department_id=1,
                        latest_release_id=release_ids[4],
                    ),
                    *[
                        MarketSkillRelease(
                            id=release_ids[version],
                            market_skill_id=market_id,
                            version=version,
                            name=f"Report v{version}",
                            description=f"Reviewed release v{version}",
                            dir_path=f"market/v{version}",
                            content_hash=f"hash-v{version}",
                            request_id=str(uuid.uuid4()),
                        )
                        for version in release_ids
                    ],
                ]
            )
            await db.flush()
            db.add(MarketSkillInstall(uid="member", market_skill_id=market_id, release_id=release_ids[2]))
            await db.commit()

        async with sessions() as db:
            before = await market.market_projection_sources(db, member)
            assert set(before) == {
                market.market_projection_slug(release_ids[1]),
                market.market_projection_slug(release_ids[2]),
            }
            skill_service.sync_user_accessible_skills("member", before)
        projection_root = tmp_path / "projections" / "member"
        assert {entry.name for entry in projection_root.iterdir()} == set(before)

        async with sessions() as db:
            listing = await db.get(MarketSkill, market_id)
            listing.publication_generation = 2
            listing.active_from_version = 4
            await db.commit()
        async with sessions() as db:
            assert await market.market_projection_sources(db, member) == {}
            assert [
                item for item in await skill_service.list_accessible_skills(db, member) if item.source_scope == "market"
            ] == []
            cards = await market.list_user_market_installs(db, user=member)
            assert cards[0]["visible"] is True
            assert cards[0]["unavailable_reason"] == "needs_confirmation"
            assert cards[0]["name"] == "Report v4"
            assert cards[0]["latest_version"] == 4
            assert cards[0]["upgrade_available"] is True
            detail = await market.get_market_skill(db, slug="report", user=member)
            assert detail["installed_version"] == 2
            assert detail["latest_version"] == 4
            assert detail["upgrade_available"] is True
        skill_service.sync_user_accessible_skills("member", {})
        assert list(projection_root.iterdir()) == []

        async with sessions() as db:
            install = await db.get(MarketSkillInstall, ("member", market_id))
            install.confirmed_generation = 2
            await db.commit()
        async with sessions() as db:
            assert await market.market_projection_sources(db, member) == {}
            assert [
                item for item in await skill_service.list_accessible_skills(db, member) if item.source_scope == "market"
            ] == []
            assert (await market.list_user_market_installs(db, user=member))[0][
                "unavailable_reason"
            ] == "needs_confirmation"

        async with sessions() as db:
            install = await db.get(MarketSkillInstall, ("member", market_id))
            install.release_id = release_ids[4]
            await db.commit()
        async with sessions() as db:
            accessible = [
                item for item in await skill_service.list_accessible_skills(db, member) if item.source_scope == "market"
            ]
            assert [(item.slug, item.version) for item in accessible] == [("report", "4")]
            sources = await market.market_projection_sources(db, member)
            assert set(sources) == {market.market_projection_slug(release_ids[4])}
            assert (await market.list_user_market_installs(db, user=member))[0]["unavailable_reason"] is None
            skill_service.sync_user_accessible_skills("member", sources)
        assert {entry.name for entry in projection_root.iterdir()} == {market.market_projection_slug(release_ids[4])}
        assert (
            (projection_root / market.market_projection_slug(release_ids[4]) / "SKILL.md")
            .read_text()
            .endswith("# V4\n")
        )
        async with sessions() as db:
            assert [release.version for release in await market.MarketSkillRepository(db).releases(market_id)] == [
                1,
                2,
                4,
            ]
            assert (await db.get(MarketSkillInstall, ("member", market_id))).confirmed_generation == 2
    finally:
        await engine.dispose()
        async with admin_engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin_engine.dispose()
