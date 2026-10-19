"""技能发布申请列表的分页和权限边界。"""

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from yuxi.agents.skills import market
from yuxi.agents.skills.sharing import list_skill_share_requests
from yuxi.storage.postgres.models_business import Base, MarketSkill, MarketSkillRelease, Skill, SkillShareRequest, User


@pytest.mark.asyncio
async def test_share_request_page_filters_before_count_and_limit():
    """部门和状态筛选先于分页，且旧记录可用 slug 继续定位。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda conn: Base.metadata.create_all(
                conn, tables=[User.__table__, Skill.__table__, SkillShareRequest.__table__]
            )
        )
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions() as db:
            db.add_all(
                [
                    User(uid="owner-1", username="Owner 1", password_hash="x", role="user", department_id=1),
                    User(uid="owner-2", username="Owner 2", password_hash="x", role="user", department_id=2),
                    SkillShareRequest(
                        id="r1",
                        owner_uid="owner-1",
                        owner_department_id=1,
                        personal_slug="demo",
                        name="Demo",
                        description="x",
                        content_hash="h",
                        channel="shared",
                        status="approved",
                    ),
                    SkillShareRequest(
                        id="r2",
                        owner_uid="owner-1",
                        owner_department_id=1,
                        personal_slug="demo",
                        name="Demo",
                        description="x",
                        content_hash="h",
                        channel="shared",
                        status="rejected",
                    ),
                    SkillShareRequest(
                        id="r3",
                        owner_uid="owner-1",
                        owner_department_id=1,
                        personal_slug="other",
                        name="Other",
                        description="x",
                        content_hash="h",
                        channel="shared",
                        status="pending",
                    ),
                    SkillShareRequest(
                        id="r4",
                        owner_uid="owner-2",
                        owner_department_id=2,
                        personal_slug="demo",
                        name="Other owner",
                        description="x",
                        content_hash="h",
                        channel="shared",
                        status="pending",
                    ),
                ]
            )
            await db.commit()
            admin = User(uid="admin", username="Admin", password_hash="x", role="admin", department_id=1)
            first = await list_skill_share_requests(db, operator=admin, limit=1, offset=0)
            second = await list_skill_share_requests(db, operator=admin, limit=1, offset=1)
            assert first["total"] == second["total"] == 3
            assert {first["items"][0]["id"], second["items"][0]["id"]} <= {"r1", "r2", "r3"}
            assert len(first["items"]) == len(second["items"]) == 1
            approved = await list_skill_share_requests(db, operator=admin, status="approved", slug="demo", limit=1)
            assert approved["total"] == 1
            assert [item["id"] for item in approved["items"]] == ["r1"]
            assert approved["items"][0]["published_version"] == "1.0.0"
            owner = User(uid="owner-2", username="Other", password_hash="x", role="user", department_id=2)
            own = await list_skill_share_requests(db, operator=owner, limit=20)
            assert [item["id"] for item in own["items"]] == ["r4"]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_unbounded_request_lists_keep_channel_owner_and_filter_scope():
    """旧版无 limit 列表取回 20 条之后的记录，同时仍先按权限与状态过滤。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda conn: Base.metadata.create_all(
                conn,
                tables=[
                    User.__table__,
                    Skill.__table__,
                    SkillShareRequest.__table__,
                    MarketSkill.__table__,
                    MarketSkillRelease.__table__,
                ],
            )
        )
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions() as db:
            db.add_all(
                [
                    User(uid="owner-1", username="Owner 1", password_hash="x", role="user", department_id=1),
                    User(uid="owner-2", username="Owner 2", password_hash="x", role="user", department_id=2),
                ]
            )
            for channel in ("shared", "market"):
                for index in range(25):
                    db.add(
                        SkillShareRequest(
                            id=f"{channel}-one-{index}",
                            owner_uid="owner-1",
                            owner_department_id=1,
                            personal_slug="demo" if index % 2 == 0 else "other",
                            name="Skill",
                            description="x",
                            content_hash="h",
                            channel=channel,
                            status="approved" if index % 4 == 0 else "rejected",
                        )
                    )
                for index in range(2):
                    db.add(
                        SkillShareRequest(
                            id=f"{channel}-two-{index}",
                            owner_uid="owner-2",
                            owner_department_id=2,
                            personal_slug="demo",
                            name="Other",
                            description="x",
                            content_hash="h",
                            channel=channel,
                            status="approved",
                        )
                    )
            await db.commit()

            admin = User(uid="admin", username="Admin", password_hash="x", role="admin", department_id=1)
            owner = User(uid="owner-1", username="Owner", password_hash="x", role="user", department_id=1)
            for list_requests in (list_skill_share_requests, market.list_market_requests):
                full = await list_requests(db, operator=admin, limit=None)
                assert full["total"] == len(full["items"]) == 25
                assert {item["owner_uid"] for item in full["items"]} == {"owner-1"}
                filtered = await list_requests(db, operator=admin, status="approved", slug="demo", limit=None)
                assert filtered["total"] == len(filtered["items"]) == 7
                assert all(
                    item["status"] == "approved" and item["personal_slug"] == "demo" for item in filtered["items"]
                )
                own = await list_requests(db, operator=owner, limit=None)
                assert len(own["items"]) == 25
                not_mine = await list_requests(db, operator=admin, mine_only=True, limit=None)
                assert not_mine["items"] == []
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_publication_group_page_keeps_latest_and_approved_without_loading_full_history():
    """按个人技能分组翻页，同时给摘要保留各渠道最近批准的版本。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda conn: Base.metadata.create_all(
                conn,
                tables=[
                    User.__table__,
                    Skill.__table__,
                    SkillShareRequest.__table__,
                    MarketSkill.__table__,
                    MarketSkillRelease.__table__,
                ],
            )
        )
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions() as db:
            db.add_all(
                [
                    User(uid="alice", username="Alice", password_hash="x", role="user"),
                    SkillShareRequest(
                        id="a1",
                        owner_uid="alice",
                        personal_slug="alpha",
                        name="Alpha",
                        description="x",
                        content_hash="h",
                        channel="shared",
                        status="approved",
                    ),
                    SkillShareRequest(
                        id="a2",
                        owner_uid="alice",
                        personal_slug="alpha",
                        name="Alpha",
                        description="x",
                        content_hash="h",
                        channel="shared",
                        status="rejected",
                    ),
                    SkillShareRequest(
                        id="m1",
                        owner_uid="alice",
                        personal_slug="alpha",
                        name="Alpha",
                        description="x",
                        content_hash="h",
                        channel="market",
                        status="approved",
                    ),
                    SkillShareRequest(
                        id="b1",
                        owner_uid="alice",
                        personal_slug="beta",
                        name="Beta",
                        description="x",
                        content_hash="h",
                        channel="shared",
                        status="pending",
                    ),
                ]
            )
            await db.commit()
            owner = User(uid="alice", username="Alice", password_hash="x", role="user")
            page = await market.list_personal_publication_records(db, operator=owner, limit=1, offset=0)
            assert page["total"] == 2
            assert len(page["slugs"]) == 1
            assert all(
                item["personal_slug"] == page["slugs"][0] for item in page["shared_requests"] + page["market_requests"]
            )
            other = await market.list_personal_publication_records(db, operator=owner, limit=1, offset=1)
            assert set(page["slugs"] + other["slugs"]) == {"alpha", "beta"}
            alpha = next(item for item in (page, other) if item["slugs"] == ["alpha"])
            assert {item["id"] for item in alpha["shared_requests"]} == {"a1", "a2"}
            assert [item["id"] for item in alpha["market_requests"]] == ["m1"]
    finally:
        await engine.dispose()
