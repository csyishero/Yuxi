"""项目目录清理只忽略创建前已解绑的旧上层目录。"""

from datetime import datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from yuxi.repositories.project_repository import ProjectRepository
from yuxi.storage.postgres.models_business import AgentRun, Base, Conversation, Project, User

pytestmark = [pytest.mark.asyncio, pytest.mark.unit]


@pytest.mark.parametrize(
    ("other_path", "other_status", "deleted_offset", "other_uid", "expected"),
    [
        ("projects", "deleted", -1, "user-1", False),
        ("projects", "active", None, "user-1", True),
        ("projects", "deleted", 1, "user-1", True),
        ("projects", "deleted", 0, "user-1", True),
        ("projects", "deleted", None, "user-1", True),
        ("projects/demo_a1b2c3d4", "deleted", -1, "user-1", True),
        ("projects/demo_a1b2c3d4/nested", "deleted", -1, "user-1", True),
        ("projects/sibling", "active", None, "user-1", False),
        ("projects", "active", None, "user-2", False),
    ],
)
async def test_workdir_overlap_respects_earlier_deleted_parent(
    other_path: str, other_status: str, deleted_offset: int | None, other_uid: str, expected: bool
):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    created_at = datetime(2026, 10, 9, 1, 15, 33)
    try:
        async with sessions() as db:
            db.add_all(
                [
                    User(username="user-1", uid="user-1", password_hash="test"),
                    User(username="user-2", uid="user-2", password_hash="test"),
                ]
            )
            target = Project(
                id="a1b2c3d4-e5f6-4789-8123-456789abcdef",
                uid="user-1",
                name="demo",
                selection_status="selectable",
                workdir_path="projects/demo_a1b2c3d4",
                directory_mode="managed",
                created_at=created_at,
            )
            other = Project(
                id="11111111-1111-4111-8111-111111111111",
                uid=other_uid,
                name="old",
                selection_status="selectable",
                workdir_path=other_path,
                directory_mode="linked",
                status=other_status,
                deleted_at=created_at + timedelta(seconds=deleted_offset) if deleted_offset is not None else None,
            )
            db.add_all([target, other])
            await db.flush()

            assert await ProjectRepository(db).has_other_workdir_overlap(target) is expected
    finally:
        await engine.dispose()


@pytest.mark.parametrize(
    ("run_status", "cleanup_pending", "expected"),
    [
        ("running", False, True),
        ("completed", True, True),
        ("completed", False, False),
    ],
)
async def test_earlier_deleted_parent_with_unfinished_work_still_blocks_cleanup(
    run_status: str, cleanup_pending: bool, expected: bool
):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    created_at = datetime(2026, 10, 9, 1, 15, 33)
    try:
        async with sessions() as db:
            db.add(User(username="user-1", uid="user-1", password_hash="test"))
            target = Project(
                id="a1b2c3d4-e5f6-4789-8123-456789abcdef",
                uid="user-1",
                name="demo",
                selection_status="selectable",
                workdir_path="projects/demo_a1b2c3d4",
                directory_mode="managed",
                created_at=created_at,
            )
            old_parent = Project(
                id="11111111-1111-4111-8111-111111111111",
                uid="user-1",
                name="old",
                selection_status="selectable",
                workdir_path="projects",
                directory_mode="linked",
                status="deleted",
                deleted_at=created_at - timedelta(seconds=1),
            )
            db.add_all([target, old_parent])
            await db.flush()
            conversation = Conversation(
                thread_id="old-thread",
                uid="user-1",
                project_id=old_parent.id,
                agent_id="main",
                status="deleted",
            )
            db.add(conversation)
            await db.flush()
            db.add(
                AgentRun(
                    id="old-run",
                    conversation_thread_id=conversation.thread_id,
                    conversation_id=conversation.id,
                    runtime_scope_id=conversation.thread_id,
                    agent_slug="main",
                    uid="user-1",
                    status=run_status,
                    runtime_cleanup_pending=cleanup_pending,
                    request_id="old-request",
                    run_type="chat",
                    input_payload={},
                )
            )
            await db.flush()

            assert await ProjectRepository(db).has_other_workdir_overlap(target) is expected
    finally:
        await engine.dispose()
