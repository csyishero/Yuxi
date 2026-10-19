from __future__ import annotations

from typing import Any

from sqlalchemy import and_, case, delete, func, select

from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import TaskRecord
from yuxi.storage.postgres.models_knowledge import (
    EvaluationDataset,
    EvaluationDatasetItem,
    EvaluationRun,
    EvaluationRunItem,
)


class EvaluationRepository:
    @staticmethod
    async def create_dataset_in_session(session, dataset_data: dict[str, Any]) -> EvaluationDataset:
        """在 service 拥有的事务中创建数据集。"""
        dataset = EvaluationDataset(**dataset_data)
        session.add(dataset)
        await session.flush()
        return dataset

    async def create_dataset_with_items(
        self, dataset_data: dict[str, Any], items_data: list[dict[str, Any]]
    ) -> EvaluationDataset:
        async with pg_manager.get_async_session_context() as session:
            dataset = await self.create_dataset_in_session(session, dataset_data)
            await self.add_dataset_items_in_session(session, items_data)
            return dataset

    @staticmethod
    async def attach_dataset_generation_task_in_session(
        session,
        dataset_id: str,
        task_id: str,
    ) -> EvaluationDataset | None:
        """在调用方事务中关联生成 Task，且不覆盖相同 Task 的终态。"""
        record = await session.scalar(
            select(EvaluationDataset).where(EvaluationDataset.dataset_id == dataset_id).with_for_update()
        )
        if record is None:
            return None
        metadata = dict(record.build_metadata or {})
        if metadata.get("source") != "generated" or metadata.get("status") == "completed":
            return record
        if metadata.get("task_id") == task_id:
            return record
        metadata.update(
            task_id=task_id,
            status="pending",
            message="等待 worker 执行",
        )
        metadata.pop("error_message", None)
        record.build_metadata = metadata
        await session.flush()
        return record

    @staticmethod
    async def update_dataset_in_session(session, dataset_id: str, data: dict[str, Any]) -> EvaluationDataset | None:
        record = await session.scalar(
            select(EvaluationDataset).where(EvaluationDataset.dataset_id == dataset_id).with_for_update()
        )
        if record is None:
            return None
        for key, value in data.items():
            setattr(record, key, value)
        await session.flush()
        return record

    async def update_dataset(self, dataset_id: str, data: dict[str, Any]) -> EvaluationDataset | None:
        async with pg_manager.get_async_session_context() as session:
            return await self.update_dataset_in_session(session, dataset_id, data)

    async def sync_active_dataset_build_metadata(self, dataset_id: str, metadata: dict[str, Any]) -> dict | None:
        """仅在构建未终结时写入 Task 投影，避免旧快照覆盖终态。"""
        async with pg_manager.get_async_session_context() as session:
            row = await session.scalar(
                select(EvaluationDataset).where(EvaluationDataset.dataset_id == dataset_id).with_for_update()
            )
            if row is None:
                return None
            current = dict(row.build_metadata or {})
            if current.get("status") in {"completed", "failed"}:
                return current
            row.build_metadata = metadata
            await session.flush()
            return metadata

    @staticmethod
    async def add_dataset_items_in_session(session, items_data: list[dict[str, Any]]) -> None:
        session.add_all(EvaluationDatasetItem(**item) for item in items_data)
        await session.flush()

    async def get_dataset(self, dataset_id: str) -> EvaluationDataset | None:
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(select(EvaluationDataset).where(EvaluationDataset.dataset_id == dataset_id))
            return result.scalar_one_or_none()

    async def list_datasets(self, kb_id: str) -> list[EvaluationDataset]:
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(EvaluationDataset)
                .where(EvaluationDataset.kb_id == kb_id)
                .order_by(EvaluationDataset.created_at.desc())
            )
            return list(result.scalars().all())

    async def page_datasets(
        self, kb_id: str, *, limit: int, offset: int, status: str | None = None, search: str | None = None
    ) -> tuple[list[EvaluationDataset], int]:
        filters = [EvaluationDataset.kb_id == kb_id]
        if status:
            stored_status = func.coalesce(EvaluationDataset.build_metadata["status"].as_string(), "completed")
            task_status = (
                select(TaskRecord.status)
                .where(TaskRecord.id == EvaluationDataset.build_metadata["task_id"].as_string())
                .scalar_subquery()
            )
            generated_active = and_(
                EvaluationDataset.build_metadata["source"].as_string() == "generated",
                stored_status.in_(("pending", "running")),
            )
            effective_status = case(
                (and_(generated_active, task_status == "success"), "completed"),
                (and_(generated_active, task_status.in_(("failed", "cancelled"))), "failed"),
                else_=stored_status,
            )
            filters.append(effective_status == status)
        if search:
            escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            filters.append(EvaluationDataset.name.ilike(f"%{escaped}%", escape="\\"))
        async with pg_manager.get_async_session_context() as session:
            total = await session.scalar(select(func.count(EvaluationDataset.id)).where(*filters))
            result = await session.execute(
                select(EvaluationDataset)
                .where(*filters)
                .order_by(EvaluationDataset.created_at.desc(), EvaluationDataset.id.desc())
                .limit(limit)
                .offset(offset)
            )
            return list(result.scalars().all()), int(total or 0)

    async def list_dataset_items(
        self, dataset_id: str, offset: int = 0, limit: int = 100
    ) -> list[EvaluationDatasetItem]:
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(EvaluationDatasetItem)
                .where(EvaluationDatasetItem.dataset_id == dataset_id)
                .order_by(EvaluationDatasetItem.item_index.asc())
                .offset(offset)
                .limit(limit)
            )
            return list(result.scalars().all())

    async def count_dataset_items(self, dataset_id: str) -> int:
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(func.count(EvaluationDatasetItem.id)).where(EvaluationDatasetItem.dataset_id == dataset_id)
            )
            return int(result.scalar() or 0)

    async def list_all_dataset_items(self, dataset_id: str) -> list[EvaluationDatasetItem]:
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(EvaluationDatasetItem)
                .where(EvaluationDatasetItem.dataset_id == dataset_id)
                .order_by(EvaluationDatasetItem.item_index.asc())
            )
            return list(result.scalars().all())

    async def update_dataset_item_with_summary(
        self,
        dataset_id: str,
        item_id: str,
        data: dict[str, Any],
    ) -> tuple[EvaluationDatasetItem, EvaluationDataset] | None:
        """原子更新题目，并同步数据集的标注汇总状态。"""

        writable_fields = {"query_text", "gold_answer"}
        sanitized_data = {key: value for key, value in data.items() if key in writable_fields}
        async with pg_manager.get_async_session_context() as session:
            dataset = await session.scalar(
                select(EvaluationDataset).where(EvaluationDataset.dataset_id == dataset_id).with_for_update()
            )
            if dataset is None:
                return None

            item = await session.scalar(
                select(EvaluationDatasetItem)
                .where(
                    EvaluationDatasetItem.dataset_id == dataset_id,
                    EvaluationDatasetItem.item_id == item_id,
                )
                .with_for_update()
            )
            if item is None:
                return None

            for key, value in sanitized_data.items():
                setattr(item, key, value)
            await session.flush()

            result = await session.execute(
                select(
                    EvaluationDatasetItem.gold_chunk_ids,
                    EvaluationDatasetItem.gold_answer,
                ).where(EvaluationDatasetItem.dataset_id == dataset_id)
            )
            annotations = result.all()
            dataset.has_gold_chunks = any(bool(chunk_ids) for chunk_ids, _ in annotations)
            dataset.has_gold_answers = any(bool((answer or "").strip()) for _, answer in annotations)
            await session.flush()
            return item, dataset

    async def delete_dataset(self, dataset_id: str) -> None:
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(select(EvaluationDataset).where(EvaluationDataset.dataset_id == dataset_id))
            record = result.scalar_one_or_none()
            if record is not None:
                await session.delete(record)

    @staticmethod
    async def create_run_in_session(session, data: dict[str, Any]) -> EvaluationRun:
        """在 service 拥有的事务中创建评估 Run。"""
        run = EvaluationRun(**data)
        session.add(run)
        await session.flush()
        return run

    async def get_run(self, run_id: str) -> EvaluationRun | None:
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(select(EvaluationRun).where(EvaluationRun.run_id == run_id))
            return result.scalar_one_or_none()

    async def list_runs(self, kb_id: str) -> list[EvaluationRun]:
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(EvaluationRun).where(EvaluationRun.kb_id == kb_id).order_by(EvaluationRun.started_at.desc())
            )
            return list(result.scalars().all())

    async def page_runs(
        self,
        kb_id: str,
        *,
        limit: int,
        offset: int,
        dataset_id: str | None = None,
        run_id: str | None = None,
        before_id: int | None = None,
    ) -> tuple[list[EvaluationRun], int, bool]:
        filters = [EvaluationRun.kb_id == kb_id]
        if dataset_id:
            filters.append(EvaluationRun.dataset_id == dataset_id)
        if run_id:
            filters.append(EvaluationRun.run_id == run_id)
        async with pg_manager.get_async_session_context() as session:
            total = await session.scalar(select(func.count(EvaluationRun.id)).where(*filters))
            page_filters = [*filters]
            if before_id is not None:
                page_filters.append(EvaluationRun.id < before_id)
            result = await session.execute(
                select(EvaluationRun)
                .where(*page_filters)
                .order_by(EvaluationRun.id.desc())
                .limit(limit + 1)
                .offset(offset)
            )
            rows = list(result.scalars().all())
            return rows[:limit], int(total or 0), len(rows) > limit

    @staticmethod
    async def update_run_in_session(session, run_id: str, data: dict[str, Any]) -> EvaluationRun | None:
        record = await session.scalar(select(EvaluationRun).where(EvaluationRun.run_id == run_id).with_for_update())
        if record is None:
            return None
        for key, value in data.items():
            setattr(record, key, value)
        await session.flush()
        return record

    async def update_run(self, run_id: str, data: dict[str, Any]) -> EvaluationRun | None:
        async with pg_manager.get_async_session_context() as session:
            return await self.update_run_in_session(session, run_id, data)

    async def delete_run(self, run_id: str) -> None:
        async with pg_manager.get_async_session_context() as session:
            await session.execute(delete(EvaluationRunItem).where(EvaluationRunItem.run_id == run_id))
            result = await session.execute(select(EvaluationRun).where(EvaluationRun.run_id == run_id))
            record = result.scalar_one_or_none()
            if record is not None:
                await session.delete(record)

    @staticmethod
    async def upsert_run_item_in_session(
        session,
        run_id: str,
        item_index: int,
        data: dict[str, Any],
    ) -> EvaluationRunItem:
        record = await session.scalar(
            select(EvaluationRunItem)
            .where(EvaluationRunItem.run_id == run_id, EvaluationRunItem.item_index == item_index)
            .with_for_update()
        )
        if record is None:
            record = EvaluationRunItem(run_id=run_id, item_index=item_index, **data)
            session.add(record)
        else:
            for key, value in data.items():
                setattr(record, key, value)
        await session.flush()
        return record

    async def list_run_items(self, run_id: str, offset: int = 0, limit: int = 100) -> list[EvaluationRunItem]:
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(EvaluationRunItem)
                .where(EvaluationRunItem.run_id == run_id)
                .order_by(EvaluationRunItem.item_index.asc())
                .offset(offset)
                .limit(limit)
            )
            return list(result.scalars().all())

    async def count_run_items(self, run_id: str) -> int:
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(func.count(EvaluationRunItem.id)).where(EvaluationRunItem.run_id == run_id)
            )
            return int(result.scalar() or 0)

    async def delete_all(self) -> None:
        async with pg_manager.get_async_session_context() as session:
            await session.execute(delete(EvaluationRunItem))
            await session.execute(delete(EvaluationRun))
            await session.execute(delete(EvaluationDatasetItem))
            await session.execute(delete(EvaluationDataset))
