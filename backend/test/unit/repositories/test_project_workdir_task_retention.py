"""项目目录清理状态必须长期保留，供用户晚些时候查询和重试。"""

from contextlib import asynccontextmanager
from datetime import datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from yuxi.repositories.task_repository import TaskRepository
from yuxi.storage.postgres.models_business import TaskRecord

pytestmark = [pytest.mark.asyncio, pytest.mark.unit]


async def test_project_workdir_tasks_survive_generic_pruning_and_manual_deletion(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(TaskRecord.metadata.create_all, tables=[TaskRecord.__table__])
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def session_context():
        async with sessions() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    monkeypatch.setattr("yuxi.repositories.task_repository.pg_manager.get_async_session_context", session_context)
    try:
        created_at = datetime(2026, 10, 8, 12, 0, 0)
        async with sessions() as session:
            session.add_all(
                [
                    TaskRecord(
                        id="project-delete-task",
                        name="Delete project",
                        type="project_workdir_delete",
                        status="failed",
                        payload={"uid": "user-1", "project_id": "project-1"},
                        created_at=created_at,
                        updated_at=created_at,
                    ),
                    TaskRecord(
                        id="project-success-task",
                        name="Retry project",
                        type="project_workdir_delete",
                        status="success",
                        payload={"uid": "user-1", "project_id": "project-1"},
                        created_at=created_at + timedelta(seconds=1),
                        updated_at=created_at + timedelta(seconds=1),
                    ),
                    TaskRecord(
                        id="another-project-delete-task",
                        name="Delete another project",
                        type="project_workdir_delete",
                        status="failed",
                        payload={"uid": "user-1", "project_id": "project-3"},
                    ),
                    TaskRecord(
                        id="other-user-delete-task",
                        name="Other project",
                        type="project_workdir_delete",
                        status="failed",
                        payload={"uid": "user-2", "project_id": "project-2"},
                    ),
                    TaskRecord(id="ordinary-task", name="Ordinary", type="knowledge_parse", status="success"),
                ]
            )
            await session.commit()

        repository = TaskRepository()
        assert await repository.prune_terminal(keep=0) == ["ordinary-task"]
        assert await repository.delete_terminal("project-delete-task") is False
        assert (await repository.get_by_id("project-delete-task")).status == "failed"
        assert [task.id for task in await repository.list_project_workdir_deletions_for_user("user-1")] == [
            "another-project-delete-task"
        ]
    finally:
        await engine.dispose()
