"""同源共享与市场技能的真实 HTTP 和 PostgreSQL 边界。"""

from __future__ import annotations

import os
import shutil
import uuid
from datetime import timedelta
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from server.routers.skill_market_router import market_router
from server.routers.skill_router import user_skills
from server.utils.auth_middleware import get_db, get_required_user
from yuxi.agents.skills import market, service as skill_service, sharing
from yuxi.agents.skills.service import select_default_skill_slugs
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
    """本文件使用隔离 Schema。"""


@pytest.fixture(scope="session", autouse=True)
def cleanup_test_knowledge_resources():
    """本文件不创建知识库资源。"""
    yield


@pytest.fixture(scope="session", autouse=True)
def cleanup_test_sandboxes():
    """本文件不创建沙盒。"""
    yield


async def test_shared_market_origin_is_visible_only_with_permission_and_owner_cannot_reinstall(
    monkeypatch, tmp_path: Path
):
    """共享关联按审核来源和可见性计算，作者的个人原件阻止重复安装。"""
    schema = f"pytest_skill_origin_{uuid.uuid4().hex[:12]}"
    admin_engine = create_async_engine(os.environ["POSTGRES_URL"])
    async with admin_engine.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_async_engine(os.environ["POSTGRES_URL"], connect_args={"server_settings": {"search_path": schema}})
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    personal_root = tmp_path / "personal"
    for uid in ("author", "other-author", "credit-viewer", "risk-viewer", "credit-admin", "root"):
        (personal_root / uid).mkdir(parents=True)
    for uid in ("author", "other-author"):
        source = personal_root / uid / "local-pptx"
        source.mkdir()
        (source / "SKILL.md").write_text(
            "---\nname: local-pptx\ndescription: 本地生成演示文稿\n---\n# Skill\n", encoding="utf-8"
        )
    (tmp_path / "shared").mkdir()

    monkeypatch.setattr(sharing, "personal_skills_root", lambda uid: personal_root / uid)
    monkeypatch.setattr(skill_service, "personal_skills_root", lambda uid: personal_root / uid)
    monkeypatch.setattr(sharing, "get_skill_data_dir", lambda: tmp_path)
    monkeypatch.setattr(skill_service, "get_skill_data_dir", lambda: tmp_path)
    monkeypatch.setattr(market, "get_skill_data_dir", lambda: tmp_path)
    monkeypatch.setattr(sharing, "get_skills_root_dir", lambda: tmp_path / "shared")
    monkeypatch.setattr(skill_service, "get_skills_root_dir", lambda: tmp_path / "shared")

    async def no_dependencies(**_kwargs):
        """本例技能没有外部依赖。"""
        return [], [], []

    monkeypatch.setattr(sharing, "validate_skill_dependencies", no_dependencies)
    monkeypatch.setattr(market, "validate_skill_dependencies", no_dependencies)

    users = {
        "author": User(uid="author", username="Author", password_hash="x", role="user", department_id=1),
        "other-author": User(
            uid="other-author", username="Other Author", password_hash="x", role="user", department_id=1
        ),
        "credit-viewer": User(
            uid="credit-viewer", username="Credit Viewer", password_hash="x", role="user", department_id=1
        ),
        "risk-viewer": User(uid="risk-viewer", username="Risk Viewer", password_hash="x", role="user", department_id=2),
        "credit-admin": User(
            uid="credit-admin", username="Credit Admin", password_hash="x", role="admin", department_id=1
        ),
        "root": User(uid="root", username="Root", password_hash="x", role="superadmin", department_id=2),
    }
    current = users["author"]
    app = FastAPI()
    app.include_router(user_skills, prefix="/api")
    app.include_router(market_router, prefix="/api")

    async def provide_user():
        """提供已认证的请求用户。"""
        return current

    async def provide_db():
        """每次请求使用独立数据库会话。"""
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
            db.add_all(users.values())
            await db.commit()

        async with sessions() as db:
            shared_request = await sharing.submit_skill_share_request(
                db, slug="local-pptx", application_reason="跨部门共享", operator=users["author"]
            )
            shared_request = await sharing.approve_skill_share_request(
                db, request_id=shared_request.id, department_ids=[1, 2], note="", operator=users["root"]
            )
            shared_slug = shared_request.published_slug
            assert shared_slug == "local-pptx"

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            submitted = await client.post(
                "/api/skill-market/personal/local-pptx/submit", json={"application_reason": "发布给本部门"}
            )
            assert submitted.status_code == 200, submitted.text
            current = users["credit-admin"]
            approved = await client.post(
                f"/api/skill-market/requests/{submitted.json()['data']['id']}/approve",
                json={"scope": "department"},
            )
            assert approved.status_code == 200, approved.text
            market_slug = approved.json()["data"]["published_slug"]
            assert market_slug == "local-pptx-2"

            async with sessions() as db:
                second_shared_request = await sharing.submit_skill_share_request(
                    db, slug="local-pptx", application_reason="第二个共享版本", operator=users["author"]
                )
                second_shared_request = await sharing.approve_skill_share_request(
                    db, request_id=second_shared_request.id, department_ids=[1, 2], note="", operator=users["root"]
                )
                second_shared_slug = second_shared_request.published_slug
                assert second_shared_slug == "local-pptx-v2"
                published = await db.scalar(select(Skill).where(Skill.slug == second_shared_slug))
                assert published.source_type == "personal_share"
                assert published.content_hash != second_shared_request.content_hash  # 发布时改写 slug，快照哈希随之变化

                other_request = await sharing.submit_skill_share_request(
                    db, slug="local-pptx", application_reason="另一作者同名技能", operator=users["other-author"]
                )
                other_request = await sharing.approve_skill_share_request(
                    db, request_id=other_request.id, department_ids=[1, 2], note="", operator=users["root"]
                )
                other_slug = other_request.published_slug

            current = users["credit-viewer"]
            cards_response = await client.get("/api/skills")
            assert cards_response.status_code == 200, cards_response.text
            cards = {item["slug"]: item for item in cards_response.json()["data"]}
            assert cards[shared_slug]["related_market_slug"] == market_slug
            assert cards[second_shared_slug]["related_market_slug"] == market_slug
            assert "related_market_slug" not in cards[other_slug]
            catalog_pages = []
            for offset in (0, 1):
                catalog = await client.get(
                    "/api/skills",
                    params={
                        "source": "shared",
                        "shared_kind": "shared",
                        "catalog_all": True,
                        "limit": 1,
                        "offset": offset,
                    },
                )
                assert catalog.status_code == 200, catalog.text
                assert catalog.json()["total"] == 2
                assert len(catalog.json()["data"]) == 1
                catalog_pages.extend(item["slug"] for item in catalog.json()["data"])
            assert set(catalog_pages) == {second_shared_slug, other_slug}

            ordinary_shared = await client.get(
                "/api/skills", params={"source": "shared", "shared_kind": "shared", "limit": 10}
            )
            assert {item["slug"] for item in ordinary_shared.json()["data"]} == {
                shared_slug,
                second_shared_slug,
                other_slug,
            }
            assert ordinary_shared.json()["total"] == 3
            async with sessions() as db:
                first_shared = await db.scalar(select(Skill).where(Skill.slug == shared_slug))
                persisted_request = await db.get(SkillShareRequest, shared_request.id)
                original_description = first_shared.description
                first_shared.description = "only-older-version"
                persisted_request.description = "only-older-version"
                await db.commit()
            searched_catalog = await client.get(
                "/api/skills",
                params={
                    "source": "shared",
                    "shared_kind": "shared",
                    "catalog_all": True,
                    "search": "only-older-version",
                    "limit": 1,
                },
            )
            assert searched_catalog.status_code == 200, searched_catalog.text
            assert searched_catalog.json()["total"] == 1
            assert [item["slug"] for item in searched_catalog.json()["data"]] == [shared_slug]
            async with sessions() as db:
                first_shared = await db.scalar(select(Skill).where(Skill.slug == shared_slug))
                persisted_request = await db.get(SkillShareRequest, shared_request.id)
                first_shared.description = original_description
                persisted_request.description = original_description
                await db.commit()
            visible_listing = (await client.get("/api/skill-market?limit=1")).json()["data"][0]
            assert visible_listing["slug"] == market_slug
            assert visible_listing["own_personal_slug"] is None
            assert (await client.get("/api/skill-market?offset=1&limit=1")).json()["data"] == []
            assert (await client.get("/api/skills")).json()["data"] == cards_response.json()["data"]
            async with sessions() as db:
                default_slugs = await select_default_skill_slugs(db, current)
                assert second_shared_slug in default_slugs
                assert shared_slug not in default_slugs
                assert other_slug in default_slugs

                first_shared = await db.scalar(select(Skill).where(Skill.slug == shared_slug))
                original_created_at = first_shared.created_at
                first_shared.created_at = shared_request.reviewed_at + timedelta(seconds=1)
                await db.commit()
            stale = {item["slug"]: item for item in (await client.get("/api/skills")).json()["data"]}
            assert "related_market_slug" not in stale[shared_slug]
            assert stale[second_shared_slug]["related_market_slug"] == market_slug
            stale_catalog = await client.get(
                "/api/skills",
                params={"source": "shared", "shared_kind": "shared", "catalog_all": True, "limit": 10},
            )
            assert stale_catalog.status_code == 200, stale_catalog.text
            assert stale_catalog.json()["total"] == 3
            assert {item["slug"] for item in stale_catalog.json()["data"]} == {
                shared_slug,
                second_shared_slug,
                other_slug,
            }
            async with sessions() as db:
                first_shared = await db.scalar(select(Skill).where(Skill.slug == shared_slug))
                first_shared.created_at = original_created_at
                await db.commit()

            current = users["risk-viewer"]
            assert (await client.get("/api/skill-market")).json()["data"] == []
            hidden_cards = {item["slug"]: item for item in (await client.get("/api/skills")).json()["data"]}
            assert shared_slug in hidden_cards
            assert "related_market_slug" not in hidden_cards[shared_slug]
            assert "related_market_slug" not in hidden_cards[second_shared_slug]
            hidden_catalog = await client.get(
                "/api/skills",
                params={"source": "shared", "shared_kind": "shared", "catalog_all": True, "limit": 10},
            )
            assert hidden_catalog.status_code == 200, hidden_catalog.text
            assert hidden_catalog.json()["total"] == 3
            assert {item["slug"] for item in hidden_catalog.json()["data"]} == {
                shared_slug,
                second_shared_slug,
                other_slug,
            }

            current = users["root"]
            root_cards = {item["slug"]: item for item in (await client.get("/api/skills")).json()["data"]}
            assert root_cards[shared_slug]["related_market_slug"] == market_slug
            assert root_cards[second_shared_slug]["related_market_slug"] == market_slug

            current = users["author"]
            author_listing = (await client.get(f"/api/skill-market/{market_slug}")).json()["data"]
            assert author_listing["own_personal_slug"] == "local-pptx"
            duplicate_install = await client.post(f"/api/skill-market/{market_slug}/install")
            assert duplicate_install.status_code == 400
            assert "个人原件已可使用" in duplicate_install.json()["detail"]
            async with sessions() as db:
                assert (await db.scalars(select(MarketSkillInstall))).all() == []

            shutil.rmtree(personal_root / "author" / "local-pptx")
            installed = await client.post(f"/api/skill-market/{market_slug}/install")
            assert installed.status_code == 200, installed.text
            unshadowed_install = await client.get(
                "/api/skill-market/-/installs", params={"catalog_all": True, "limit": 1}
            )
            assert unshadowed_install.status_code == 200, unshadowed_install.text
            assert unshadowed_install.json()["total"] == 1
            assert [item["slug"] for item in unshadowed_install.json()["data"]] == [market_slug]
            installed_catalog = await client.get(
                "/api/skills",
                params={"source": "shared", "shared_kind": "shared", "catalog_all": True, "limit": 1},
            )
            assert installed_catalog.status_code == 200, installed_catalog.text
            assert installed_catalog.json()["total"] == 1
            assert [item["slug"] for item in installed_catalog.json()["data"]] == [other_slug]
            async with sessions() as db:
                installs = (await db.scalars(select(MarketSkillInstall))).all()
                assert len(installs) == 1 and installs[0].uid == "author"
                default_slugs = await select_default_skill_slugs(db, current)
                assert market_slug in default_slugs
                assert second_shared_slug not in default_slugs

            (personal_root / "author" / "local-pptx").mkdir()
            (personal_root / "author" / "local-pptx" / "SKILL.md").write_text(
                "---\nname: local-pptx\ndescription: 本地生成演示文稿\n---\n# Skill\n", encoding="utf-8"
            )
            assert (await client.post(f"/api/skill-market/{market_slug}/install")).status_code == 200
            folded_install = await client.get("/api/skill-market/-/installs", params={"catalog_all": True, "limit": 1})
            assert folded_install.status_code == 200, folded_install.text
            assert folded_install.json()["total"] == 0
            assert folded_install.json()["data"] == []
            ordinary_install = await client.get("/api/skill-market/-/installs", params={"limit": 1})
            assert ordinary_install.status_code == 200, ordinary_install.text
            assert ordinary_install.json()["total"] == 1
            assert [item["slug"] for item in ordinary_install.json()["data"]] == [market_slug]
            market_only_search = await client.get(
                "/api/skill-market/-/installs",
                params={"catalog_all": True, "limit": 1, "search": market_slug},
            )
            assert market_only_search.status_code == 200, market_only_search.text
            assert market_only_search.json()["total"] == 1
            async with sessions() as db:
                default_slugs = await select_default_skill_slugs(db, current)
                assert shared_slug in default_slugs  # 与个人原件同 slug，现由个人原件生效
                assert market_slug not in default_slugs
                assert second_shared_slug not in default_slugs
                preloaded = await select_default_skill_slugs(
                    db, current, [shared_slug, second_shared_slug, market_slug]
                )
                assert {shared_slug, second_shared_slug, market_slug}.issubset(preloaded)

            async with sessions() as db:
                db.add(
                    SkillShareRequest(
                        id=str(uuid.uuid4()),
                        owner_uid="other-author",
                        owner_department_id=1,
                        personal_slug="local-pptx",
                        name="local-pptx",
                        description="本地生成演示文稿",
                        content_hash="conflicting-origin",
                        channel="shared",
                        status="approved",
                        published_slug=shared_slug,
                    )
                )
                await db.commit()
            current = users["credit-viewer"]
            ambiguous = {item["slug"]: item for item in (await client.get("/api/skills")).json()["data"]}
            assert "related_market_slug" not in ambiguous[shared_slug]
            ambiguous_catalog = await client.get(
                "/api/skills",
                params={"source": "shared", "shared_kind": "shared", "catalog_all": True, "limit": 10},
            )
            assert ambiguous_catalog.status_code == 200, ambiguous_catalog.text
            assert ambiguous_catalog.json()["total"] == 3
            assert {item["slug"] for item in ambiguous_catalog.json()["data"]} == {
                shared_slug,
                second_shared_slug,
                other_slug,
            }

            unrelated_market_id = str(uuid.uuid4())
            unrelated_release_id = str(uuid.uuid4())
            async with sessions() as db:
                db.add(
                    MarketSkill(
                        id=unrelated_market_id,
                        slug=other_slug,
                        owner_uid="credit-viewer",
                        personal_slug="deleted-source",
                        scope="global",
                        latest_release_id=unrelated_release_id,
                    )
                )
                db.add(
                    MarketSkillRelease(
                        id=unrelated_release_id,
                        market_skill_id=unrelated_market_id,
                        version=1,
                        name=other_slug,
                        description="unrelated listing",
                        dir_path="market_releases/unrelated",
                        content_hash="unrelated",
                        request_id=str(uuid.uuid4()),
                    )
                )
                db.add(
                    MarketSkillInstall(
                        uid="credit-viewer", market_skill_id=unrelated_market_id, release_id=unrelated_release_id
                    )
                )
                await db.commit()
            same_slug_catalog = await client.get(
                "/api/skills",
                params={"source": "shared", "shared_kind": "shared", "catalog_all": True, "limit": 10},
            )
            assert same_slug_catalog.status_code == 200, same_slug_catalog.text
            assert same_slug_catalog.json()["total"] == 2
            assert {item["slug"] for item in same_slug_catalog.json()["data"]} == {
                shared_slug,
                second_shared_slug,
            }

            async with sessions() as db:
                db.add(
                    Skill(
                        slug=market_slug,
                        name=market_slug,
                        description="builtin catalog collision",
                        source_type="builtin",
                        dir_path=f"shared/{market_slug}",
                        share_config={
                            "version": 2,
                            "read_scope": {"access_level": "global", "department_ids": [], "user_uids": []},
                            "manage_scope": None,
                        },
                        enabled=True,
                        created_by="root",
                    )
                )
                await db.commit()
            current = users["author"]
            ordinary_builtin = await client.get(
                "/api/skills", params={"source": "shared", "shared_kind": "builtin", "limit": 1}
            )
            assert ordinary_builtin.status_code == 200, ordinary_builtin.text
            assert ordinary_builtin.json()["total"] == 1
            assert [item["slug"] for item in ordinary_builtin.json()["data"]] == [market_slug]
            all_builtin = await client.get(
                "/api/skills",
                params={"source": "shared", "shared_kind": "builtin", "catalog_all": True, "limit": 1},
            )
            assert all_builtin.status_code == 200, all_builtin.text
            assert all_builtin.json()["total"] == 0
            assert all_builtin.json()["data"] == []
    finally:
        await engine.dispose()
        async with admin_engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin_engine.dispose()
