"""任务中心分页和状态筛选的持久查询回归测试。"""

from contextlib import asynccontextmanager
from datetime import datetime

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from yuxi.repositories import task_repository as task_repository_module
from yuxi.services.task_service import Tasker
from yuxi.storage.postgres.models_business import TaskRecord


@pytest.mark.asyncio
async def test_task_pages_filter_before_offset_and_break_timestamp_ties(monkeypatch) -> None:
    """同一时间的任务也可稳定翻页，状态组总数不被页大小截断。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    now = datetime(2026, 1, 1)
    rows = [(f"success-{index:03d}", "success") for index in range(25)]
    rows += [("active-pending", "pending"), ("active-running", "running")]
    rows += [("failed-one", "failed"), ("failed-cancelled", "cancelled")]
    async with engine.begin() as conn:
        await conn.run_sync(TaskRecord.__table__.create)
        await conn.execute(
            TaskRecord.__table__.insert(),
            [
                {
                    "id": task_id,
                    "name": task_id,
                    "type": "demo",
                    "status": status,
                    "progress": 100.0,
                    "message": "done",
                    "payload": {},
                    "created_at": now,
                    "updated_at": now,
                }
                for task_id, status in rows
            ],
        )

    factory = async_sessionmaker(engine, expire_on_commit=False)

    class SessionManager:
        @asynccontextmanager
        async def get_async_session_context(self):
            async with factory() as session:
                yield session

    monkeypatch.setattr(task_repository_module, "pg_manager", SessionManager())
    try:
        tasker = Tasker()
        first = await tasker.list_tasks(status_group="success", offset=0, limit=20)
        second = await tasker.list_tasks(status_group="success", offset=20, limit=20)
        active = await tasker.list_tasks(status_group="active", offset=0, limit=20)
        failed = await tasker.list_tasks(status_group="failed", offset=0, limit=20)
        exact_failed = await tasker.list_tasks(status="failed", offset=0, limit=20)

        assert [row["id"] for row in first["tasks"]] == [f"success-{index:03d}" for index in range(24, 4, -1)]
        assert [row["id"] for row in second["tasks"]] == [f"success-{index:03d}" for index in range(4, -1, -1)]
        assert first["summary"]["total"] == 29
        assert first["summary"]["filtered_total"] == 25
        assert second["summary"]["filtered_total"] == 25
        assert {row["id"] for row in active["tasks"]} == {"active-pending", "active-running"}
        assert active["summary"]["filtered_total"] == 2
        assert {row["id"] for row in failed["tasks"]} == {"failed-one", "failed-cancelled"}
        assert failed["summary"]["filtered_total"] == 2
        assert [row["id"] for row in exact_failed["tasks"]] == ["failed-one"]
    finally:
        await engine.dispose()
