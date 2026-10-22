"""Skill 市场的真实 HTTP、PostgreSQL 与发布文件集成测试。"""

from __future__ import annotations

import os
import uuid
from datetime import datetime
from pathlib import Path
from contextlib import asynccontextmanager

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from server.routers.skill_market_router import market_router
from server.utils.auth_middleware import get_current_user, get_db, get_required_user
from yuxi.agents.skills import market, service as skill_service, sharing
from yuxi.agents.skills.service import list_accessible_skills
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
    """使用隔离 Schema，不依赖运行中的 API。"""


@pytest.fixture(scope="session", autouse=True)
def cleanup_test_knowledge_resources():
    """本文件不创建知识库资源。"""
    yield


@pytest.fixture(scope="session", autouse=True)
def cleanup_test_sandboxes():
    """本文件不创建沙盒。"""
    yield


@pytest.fixture
async def market_lifecycle(monkeypatch, tmp_path: Path):
    """用独立 PostgreSQL Schema 建立已发布、已安装的首批次市场 Skill。"""
    schema = f"pytest_skill_lifecycle_{uuid.uuid4().hex[:12]}"
    admin_engine = create_async_engine(os.environ["POSTGRES_URL"])
    async with admin_engine.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_async_engine(os.environ["POSTGRES_URL"], connect_args={"server_settings": {"search_path": schema}})
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def isolated_session():
        """投影刷新继续使用当前隔离 Schema。"""
        async with sessions() as db:
            yield db

    monkeypatch.setattr(pg_manager, "get_async_session_context", isolated_session)
    personal_root = tmp_path / "personal"
    skill_dir = personal_root / "batch-guide"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: batch-guide\ndescription: first release\n---\n# First\n", encoding="utf-8"
    )
    (tmp_path / "shared").mkdir()
    monkeypatch.setattr(sharing, "personal_skills_root", lambda _uid: personal_root)
    monkeypatch.setattr(sharing, "get_skill_data_dir", lambda: tmp_path)
    monkeypatch.setattr(market, "get_skill_data_dir", lambda: tmp_path)
    monkeypatch.setattr(skill_service, "get_skill_projection_dir", lambda: tmp_path / "projections")

    async def no_personal_skills(_uid):
        """隔离测试用户的真实个人工作区。"""
        return []

    async def no_dependencies(**_kwargs):
        """本组发布快照没有外部依赖。"""
        return [], [], []

    monkeypatch.setattr(market, "list_personal_skills", no_personal_skills)
    monkeypatch.setattr(skill_service, "list_personal_skills", no_personal_skills)
    monkeypatch.setattr(market, "validate_skill_dependencies", no_dependencies)
    current = [User(uid="author", username="Author", password_hash="x", department_id=1)]
    app = FastAPI()
    app.include_router(market_router, prefix="/api")

    async def provide_user():
        """为每个 HTTP 请求提供可切换的真实用户身份。"""
        async with sessions() as db:
            return await db.scalar(select(User).where(User.uid == current[0].uid))

    async def provide_db():
        """为每个 HTTP 请求建立独立数据库会话。"""
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
                        MarketSkillLike.__table__,
                        MCPServer.__table__,
                    ],
                )
            )
            await connection.execute(text("INSERT INTO departments (id, name) VALUES (1, 'Credit')"))
        async with sessions() as db:
            db.add_all(
                [
                    User(uid="author", username="Author", password_hash="x", department_id=1),
                    User(uid="reader", username="Reader", password_hash="x", department_id=1),
                    User(uid="new-reader", username="New Reader", password_hash="x", department_id=1),
                    User(uid="admin", username="Admin", password_hash="x", role="admin", department_id=1),
                ]
            )
            await db.commit()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            first = await client.post(
                "/api/skill-market/personal/batch-guide/submit", json={"application_reason": "首版发布"}
            )
            assert first.status_code == 200, first.text
            current[0] = User(uid="admin", username="Admin", password_hash="x", role="admin", department_id=1)
            approved = await client.post(
                f"/api/skill-market/requests/{first.json()['data']['id']}/approve",
                json={"scope": "department"},
            )
            assert approved.status_code == 200, approved.text
            current[0] = User(uid="reader", username="Reader", password_hash="x", department_id=1)
            installed = await client.post("/api/skill-market/batch-guide/install")
            assert installed.status_code == 200, installed.text
            current[0] = User(uid="author", username="Author", password_hash="x", department_id=1)
            yield {"client": client, "current": current, "sessions": sessions, "skill_dir": skill_dir, "root": tmp_path}
    finally:
        await engine.dispose()
        async with admin_engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin_engine.dispose()


async def _withdraw_lifecycle_listing(sessions):
    """模拟待实现的下架事务，保留旧版本和安装指针。"""
    async with sessions() as db:
        listing = await db.scalar(select(MarketSkill).where(MarketSkill.slug == "batch-guide"))
        listing.is_published = False
        listing.publication_generation = 2
        listing.state_revision = 2
        await db.commit()


async def _relist_lifecycle_fixture(fixture):
    """从新的个人快照审核同一条目的第二批次。"""
    await _withdraw_lifecycle_listing(fixture["sessions"])
    (fixture["skill_dir"] / "SKILL.md").write_text(
        "---\nname: batch-guide\ndescription: second release\n---\n# Second\n", encoding="utf-8"
    )
    client = fixture["client"]
    submitted = await client.post(
        "/api/skill-market/personal/batch-guide/submit", json={"application_reason": "下架后重新申请"}
    )
    assert submitted.status_code == 200, submitted.text
    fixture["current"][0] = User(uid="admin", username="Admin", password_hash="x", role="admin", department_id=1)
    approved = await client.post(
        f"/api/skill-market/requests/{submitted.json()['data']['id']}/approve", json={"scope": "department"}
    )
    assert approved.status_code == 200, approved.text
    fixture["current"][0] = User(uid="reader", username="Reader", password_hash="x", department_id=1)
    return submitted.json()["data"]["id"]


async def test_old_pending_request_cannot_relist(market_lifecycle):
    """旧批次待审申请不能把下架条目重新发布。"""
    fixture = market_lifecycle
    client = fixture["client"]
    pending = await client.post(
        "/api/skill-market/personal/batch-guide/submit", json={"application_reason": "下架前旧快照"}
    )
    assert pending.status_code == 200, pending.text
    request_id = pending.json()["data"]["id"]
    async with fixture["sessions"]() as db:
        assert (await db.get(SkillShareRequest, request_id)).market_generation == 1
        first = await db.scalar(select(MarketSkill).where(MarketSkill.slug == "batch-guide"))
        first_release_id = first.latest_release_id
    await _withdraw_lifecycle_listing(fixture["sessions"])

    fixture["current"][0] = User(uid="admin", username="Admin", password_hash="x", role="admin", department_id=1)
    denied = await client.post(f"/api/skill-market/requests/{request_id}/approve", json={"scope": "department"})
    assert denied.status_code == 400, denied.text
    async with fixture["sessions"]() as db:
        listing = await db.scalar(select(MarketSkill).where(MarketSkill.slug == "batch-guide"))
        assert listing.is_published is False
        assert listing.publication_generation == 2
        assert listing.latest_release_id == first_release_id
        assert (await db.get(SkillShareRequest, request_id)).status == "pending"
        assert [release.version for release in (await db.scalars(select(MarketSkillRelease))).all()] == [1]


async def test_fresh_snapshot_relists_same_slug_next_version(market_lifecycle):
    """下架后的新快照沿用条目和 slug，并设定新批次版本下限。"""
    fixture = market_lifecycle
    async with fixture["sessions"]() as db:
        original = await db.scalar(select(MarketSkill).where(MarketSkill.slug == "batch-guide"))
        original_id = original.id
        first_release_id = original.latest_release_id
    request_id = await _relist_lifecycle_fixture(fixture)

    async with fixture["sessions"]() as db:
        request = await db.get(SkillShareRequest, request_id)
        listing = await db.scalar(select(MarketSkill).where(MarketSkill.slug == "batch-guide"))
        releases = (await db.scalars(select(MarketSkillRelease).order_by(MarketSkillRelease.version))).all()
        assert request.market_generation == 2
        assert listing.id == original_id
        assert listing.slug == "batch-guide"
        assert listing.scope == "department" and listing.department_id == 1
        assert listing.is_published is True
        assert listing.publication_generation == 2
        assert listing.active_from_version == 2
        assert listing.state_revision == 3
        assert [release.version for release in releases] == [1, 2]
        assert releases[0].id == first_release_id
        assert listing.latest_release_id == releases[1].id
        assert (fixture["root"] / releases[0].dir_path / "SKILL.md").is_file()
        assert (fixture["root"] / releases[1].dir_path / "SKILL.md").read_text(encoding="utf-8").endswith("# Second\n")


async def test_install_cannot_confirm_relisted_generation(market_lifecycle):
    """已有安装行调用普通安装，不得确认再次上架后的批次。"""
    fixture = market_lifecycle
    await _relist_lifecycle_fixture(fixture)
    installed = await fixture["client"].post("/api/skill-market/batch-guide/install")
    assert installed.status_code == 200, installed.text
    assert installed.json()["data"]["installed_version"] == 1
    async with fixture["sessions"]() as db:
        listing = await db.scalar(select(MarketSkill).where(MarketSkill.slug == "batch-guide"))
        install = await db.get(MarketSkillInstall, ("reader", listing.id))
        assert install.confirmed_generation == 1
        assert (await db.get(MarketSkillRelease, install.release_id)).version == 1
        reader = User(uid="reader", username="Reader", password_hash="x", department_id=1)
        assert [item for item in await list_accessible_skills(db, reader) if item.source_scope == "market"] == []
        assert await market.market_projection_sources(db, reader) == {}


async def test_upgrade_confirms_relisted_generation(market_lifecycle):
    """本人明确升级后，只恢复当前批次版本的运行时和文件投影。"""
    fixture = market_lifecycle
    await _relist_lifecycle_fixture(fixture)
    upgraded = await fixture["client"].post("/api/skill-market/batch-guide/upgrade")
    assert upgraded.status_code == 200, upgraded.text
    assert upgraded.json()["data"]["installed_version"] == 2
    async with fixture["sessions"]() as db:
        listing = await db.scalar(select(MarketSkill).where(MarketSkill.slug == "batch-guide"))
        install = await db.get(MarketSkillInstall, ("reader", listing.id))
        assert install.confirmed_generation == 2
        assert install.release_id == listing.latest_release_id
        reader = User(uid="reader", username="Reader", password_hash="x", department_id=1)
        usable = [item for item in await list_accessible_skills(db, reader) if item.source_scope == "market"]
        assert [(item.slug, item.version) for item in usable] == [("batch-guide", "2")]
        sources = await market.market_projection_sources(db, reader)
        assert set(sources) == {market.market_projection_slug(listing.latest_release_id)}
        skill_service.sync_user_accessible_skills("reader", sources)
        projection = fixture["root"] / "projections" / "reader"
        assert {path.name for path in projection.iterdir()} == set(sources)
        assert (projection / next(iter(sources)) / "SKILL.md").read_text(encoding="utf-8").endswith("# Second\n")


async def test_first_install_confirms_relisted_generation(market_lifecycle):
    """新成员首次安装再上架条目时，确认当前批次并立即获得新版。"""
    fixture = market_lifecycle
    await _relist_lifecycle_fixture(fixture)
    fixture["current"][0] = User(uid="new-reader", username="New Reader", password_hash="x", department_id=1)
    installed = await fixture["client"].post("/api/skill-market/batch-guide/install")
    assert installed.status_code == 200, installed.text
    assert installed.json()["data"]["installed_version"] == 2
    async with fixture["sessions"]() as db:
        listing = await db.scalar(select(MarketSkill).where(MarketSkill.slug == "batch-guide"))
        fresh = await db.get(MarketSkillInstall, ("new-reader", listing.id))
        old = await db.get(MarketSkillInstall, ("reader", listing.id))
        assert fresh.confirmed_generation == 2
        assert fresh.release_id == listing.latest_release_id
        assert old.confirmed_generation == 1
        usable = [
            item for item in await list_accessible_skills(db, fixture["current"][0]) if item.source_scope == "market"
        ]
        assert [(item.slug, item.version) for item in usable] == [("batch-guide", "2")]


async def test_submission_reloads_generation_after_listing_lock(market_lifecycle):
    """旧会话已缓存条目时，提交仍应读取另一事务已提交的下架批次。"""
    fixture = market_lifecycle
    async with fixture["sessions"]() as submitting_db:
        cached = await submitting_db.scalar(select(MarketSkill).where(MarketSkill.slug == "batch-guide"))
        assert cached.publication_generation == 1
        await _withdraw_lifecycle_listing(fixture["sessions"])
        (fixture["skill_dir"] / "SKILL.md").write_text(
            "---\nname: batch-guide\ndescription: second release\n---\n# Second\n", encoding="utf-8"
        )
        submitted = await market.submit_market_skill(
            submitting_db,
            slug="batch-guide",
            application_reason="缓存条目后重新申请",
            operator=User(uid="author", username="Author", password_hash="x", department_id=1),
        )
        assert submitted.market_generation == 2
        assert (
            (fixture["root"] / "share_requests" / submitted.id / "SKILL.md")
            .read_text(encoding="utf-8")
            .endswith("# Second\n")
        )
    async with fixture["sessions"]() as db:
        assert (await db.get(SkillShareRequest, submitted.id)).market_generation == 2


async def test_upgrade_reloads_listing_after_lock(market_lifecycle):
    """旧会话缓存首批次后，手动升级必须确认另一事务已发布的新版。"""
    fixture = market_lifecycle
    async with fixture["sessions"]() as upgrading_db:
        cached = await upgrading_db.scalar(select(MarketSkill).where(MarketSkill.slug == "batch-guide"))
        old_install = await upgrading_db.get(MarketSkillInstall, ("reader", cached.id))
        assert cached.publication_generation == old_install.confirmed_generation == 1
        await _relist_lifecycle_fixture(fixture)
        result = await market.install_market_skill(
            upgrading_db,
            slug="batch-guide",
            user=User(uid="reader", username="Reader", password_hash="x", department_id=1),
            upgrade=True,
        )
        assert result["installed_version"] == 2
    async with fixture["sessions"]() as db:
        listing = await db.scalar(select(MarketSkill).where(MarketSkill.slug == "batch-guide"))
        install = await db.get(MarketSkillInstall, ("reader", listing.id))
        assert install.confirmed_generation == 2
        assert install.release_id == listing.latest_release_id


async def test_review_reloads_request_after_lock(market_lifecycle):
    """旧会话缓存待审状态后，不得覆盖另一事务已提交的驳回。"""
    fixture = market_lifecycle
    submitted = await fixture["client"].post(
        "/api/skill-market/personal/batch-guide/submit", json={"application_reason": "并发审核快照"}
    )
    assert submitted.status_code == 200, submitted.text
    request_id = submitted.json()["data"]["id"]
    admin = User(uid="admin", username="Admin", password_hash="x", role="admin", department_id=1)
    async with fixture["sessions"]() as approving_db:
        cached = await approving_db.get(SkillShareRequest, request_id)
        assert cached.status == "pending"
        async with fixture["sessions"]() as rejecting_db:
            rejected = await market.review_market_skill(
                rejecting_db, request_id=request_id, approved=False, scope=None, note="不符合要求", operator=admin
            )
            assert rejected.status == "rejected"
        with pytest.raises(ValueError, match="已审核"):
            await market.review_market_skill(
                approving_db, request_id=request_id, approved=True, scope="department", note="", operator=admin
            )
    async with fixture["sessions"]() as db:
        assert (await db.get(SkillShareRequest, request_id)).status == "rejected"
        assert [release.version for release in (await db.scalars(select(MarketSkillRelease))).all()] == [1]


async def test_market_review_visibility_install_like_and_manual_upgrade(monkeypatch, tmp_path: Path):
    """市场申请隔离、安装指针和版本快照均落在真实 PostgreSQL。"""
    schema = f"pytest_skill_market_{uuid.uuid4().hex[:12]}"
    admin_engine = create_async_engine(os.environ["POSTGRES_URL"])
    async with admin_engine.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_async_engine(os.environ["POSTGRES_URL"], connect_args={"server_settings": {"search_path": schema}})
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    personal_root = tmp_path / "personal"
    skill_dir = personal_root / "credit-review"
    skill_dir.mkdir(parents=True)
    (tmp_path / "shared").mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: credit-review\ndescription: first release\n---\n# First\n", encoding="utf-8"
    )
    monkeypatch.setattr(sharing, "personal_skills_root", lambda _uid: personal_root)
    monkeypatch.setattr(sharing, "get_skill_data_dir", lambda: tmp_path)
    monkeypatch.setattr(market, "get_skill_data_dir", lambda: tmp_path)
    monkeypatch.setattr(skill_service, "get_skill_projection_dir", lambda: tmp_path / "projections")
    monkeypatch.setattr(skill_service, "get_skills_root_dir", lambda: tmp_path / "shared")
    monkeypatch.setattr(sharing, "get_skills_root_dir", lambda: tmp_path / "shared")

    async def no_personal_skills(_uid):
        """仅隔离与市场断言无关的个人列表。"""
        return []

    monkeypatch.setattr(market, "list_personal_skills", no_personal_skills)
    monkeypatch.setattr(skill_service, "list_personal_skills", no_personal_skills)

    async def no_dependencies(**_kwargs):
        """本例没有外部工具依赖。"""
        return [], [], []

    monkeypatch.setattr(market, "validate_skill_dependencies", no_dependencies)
    monkeypatch.setattr(sharing, "validate_skill_dependencies", no_dependencies)
    current = User(uid="author", username="Author", password_hash="x", role="user", department_id=1)
    app = FastAPI()
    app.include_router(market_router, prefix="/api")

    async def provide_user():
        """模拟已认证 HTTP 用户。"""
        return current

    async def provide_db():
        """每次请求使用独立事务会话。"""
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
                        MarketSkillLike.__table__,
                        MCPServer.__table__,
                    ],
                )
            )
            await connection.execute(text("INSERT INTO departments (id, name) VALUES (1, 'Credit'), (2, 'Risk')"))
        async with sessions() as db:
            db.add_all(
                [
                    User(uid="author", username="Author", password_hash="x", department_id=1),
                    User(uid="credit-user", username="Credit User", password_hash="x", department_id=1),
                    User(uid="risk-user", username="Risk User", password_hash="x", department_id=2),
                    User(uid="root", username="Root", password_hash="x", role="superadmin", department_id=2),
                ]
            )
            await db.commit()

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            invalid_category = await client.post(
                "/api/skill-market/personal/credit-review/submit",
                json={"application_reason": "分类值不在列表", "category": "arbitrary"},
            )
            assert invalid_category.status_code == 400
            assert (await client.get("/api/skill-market/requests")).json()["data"] == []
            submitted = await client.post(
                "/api/skill-market/personal/credit-review/submit",
                json={"application_reason": "复用信贷审核步骤", "category": "finance_analysis"},
            )
            assert submitted.status_code == 200, submitted.text
            request_id = submitted.json()["data"]["id"]
            assert submitted.json()["data"]["channel"] == "market"
            assert submitted.json()["data"]["category"] == "finance_analysis"
            assert (await client.get("/api/skill-market")).json()["data"] == []
            async with sessions() as db:
                shared_request = await sharing.submit_skill_share_request(
                    db, slug="credit-review", application_reason="同时申请传统共享", operator=current
                )
                assert shared_request.channel == "shared"
                assert shared_request.status == "pending"

            current = User(uid="other-admin", username="Other", password_hash="x", role="admin", department_id=2)
            assert (await client.get("/api/skill-market/requests")).json()["data"] == []
            wrong_admin = await client.post(
                f"/api/skill-market/requests/{request_id}/approve", json={"scope": "department"}
            )
            assert wrong_admin.status_code == 404

            current = User(uid="credit-admin", username="Admin", password_hash="x", role="admin", department_id=1)
            first_pending = (await client.get("/api/skill-market/requests")).json()["data"][0]
            assert first_pending["existing_market_scope"] is None
            assert first_pending["existing_market_department_id"] is None
            assert first_pending["existing_market_is_published"] is None
            assert first_pending["current_market_version"] is None
            assert first_pending["published_version"] is None
            forbidden_global = await client.post(
                f"/api/skill-market/requests/{request_id}/approve", json={"scope": "global"}
            )
            assert forbidden_global.status_code == 403
            approved = await client.post(
                f"/api/skill-market/requests/{request_id}/approve", json={"scope": "department"}
            )
            assert approved.status_code == 200, approved.text
            assert approved.json()["data"]["published_slug"] == "credit-review"
            assert approved.json()["data"]["category"] == "finance_analysis"
            async with sessions() as db:
                shared_published = await sharing.approve_skill_share_request(
                    db,
                    request_id=shared_request.id,
                    department_ids=[1],
                    note="传统共享仍可独立发布",
                    operator=current,
                )
                assert shared_published.published_slug != "credit-review"
                assert (
                    await db.scalar(select(Skill.slug).where(Skill.slug == shared_published.published_slug))
                ) == shared_published.published_slug

            current = User(uid="risk-user", username="Risk", password_hash="x", role="user", department_id=2)
            risk_catalog = (await client.get("/api/skill-market")).json()
            assert risk_catalog["data"] == []
            assert risk_catalog["total"] == 0
            assert (await client.get("/api/skill-market?category=finance_analysis")).json()["data"] == []
            assert (await client.get("/api/skill-market/credit-review")).status_code == 404
            for path in ("credit-review/tree", "credit-review/file"):
                assert (await client.get(f"/api/skill-market/{path}")).status_code == 404
            for action in ("install", "upgrade", "like"):
                assert (await client.post(f"/api/skill-market/credit-review/{action}")).status_code == 404

            current = User(uid="other-admin", username="Other", password_hash="x", role="admin", department_id=2)
            assert (await client.get("/api/skill-market")).json()["data"] == []
            assert (await client.get("/api/skill-market/credit-review")).status_code == 404

            current = User(uid="credit-user", username="Credit", password_hash="x", role="user", department_id=1)
            listing = (await client.get("/api/skill-market?sort=installs")).json()["data"][0]
            assert listing["category"] == "finance_analysis"
            finance_page = (await client.get("/api/skill-market?category=finance_analysis&limit=1")).json()
            assert len(finance_page["data"]) == 1
            assert (finance_page["total"], finance_page["has_more"]) == (1, False)
            assert (await client.get("/api/skill-market?category=office_tools")).json()["data"] == []
            assert (await client.get("/api/skill-market?category=invalid")).status_code == 400
            assert listing["installed_version"] is None
            assert (await client.get("/api/skill-market?installed_only=true")).json()["data"] == []
            installed = await client.post("/api/skill-market/credit-review/install")
            assert installed.status_code == 200, installed.text
            assert installed.json()["data"]["installed_version"] == 1
            assert installed.json()["data"]["install_count"] == 1
            installed_page = (await client.get("/api/skill-market?installed_only=true")).json()
            assert len(installed_page["data"]) == 1
            assert installed_page["total"] == 1
            assert len((await client.get("/api/skill-market?limit=1&offset=0")).json()["data"]) == 1
            assert (await client.get("/api/skill-market?limit=1&offset=1")).json()["data"] == []
            liked = await client.post("/api/skill-market/credit-review/like")
            assert liked.json()["data"]["likes_count"] == 1
            assert liked.json()["data"]["liked"] is True
            assert (await client.post("/api/skill-market/credit-review/like")).json()["data"]["likes_count"] == 0

            (skill_dir / "SKILL.md").write_text(
                "---\nname: credit-review\ndescription: second release\n---\n# Second\n", encoding="utf-8"
            )
            current = User(uid="author", username="Author", password_hash="x", role="user", department_id=1)
            second = await client.post(
                "/api/skill-market/personal/credit-review/submit",
                json={"application_reason": "更新审核步骤", "category": "office_efficiency"},
            )
            assert second.status_code == 200, second.text
            current = User(uid="credit-admin", username="Admin", password_hash="x", role="admin", department_id=1)
            pending = next(
                item
                for item in (await client.get("/api/skill-market/requests")).json()["data"]
                if item["id"] == second.json()["data"]["id"]
            )
            assert pending["existing_market_scope"] == "department"
            assert pending["existing_market_department_id"] == 1
            assert pending["existing_market_is_published"] is True
            assert pending["current_market_version"] == 1
            assert pending["published_version"] is None
            older = await client.get(
                "/api/skill-market/requests", params={"slug": "credit-review", "limit": 1, "offset": 1}
            )
            assert older.status_code == 200, older.text
            assert older.json()["data"]["total"] == 2
            assert [item["id"] for item in older.json()["data"]["items"]] == [request_id]
            current = User(uid="root", username="Root", password_hash="x", role="superadmin", department_id=2)
            assert (await client.get("/api/skill-market/requests?mine_only=true")).json()["data"] == []
            current = User(uid="credit-admin", username="Admin", password_hash="x", role="admin", department_id=1)
            second_approval = await client.post(
                f"/api/skill-market/requests/{second.json()['data']['id']}/approve",
                json={"scope": "department"},
            )
            assert second_approval.status_code == 200, second_approval.text
            published = next(
                item
                for item in (await client.get("/api/skill-market/requests")).json()["data"]
                if item["id"] == second.json()["data"]["id"]
            )
            assert published["current_market_version"] == 2
            assert published["published_version"] == 2

            current = User(uid="credit-user", username="Credit", password_hash="x", role="user", department_id=1)
            detail = (await client.get("/api/skill-market/credit-review")).json()["data"]
            assert detail["latest_version"] == 2
            assert detail["category"] == "office_efficiency"
            assert (await client.get("/api/skill-market?category=finance_analysis")).json()["data"] == []
            assert len((await client.get("/api/skill-market?category=office_efficiency")).json()["data"]) == 1
            assert detail["installed_version"] == 1
            assert detail["upgrade_available"] is True
            async with sessions() as db:
                accessible = await list_accessible_skills(db, current)
                market_items = [item for item in accessible if item.source_scope == "market"]
                assert len(market_items) == 1
                assert market_items[0].version == "1"
                projection_sources = await market.market_projection_sources(db, current)
                releases = (await db.scalars(select(MarketSkillRelease).order_by(MarketSkillRelease.version))).all()
                assert [release.category for release in releases] == ["finance_analysis", "office_efficiency"]
                assert set(projection_sources) == {market.market_projection_slug(releases[0].id)}
                skill_service.sync_user_accessible_skills("credit-user", projection_sources)
                old_path = (
                    tmp_path
                    / "projections"
                    / "credit-user"
                    / market.market_projection_slug(releases[0].id)
                    / "SKILL.md"
                )
                assert old_path.read_text(encoding="utf-8").endswith("# First\n")
            upgraded = await client.post("/api/skill-market/credit-review/upgrade")
            assert upgraded.json()["data"]["installed_version"] == 2
            async with sessions() as db:
                assert set(await market.market_projection_sources(db, current)) == {
                    market.market_projection_slug(release.id) for release in releases
                }
                skill_service.sync_user_accessible_skills(
                    "credit-user", await market.market_projection_sources(db, current)
                )
                assert old_path.read_text(encoding="utf-8").endswith("# First\n")
                new_path = (
                    tmp_path
                    / "projections"
                    / "credit-user"
                    / market.market_projection_slug(releases[1].id)
                    / "SKILL.md"
                )
                assert new_path.read_text(encoding="utf-8").endswith("# Second\n")
            assert (
                (await client.get("/api/skill-market/credit-review/file"))
                .json()["data"]["content"]
                .endswith("# Second\n")
            )

            async with sessions() as db:
                listing = await db.scalar(select(MarketSkill).where(MarketSkill.slug == "credit-review"))
                listing.is_published = False
                listing.publication_generation = 2
                await db.commit()
            assert (await client.get("/api/skill-market")).json()["data"] == []
            assert (await client.get("/api/skill-market?installed_only=true")).json()["data"] == []
            assert (await client.get("/api/skill-market/-/rankings?scope=company")).json()["data"] == []
            for path in ("credit-review", "credit-review/tree", "credit-review/file"):
                assert (await client.get(f"/api/skill-market/{path}")).status_code == 404
            for action in ("install", "upgrade", "like"):
                assert (await client.post(f"/api/skill-market/credit-review/{action}")).status_code == 404
            withdrawn = (await client.get("/api/skill-market/-/installs")).json()["data"]
            assert withdrawn == [
                {
                    "slug": "credit-review",
                    "installed_version": 2,
                    "visible": False,
                    "unavailable_reason": "unpublished",
                }
            ]
            async with sessions() as db:
                repo = market.MarketSkillRepository(db)
                assert await repo.published_slug_exists("credit-review")
                assert await repo.visible_by_sources(current, {("author", "credit-review")}) == []
                assert await repo.visible_by_ids(current, {listing.id}) == []
                assert [
                    item for item in await list_accessible_skills(db, current) if item.source_scope == "market"
                ] == []
                assert await market.market_projection_sources(db, current) == {}
                assert (await db.scalar(select(MarketSkill))).latest_release_id == releases[1].id
                assert (await db.scalars(select(MarketSkillInstall))).one().release_id == releases[1].id
            current = User(uid="root", username="Root", password_hash="x", role="superadmin", department_id=2)
            assert (await client.get("/api/skill-market")).json()["data"] == []
            assert (await client.get("/api/skill-market/credit-review")).status_code == 404
            current = User(uid="author", username="Author", password_hash="x", role="user", department_id=1)
            historic = next(
                item
                for item in (await client.get("/api/skill-market/requests?mine_only=true")).json()["data"]
                if item["id"] == request_id
            )
            assert historic["existing_market_is_published"] is False
            assert historic["existing_market_scope"] == "department"
            assert historic["existing_market_department_id"] == 1
            assert historic["read_scope"]["access_level"] == "department"
            assert historic["department_ids"] == [1]

            async with sessions() as db:
                listing = await db.scalar(select(MarketSkill).where(MarketSkill.slug == "credit-review"))
                listing.scope = "global"
                listing.department_id = None
                await db.commit()
            current_scope = next(
                item
                for item in (await client.get("/api/skill-market/requests?mine_only=true")).json()["data"]
                if item["id"] == request_id
            )
            assert current_scope["existing_market_scope"] == "global"
            assert current_scope["existing_market_department_id"] is None
            assert current_scope["existing_market_is_published"] is False
            assert current_scope["read_scope"]["access_level"] == "department"
            assert current_scope["department_ids"] == [1]

            async with sessions() as db:
                listing = await db.scalar(select(MarketSkill).where(MarketSkill.slug == "credit-review"))
                listing.is_published = True
                listing.publication_generation = 1
                listing.scope = "department"
                listing.department_id = 1
                await db.commit()

        async with sessions() as db:
            installs = (await db.scalars(select(MarketSkillInstall))).all()
            releases = (await db.scalars(select(MarketSkillRelease).order_by(MarketSkillRelease.version))).all()
            assert len(installs) == 1
            assert [release.version for release in releases] == [1, 2]
            assert installs[0].release_id == releases[1].id
            assert (tmp_path / releases[0].dir_path / "SKILL.md").read_text(encoding="utf-8").endswith("# First\n")

        (skill_dir / "SKILL.md").write_text(
            "---\nname: credit-review\ndescription: third release\n---\n# Third\n", encoding="utf-8"
        )
        async with sessions() as db:
            third = await market.submit_market_skill(
                db,
                slug="credit-review",
                application_reason="验证提交后连接异常",
                operator=User(uid="author", username="Author", password_hash="x", department_id=1),
            )
        async with sessions() as db:
            real_commit = db.commit

            async def commit_then_disconnect():
                """模拟 PostgreSQL 已提交，但确认响应在连接上丢失。"""
                await real_commit()
                raise OSError("commit confirmation lost")

            monkeypatch.setattr(db, "commit", commit_then_disconnect)
            with pytest.raises(OSError, match="commit confirmation lost"):
                await market.review_market_skill(
                    db,
                    request_id=third.id,
                    approved=True,
                    scope="department",
                    note="",
                    operator=User(
                        uid="credit-admin", username="Admin", password_hash="x", role="admin", department_id=1
                    ),
                )
        async with sessions() as db:
            last_release = await db.scalar(select(MarketSkillRelease).where(MarketSkillRelease.version == 3))
            assert last_release is not None
            assert (tmp_path / last_release.dir_path / "SKILL.md").read_text(encoding="utf-8").endswith("# Third\n")

        current = User(uid="root", username="Root", password_hash="x", role="superadmin", department_id=2)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            assert [item["slug"] for item in (await client.get("/api/skill-market")).json()["data"]] == [
                "credit-review"
            ]
            assert (await client.get("/api/skill-market/credit-review")).status_code == 200
            installed = await client.post("/api/skill-market/credit-review/install")
            assert installed.status_code == 200, installed.text
            assert installed.json()["data"]["installed_version"] == 3
        async with sessions() as db:
            accessible = await list_accessible_skills(db, current)
            assert [item.slug for item in accessible if item.source_scope == "market"] == ["credit-review"]
            assert len(await market.market_projection_sources(db, current)) == 3

        monkeypatch.setattr(skill_service, "get_skill_drafts_root_dir", lambda: tmp_path / "drafts")
        async with sessions() as db:
            shared_draft = await skill_service.prepare_skill_upload(
                db,
                filename="SKILL.md",
                file_bytes=b"---\nname: recent-other\ndescription: shared draft\n---\n# Shared\n",
                operator=User(uid="root", username="Root", password_hash="x", role="superadmin", department_id=2),
            )
            assert shared_draft["items"][0]["slug"] == "recent-other"

        newer_dir = personal_root / "recent-other"
        newer_dir.mkdir()
        (newer_dir / "SKILL.md").write_text(
            "---\nname: recent-other\ndescription: second catalog entry\n---\n# Recent\n", encoding="utf-8"
        )
        current = User(uid="author", username="Author", password_hash="x", role="user", department_id=1)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            second_item = await client.post(
                "/api/skill-market/personal/recent-other/submit",
                json={"application_reason": "验证分类分页", "category": "office_tools"},
            )
            assert second_item.status_code == 200, second_item.text
            current = User(uid="credit-admin", username="Admin", password_hash="x", role="admin", department_id=1)
            assert (
                await client.post(
                    f"/api/skill-market/requests/{second_item.json()['data']['id']}/approve",
                    json={"scope": "department"},
                )
            ).status_code == 200
            current = User(uid="credit-user", username="Credit", password_hash="x", role="user", department_id=1)
            assert [item["slug"] for item in (await client.get("/api/skill-market?limit=1")).json()["data"]] == [
                "recent-other"
            ]
            assert [
                item["slug"]
                for item in (await client.get("/api/skill-market?category=other&limit=1&offset=0")).json()["data"]
            ] == ["credit-review"]
            assert (await client.get("/api/skill-market?category=other&limit=1&offset=1")).json()["data"] == []
            current = User(uid="risk-user", username="Risk", password_hash="x", role="user", department_id=2)
            assert (await client.get("/api/skill-market?category=other&limit=1")).json()["data"] == []
        async with sessions() as db:
            shared_results = await skill_service.confirm_skill_install_draft(
                db,
                draft_id=shared_draft["draft_id"],
                share_config=None,
                slugs=["recent-other"],
                operator=User(uid="root", username="Root", password_hash="x", role="superadmin", department_id=2),
            )
            assert shared_results == [
                {"slug": "recent-other", "success": False, "error": "Skill slug 已被占用，请重新解析安装"}
            ]
            assert await db.scalar(select(Skill.id).where(Skill.slug == "recent-other")) is None

        global_dir = personal_root / "company-guide"
        global_dir.mkdir()
        (global_dir / "SKILL.md").write_text(
            "---\nname: company-guide\ndescription: company guide\n---\n# First\n", encoding="utf-8"
        )
        current = User(uid="author", username="Author", password_hash="x", role="user", department_id=1)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            first = await client.post(
                "/api/skill-market/personal/company-guide/submit", json={"application_reason": "全公司使用"}
            )
            assert first.status_code == 200, first.text
            current = User(uid="root", username="Root", password_hash="x", role="superadmin", department_id=2)
            approved = await client.post(
                f"/api/skill-market/requests/{first.json()['data']['id']}/approve", json={"scope": "global"}
            )
            assert approved.status_code == 200, approved.text
            (global_dir / "SKILL.md").write_text(
                "---\nname: company-guide\ndescription: revised guide\n---\n# Second\n", encoding="utf-8"
            )
            current = User(uid="author", username="Author", password_hash="x", role="user", department_id=1)
            second = await client.post(
                "/api/skill-market/personal/company-guide/submit", json={"application_reason": "更新全公司版本"}
            )
            assert second.status_code == 200, second.text
            current = User(uid="root", username="Root", password_hash="x", role="superadmin", department_id=2)
            pending = next(
                item
                for item in (await client.get("/api/skill-market/requests")).json()["data"]
                if item["id"] == second.json()["data"]["id"]
            )
            assert pending["existing_market_scope"] == "global"
            assert pending["existing_market_department_id"] is None
            assert pending["current_market_version"] == 1
            assert pending["published_version"] is None
            wrong_scope = await client.post(
                f"/api/skill-market/requests/{pending['id']}/approve", json={"scope": "department"}
            )
            assert wrong_scope.status_code == 400
            assert "发布范围" in wrong_scope.json()["detail"]
            correct_scope = await client.post(f"/api/skill-market/requests/{pending['id']}/approve", json={})
            assert correct_scope.status_code == 200, correct_scope.text
            published = next(
                item
                for item in (await client.get("/api/skill-market/requests")).json()["data"]
                if item["id"] == pending["id"]
            )
            assert published["current_market_version"] == 2
            assert published["published_version"] == 2
    finally:
        await engine.dispose()
        async with admin_engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin_engine.dispose()


async def test_market_department_release_stays_with_original_department_after_author_moves(monkeypatch, tmp_path: Path):
    """作者换部门后仅超级管理员可续版，原部门仍是市场受众。"""
    schema = f"pytest_skill_market_move_{uuid.uuid4().hex[:12]}"
    admin_engine = create_async_engine(os.environ["POSTGRES_URL"])
    async with admin_engine.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_async_engine(os.environ["POSTGRES_URL"], connect_args={"server_settings": {"search_path": schema}})
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    personal_root = tmp_path / "personal"
    skill_dir = personal_root / "department-guide"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: department-guide\ndescription: first release\n---\n# First\n", encoding="utf-8"
    )
    monkeypatch.setattr(sharing, "personal_skills_root", lambda _uid: personal_root)
    monkeypatch.setattr(sharing, "get_skill_data_dir", lambda: tmp_path)
    monkeypatch.setattr(market, "get_skill_data_dir", lambda: tmp_path)

    async def no_dependencies(**_kwargs):
        """本例没有外部工具依赖。"""
        return [], [], []

    monkeypatch.setattr(market, "validate_skill_dependencies", no_dependencies)
    current = User(uid="author", username="Author", password_hash="x", department_id=1)
    app = FastAPI()
    app.include_router(market_router, prefix="/api")

    async def provide_user():
        """模拟当前认证用户。"""
        return current

    async def provide_db():
        """每次请求使用独立事务会话。"""
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
            await connection.execute(text("INSERT INTO departments (id, name) VALUES (1, 'Original'), (2, 'New')"))
        async with sessions() as db:
            db.add(User(uid="author", username="Author", password_hash="x", department_id=1))
            await db.commit()

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            first = await client.post(
                "/api/skill-market/personal/department-guide/submit", json={"application_reason": "首次发布"}
            )
            assert first.status_code == 200, first.text
            current = User(uid="old-admin", username="Old Admin", password_hash="x", role="admin", department_id=1)
            first_approval = await client.post(
                f"/api/skill-market/requests/{first.json()['data']['id']}/approve", json={"scope": "department"}
            )
            assert first_approval.status_code == 200, first_approval.text

            async with sessions() as db:
                author = await db.scalar(select(User).where(User.uid == "author"))
                author.department_id = 2
                await db.commit()
            (skill_dir / "SKILL.md").write_text(
                "---\nname: department-guide\ndescription: second release\n---\n# Second\n", encoding="utf-8"
            )
            current = User(uid="author", username="Author", password_hash="x", department_id=2)
            second = await client.post(
                "/api/skill-market/personal/department-guide/submit", json={"application_reason": "换部门后续版"}
            )
            assert second.status_code == 200, second.text
            second_id = second.json()["data"]["id"]
            assert second.json()["data"]["owner_department_id"] == 2

            current = User(uid="new-admin", username="New Admin", password_hash="x", role="admin", department_id=2)
            pending = next(
                item
                for item in (await client.get("/api/skill-market/requests")).json()["data"]
                if item["id"] == second_id
            )
            assert pending["existing_market_scope"] == "department"
            assert pending["existing_market_department_id"] == 1
            denied = await client.post(f"/api/skill-market/requests/{second_id}/approve", json={"scope": "department"})
            assert denied.status_code == 403
            assert "仅超级管理员" in denied.json()["detail"]
            async with sessions() as db:
                assert (await db.get(SkillShareRequest, second_id)).status == "pending"
                assert (await db.scalar(select(MarketSkill))).department_id == 1
                assert (
                    await db.scalar(select(MarketSkillRelease).order_by(MarketSkillRelease.version.desc()))
                ).version == 1

            current = User(uid="old-admin", username="Old Admin", password_hash="x", role="admin", department_id=1)
            assert all(
                item["id"] != second_id for item in (await client.get("/api/skill-market/requests")).json()["data"]
            )
            assert (await client.post(f"/api/skill-market/requests/{second_id}/approve", json={})).status_code == 404

            current = User(uid="root", username="Root", password_hash="x", role="superadmin", department_id=2)
            approved = await client.post(
                f"/api/skill-market/requests/{second_id}/approve", json={"scope": "department"}
            )
            assert approved.status_code == 200, approved.text
            assert approved.json()["data"]["department_ids"] == [1]
            assert approved.json()["data"]["read_scope"]["department_ids"] == [1]
            async with sessions() as db:
                listing = await db.scalar(select(MarketSkill))
                assert listing.department_id == 1
                assert (await db.get(SkillShareRequest, second_id)).owner_department_id == 2
                assert (
                    await db.scalar(select(MarketSkillRelease).order_by(MarketSkillRelease.version.desc()))
                ).version == 2

            current = User(uid="old-reader", username="Old Reader", password_hash="x", department_id=1)
            assert [item["slug"] for item in (await client.get("/api/skill-market")).json()["data"]] == [
                "department-guide"
            ]
            assert (await client.get("/api/skill-market/department-guide")).json()["data"]["latest_version"] == 2
            current = User(uid="author", username="Author", password_hash="x", department_id=2)
            assert (await client.get("/api/skill-market")).json()["data"] == []
            assert (await client.get("/api/skill-market/department-guide")).status_code == 404
    finally:
        await engine.dispose()
        async with admin_engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin_engine.dispose()


async def test_market_rankings_count_active_users_in_current_department():
    """真实 HTTP 和 PostgreSQL 同时验证排行榜排序、计数及部门隔离。"""
    schema = f"pytest_skill_rankings_{uuid.uuid4().hex[:12]}"
    admin_engine = create_async_engine(os.environ["POSTGRES_URL"])
    async with admin_engine.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_async_engine(os.environ["POSTGRES_URL"], connect_args={"server_settings": {"search_path": schema}})
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    current = User(uid="credit-a", username="Credit A", password_hash="x", department_id=1)
    app = FastAPI()
    app.include_router(market_router, prefix="/api")

    async def provide_user():
        """提供当前请求身份。"""
        return current

    async def provide_db():
        """为 HTTP 请求提供隔离数据库会话。"""
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
            await connection.execute(text("INSERT INTO departments (id, name) VALUES (1, 'Credit'), (2, 'Risk')"))

        async with sessions() as db:
            db.add_all(
                [
                    User(uid="credit-a", username="Credit A", password_hash="x", department_id=1),
                    User(uid="credit-b", username="Credit B", password_hash="x", department_id=1),
                    User(uid="risk-a", username="Risk A", password_hash="x", department_id=2),
                    User(uid="risk-b", username="Risk B", password_hash="x", department_id=2),
                    User(uid="risk-deleted", username="Former Risk", password_hash="x", department_id=2, is_deleted=1),
                ]
            )
            await db.flush()
            for slug, scope, department_id in (
                ("global-a", "global", None),
                ("global-b", "global", None),
                ("global-zero", "global", None),
                ("rankings", "global", None),
                ("risk-only", "department", 2),
            ):
                db.add(
                    MarketSkill(
                        id=slug,
                        slug=slug,
                        owner_uid="credit-a",
                        personal_slug=slug,
                        scope=scope,
                        department_id=department_id,
                        latest_release_id=f"{slug}-release",
                    )
                )
                db.add(
                    MarketSkillRelease(
                        id=f"{slug}-release",
                        market_skill_id=slug,
                        version=1,
                        name=slug,
                        description=slug,
                        dir_path=f"market/{slug}",
                        content_hash=slug,
                        request_id=f"{slug}-request",
                    )
                )
            await db.flush()
            for uid, slug in (
                ("credit-a", "global-a"),
                ("risk-a", "global-a"),
                ("risk-b", "global-a"),
                ("risk-deleted", "global-a"),
                ("credit-a", "global-b"),
                ("credit-b", "global-b"),
                ("risk-a", "risk-only"),
            ):
                db.add(MarketSkillInstall(uid=uid, market_skill_id=slug, release_id=f"{slug}-release"))
            for uid, slug in (
                ("credit-a", "global-a"),
                ("credit-b", "global-a"),
                ("risk-deleted", "global-a"),
                ("credit-a", "global-b"),
                ("risk-a", "global-b"),
                ("risk-b", "global-b"),
                ("risk-a", "risk-only"),
            ):
                db.add(MarketSkillLike(uid=uid, market_skill_id=slug))
            await db.commit()

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            same_name_detail = await client.get("/api/skill-market/rankings")
            assert same_name_detail.status_code == 200
            assert same_name_detail.json()["data"]["slug"] == "rankings"
            company_installs = (await client.get("/api/skill-market/-/rankings?metric=installs&scope=company")).json()[
                "data"
            ]
            assert [(item["slug"], item["install_count"]) for item in company_installs] == [
                ("global-a", 3),
                ("global-b", 2),
            ]
            assert [item["likes_count"] for item in company_installs] == [2, 3]
            assert "global-zero" not in {item["slug"] for item in company_installs}

            department_installs = (
                await client.get("/api/skill-market/-/rankings?metric=installs&scope=department")
            ).json()["data"]
            assert [(item["slug"], item["install_count"]) for item in department_installs] == [
                ("global-b", 2),
                ("global-a", 1),
            ]
            assert [item["likes_count"] for item in department_installs] == [1, 2]
            assert [
                item["slug"]
                for item in (await client.get("/api/skill-market/-/rankings?metric=likes&scope=company")).json()["data"]
            ] == ["global-b", "global-a"]
            assert [
                item["slug"]
                for item in (await client.get("/api/skill-market/-/rankings?metric=likes&scope=department")).json()[
                    "data"
                ]
            ] == ["global-a", "global-b"]
            assert [
                item["slug"]
                for item in (
                    await client.get("/api/skill-market/-/rankings?metric=installs&scope=company&offset=1&limit=1")
                ).json()["data"]
            ] == ["global-b"]

            catalog = (await client.get("/api/skill-market")).json()["data"]
            assert next(item for item in catalog if item["slug"] == "global-a")["install_count"] == 4
            assert (await client.get("/api/skill-market/-/rankings?metric=bogus")).status_code == 400
            assert (await client.get("/api/skill-market/-/rankings?scope=bogus")).status_code == 400
            assert (await client.get("/api/skill-market/-/rankings?offset=-1")).status_code == 422
            assert (await client.get("/api/skill-market/-/rankings?limit=101")).status_code == 422

            current = User(uid="root", username="Root", password_hash="x", role="superadmin", department_id=1)
            assert "risk-only" in {item["slug"] for item in (await client.get("/api/skill-market")).json()["data"]}
            company_ranking = (await client.get("/api/skill-market/-/rankings?scope=company")).json()["data"]
            assert "risk-only" in {item["slug"] for item in company_ranking}
            selected_department = (
                await client.get("/api/skill-market/-/rankings?scope=department&department_id=2")
            ).json()["data"]
            assert [(item["slug"], item["install_count"]) for item in selected_department] == [
                ("global-a", 2),
                ("risk-only", 1),
            ]
            assert (
                await client.get("/api/skill-market/-/rankings?scope=department&department_id=999")
            ).status_code == 404
            assert (await client.get("/api/skill-market/-/rankings?scope=company&department_id=2")).status_code == 400

            current = User(uid="credit-admin", username="Admin", password_hash="x", role="admin", department_id=1)
            assert (
                await client.get("/api/skill-market/-/rankings?scope=department&department_id=2")
            ).status_code == 403
            assert (
                await client.get("/api/skill-market/-/rankings?scope=department&department_id=1")
            ).status_code == 200

            current = User(uid="credit-a", username="Credit A", password_hash="x", department_id=1)
            assert (
                await client.get("/api/skill-market/-/rankings?scope=department&department_id=2")
            ).status_code == 403

            current = User(uid="risk-a", username="Risk A", password_hash="x", department_id=2)
            risk_ranking = (await client.get("/api/skill-market/-/rankings?scope=department")).json()["data"]
            assert "risk-only" in {item["slug"] for item in risk_ranking}

            current = User(uid="no-department", username="No Department", password_hash="x")
            assert (await client.get("/api/skill-market/-/rankings?scope=department")).status_code == 400
            assert (await client.get("/api/skill-market/-/rankings?scope=company")).status_code == 200
    finally:
        await engine.dispose()
        async with admin_engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin_engine.dispose()


@pytest.fixture
async def governance(market_lifecycle):
    """补齐治理事务使用的真实审计表与管理员身份。"""
    from yuxi.storage.postgres.models_business import OperationLog

    fixture = market_lifecycle
    async with fixture["sessions"]() as db:
        connection = await db.connection()
        await connection.run_sync(lambda conn: OperationLog.__table__.create(conn, checkfirst=True))
        db.add(Department(id=2, name="Risk"))
        db.add(User(uid="root", username="Root", password_hash="x", role="superadmin", department_id=2))
        await db.commit()
    return fixture


async def _governance_operator(db, uid="root"):
    """读取真正持久化的操作人。"""
    return await db.scalar(select(User).where(User.uid == uid))


def _add_management_listing(
    db,
    *,
    slug: str,
    name: str,
    scope: str = "department",
    department_id: int | None = 1,
    published: bool = True,
):
    """在隔离 Schema 中创建带最新版本的可管理市场条目。"""
    item_id = f"management-{slug}"
    release_id = f"release-{slug}"
    db.add(
        MarketSkill(
            id=item_id,
            slug=slug,
            owner_uid="author",
            personal_slug=slug,
            scope=scope,
            department_id=department_id,
            latest_release_id=release_id,
            is_published=published,
            updated_at=datetime(2030, 1, 1),
        )
    )
    db.add(
        MarketSkillRelease(
            id=release_id,
            market_skill_id=item_id,
            version=1,
            name=name,
            description="Management listing",
            category="other",
            dir_path="/test/management",
            content_hash=f"hash-{slug}",
            request_id=f"request-{slug}",
        )
    )


async def _assert_initial_governance_state(fixture, projection: Path):
    """拒绝请求后重读条目、审计、安装及实际用户投影。"""
    from yuxi.storage.postgres.models_business import OperationLog

    async with fixture["sessions"]() as db:
        row = await db.scalar(select(MarketSkill))
        installs = (await db.scalars(select(MarketSkillInstall))).all()
        assert (row.is_published, row.scope, row.department_id) == (True, "department", 1)
        assert (row.state_revision, row.publication_generation) == (1, 1)
        assert [(install.uid, install.release_id, install.confirmed_generation) for install in installs] == [
            ("reader", row.latest_release_id, 1)
        ]
        assert (await db.scalars(select(OperationLog))).all() == []
    assert (projection / "SKILL.md").read_text(encoding="utf-8") == "reviewed content"


@pytest.mark.parametrize("action", ["unpublish", "scope"])
async def test_governance_revokes_real_published_projection(governance, action):
    """下架和范围收窄立即移除真实发布流程生成的 UUID 版本投影。"""
    from yuxi.storage.postgres.models_business import OperationLog

    fixture = governance
    async with fixture["sessions"]() as db:
        listing = await db.scalar(select(MarketSkill).where(MarketSkill.slug == "batch-guide"))
        release = await db.get(MarketSkillRelease, listing.latest_release_id)
        reader = await db.scalar(select(User).where(User.uid == "reader"))
        sources = await market.market_projection_sources(db, reader)
        assert len(release.id.split("-")) == 5
        assert len(sources) == 1
        projection_name = next(iter(sources))
        assert projection_name == f"market-release-{release.id.replace('-', '')}"
        skill_service.sync_user_accessible_skills("reader", sources)
        projection = skill_service.get_user_skills_root_dir("reader") / projection_name
        assert (projection / "SKILL.md").read_text(encoding="utf-8").endswith("# First\n")

    if action == "unpublish":
        fixture["current"][0] = User(uid="admin", username="Admin", password_hash="x", role="admin", department_id=1)
        response = await fixture["client"].post(
            "/api/skill-market/batch-guide/unpublish", json={"expected_revision": 1}
        )
    else:
        fixture["current"][0] = User(uid="root", username="Root", password_hash="x", role="superadmin", department_id=2)
        response = await fixture["client"].put(
            "/api/skill-market/batch-guide/scope",
            json={"scope": "department", "department_id": 2, "expected_revision": 1},
        )
    assert response.status_code == 200, response.text
    assert not projection.exists()

    async with fixture["sessions"]() as db:
        listing = await db.scalar(select(MarketSkill).where(MarketSkill.slug == "batch-guide"))
        install = await db.get(MarketSkillInstall, ("reader", listing.id))
        assert install.release_id == release.id
        assert (fixture["root"] / release.dir_path / "SKILL.md").is_file()
        assert len((await db.scalars(select(OperationLog))).all()) == 1
        if action == "unpublish":
            assert (listing.is_published, listing.publication_generation, listing.state_revision) == (False, 2, 2)
        else:
            assert (listing.is_published, listing.scope, listing.department_id, listing.state_revision) == (
                True,
                "department",
                2,
                2,
            )


@pytest.mark.parametrize("action", ["unpublish", "scope"])
async def test_superadmin_governance_writes_with_readonly_projection_parent(governance, monkeypatch, action):
    """容器只允许写投影根时，超管仍可下架或收窄范围并撤销旧投影。"""
    fixture = governance
    async with fixture["sessions"]() as db:
        listing = await db.scalar(select(MarketSkill).where(MarketSkill.slug == "batch-guide"))
        release = await db.get(MarketSkillRelease, listing.latest_release_id)
        reader = await db.scalar(select(User).where(User.uid == "reader"))
        sources = await market.market_projection_sources(db, reader)
        skill_service.sync_user_accessible_skills("reader", sources)
    projection_root = skill_service.get_skill_projection_dir()
    readonly_parent = fixture["root"] / "readonly-parent"
    readonly_parent.mkdir()
    nested_projection_root = readonly_parent / "projections"
    projection_root.rename(nested_projection_root)
    monkeypatch.setattr(skill_service, "get_skill_projection_dir", lambda: nested_projection_root)
    reader_projection = skill_service.get_user_skills_root_dir("reader") / market.market_projection_slug(release.id)
    assert reader_projection.is_dir()
    fixture["current"][0] = User(uid="root", username="Root", password_hash="x", role="superadmin", department_id=2)
    readonly_parent.chmod(0o555)
    try:
        if os.access(readonly_parent, os.W_OK):
            pytest.skip("当前测试进程可以绕过只读目录权限")
        if action == "unpublish":
            response = await fixture["client"].post(
                "/api/skill-market/batch-guide/unpublish", json={"expected_revision": 1}
            )
        else:
            response = await fixture["client"].put(
                "/api/skill-market/batch-guide/scope",
                json={"scope": "department", "department_id": 2, "expected_revision": 1},
            )
        assert response.status_code == 200, response.text
        assert not reader_projection.exists()
        assert not any(path.name.startswith(".market-revocation-") for path in nested_projection_root.iterdir())
        async with fixture["sessions"]() as db:
            listing = await db.scalar(select(MarketSkill).where(MarketSkill.slug == "batch-guide"))
            assert (listing.is_published, listing.scope, listing.department_id, listing.state_revision) == (
                action == "scope",
                "department",
                1 if action == "unpublish" else 2,
                2,
            )
    finally:
        readonly_parent.chmod(0o755)


async def test_management_http_roles_and_downlisted_visibility(governance):
    """管理清单跟随当前部门，已下架详情只供获授权管理员查看。"""
    f = governance
    client = f["client"]
    async with f["sessions"]() as db:
        release = await db.scalar(select(MarketSkillRelease))
        projection = skill_service.get_user_skills_root_dir("reader") / market.market_projection_slug(release.id)
        projection.mkdir()
        (projection / "SKILL.md").write_text("reviewed content", encoding="utf-8")
    f["current"][0] = User(uid="reader", username="Reader", password_hash="x", department_id=1)
    assert (await client.get("/api/skill-market/-/management")).status_code == 403
    assert (await client.get("/api/skill-market/-/management/batch-guide")).status_code == 403
    assert (
        await client.post("/api/skill-market/batch-guide/unpublish", json={"expected_revision": 1})
    ).status_code == 403
    await _assert_initial_governance_state(f, projection)
    f["current"][0] = User(uid="author", username="Author", password_hash="x", department_id=1)
    assert (await client.get("/api/skill-market/-/management")).status_code == 403
    assert (await client.get("/api/skill-market/-/management/batch-guide")).status_code == 403
    assert (
        await client.post("/api/skill-market/batch-guide/unpublish", json={"expected_revision": 1})
    ).status_code == 403
    await _assert_initial_governance_state(f, projection)

    f["current"][0] = User(uid="admin", username="Admin", password_hash="x", role="admin", department_id=1)
    listing = await client.get("/api/skill-market/-/management")
    assert listing.status_code == 200, listing.text
    assert [row["slug"] for row in listing.json()["data"]] == ["batch-guide"]
    detail = await client.get("/api/skill-market/-/management/batch-guide")
    assert detail.status_code == 200, detail.text
    assert detail.json()["data"]["state_revision"] == 1
    downlisted = await client.post("/api/skill-market/batch-guide/unpublish", json={"expected_revision": 1})
    assert downlisted.status_code == 200, downlisted.text
    assert downlisted.json()["data"]["is_published"] is False
    assert not projection.exists()
    assert (
        await client.post("/api/skill-market/batch-guide/unpublish", json={"expected_revision": 1})
    ).status_code == 409
    assert (await client.get("/api/skill-market/-/management/batch-guide")).json()["data"]["status"] == "unpublished"
    assert (await client.get("/api/skill-market/-/management")).json()["data"][0]["slug"] == "batch-guide"

    f["current"][0] = User(uid="root", username="Root", password_hash="x", role="superadmin", department_id=2)
    assert (await client.get("/api/skill-market/-/management/batch-guide")).status_code == 200
    f["current"][0] = User(uid="reader", username="Reader", password_hash="x", department_id=1)
    assert (await client.get("/api/skill-market/batch-guide")).status_code == 404
    assert (await client.get("/api/skill-market")).json()["data"] == []
    assert (await client.get("/api/skill-market/batch-guide/tree")).status_code == 404
    assert (await client.get("/api/skill-market/batch-guide/file")).status_code == 404
    assert (await client.post("/api/skill-market/batch-guide/like")).status_code == 404
    assert (await client.post("/api/skill-market/batch-guide/install")).status_code == 404
    assert (await client.post("/api/skill-market/batch-guide/upgrade")).status_code == 404
    assert (await client.get("/api/skill-market?installed_only=true")).json()["data"] == []
    assert (await client.get("/api/skill-market/-/rankings?scope=department")).json()["data"] == []
    assert (await client.get("/api/skill-market/-/installs")).json()["data"] == [
        {"slug": "batch-guide", "installed_version": 1, "visible": False, "unavailable_reason": "unpublished"}
    ]
    removed = await client.delete("/api/skill-market/batch-guide/install")
    assert removed.status_code == 200 and removed.json()["data"]["uninstalled"] is True
    async with f["sessions"]() as db:
        assert (await db.scalar(select(MarketSkill))).publication_generation == 2
        assert (await db.scalars(select(MarketSkillInstall))).all() == []
        assert len((await db.scalars(select(MarketSkillRelease))).all()) == 1


async def test_management_http_filters_within_authorized_scope(governance):
    """最新版本名称、slug、上架状态和范围筛选不能放大部门管理员权限。"""
    f = governance
    client = f["client"]
    async with f["sessions"]() as db:
        _add_management_listing(db, slug="quartz-docs", name="Quartz Handbook")
        _add_management_listing(db, slug="team-manual", name="Blue Team", published=False)
        _add_management_listing(db, slug="other-quartz", name="Quartz Elsewhere", department_id=2, published=False)
        _add_management_listing(db, slug="global-guide", name="Global Quartz", scope="global", department_id=None)
        _add_management_listing(
            db, slug="global-retired", name="Old Company", scope="global", department_id=None, published=False
        )
        db.add(
            MarketSkillRelease(
                id="historical-quartz",
                market_skill_id="management-team-manual",
                version=0,
                name="Historic Keyword",
                description="Older release",
                category="other",
                dir_path="/test/management",
                content_hash="historical-hash",
                request_id="historical-request",
            )
        )
        await db.commit()

    f["current"][0] = User(uid="admin", username="Admin", password_hash="x", role="admin", department_id=1)
    by_name = await client.get("/api/skill-market/-/management", params={"query": "QUARTZ"})
    assert by_name.status_code == 200, by_name.text
    assert [row["slug"] for row in by_name.json()["data"]] == ["quartz-docs"]
    assert by_name.json()["total"] == 1

    by_slug_and_status = await client.get(
        "/api/skill-market/-/management",
        params={"query": "TEAM-MANUAL", "status": "withdrawn", "scope": "department"},
    )
    assert by_slug_and_status.status_code == 200, by_slug_and_status.text
    assert [row["slug"] for row in by_slug_and_status.json()["data"]] == ["team-manual"]
    assert by_slug_and_status.json()["data"][0]["status"] == "unpublished"
    assert by_slug_and_status.json()["total"] == 1
    assert (await client.get("/api/skill-market/-/management", params={"query": "Historic Keyword"})).json()[
        "total"
    ] == 0
    assert (await client.get("/api/skill-market/-/management", params={"scope": "global"})).json() == {
        "success": True,
        "data": [],
        "total": 0,
        "has_more": False,
        "limit": 20,
        "offset": 0,
    }

    f["current"][0] = User(uid="root", username="Root", password_hash="x", role="superadmin", department_id=2)
    global_withdrawn = await client.get(
        "/api/skill-market/-/management", params={"scope": "global", "status": "withdrawn"}
    )
    assert global_withdrawn.status_code == 200, global_withdrawn.text
    assert [row["slug"] for row in global_withdrawn.json()["data"]] == ["global-retired"]
    assert global_withdrawn.json()["total"] == 1
    all_quartz = await client.get("/api/skill-market/-/management", params={"query": "quartz"})
    assert {row["slug"] for row in all_quartz.json()["data"]} == {"quartz-docs", "other-quartz", "global-guide"}
    assert all_quartz.json()["total"] == 3


async def test_management_http_exact_slug_finds_target_after_fuzzy_page(governance):
    """精确 slug 在服务端筛选，目标不会被前二十个名称命中项挤出第一页。"""
    f = governance
    async with f["sessions"]() as db:
        for index in range(21):
            _add_management_listing(db, slug=f"copy-{index:02d}", name="target-skill")
        _add_management_listing(db, slug="target-skill", name="Actual Target")
        await db.commit()

    f["current"][0] = User(uid="admin", username="Admin", password_hash="x", role="admin", department_id=1)
    client = f["client"]
    fuzzy = await client.get("/api/skill-market/-/management", params={"query": "target-skill"})
    assert fuzzy.status_code == 200, fuzzy.text
    assert fuzzy.json()["total"] == 22
    assert "target-skill" not in {row["slug"] for row in fuzzy.json()["data"]}

    exact = await client.get("/api/skill-market/-/management", params={"query": "target-skill", "slug": "target-skill"})
    assert exact.status_code == 200, exact.text
    assert [row["slug"] for row in exact.json()["data"]] == ["target-skill"]
    assert (exact.json()["total"], exact.json()["has_more"]) == (1, False)


async def test_management_http_exact_slug_cannot_cross_department(governance):
    """其他部门的精确 slug 不得借本部门模糊命中项泄漏到结果或总数。"""
    f = governance
    async with f["sessions"]() as db:
        _add_management_listing(db, slug="own-copy", name="outside-exact")
        _add_management_listing(db, slug="outside-exact", name="Other department", department_id=2)
        await db.commit()

    f["current"][0] = User(uid="admin", username="Admin", password_hash="x", role="admin", department_id=1)
    response = await f["client"].get(
        "/api/skill-market/-/management", params={"query": "outside-exact", "slug": "outside-exact"}
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"] == []
    assert response.json()["total"] == 0


async def test_management_http_accepts_full_length_slug_search(governance):
    """管理直达可用合法的 128 字符 slug 同时作搜索词和精确筛选。"""
    f = governance
    long_slug = "s" * 128
    async with f["sessions"]() as db:
        db.add(
            MarketSkill(
                id="long-management-id",
                slug=long_slug,
                owner_uid="author",
                personal_slug=long_slug,
                scope="department",
                department_id=1,
                latest_release_id="long-release-id",
            )
        )
        db.add(
            MarketSkillRelease(
                id="long-release-id",
                market_skill_id="long-management-id",
                version=1,
                name="Long Slug",
                description="Full length slug",
                category="other",
                dir_path="/test/management",
                content_hash="long-hash",
                request_id="long-request-id",
            )
        )
        await db.commit()

    f["current"][0] = User(uid="admin", username="Admin", password_hash="x", role="admin", department_id=1)
    response = await f["client"].get("/api/skill-market/-/management", params={"query": long_slug, "slug": long_slug})
    assert response.status_code == 200, response.text
    assert [row["slug"] for row in response.json()["data"]] == [long_slug]
    assert response.json()["total"] == 1


async def test_management_http_pages_after_filtering_and_keeps_total(governance):
    """服务端默认每页二十条，等时间条目按 ID 稳定排序且总数不随翻页变化。"""
    f = governance
    async with f["sessions"]() as db:
        existing = await db.scalar(select(MarketSkill).where(MarketSkill.slug == "batch-guide"))
        existing.updated_at = datetime(2020, 1, 1)
        for index in range(21):
            _add_management_listing(db, slug=f"page-{index:02d}", name=f"Page {index:02d}")
        _add_management_listing(db, slug="outside-page", name="Outside", department_id=2)
        await db.commit()

    f["current"][0] = User(uid="admin", username="Admin", password_hash="x", role="admin", department_id=1)
    client = f["client"]
    first = await client.get("/api/skill-market/-/management")
    assert first.status_code == 200, first.text
    assert [row["slug"] for row in first.json()["data"]] == [f"page-{index:02d}" for index in range(20)]
    assert {key: first.json()[key] for key in ("total", "limit", "offset", "has_more")} == {
        "total": 22,
        "limit": 20,
        "offset": 0,
        "has_more": True,
    }

    second = await client.get("/api/skill-market/-/management", params={"offset": 20})
    assert second.status_code == 200, second.text
    assert [row["slug"] for row in second.json()["data"]] == ["page-20", "batch-guide"]
    assert (second.json()["total"], second.json()["limit"], second.json()["offset"], second.json()["has_more"]) == (
        22,
        20,
        20,
        False,
    )
    small = await client.get("/api/skill-market/-/management", params={"offset": 19, "limit": 2})
    assert [row["slug"] for row in small.json()["data"]] == ["page-19", "page-20"]
    assert small.json()["total"] == 22
    assert small.json()["has_more"] is True
    beyond = await client.get("/api/skill-market/-/management", params={"offset": 22, "limit": 2})
    assert beyond.json()["data"] == []
    assert beyond.json()["total"] == 22
    assert beyond.json()["has_more"] is False


@pytest.mark.parametrize(
    "params",
    [
        {"status": "unpublished"},
        {"scope": "company"},
        {"offset": -1},
        {"offset": "invalid"},
        {"limit": 0},
        {"limit": 101},
        {"query": "x" * 129},
        {"slug": "x" * 129},
    ],
)
async def test_management_http_rejects_invalid_filters_and_page_bounds(governance, params):
    """无效筛选和分页参数在 HTTP 边界被拒绝。"""
    f = governance
    f["current"][0] = User(uid="admin", username="Admin", password_hash="x", role="admin", department_id=1)
    response = await f["client"].get("/api/skill-market/-/management", params=params)
    assert response.status_code == 422, response.text


async def test_management_http_scope_transfer_and_conflict(governance):
    """只有超级管理员能显式转部门，旧 revision 无法覆盖其决定。"""
    from yuxi.storage.postgres.models_business import OperationLog

    f = governance
    client = f["client"]
    async with f["sessions"]() as db:
        release = await db.scalar(select(MarketSkillRelease))
        projection = skill_service.get_user_skills_root_dir("reader") / market.market_projection_slug(release.id)
        projection.mkdir()
        (projection / "SKILL.md").write_text("reviewed content", encoding="utf-8")
    f["current"][0] = User(uid="admin", username="Admin", password_hash="x", role="admin", department_id=1)
    assert (
        await client.put(
            "/api/skill-market/batch-guide/scope",
            json={"scope": "department", "department_id": 2, "expected_revision": 1},
        )
    ).status_code == 403
    await _assert_initial_governance_state(f, projection)
    f["current"][0] = User(uid="root", username="Root", password_hash="x", role="superadmin", department_id=2)
    moved = await client.put(
        "/api/skill-market/batch-guide/scope",
        json={"scope": "department", "department_id": 2, "expected_revision": 1},
    )
    assert moved.status_code == 200, moved.text
    assert (moved.json()["data"]["department_id"], moved.json()["data"]["state_revision"]) == (2, 2)
    assert not projection.exists()
    stale = await client.put(
        "/api/skill-market/batch-guide/scope",
        json={"scope": "global", "department_id": None, "expected_revision": 1},
    )
    assert stale.status_code == 409
    f["current"][0] = User(uid="admin", username="Admin", password_hash="x", role="admin", department_id=1)
    assert (await client.get("/api/skill-market/-/management")).json()["data"] == []
    assert (await client.get("/api/skill-market/-/management/batch-guide")).status_code == 404
    assert (
        await client.post("/api/skill-market/batch-guide/unpublish", json={"expected_revision": 2})
    ).status_code == 404
    f["current"][0] = User(uid="reader", username="Reader", password_hash="x", department_id=1)
    assert (await client.get("/api/skill-market/batch-guide")).status_code == 404
    assert (await client.get("/api/skill-market/-/installs")).json()["data"] == [
        {"slug": "batch-guide", "installed_version": 1, "visible": False, "unavailable_reason": "out_of_scope"}
    ]
    f["current"][0] = User(uid="root", username="Root", password_hash="x", role="superadmin", department_id=2)
    async with f["sessions"]() as db:
        row = await db.scalar(select(MarketSkill))
        assert (row.scope, row.department_id, row.state_revision, row.publication_generation) == ("department", 2, 2, 1)
        assert len((await db.scalars(select(OperationLog))).all()) == 1
        assert (await db.scalar(select(MarketSkillInstall))).confirmed_generation == 1


@pytest.mark.parametrize(
    "scope,department_id,detail",
    [
        ("invalid", None, {"code": "invalid_scope"}),
        ("department", None, {"code": "target_department_required"}),
        ("department", 999, {"code": "target_department_not_found"}),
    ],
)
async def test_management_http_scope_errors_are_controlled(governance, scope, department_id, detail):
    """错误响应只含受控字段，拒绝后数据库和文件均不变。"""

    f = governance
    async with f["sessions"]() as db:
        release = await db.scalar(select(MarketSkillRelease))
        projection = skill_service.get_user_skills_root_dir("reader") / market.market_projection_slug(release.id)
        projection.mkdir()
        (projection / "SKILL.md").write_text("reviewed content", encoding="utf-8")
    f["current"][0] = User(uid="root", username="Root", password_hash="x", role="superadmin", department_id=2)
    response = await f["client"].put(
        "/api/skill-market/batch-guide/scope",
        json={"scope": scope, "department_id": department_id, "expected_revision": 1},
    )
    assert response.status_code == 400, response.text
    assert response.json()["detail"] == detail
    await _assert_initial_governance_state(f, projection)


async def test_management_http_dependency_error_has_only_safe_identity(governance):
    """依赖拒绝只透露已审核依赖身份，且不写入治理状态。"""

    f = governance
    async with f["sessions"]() as db:
        release = await db.scalar(select(MarketSkillRelease))
        release.skill_dependencies = ["missing-shared-skill"]
        await db.commit()
        projection = skill_service.get_user_skills_root_dir("reader") / market.market_projection_slug(release.id)
        projection.mkdir()
        (projection / "SKILL.md").write_text("reviewed content", encoding="utf-8")
    f["current"][0] = User(uid="root", username="Root", password_hash="x", role="superadmin", department_id=2)
    response = await f["client"].put(
        "/api/skill-market/batch-guide/scope",
        json={"scope": "global", "department_id": None, "expected_revision": 1},
    )
    assert response.status_code == 400, response.text
    assert response.json()["detail"] == {
        "code": "dependency_unavailable",
        "dependency_type": "skill",
        "dependency_slug": "missing-shared-skill",
    }
    await _assert_initial_governance_state(f, projection)


async def test_governance_unpublish_invalidates_pending_and_logs(governance):
    """真实下架必须驳回旧申请并允许新批次重新提交。"""
    import json
    from yuxi.services import skill_market_governance_service as service
    from yuxi.storage.postgres.models_business import OperationLog

    f = governance
    pending = await f["client"].post(
        "/api/skill-market/personal/batch-guide/submit", json={"application_reason": "旧申请"}
    )
    assert pending.status_code == 200, pending.text
    pending_id = pending.json()["data"]["id"]
    async with f["sessions"]() as db:
        result = await service.unpublish_market_skill(
            db, slug="batch-guide", expected_revision=1, operator=await _governance_operator(db, "admin")
        )
    assert result["is_published"] is False
    assert result["publication_generation"] == 2
    assert result["state_revision"] == 2
    async with f["sessions"]() as db:
        old = await db.get(SkillShareRequest, pending_id)
        assert (old.status, old.review_note, old.reviewer_uid) == ("rejected", "条目已下架，需重新提交", "admin")
        assert old.reviewed_at is not None
        assert old.market_generation == 1
        assert len((await db.scalars(select(MarketSkillRelease))).all()) == 1
        assert (await db.scalar(select(MarketSkillInstall))).confirmed_generation == 1
        log = (await db.scalars(select(OperationLog))).one()
        assert log.user_id == (await _governance_operator(db, "admin")).id
        details = json.loads(log.details)
        assert details["before"]["is_published"] is True
        assert details["after"]["is_published"] is False
        with pytest.raises(ValueError):
            await market.review_market_skill(
                db, request_id=pending_id, approved=True, scope=None, note="", operator=await _governance_operator(db)
            )
    fresh = await f["client"].post(
        "/api/skill-market/personal/batch-guide/submit", json={"application_reason": "新申请"}
    )
    assert fresh.status_code == 200, fresh.text
    async with f["sessions"]() as db:
        assert (await db.get(SkillShareRequest, fresh.json()["data"]["id"])).market_generation == 2


async def test_governance_authority_follows_current_department(governance):
    """管理授权跟随当前市场部门，并保留已下架条目。"""
    from yuxi.services import skill_market_governance_service as service

    f = governance
    async with f["sessions"]() as db:
        author = await _governance_operator(db, "author")
        author.department_id = 2
        await db.commit()
        admin = await _governance_operator(db, "admin")
        items, total = await service.list_manageable_market_skills(db, operator=admin)
        assert ([row["slug"] for row in items], total) == (["batch-guide"], 1)
        for uid in ("author", "reader"):
            with pytest.raises(PermissionError):
                await service.list_manageable_market_skills(db, operator=await _governance_operator(db, uid))
        with pytest.raises(PermissionError):
            await service.change_market_skill_scope(
                db, slug="batch-guide", scope="global", department_id=None, expected_revision=1, operator=admin
            )
        result = await service.change_market_skill_scope(
            db,
            slug="batch-guide",
            scope="department",
            department_id=2,
            expected_revision=1,
            operator=await _governance_operator(db),
        )
        assert result["department_id"] == 2
        assert await service.list_manageable_market_skills(db, operator=admin) == ([], 0)
        with pytest.raises(ValueError, match="不存在"):
            await service.get_manageable_market_skill(db, slug="batch-guide", operator=admin)
        with pytest.raises(ValueError, match="不存在"):
            await service.unpublish_market_skill(db, slug="batch-guide", expected_revision=2, operator=admin)
        await service.unpublish_market_skill(
            db, slug="batch-guide", expected_revision=2, operator=await _governance_operator(db)
        )
        row = await service.get_manageable_market_skill(db, slug="batch-guide", operator=await _governance_operator(db))
        assert row["is_published"] is False
        assert row["name"] == "batch-guide"
        assert row["latest_version"] == 1
        with pytest.raises(ValueError):
            await service.unpublish_market_skill(
                db, slug="batch-guide", expected_revision=3, operator=await _governance_operator(db)
            )


async def test_scope_aba_stale_revision(governance):
    """范围恢复原值仍必须拒绝旧 revision，锁定读取必须刷新缓存。"""
    from yuxi.services import skill_market_governance_service as service

    f = governance
    async with f["sessions"]() as stale:
        cached = await stale.scalar(select(MarketSkill))
        assert cached.state_revision == 1
        async with f["sessions"]() as writer:
            root = await _governance_operator(writer)
            await service.change_market_skill_scope(
                writer, slug="batch-guide", scope="global", department_id=None, expected_revision=1, operator=root
            )
            await service.change_market_skill_scope(
                writer, slug="batch-guide", scope="department", department_id=1, expected_revision=2, operator=root
            )
        with pytest.raises(service.MarketConflictError):
            await service.unpublish_market_skill(
                stale, slug="batch-guide", expected_revision=1, operator=await _governance_operator(stale)
            )
    async with f["sessions"]() as db:
        listing = await db.scalar(select(MarketSkill))
        assert (listing.scope, listing.state_revision, listing.publication_generation) == ("department", 3, 1)
        approved = await db.scalar(select(SkillShareRequest).where(SkillShareRequest.status == "approved"))
        assert approved.department_ids == [1]
        assert approved.read_scope["department_ids"] == [1]


@pytest.mark.parametrize(
    "scope,department_id,code",
    [
        ("invalid", None, "invalid_scope"),
        ("department", None, "target_department_required"),
        ("department", 999, "target_department_not_found"),
    ],
)
async def test_governance_structured_scope_errors(governance, scope, department_id, code):
    """无效范围使用受控 code，并且不改变数据库。"""
    from yuxi.services import skill_market_governance_service as service
    from yuxi.storage.postgres.models_business import OperationLog

    async with governance["sessions"]() as db:
        with pytest.raises(service.MarketScopeValidationError) as error:
            await service.change_market_skill_scope(
                db,
                slug="batch-guide",
                scope=scope,
                department_id=department_id,
                expected_revision=1,
                operator=await _governance_operator(db),
            )
        assert error.value.code == code
        assert error.value.dependency_type is None
        assert (await db.scalar(select(MarketSkill))).state_revision == 1
        assert (await db.scalars(select(OperationLog))).all() == []


@pytest.mark.parametrize("dependency_type", ["mcp", "skill"])
@pytest.mark.parametrize("target_scope", ["global", "department"])
async def test_global_scope_rejects_uid_only_dependency(governance, dependency_type, target_scope):
    """旧版依赖仅授权 UID 时，即使最新版无依赖也不能覆盖完整受众。"""
    from yuxi.services import skill_market_governance_service as service

    f = governance
    private = {"access_level": "user", "department_ids": [], "user_uids": ["root", "author", "reader"]}
    async with f["sessions"]() as db:
        listing = await db.scalar(select(MarketSkill))
        old = await db.get(MarketSkillRelease, listing.latest_release_id)
        if dependency_type == "mcp":
            db.add(
                MCPServer(
                    slug="restricted",
                    name="restricted",
                    transport="streamable_http",
                    url="http://example.invalid/mcp",
                    enabled=1,
                    created_by="root",
                    updated_by="root",
                    share_config={"version": 2, "read_scope": private, "edit_scope": private, "manage_scope": None},
                )
            )
            old.mcp_dependencies = ["restricted"]
        else:
            db.add(
                Skill(
                    slug="restricted",
                    name="restricted",
                    description="private",
                    dir_path="shared/restricted",
                    enabled=True,
                    source_type="upload",
                    created_by="root",
                    share_config={"version": 2, "read_scope": private, "manage_scope": None},
                )
            )
            old.skill_dependencies = ["restricted"]
        fresh = MarketSkillRelease(
            id=str(uuid.uuid4()),
            market_skill_id=listing.id,
            version=2,
            name="safe",
            description="no dependencies",
            dir_path="market/safe",
            content_hash="safe",
            request_id=str(uuid.uuid4()),
        )
        db.add(fresh)
        listing.latest_release_id = fresh.id
        await db.commit()
        with pytest.raises(service.MarketScopeValidationError) as error:
            await service.change_market_skill_scope(
                db,
                slug="batch-guide",
                scope=target_scope,
                department_id=1 if target_scope == "department" else None,
                expected_revision=1,
                operator=await _governance_operator(db),
            )
        assert (error.value.code, error.value.dependency_type, error.value.dependency_slug) == (
            "dependency_scope",
            dependency_type,
            "restricted",
        )
        assert (await db.scalar(select(MarketSkill))).state_revision == 1


@pytest.mark.parametrize(
    "kind,state",
    [
        ("mcp", "missing"),
        ("mcp", "disabled"),
        ("mcp", "stdio"),
        ("mcp", "read_only"),
        ("skill", "missing"),
        ("skill", "disabled"),
        ("tool", "missing"),
    ],
)
async def test_governance_rejects_unavailable_dependencies(governance, kind, state):
    """缺失、停用、旧 stdio 和仅可读 MCP 均阻止范围修改。"""
    from yuxi.services import skill_market_governance_service as service

    global_scope = {"access_level": "global", "department_ids": [], "user_uids": []}
    async with governance["sessions"]() as db:
        release = await db.scalar(select(MarketSkillRelease))
        setattr(release, f"{kind}_dependencies", ["unavailable"])
        if kind == "mcp" and state != "missing":
            db.add(
                MCPServer(
                    slug="unavailable",
                    name="Unavailable",
                    transport="stdio" if state == "stdio" else "streamable_http",
                    url="http://example.invalid/mcp",
                    enabled=0 if state == "disabled" else 1,
                    created_by="root",
                    updated_by="root",
                    share_config={
                        "version": 2,
                        "read_scope": global_scope,
                        "edit_scope": None if state == "read_only" else global_scope,
                        "manage_scope": None,
                    },
                )
            )
        if kind == "skill" and state == "disabled":
            db.add(
                Skill(
                    slug="unavailable",
                    name="Unavailable",
                    description="disabled",
                    dir_path="shared/unavailable",
                    enabled=False,
                    source_type="builtin",
                    share_config={"version": 2, "read_scope": global_scope, "manage_scope": None},
                )
            )
        await db.commit()
        with pytest.raises(service.MarketScopeValidationError) as error:
            await service.change_market_skill_scope(
                db,
                slug="batch-guide",
                scope="global",
                department_id=None,
                expected_revision=1,
                operator=await _governance_operator(db),
            )
        assert error.value.code == ("dependency_scope" if state == "read_only" else "dependency_unavailable")
        assert error.value.dependency_type == kind
        assert (await db.scalar(select(MarketSkill))).state_revision == 1


async def _wait_for_listing_waiter(sessions, pid):
    """必须由 PostgreSQL 证明竞争会话实际等待锁。"""
    import asyncio

    async with sessions() as observer:
        for _ in range(500):
            if await observer.scalar(
                text("SELECT wait_event_type = 'Lock' FROM pg_stat_activity WHERE pid=:pid"), {"pid": pid}
            ):
                return
            await asyncio.sleep(0.01)
    pytest.fail("competing operation never waited for the held listing lock")


@pytest.mark.parametrize("first", ["submit", "unpublish"])
async def test_submit_unpublish_held_lock_interleavings(governance, first):
    """两种实际取锁顺序都不允许旧批次申请重新上架。"""
    import asyncio
    from yuxi.services import skill_market_governance_service as service

    f = governance
    async with f["sessions"]() as winner, f["sessions"]() as loser:
        await winner.scalar(select(MarketSkill).with_for_update())
        pid = await loser.scalar(text("SELECT pg_backend_pid()"))
        author = await _governance_operator(winner, "author")
        root = await _governance_operator(winner)

        async def submit(db):
            return await market.submit_market_skill(
                db, slug="batch-guide", application_reason="concurrent", operator=author
            )

        async def unpublish(db):
            return await service.unpublish_market_skill(db, slug="batch-guide", expected_revision=1, operator=root)

        task = asyncio.create_task(unpublish(loser) if first == "submit" else submit(loser))
        try:
            await _wait_for_listing_waiter(f["sessions"], pid)
            result = await (submit(winner) if first == "submit" else unpublish(winner))
            other = await asyncio.wait_for(task, 5)
            request_id = result.id if first == "submit" else other.id
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
    async with f["sessions"]() as db:
        request = await db.get(SkillShareRequest, request_id)
        assert (request.status, request.market_generation) == (("rejected", 1) if first == "submit" else ("pending", 2))
        assert (await db.scalar(select(MarketSkill))).publication_generation == 2


@pytest.mark.parametrize("first", ["upgrade", "unpublish"])
async def test_upgrade_unpublish_held_lock_interleavings(governance, first):
    """升级确认和下架竞争后旧安装绝不保持有效批次。"""
    import asyncio
    from yuxi.services import skill_market_governance_service as service

    f = governance
    async with f["sessions"]() as winner, f["sessions"]() as loser:
        await winner.scalar(select(MarketSkill).with_for_update())
        pid = await loser.scalar(text("SELECT pg_backend_pid()"))
        reader = await _governance_operator(winner, "reader")
        root = await _governance_operator(winner)

        async def upgrade(db):
            return await market.install_market_skill(db, slug="batch-guide", user=reader, upgrade=True)

        async def unpublish(db):
            return await service.unpublish_market_skill(db, slug="batch-guide", expected_revision=1, operator=root)

        task = asyncio.create_task(unpublish(loser) if first == "upgrade" else upgrade(loser))
        try:
            await _wait_for_listing_waiter(f["sessions"], pid)
            await (upgrade(winner) if first == "upgrade" else unpublish(winner))
            if first == "unpublish":
                with pytest.raises(ValueError, match="不存在"):
                    await asyncio.wait_for(task, 5)
            else:
                await asyncio.wait_for(task, 5)
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
    async with f["sessions"]() as db:
        listing = await db.scalar(select(MarketSkill))
        install = await db.scalar(select(MarketSkillInstall))
        assert not listing.is_published
        assert install.confirmed_generation < listing.publication_generation


async def test_unpublish_revokes_orphaned_projection(governance):
    """删除用户、无安装持有人及不可逆哈希孤儿目录都必须撤销。"""
    from yuxi.services import skill_market_governance_service as service

    f = governance
    async with f["sessions"]() as db:
        release = await db.scalar(select(MarketSkillRelease))
        reader = await _governance_operator(db, "reader")
        reader.is_deleted = 1
        await db.commit()
        projection_name = market.market_projection_slug(release.id)
        roots = [skill_service.get_user_skills_root_dir(uid) for uid in ("reader", "author", "gone:user@example.com")]
        for root in roots:
            (root / projection_name).mkdir()
            (root / projection_name / "SKILL.md").write_text("old release")
            (root / "unrelated").mkdir()
        await service.unpublish_market_skill(
            db, slug="batch-guide", expected_revision=1, operator=await _governance_operator(db)
        )
    for root in roots:
        assert not (root / projection_name).exists()
        assert (root / "unrelated").is_dir()


async def test_partial_revocation_failure_restores_projection(governance, monkeypatch):
    """第二次真实移动失败时，首个目录必须在锁内恢复，数据库不得提交。"""
    from yuxi.services import skill_market_governance_service as service
    from yuxi.storage.postgres.models_business import OperationLog

    f = governance
    pending = await f["client"].post(
        "/api/skill-market/personal/batch-guide/submit", json={"application_reason": "待审"}
    )
    pending_id = pending.json()["data"]["id"]
    async with f["sessions"]() as db:
        release = await db.scalar(select(MarketSkillRelease))
        projection_name = market.market_projection_slug(release.id)
        roots = [skill_service.get_user_skills_root_dir(uid) for uid in ("reader", "author")]
        for root in roots:
            (root / projection_name).mkdir()
            (root / projection_name / "SKILL.md").write_text(root.name)
        original_replace = skill_service.os.replace
        moves = []

        def fail_second_move(source, destination, *args, **kwargs):
            if Path(source).name == projection_name:
                moves.append((Path(source), Path(destination)))
                assert all(not Path(destination).is_relative_to(root) for root in roots)
                if len(moves) == 2:
                    raise OSError("injected second projection move failure")
            return original_replace(source, destination, *args, **kwargs)

        monkeypatch.setattr(skill_service.os, "replace", fail_second_move)
        with pytest.raises(OSError, match="injected second"):
            await service.unpublish_market_skill(
                db, slug="batch-guide", expected_revision=1, operator=await _governance_operator(db)
            )
    assert len(moves) == 2
    for root in roots:
        assert (root / projection_name / "SKILL.md").read_text() == root.name
    async with f["sessions"]() as db:
        listing = await db.scalar(select(MarketSkill))
        assert (listing.is_published, listing.publication_generation, listing.state_revision) == (True, 1, 1)
        assert (await db.get(SkillShareRequest, pending_id)).status == "pending"
        assert (await db.scalars(select(OperationLog))).all() == []


@pytest.mark.parametrize("legacy_mcp", [False, True])
async def test_governance_valid_dependencies_allow_widen_and_shrink(governance, legacy_mcp):
    """全受众依赖允许扩缩范围，安装批次及历史授权保持不变。"""
    from yuxi.services import skill_market_governance_service as service

    global_scope = {"access_level": "global", "department_ids": [], "user_uids": []}
    async with governance["sessions"]() as db:
        db.add(
            MCPServer(
                slug="allowed",
                name="Allowed",
                transport="streamable_http",
                url="http://example.invalid/mcp",
                enabled=1,
                created_by="root",
                updated_by="root",
                share_config=None
                if legacy_mcp
                else {"version": 2, "read_scope": global_scope, "edit_scope": global_scope, "manage_scope": None},
            )
        )
        db.add(
            Skill(
                slug="allowed",
                name="Allowed",
                description="allowed",
                source_type="upload",
                enabled=True,
                dir_path="shared/allowed",
                share_config={"version": 2, "read_scope": global_scope, "manage_scope": None},
            )
        )
        release = await db.scalar(select(MarketSkillRelease))
        release.mcp_dependencies = ["allowed"]
        release.skill_dependencies = ["allowed"]
        await db.commit()
        root = await _governance_operator(db)
        widened = await service.change_market_skill_scope(
            db, slug="batch-guide", scope="global", department_id=2, expected_revision=1, operator=root
        )
        assert widened["department_id"] is None
        shrunk = await service.change_market_skill_scope(
            db, slug="batch-guide", scope="department", department_id=2, expected_revision=2, operator=root
        )
        assert (shrunk["scope"], shrunk["department_id"], shrunk["publication_generation"]) == ("department", 2, 1)
        assert (await db.scalar(select(MarketSkillInstall))).confirmed_generation == 1
        request = await db.scalar(select(SkillShareRequest))
        assert request.department_ids == [1]
        assert request.read_scope["department_ids"] == [1]


@pytest.mark.parametrize("kind", ["mcp", "skill"])
async def test_governance_department_dependency_covers_only_its_department(governance, kind):
    """部门授权可覆盖目标整部门，但不能被扩大为其他部门。"""
    from yuxi.services import skill_market_governance_service as service

    stored = {"access_level": "department", "department_ids": [2], "user_uids": []}
    async with governance["sessions"]() as db:
        release = await db.scalar(select(MarketSkillRelease))
        setattr(release, f"{kind}_dependencies", ["department-only"])
        if kind == "mcp":
            db.add(
                MCPServer(
                    slug="department-only",
                    name="Department",
                    transport="streamable_http",
                    url="http://example.invalid/mcp",
                    enabled=1,
                    created_by="root",
                    updated_by="root",
                    share_config={"version": 2, "read_scope": stored, "edit_scope": stored, "manage_scope": None},
                )
            )
        else:
            db.add(
                Skill(
                    slug="department-only",
                    name="Department",
                    description="department",
                    source_type="upload",
                    dir_path="shared/department-only",
                    enabled=True,
                    share_config={"version": 2, "read_scope": stored, "manage_scope": None},
                )
            )
        await db.commit()
        root = await _governance_operator(db)
        result = await service.change_market_skill_scope(
            db, slug="batch-guide", scope="department", department_id=2, expected_revision=1, operator=root
        )
        assert result["department_id"] == 2
        with pytest.raises(service.MarketScopeValidationError) as error:
            await service.change_market_skill_scope(
                db, slug="batch-guide", scope="department", department_id=1, expected_revision=2, operator=root
            )
        assert error.value.code == "dependency_scope"
        assert (await db.scalar(select(MarketSkill))).department_id == 2


async def test_governance_builtin_dependency_still_requires_stored_audience(governance):
    """内置来源标签不能绕过数据库中实际存储的受众范围。"""
    from yuxi.services import skill_market_governance_service as service

    async with governance["sessions"]() as db:
        release = await db.scalar(select(MarketSkillRelease))
        release.skill_dependencies = ["restricted-builtin"]
        db.add(
            Skill(
                slug="restricted-builtin",
                name="Builtin",
                description="restricted",
                source_type="builtin",
                enabled=True,
                dir_path="shared/restricted-builtin",
                share_config={
                    "version": 2,
                    "read_scope": {"access_level": "user", "department_ids": [], "user_uids": ["root"]},
                    "manage_scope": None,
                },
            )
        )
        await db.commit()
        with pytest.raises(service.MarketScopeValidationError) as error:
            await service.change_market_skill_scope(
                db,
                slug="batch-guide",
                scope="global",
                department_id=None,
                expected_revision=1,
                operator=await _governance_operator(db),
            )
        assert (error.value.code, error.value.dependency_type) == ("dependency_scope", "skill")


@pytest.mark.parametrize("kind", ["mcp", "skill"])
@pytest.mark.parametrize("mutation", ["disabled", "restricted"])
async def test_governance_refreshes_cached_dependency_policy(governance, kind, mutation):
    """治理会话已缓存的依赖不能掩盖其他事务刚撤销的权限。"""
    from yuxi.services import skill_market_governance_service as service

    scope = {"access_level": "global", "department_ids": [], "user_uids": []}
    config = {"version": 2, "read_scope": scope, "manage_scope": None}
    model = MCPServer if kind == "mcp" else Skill
    async with governance["sessions"]() as db:
        release = await db.scalar(select(MarketSkillRelease))
        setattr(release, f"{kind}_dependencies", ["changing"])
        if kind == "mcp":
            db.add(
                MCPServer(
                    slug="changing",
                    name="Changing",
                    transport="streamable_http",
                    url="http://example.invalid/mcp",
                    enabled=1,
                    created_by="root",
                    updated_by="root",
                    share_config={**config, "edit_scope": scope},
                )
            )
        else:
            db.add(
                Skill(
                    slug="changing",
                    name="Changing",
                    description="changing",
                    enabled=True,
                    dir_path="shared/changing",
                    share_config=config,
                )
            )
        await db.commit()
    async with governance["sessions"]() as stale:
        cached = await stale.scalar(select(model).where(model.slug == "changing"))
        async with governance["sessions"]() as writer:
            changed = await writer.scalar(select(model).where(model.slug == "changing"))
            if mutation == "disabled":
                changed.enabled = False if kind == "skill" else 0
            else:
                private = {"access_level": "user", "department_ids": [], "user_uids": ["root"]}
                changed.share_config = {
                    **config,
                    "read_scope": private,
                    **({"edit_scope": private} if kind == "mcp" else {}),
                }
            await writer.commit()
        assert cached.enabled
        assert cached.share_config["read_scope"]["access_level"] == "global"
        with pytest.raises(service.MarketScopeValidationError) as error:
            await service.change_market_skill_scope(
                stale,
                slug="batch-guide",
                scope="global",
                department_id=None,
                expected_revision=1,
                operator=await _governance_operator(stale),
            )
        assert error.value.code == ("dependency_unavailable" if mutation == "disabled" else "dependency_scope")
    async with governance["sessions"]() as db:
        assert (await db.scalar(select(MarketSkill))).state_revision == 1


async def test_paginated_installs_keep_governance_redaction_and_current_generation_search(market_lifecycle):
    """分页与总数只搜索当前可展示内容，下架后仍保留安全卸载摘要。"""
    fixture = market_lifecycle
    client = fixture["client"]
    reader = User(uid="reader", username="Reader", password_hash="x", department_id=1)
    fixture["current"][0] = reader
    before = await client.get("/api/skill-market/-/installs?limit=1&offset=0&search=first%20release")
    assert before.status_code == 200, before.text
    assert before.json()["total"] == 1
    assert before.json()["data"][0]["description"] == "first release"

    await _withdraw_lifecycle_listing(fixture["sessions"])
    hidden = await client.get("/api/skill-market/-/installs?limit=1&offset=0&search=first%20release")
    assert hidden.json()["total"] == 0
    summary = await client.get("/api/skill-market/-/installs?limit=1&offset=0&search=batch-guide")
    assert summary.json()["total"] == 1
    assert summary.json()["data"] == [
        {"slug": "batch-guide", "installed_version": 1, "visible": False, "unavailable_reason": "unpublished"}
    ]
    empty = await client.get("/api/skill-market/-/installs?limit=1&offset=1&search=batch-guide")
    assert empty.json()["total"] == 1
    assert empty.json()["data"] == []

    fixture["current"][0] = User(uid="author", username="Author", password_hash="x", department_id=1)
    await _relist_lifecycle_fixture(fixture)
    fixture["current"][0] = reader
    old = await client.get("/api/skill-market/-/installs?limit=1&offset=0&search=first%20release")
    assert old.json()["total"] == 0
    current = await client.get("/api/skill-market/-/installs?limit=1&offset=0&search=second%20release")
    assert current.json()["total"] == 1
    assert current.json()["data"][0]["description"] == "second release"
    assert current.json()["data"][0]["unavailable_reason"] == "needs_confirmation"
    assert current.json()["data"][0]["upgrade_available"] is True


async def test_catalog_all_preserves_two_shared_copies_after_market_unpublish(market_lifecycle):
    """下架市场身份不再折叠两份仍授权可用的直接共享副本。"""
    from datetime import timedelta

    from yuxi.agents.skills.repository import SkillRepository
    from yuxi.utils.datetime_utils import utc_now_naive

    async with market_lifecycle["sessions"]() as db:
        now = utc_now_naive()
        for slug in ("shared-one", "shared-two"):
            skill = Skill(
                slug=slug,
                name=slug,
                description="shared copy",
                source_type="personal_share",
                content_hash="hash",
                dir_path=slug,
                enabled=True,
                created_by="admin",
                created_at=now,
                share_config={"version": 2, "read_scope": {"access_level": "global"}, "manage_scope": None},
            )
            db.add(skill)
            await db.flush()
            db.add(
                SkillShareRequest(
                    id=slug,
                    owner_uid="author",
                    personal_slug="batch-guide",
                    name=slug,
                    description="shared copy",
                    channel="shared",
                    status="approved",
                    content_hash="hash",
                    reviewer_uid="admin",
                    published_slug=slug,
                    published_skill_id=skill.id,
                    created_at=now - timedelta(seconds=1),
                    reviewed_at=now + timedelta(seconds=1),
                )
            )
        await db.commit()
        viewer = await db.scalar(select(User).where(User.uid == "reader"))
        rows, total = await SkillRepository(db).page_visible(
            viewer, offset=0, limit=30, catalog_all=True, shared_kind="shared"
        )
        assert total == 1
        listing = await db.scalar(select(MarketSkill))
        listing.is_published = False
        await db.commit()
        rows, total = await SkillRepository(db).page_visible(
            viewer, offset=0, limit=30, catalog_all=True, shared_kind="shared"
        )
        assert total == 2
        assert {row.slug for row in rows} == {"shared-one", "shared-two"}
