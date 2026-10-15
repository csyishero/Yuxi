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
            assert (await client.get("/api/skill-market")).json()["data"] == []
            assert (await client.get("/api/skill-market?category=finance_analysis")).json()["data"] == []
            assert (await client.get("/api/skill-market/credit-review")).status_code == 404
            assert (await client.post("/api/skill-market/credit-review/install")).status_code == 404

            current = User(uid="other-admin", username="Other", password_hash="x", role="admin", department_id=2)
            assert (await client.get("/api/skill-market")).json()["data"] == []
            assert (await client.get("/api/skill-market/credit-review")).status_code == 404

            current = User(uid="credit-user", username="Credit", password_hash="x", role="user", department_id=1)
            listing = (await client.get("/api/skill-market?sort=installs")).json()["data"][0]
            assert listing["category"] == "finance_analysis"
            assert len((await client.get("/api/skill-market?category=finance_analysis&limit=1")).json()["data"]) == 1
            assert (await client.get("/api/skill-market?category=office_tools")).json()["data"] == []
            assert (await client.get("/api/skill-market?category=invalid")).status_code == 400
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
            assert pending["current_market_version"] == 1
            assert pending["published_version"] is None
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
