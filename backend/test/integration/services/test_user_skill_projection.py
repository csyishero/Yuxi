"""真实 PostgreSQL 上的用户 Skill 投影并发授权测试。"""

from __future__ import annotations

import asyncio
import os
import uuid
from contextlib import asynccontextmanager, suppress
from pathlib import Path

import pytest
from sqlalchemy import delete, select, text, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from yuxi.agents.skills import service as skill_service
from yuxi.storage_migrations import v071_skills
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import (
    Base,
    Department,
    MarketSkill,
    MarketSkillInstall,
    MarketSkillRelease,
    MCPServer,
    Skill,
    User,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


@pytest.fixture(scope="session", autouse=True)
def ensure_live_api_schema():
    """本文件自行创建测试数据，不依赖运行中的 API。"""


@pytest.fixture(scope="session", autouse=True)
def cleanup_test_knowledge_resources():
    """本文件没有外部知识库资源需要清理。"""
    yield


@pytest.fixture(scope="session", autouse=True)
def cleanup_test_sandboxes():
    """本文件没有沙盒资源需要清理。"""
    yield


@pytest.fixture(scope="module", autouse=True)
def ensure_disposable_projection_tables():
    """在一次性测试库创建本模块使用的用户和 Skill 表。"""

    async def create_tables():
        engine = create_async_engine(os.environ["POSTGRES_URL"])
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
                            MCPServer.__table__,
                        ],
                    )
                )
        finally:
            await engine.dispose()

    asyncio.run(create_tables())


async def _wait_for_advisory_waiter(session_factory, lock_identity) -> bool:
    """等待真实 PostgreSQL 观察到同一 advisory lock 的阻塞者。"""
    deadline = asyncio.get_running_loop().time() + 5
    async with session_factory() as observer_db:
        while asyncio.get_running_loop().time() < deadline:
            waiting = bool(
                await observer_db.scalar(
                    text(
                        "SELECT EXISTS (SELECT 1 FROM pg_locks "
                        "WHERE locktype = 'advisory' AND NOT granted "
                        "AND classid::bigint = :classid "
                        "AND objid::bigint = :objid AND objsubid = :objsubid)"
                    ),
                    {
                        "classid": lock_identity.classid,
                        "objid": lock_identity.objid,
                        "objsubid": lock_identity.objsubid,
                    },
                )
            )
            if waiting:
                return True
            await asyncio.sleep(0.01)
    return False


async def test_projection_refresh_waits_for_lock_then_reloads_revoked_authorization(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
):
    """等待 uid 锁的 refresh 必须在取锁后重读最新授权。"""
    engine = create_async_engine(os.environ["POSTGRES_URL"], pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def local_session_context():
        async with session_factory() as db:
            try:
                yield db
                await db.commit()
            except BaseException:
                await db.rollback()
                raise

    monkeypatch.setattr(pg_manager, "get_async_session_context", local_session_context)
    monkeypatch.setattr(skill_service, "get_skill_data_dir", lambda: tmp_path / "skill-sources")
    monkeypatch.setattr(skill_service, "get_skill_projection_dir", lambda: tmp_path / "skill-projections")

    async def no_personal_skills(_uid: str):
        return []

    monkeypatch.setattr(skill_service, "list_personal_skills", no_personal_skills)

    suffix = uuid.uuid4().hex
    uid = f"pytest-skill-user-{suffix}"
    slug = f"pytest-skill-{suffix}"
    source_dir = tmp_path / "skill-sources/shared" / slug
    source_dir.mkdir(parents=True)
    (source_dir / "SKILL.md").write_text("# authorized\n", encoding="utf-8")
    user_id: int | None = None
    skill_id: int | None = None
    refresh_task: asyncio.Task[dict[str, str]] | None = None
    policy_task: asyncio.Task[None] | None = None
    lock_scope = f"yuxi:skills:user-projection:v1:{uid}"

    try:
        async with session_factory() as db:
            user = User(username=uid, uid=uid, password_hash="test", role="user")
            skill = Skill(
                slug=slug,
                name=slug,
                description="PostgreSQL advisory lock integration fixture",
                source_type="upload",
                tool_dependencies=[],
                mcp_dependencies=[],
                skill_dependencies=[],
                dir_path=f"shared/{slug}",
                share_config={
                    "version": 2,
                    "read_scope": {
                        "access_level": "user",
                        "department_ids": [],
                        "user_uids": [uid],
                    },
                    "manage_scope": None,
                },
                enabled=True,
                created_by="another-user",
            )
            db.add_all([user, skill])
            await db.commit()
            user_id = user.id
            skill_id = skill.id

        skill_service.sync_user_accessible_skills(uid, {slug: source_dir})
        projection = skill_service.get_user_skills_root_dir(uid)
        assert (projection / slug / "SKILL.md").is_file()

        async with session_factory() as lock_db:
            await lock_db.execute(
                text("SELECT pg_advisory_xact_lock(hashtext(:lock_scope))"),
                {"lock_scope": lock_scope},
            )
            lock_identity = (
                await lock_db.execute(
                    text(
                        "SELECT classid::bigint, objid::bigint, objsubid "
                        "FROM pg_locks WHERE pid = pg_backend_pid() "
                        "AND locktype = 'advisory' AND granted"
                    )
                )
            ).one()
            refresh_task = asyncio.create_task(skill_service.refresh_user_skill_projection_async(uid))

            assert await _wait_for_advisory_waiter(session_factory, lock_identity), (
                "refresh did not wait on the expected PostgreSQL advisory lock"
            )
            assert not refresh_task.done()

            async with session_factory() as revoke_db:
                await revoke_db.execute(
                    update(Skill)
                    .where(Skill.id == skill_id)
                    .values(
                        share_config={
                            "version": 2,
                            "read_scope": {
                                "access_level": "user",
                                "department_ids": [],
                                "user_uids": ["different-user"],
                            },
                            "manage_scope": None,
                        }
                    )
                )
                await revoke_db.commit()

            await lock_db.commit()

        refreshed_sources = await asyncio.wait_for(refresh_task, timeout=5)
        assert slug not in refreshed_sources
        assert not (projection / slug).exists()

        async with session_factory() as db:
            await db.execute(
                update(Skill)
                .where(Skill.id == skill_id)
                .values(
                    share_config={
                        "version": 2,
                        "read_scope": {
                            "access_level": "user",
                            "department_ids": [],
                            "user_uids": [uid],
                        },
                        "manage_scope": None,
                    }
                )
            )
            await db.commit()
        await skill_service.refresh_user_skill_projection_async(uid)
        assert (projection / slug / "SKILL.md").is_file()

        async with session_factory() as lock_db:
            await lock_db.execute(
                text("SELECT pg_advisory_xact_lock(hashtext(:lock_scope))"),
                {"lock_scope": lock_scope},
            )
            lock_identity = (
                await lock_db.execute(
                    text(
                        "SELECT classid::bigint, objid::bigint, objsubid "
                        "FROM pg_locks WHERE pid = pg_backend_pid() "
                        "AND locktype = 'advisory' AND granted"
                    )
                )
            ).one()
            async with session_factory() as policy_db:
                await policy_db.execute(
                    update(Skill)
                    .where(Skill.id == skill_id)
                    .values(
                        share_config={
                            "version": 2,
                            "read_scope": {
                                "access_level": "user",
                                "department_ids": [],
                                "user_uids": ["different-user"],
                            },
                            "manage_scope": None,
                        }
                    )
                )
                policy_task = asyncio.create_task(skill_service.apply_skill_projection_policy_change(policy_db, slug))
                assert await _wait_for_advisory_waiter(session_factory, lock_identity), (
                    "policy mutation did not wait on the uid projection lock"
                )
                assert not policy_task.done()
                assert (projection / slug / "SKILL.md").is_file()
                await lock_db.commit()
                await asyncio.wait_for(policy_task, timeout=5)

        assert not (projection / slug).exists()
        async with session_factory() as db:
            persisted_share_config = await db.scalar(select(Skill.share_config).where(Skill.id == skill_id))
        assert persisted_share_config["read_scope"]["user_uids"] == ["different-user"]
    finally:
        for task in (refresh_task, policy_task):
            if task is None:
                continue
            if not task.done():
                task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await task
        async with session_factory() as db:
            if skill_id is not None:
                await db.execute(delete(Skill).where(Skill.id == skill_id))
            if user_id is not None:
                await db.execute(delete(User).where(User.id == user_id))
            await db.commit()
        await engine.dispose()


async def test_legacy_shared_skill_migrates_without_touching_personal_workspace(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
):
    """旧共享来源完成切换时，UserWorkspace 中的个人 Skill 必须原地保留。"""
    engine = create_async_engine(os.environ["POSTGRES_URL"], pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    suffix = uuid.uuid4().hex
    uid = f"pytest-skill-migration-{suffix}"
    shared_slug = f"shared-{suffix}"
    personal_slug = f"personal-{suffix}"
    monkeypatch.setenv("YUXI_LEGACY_STORAGE_DIR", str(tmp_path))
    monkeypatch.setenv("YUXI_USER_DATA_DIR", str(tmp_path / "threads"))
    monkeypatch.setenv("YUXI_SKILL_DATA_DIR", str(tmp_path / "skill-sources"))
    monkeypatch.setenv("YUXI_SKILL_PROJECTION_DIR", str(tmp_path / "skill-projections"))

    legacy_shared = tmp_path / "skills" / shared_slug
    legacy_personal = tmp_path / "threads/shared" / uid / "workspace/agents/skills" / personal_slug
    for path, slug, marker in (
        (legacy_shared, shared_slug, "shared-marker"),
        (legacy_personal, personal_slug, "personal-marker"),
    ):
        path.mkdir(parents=True)
        (path / "SKILL.md").write_text(
            f"---\nname: {slug}\ndescription: migration fixture\n---\n{marker}\n",
            encoding="utf-8",
        )

    user_id: int | None = None
    skill_id: int | None = None
    try:
        async with session_factory() as db:
            user = User(username=uid, uid=uid, password_hash="test", role="user")
            skill = Skill(
                slug=shared_slug,
                name=shared_slug,
                description="migration fixture",
                source_type="upload",
                tool_dependencies=[],
                mcp_dependencies=[],
                skill_dependencies=[],
                dir_path=f"skills/{shared_slug}",
                share_config={
                    "version": 2,
                    "read_scope": {"access_level": "global", "department_ids": [], "user_uids": []},
                    "manage_scope": None,
                },
                enabled=True,
                created_by=uid,
            )
            db.add_all([user, skill])
            await db.commit()
            user_id = user.id
            skill_id = skill.id

        original_rmtree = skill_service.shutil.rmtree
        cleanup_failed = False

        def fail_shared_cleanup_once(path, *args, **kwargs):
            nonlocal cleanup_failed
            if not cleanup_failed and Path(path) == legacy_shared:
                cleanup_failed = True
                raise OSError("injected legacy cleanup failure")
            return original_rmtree(path, *args, **kwargs)

        monkeypatch.setattr(v071_skills.shutil, "rmtree", fail_shared_cleanup_once)
        with pytest.raises(OSError, match="injected legacy cleanup failure"):
            async with session_factory() as db:
                await v071_skills.migrate_shared_skills(db)

        async with session_factory() as db:
            persisted_after_failure = await db.scalar(select(Skill.dir_path).where(Skill.id == skill_id))
        assert persisted_after_failure == f"shared/{shared_slug}"
        assert legacy_shared.is_dir()

        monkeypatch.setattr(v071_skills.shutil, "rmtree", original_rmtree)
        async with session_factory() as db:
            await v071_skills.migrate_shared_skills(db)

        async with session_factory() as db:
            persisted_path = await db.scalar(select(Skill.dir_path).where(Skill.id == skill_id))
        assert persisted_path == f"shared/{shared_slug}"
        assert "shared-marker" in (tmp_path / "skill-sources/shared" / shared_slug / "SKILL.md").read_text(
            encoding="utf-8"
        )
        assert skill_service.get_personal_skills_root_dir(uid) / personal_slug == legacy_personal
        assert "personal-marker" in (legacy_personal / "SKILL.md").read_text(encoding="utf-8")
        assert not legacy_shared.exists()
        assert legacy_personal.is_dir()
        assert not (tmp_path / "skill-sources/personal" / uid / personal_slug).exists()

        # 停机迁移重复执行也不得扫描或删除个人 Workspace。
        async with session_factory() as db:
            await v071_skills.migrate_shared_skills(db)
        assert legacy_personal.is_dir()
    finally:
        async with session_factory() as db:
            if skill_id is not None:
                await db.execute(delete(Skill).where(Skill.id == skill_id))
            if user_id is not None:
                await db.execute(delete(User).where(User.id == user_id))
            await db.commit()
        await engine.dispose()


@pytest.fixture
async def market_projection(tmp_path, monkeypatch):
    """为市场治理与真实刷新创建独立 Schema 和不可变发布目录。"""
    from yuxi.agents.skills import market
    from yuxi.storage.postgres.models_business import OperationLog, SkillShareRequest

    schema = f"pytest_governance_projection_{uuid.uuid4().hex[:12]}"
    admin_engine = create_async_engine(os.environ["POSTGRES_URL"])
    async with admin_engine.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_async_engine(os.environ["POSTGRES_URL"], connect_args={"server_settings": {"search_path": schema}})
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def local_session_context():
        async with sessions() as db:
            try:
                yield db
                await db.commit()
            except BaseException:
                await db.rollback()
                raise

    monkeypatch.setattr(pg_manager, "get_async_session_context", local_session_context)
    monkeypatch.setattr(skill_service, "get_skill_projection_dir", lambda: tmp_path / "projections")
    monkeypatch.setattr(skill_service, "get_skill_data_dir", lambda: tmp_path)
    monkeypatch.setattr(market, "get_skill_data_dir", lambda: tmp_path)
    source = tmp_path / "market/release"
    source.mkdir(parents=True)
    (source / "SKILL.md").write_text("---\nname: projection-guide\ndescription: old\n---\nOld release\n")
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
                        MCPServer.__table__,
                        OperationLog.__table__,
                        SkillShareRequest.__table__,
                    ],
                )
            )
        async with sessions() as db:
            db.add_all([Department(id=1, name="A"), Department(id=2, name="B")])
            await db.flush()
            db.add_all(
                [
                    User(uid="z-reader", username="Z", password_hash="x", department_id=2),
                    User(uid="a-reader", username="A", password_hash="x", department_id=1),
                    User(uid="root", username="Root", password_hash="x", role="superadmin"),
                ]
            )
            db.add(
                MarketSkill(
                    id="listing",
                    slug="projection-guide",
                    owner_uid="root",
                    personal_slug="projection-guide",
                    scope="global",
                    latest_release_id="release",
                )
            )
            await db.flush()
            db.add(
                MarketSkillRelease(
                    id="release",
                    market_skill_id="listing",
                    version=1,
                    name="projection-guide",
                    description="old",
                    dir_path="market/release",
                    content_hash="old",
                    request_id="request",
                )
            )
            await db.flush()
            db.add_all(
                [
                    MarketSkillInstall(uid=uid, market_skill_id="listing", release_id="release")
                    for uid in ("z-reader", "a-reader")
                ]
            )
            await db.commit()
        for uid in ("z-reader", "a-reader"):
            await skill_service.refresh_user_skill_projection_async(uid)
        yield sessions
    finally:
        await engine.dispose()
        async with admin_engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin_engine.dispose()


@pytest.mark.parametrize("mutation", ["unpublish", "scope"])
async def test_governance_waits_for_refresh_uid_lock_then_revokes(market_projection, mutation):
    """先行刷新持锁时治理等待，提交后新刷新也无法恢复已撤目录。"""
    from yuxi.services import skill_market_governance_service as service

    sessions = market_projection
    async with sessions() as holder, sessions() as writer:
        await holder.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:scope))"), {"scope": "yuxi:skills:user-projection:v1:z-reader"}
        )
        identity = (
            await holder.execute(
                text(
                    "SELECT classid::bigint, objid::bigint, objsubid FROM pg_locks "
                    "WHERE pid=pg_backend_pid() AND locktype='advisory' AND granted"
                )
            )
        ).one()
        root = await writer.scalar(select(User).where(User.uid == "root"))
        if mutation == "unpublish":
            call = service.unpublish_market_skill(writer, slug="projection-guide", expected_revision=1, operator=root)
        else:
            call = service.change_market_skill_scope(
                writer, slug="projection-guide", scope="department", department_id=1, expected_revision=1, operator=root
            )
        task = asyncio.create_task(call)
        try:
            assert await _wait_for_advisory_waiter(sessions, identity)
            path = skill_service.get_user_skills_root_dir("z-reader") / "market-release-release"
            assert (path / "SKILL.md").is_file()
            await holder.commit()
            await asyncio.wait_for(task, 5)
            assert not path.exists()
            await skill_service.refresh_user_skill_projection_async("z-reader")
            assert not path.exists()
            allowed = skill_service.get_user_skills_root_dir("a-reader") / "market-release-release"
            assert allowed.exists() is (mutation == "scope")
            if mutation == "scope":
                await service.change_market_skill_scope(
                    writer,
                    slug="projection-guide",
                    scope="global",
                    department_id=None,
                    expected_revision=2,
                    operator=root,
                )
                await skill_service.refresh_user_skill_projection_async("z-reader")
                assert "Old release" in (path / "SKILL.md").read_text()
                install = await writer.get(MarketSkillInstall, ("z-reader", "listing"))
                assert (install.release_id, install.confirmed_generation) == ("release", 1)
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)


async def test_governance_restores_before_uid_locks_release(market_projection, monkeypatch):
    """提交失败回滚前，文件恢复必须完成且刷新仍在真实 UID 锁上等待。"""
    from yuxi.services import skill_market_governance_service as service

    sessions = market_projection
    refresh = None
    async with sessions() as writer:
        root = await writer.scalar(select(User).where(User.uid == "root"))

        async def fail_commit():
            nonlocal refresh
            path = skill_service.get_user_skills_root_dir("z-reader") / "market-release-release"
            assert not path.exists()
            identities = (
                await writer.execute(
                    text(
                        "SELECT classid::bigint, objid::bigint, objsubid FROM pg_locks "
                        "WHERE pid=pg_backend_pid() AND locktype='advisory' AND granted"
                    )
                )
            ).all()
            refresh = asyncio.create_task(skill_service.refresh_user_skill_projection_async("z-reader"))
            assert any([await _wait_for_advisory_waiter(sessions, identity) for identity in identities])
            raise OSError("commit refused")

        monkeypatch.setattr(writer, "commit", fail_commit)
        original_rollback = writer.rollback

        async def check_restore_then_rollback():
            assert not refresh.done()
            for uid in ("a-reader", "z-reader"):
                path = skill_service.get_user_skills_root_dir(uid) / "market-release-release/SKILL.md"
                assert "Old release" in path.read_text()
            await original_rollback()

        monkeypatch.setattr(writer, "rollback", check_restore_then_rollback)
        try:
            with pytest.raises(OSError, match="commit refused"):
                await service.unpublish_market_skill(
                    writer, slug="projection-guide", expected_revision=1, operator=root
                )
            await asyncio.wait_for(refresh, 5)
            async with sessions() as observer:
                listing = await observer.get(MarketSkill, "listing")
                assert listing.is_published and listing.state_revision == 1
        finally:
            if refresh is not None and not refresh.done():
                refresh.cancel()
                await asyncio.gather(refresh, return_exceptions=True)


async def test_refresh_waits_for_governance_commit(market_projection, monkeypatch):
    """治理先拿 UID 锁时，真实刷新只能在提交后读取撤销结果。"""
    from yuxi.services import skill_market_governance_service as service

    sessions = market_projection
    refresh = None
    async with sessions() as writer:
        root = await writer.scalar(select(User).where(User.uid == "root"))
        original_commit = writer.commit

        async def pause_before_commit():
            nonlocal refresh
            assert not (skill_service.get_user_skills_root_dir("z-reader") / "market-release-release").exists()
            identity = (
                await writer.execute(
                    text(
                        "SELECT classid::bigint, objid::bigint, objsubid FROM pg_locks "
                        "WHERE pid=pg_backend_pid() AND locktype='advisory' AND granted "
                        "AND objid::bigint=(hashtext('yuxi:skills:user-projection:v1:z-reader')::bigint & 4294967295)"
                    )
                )
            ).one()
            refresh = asyncio.create_task(skill_service.refresh_user_skill_projection_async("z-reader"))
            assert await _wait_for_advisory_waiter(sessions, identity)
            await original_commit()

        monkeypatch.setattr(writer, "commit", pause_before_commit)
        try:
            await service.unpublish_market_skill(writer, slug="projection-guide", expected_revision=1, operator=root)
            sources = await asyncio.wait_for(refresh, 5)
            assert "market-release-release" not in sources
            assert not (skill_service.get_user_skills_root_dir("z-reader") / "market-release-release").exists()
        finally:
            if refresh is not None and not refresh.done():
                refresh.cancel()
                await asyncio.gather(refresh, return_exceptions=True)


async def test_governance_flushes_audit_before_staging_files(market_projection, monkeypatch):
    """审计 flush 可能自动回滚并释放锁，因此必须先于文件撤销。"""
    from sqlalchemy.exc import IntegrityError
    from yuxi.services import skill_market_governance_service as service
    from yuxi.storage.postgres.models_business import OperationLog

    sessions = market_projection
    async with sessions() as writer:
        root = await writer.scalar(select(User).where(User.uid == "root"))

        async def invalid_audit(db, *args, **kwargs):
            for uid in ("a-reader", "z-reader"):
                assert (skill_service.get_user_skills_root_dir(uid) / "market-release-release/SKILL.md").is_file()
            db.add(OperationLog(user_id=-999, operation="invalid audit"))
            await db.flush()

        monkeypatch.setattr(service, "log_operation", invalid_audit)
        with pytest.raises(IntegrityError):
            await service.unpublish_market_skill(writer, slug="projection-guide", expected_revision=1, operator=root)
    async with sessions() as db:
        assert (await db.get(MarketSkill, "listing")).is_published
        assert (await db.scalars(select(OperationLog))).all() == []


async def test_committed_revocation_does_not_report_cleanup_as_policy_failure(market_projection, monkeypatch):
    """提交后的不可见暂存清理失败不能把已提交治理误报成回滚。"""
    from yuxi.services import skill_market_governance_service as service

    original_rmtree = skill_service.shutil.rmtree

    def fail_staging_cleanup(path, *args, **kwargs):
        if Path(path).name.startswith(".market-revocation-"):
            raise OSError("post-commit cleanup unavailable")
        return original_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(skill_service.shutil, "rmtree", fail_staging_cleanup)
    async with market_projection() as db:
        root = await db.scalar(select(User).where(User.uid == "root"))
        result = await service.unpublish_market_skill(db, slug="projection-guide", expected_revision=1, operator=root)
        assert result["is_published"] is False
    async with market_projection() as db:
        assert (await db.get(MarketSkill, "listing")).is_published is False
    for uid in ("a-reader", "z-reader"):
        assert not (skill_service.get_user_skills_root_dir(uid) / "market-release-release").exists()


async def test_cancelled_refresh_holds_uid_lock_until_worker_finishes(market_projection, monkeypatch):
    """取消刷新不能让仍在写文件的工作线程越过治理撤销事务。"""
    import threading
    from yuxi.services import skill_market_governance_service as service

    sessions = market_projection
    entered = threading.Event()
    resume = threading.Event()
    target = skill_service.get_user_skills_root_dir("z-reader") / "market-release-release"
    (target / "SKILL.md").write_text("force refresh copy")
    original_rename = Path.rename

    def pause_final_rename(source, destination):
        if Path(destination) == target:
            entered.set()
            if not resume.wait(timeout=15):
                raise TimeoutError("test did not release projection worker")
        return original_rename(source, destination)

    monkeypatch.setattr(Path, "rename", pause_final_rename)
    refresh = asyncio.create_task(skill_service.refresh_user_skill_projection_async("z-reader"))
    governance = None
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        async with sessions() as observer:
            identity = (
                await observer.execute(
                    text(
                        "SELECT classid::bigint, objid::bigint, objsubid FROM pg_locks "
                        "WHERE locktype='advisory' AND granted "
                        "AND objid::bigint=(hashtext('yuxi:skills:user-projection:v1:z-reader')::bigint & 4294967295)"
                    )
                )
            ).one()
        refresh.cancel()
        async with sessions() as writer:
            root = await writer.scalar(select(User).where(User.uid == "root"))
            governance = asyncio.create_task(
                service.unpublish_market_skill(writer, slug="projection-guide", expected_revision=1, operator=root)
            )
            assert await _wait_for_advisory_waiter(sessions, identity)
            assert not refresh.done()
            refresh.cancel()  # 第二次取消也不能释放仍有工作线程写入的锁。
            await asyncio.sleep(0)
            assert not refresh.done()
            resume.set()
            with pytest.raises(asyncio.CancelledError):
                await refresh
            await asyncio.wait_for(governance, 5)
        assert not target.exists()
        async with sessions() as db:
            assert not (await db.get(MarketSkill, "listing")).is_published
    finally:
        resume.set()
        await asyncio.gather(refresh, *([governance] if governance else []), return_exceptions=True)


async def test_empty_staging_cleanup_failure_keeps_committed_success(market_projection, monkeypatch):
    """没有目录需要撤去时，提交后的 rmdir 失败也不得误报策略失败。"""
    from yuxi.services import skill_market_governance_service as service

    original_rmdir = Path.rmdir

    def fail_empty_staging(path):
        if path.name.startswith(".market-revocation-"):
            raise OSError("empty staging cleanup unavailable")
        return original_rmdir(path)

    monkeypatch.setattr(Path, "rmdir", fail_empty_staging)
    async with market_projection() as db:
        root = await db.scalar(select(User).where(User.uid == "root"))
        result = await service.change_market_skill_scope(
            db, slug="projection-guide", scope="global", department_id=None, expected_revision=1, operator=root
        )
        assert result["state_revision"] == 2
    async with market_projection() as db:
        assert (await db.get(MarketSkill, "listing")).state_revision == 2
    for uid in ("a-reader", "z-reader"):
        assert (skill_service.get_user_skills_root_dir(uid) / "market-release-release/SKILL.md").is_file()
