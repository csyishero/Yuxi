"""Bounded evaluation list queries keep totals and filters on the server."""

from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from yuxi.knowledge.eval.service import EvaluationService
from yuxi.repositories.evaluation_repository import EvaluationRepository
from yuxi.repositories import evaluation_repository as repository_module
from yuxi.storage.postgres.models_knowledge import Base, EvaluationDataset, EvaluationRun, KnowledgeBase
from yuxi.storage.postgres.models_business import TaskRecord


@pytest.mark.asyncio
async def test_dataset_page_syncs_only_returned_rows():
    class Repo:
        async def page_datasets(self, kb_id, *, limit, offset, status, search):
            assert (kb_id, limit, offset, status, search) == ("kb-1", 10, 20, "completed", "report")
            return [SimpleNamespace(dataset_id="dataset-21")], 47

    service = EvaluationService()
    service.eval_repo = Repo()
    synced = []

    async def sync(row):
        synced.append(row.dataset_id)

    service._sync_dataset_build_metadata = sync
    service._dataset_to_dict = lambda row: {"dataset_id": row.dataset_id}

    page = await service.list_datasets_page("kb-1", limit=10, offset=20, status="completed", search="report")
    assert page == {
        "items": [{"dataset_id": "dataset-21"}],
        "total": 47,
        "limit": 10,
        "offset": 20,
        "has_more": True,
    }
    assert synced == ["dataset-21"]


@pytest.mark.asyncio
async def test_unbounded_dataset_list_applies_filters_before_offset():
    class Repo:
        async def list_datasets(self, kb_id):
            assert kb_id == "kb-1"
            return [
                SimpleNamespace(dataset_id="one", name="report one", build_metadata={"status": "completed"}),
                SimpleNamespace(dataset_id="two", name="report two", build_metadata={"status": "completed"}),
                SimpleNamespace(dataset_id="three", name="report three", build_metadata={"status": "failed"}),
            ]

    service = EvaluationService()
    service.eval_repo = Repo()

    async def sync(_row):
        return None

    service._sync_dataset_build_metadata = sync
    service._dataset_to_dict = lambda row: {"dataset_id": row.dataset_id}

    rows = await service.list_datasets("kb-1", offset=1, status="completed", search="REPORT")
    assert rows == [{"dataset_id": "two"}]


@pytest.mark.asyncio
async def test_unbounded_run_list_applies_filters_and_cursor_before_offset():
    class Repo:
        async def list_runs(self, kb_id):
            assert kb_id == "kb-1"
            return [
                SimpleNamespace(id=8, run_id="run-8", dataset_id="dataset-1"),
                SimpleNamespace(id=7, run_id="run-7", dataset_id="dataset-1"),
                SimpleNamespace(id=6, run_id="run-6", dataset_id="dataset-1"),
                SimpleNamespace(id=5, run_id="run-5", dataset_id="dataset-2"),
            ]

    service = EvaluationService()
    service.eval_repo = Repo()

    async def serialize(rows):
        return [{"run_id": row.run_id, "id": row.id} for row in rows]

    service._serialize_run_rows = serialize

    rows = await service.list_runs("kb-1", offset=1, dataset_id="dataset-1", before_id=8)
    assert rows == [{"run_id": "run-6", "id": 6}]

    exact = await service.list_runs("kb-1", run_id="run-7")
    assert exact == [{"run_id": "run-7", "id": 7}]


@pytest.mark.asyncio
async def test_run_page_only_requests_one_filtered_slice():
    class Repo:
        async def page_runs(self, kb_id, *, limit, offset, dataset_id, run_id, before_id):
            assert (kb_id, limit, offset, dataset_id, run_id, before_id) == ("kb-1", 8, 8, "dataset-1", None, None)
            return (
                [
                    SimpleNamespace(
                        id=9,
                        run_id="run-9",
                        name="run 9",
                        dataset_id="dataset-1",
                        status="completed",
                        started_at=None,
                        completed_at=None,
                        total_items=1,
                        completed_items=1,
                        overall_score=1.0,
                        retrieval_config={},
                        metrics={},
                    )
                ],
                23,
                True,
            )

    service = EvaluationService()
    service.eval_repo = Repo()
    page = await service.list_runs_page("kb-1", limit=8, offset=8, dataset_id="dataset-1")
    assert page["total"] == 23
    assert page["has_more"] is True
    assert page["next_cursor"] == 9
    assert [run["run_id"] for run in page["items"]] == ["run-9"]


@pytest.mark.asyncio
async def test_evaluation_repository_filters_before_count_and_page(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync_connection: Base.metadata.create_all(
                sync_connection,
                tables=[
                    KnowledgeBase.__table__,
                    EvaluationDataset.__table__,
                    EvaluationRun.__table__,
                    TaskRecord.__table__,
                ],
            )
        )
    factory = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def session_context():
        async with factory() as session:
            yield session

    monkeypatch.setattr(repository_module.pg_manager, "get_async_session_context", session_context)
    now = datetime(2026, 1, 1)
    async with factory() as session:
        session.add_all(
            [
                KnowledgeBase(kb_id="kb-1", name="One", kb_type="milvus"),
                KnowledgeBase(kb_id="kb-2", name="Two", kb_type="milvus"),
                EvaluationDataset(
                    dataset_id="report-1",
                    kb_id="kb-1",
                    name="report one",
                    build_metadata={"status": "completed"},
                    created_at=now,
                ),
                EvaluationDataset(
                    dataset_id="report-2",
                    kb_id="kb-1",
                    name="report two",
                    build_metadata={"status": "completed"},
                    created_at=now + timedelta(days=1),
                ),
                EvaluationDataset(
                    dataset_id="running",
                    kb_id="kb-1",
                    name="report draft",
                    build_metadata={"status": "running"},
                    created_at=now + timedelta(days=2),
                ),
                EvaluationDataset(
                    dataset_id="other-kb",
                    kb_id="kb-2",
                    name="report hidden",
                    build_metadata={"status": "completed"},
                    created_at=now + timedelta(days=3),
                ),
                EvaluationDataset(
                    dataset_id="stale-completed",
                    kb_id="kb-1",
                    name="stale report",
                    build_metadata={"source": "generated", "status": "running", "task_id": "done-task"},
                    created_at=now + timedelta(days=4),
                ),
                TaskRecord(id="done-task", name="Generate", type="dataset_generation", status="success"),
                EvaluationRun(run_id="run-old", kb_id="kb-1", dataset_id="report-1", name="Old", started_at=now),
                EvaluationRun(
                    run_id="run-new",
                    kb_id="kb-1",
                    dataset_id="report-1",
                    name="New",
                    started_at=now + timedelta(days=1),
                ),
                EvaluationRun(run_id="other-run", kb_id="kb-2", dataset_id="other-kb", name="Other"),
            ]
        )
        await session.commit()

    repo = EvaluationRepository()
    rows, total = await repo.page_datasets("kb-1", limit=1, offset=1, status="completed", search="report")
    assert total == 3
    assert [row.dataset_id for row in rows] == ["report-2"]
    recovered, recovered_total = await repo.page_datasets(
        "kb-1", limit=10, offset=0, status="completed", search="stale"
    )
    assert recovered_total == 1
    assert [row.dataset_id for row in recovered] == ["stale-completed"]
    runs, run_total, has_more = await repo.page_runs("kb-1", limit=1, offset=1, dataset_id="report-1")
    assert run_total == 2
    assert has_more is False
    assert [run.run_id for run in runs] == ["run-old"]
    exact, exact_total, _ = await repo.page_runs("kb-1", limit=1, offset=0, run_id="run-old")
    assert exact_total == 1
    assert [run.run_id for run in exact] == ["run-old"]

    first, _, _ = await repo.page_runs("kb-1", limit=1, offset=0, dataset_id="report-1")
    async with factory() as session:
        await session.delete(await session.get(EvaluationRun, first[0].id))
        await session.commit()
    older, _, _ = await repo.page_runs("kb-1", limit=1, offset=0, dataset_id="report-1", before_id=first[0].id)
    assert [run.run_id for run in older] == ["run-old"]

    class RunningTaskRepo:
        async def get_by_id(self, _task_id):
            return SimpleNamespace(status="running", progress=50, message="working")

    service = EvaluationService()
    service.eval_repo = repo
    service.task_repo = RunningTaskRepo()
    stale_row = SimpleNamespace(
        dataset_id="report-1",
        build_metadata={"source": "generated", "status": "running", "task_id": "older-task"},
    )
    await service._sync_dataset_build_metadata(stale_row)
    persisted = await repo.get_dataset("report-1")
    assert persisted.build_metadata["status"] == "completed"
    assert stale_row.build_metadata["status"] == "completed"
    await engine.dispose()
