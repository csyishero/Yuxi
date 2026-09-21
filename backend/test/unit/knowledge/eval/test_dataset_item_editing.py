from types import SimpleNamespace

import pytest

from yuxi.knowledge.eval.service import EvaluationService


class FakeChunkRepository:
    async def list_by_chunk_ids(self, chunk_ids):
        chunks = {
            "chunk-1": SimpleNamespace(
                chunk_id="chunk-1",
                kb_id="kb-1",
                file_id="file-1",
                chunk_index=2,
                start_char_pos=10,
                end_char_pos=80,
                content="  第一段内容\n包含   多余空白。  ",
            ),
            "foreign-chunk": SimpleNamespace(
                chunk_id="foreign-chunk",
                kb_id="kb-other",
                file_id="file-other",
                chunk_index=0,
                start_char_pos=0,
                end_char_pos=10,
                content="不能泄露",
            ),
        }
        return [chunks[chunk_id] for chunk_id in chunk_ids if chunk_id in chunks]


class FakeFileRepository:
    async def list_by_file_ids(self, file_ids):
        if "file-1" not in file_ids:
            return []
        return [
            SimpleNamespace(
                file_id="file-1",
                kb_id="kb-1",
                filename="真实文件.md",
                original_filename="原始文件.md",
            )
        ]


@pytest.mark.asyncio
async def test_dataset_items_resolve_gold_chunks_to_real_files_and_keep_missing_ids():
    service = EvaluationService.__new__(EvaluationService)
    service.chunk_repo = FakeChunkRepository()
    service.file_repo = FakeFileRepository()
    item = SimpleNamespace(
        item_id="item-1",
        item_index=0,
        query_text="问题",
        gold_chunk_ids=["chunk-1", "missing-chunk", "foreign-chunk"],
        gold_answer="答案",
    )

    result = await service._dataset_items_to_dicts([item], "kb-1")

    assert result[0]["gold_chunk_ids"] == ["chunk-1", "missing-chunk", "foreign-chunk"]
    assert result[0]["gold_chunks"][0] == {
        "chunk_id": "chunk-1",
        "exists": True,
        "file_id": "file-1",
        "file_exists": True,
        "filename": "真实文件.md",
        "chunk_index": 2,
        "start_char_pos": 10,
        "end_char_pos": 80,
        "content_preview": "第一段内容 包含 多余空白。",
    }
    assert result[0]["gold_chunks"][1] == {
        "chunk_id": "missing-chunk",
        "exists": False,
        "file_exists": False,
    }
    assert result[0]["gold_chunks"][2]["exists"] is False


class FakeEvaluationRepository:
    def __init__(self):
        self.dataset = SimpleNamespace(
            dataset_id="dataset-1",
            kb_id="kb-1",
            name="基准",
            description="",
            item_count=1,
            has_gold_chunks=False,
            has_gold_answers=False,
            build_metadata={"status": "completed"},
            created_by="admin",
            created_at=None,
            updated_at=None,
        )
        self.item = SimpleNamespace(
            item_id="item-1",
            item_index=0,
            query_text="旧问题",
            gold_chunk_ids=[],
            gold_answer=None,
        )

    async def get_dataset(self, _dataset_id):
        return self.dataset

    async def update_dataset_item_with_summary(self, dataset_id, item_id, data):
        assert (dataset_id, item_id) == ("dataset-1", "item-1")
        self.item.query_text = data["query_text"]
        self.item.gold_answer = data["gold_answer"]
        self.dataset.has_gold_answers = bool(data["gold_answer"])
        return self.item, self.dataset


@pytest.mark.asyncio
async def test_update_dataset_item_normalizes_question_and_answer():
    service = EvaluationService.__new__(EvaluationService)
    service.eval_repo = FakeEvaluationRepository()
    service.chunk_repo = FakeChunkRepository()
    service.file_repo = FakeFileRepository()
    service._sync_dataset_build_metadata = lambda _row: _noop()

    result = await service.update_dataset_item(
        "dataset-1",
        "item-1",
        query="  新问题  ",
        gold_answer="  新答案  ",
    )

    assert result["item"]["query"] == "新问题"
    assert result["item"]["gold_answer"] == "新答案"
    assert result["dataset"]["has_gold_answers"] is True


async def _noop():
    return None
