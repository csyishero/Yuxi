"""Skill 市场的真实 HTTP、PostgreSQL 与发布文件集成测试。"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from server.routers.skill_market_router import market_router
from server.utils.auth_middleware import get_db, get_required_user
from yuxi.agents.skills import market, service as skill_service, sharing
from yuxi.agents.skills.service import list_accessible_skills
from yuxi.storage.postgres.models_business import (
    Base,
    Department,
    MarketSkill,
    MarketSkillInstall,
    MarketSkillLike,
    MarketSkillRelease,
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
            submitted = await client.post(
                "/api/skill-market/personal/credit-review/submit",
                json={"application_reason": "复用信贷审核步骤"},
            )
            assert submitted.status_code == 200, submitted.text
            request_id = submitted.json()["data"]["id"]
            assert submitted.json()["data"]["channel"] == "market"
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
            forbidden_global = await client.post(
                f"/api/skill-market/requests/{request_id}/approve", json={"scope": "global"}
            )
            assert forbidden_global.status_code == 403
            approved = await client.post(
                f"/api/skill-market/requests/{request_id}/approve", json={"scope": "department"}
            )
            assert approved.status_code == 200, approved.text
            assert approved.json()["data"]["published_slug"] == "credit-review"
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
            assert (await client.get("/api/skill-market")).json()["data"] == []
            assert (await client.get("/api/skill-market/credit-review")).status_code == 404
            assert (await client.post("/api/skill-market/credit-review/install")).status_code == 404

            current = User(uid="other-admin", username="Other", password_hash="x", role="admin", department_id=2)
            assert (await client.get("/api/skill-market")).json()["data"] == []
            assert (await client.get("/api/skill-market/credit-review")).status_code == 404

            current = User(uid="credit-user", username="Credit", password_hash="x", role="user", department_id=1)
            listing = (await client.get("/api/skill-market?sort=installs")).json()["data"][0]
            assert listing["installed_version"] is None
            assert (await client.get("/api/skill-market?installed_only=true")).json()["data"] == []
            installed = await client.post("/api/skill-market/credit-review/install")
            assert installed.status_code == 200, installed.text
            assert installed.json()["data"]["installed_version"] == 1
            assert installed.json()["data"]["install_count"] == 1
            assert len((await client.get("/api/skill-market?installed_only=true")).json()["data"]) == 1
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
                json={"application_reason": "更新审核步骤"},
            )
            assert second.status_code == 200, second.text
            current = User(uid="credit-admin", username="Admin", password_hash="x", role="admin", department_id=1)
            second_approval = await client.post(
                f"/api/skill-market/requests/{second.json()['data']['id']}/approve",
                json={"scope": "department"},
            )
            assert second_approval.status_code == 200, second_approval.text

            current = User(uid="credit-user", username="Credit", password_hash="x", role="user", department_id=1)
            detail = (await client.get("/api/skill-market/credit-review")).json()["data"]
            assert detail["latest_version"] == 2
            assert detail["installed_version"] == 1
            assert detail["upgrade_available"] is True
            async with sessions() as db:
                accessible = await list_accessible_skills(db, current)
                market_items = [item for item in accessible if item.source_scope == "market"]
                assert len(market_items) == 1
                assert market_items[0].version == "1"
                projection_sources = await market.market_projection_sources(db, current)
                releases = (await db.scalars(select(MarketSkillRelease).order_by(MarketSkillRelease.version))).all()
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
            third = await sharing.submit_skill_share_request(
                db,
                slug="credit-review",
                application_reason="验证提交后连接异常",
                operator=User(uid="author", username="Author", password_hash="x", department_id=1),
                channel="market",
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
                        MarketSkill.__table__,
                        MarketSkillRelease.__table__,
                        MarketSkillInstall.__table__,
                        MarketSkillLike.__table__,
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
