"""目录上传在真实 PostgreSQL 中生成可管理的目录记录。"""

from __future__ import annotations

import asyncio
import os
import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
import httpx
from fastapi import FastAPI
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from yuxi.knowledge.base import KnowledgeBase
from yuxi.knowledge.utils import file_delete, mindmap_utils
from server.routers import knowledge_router
from yuxi.repositories import knowledge_file_repository as repository_module
from yuxi.storage.postgres.models_knowledge import Base, KnowledgeBase as KnowledgeBaseRecord, KnowledgeFile

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


@pytest.fixture(scope="session", autouse=True)
def ensure_live_api_schema():
    """本文件使用独立 PostgreSQL schema。"""


@pytest.fixture(scope="session", autouse=True)
def cleanup_test_knowledge_resources():
    """隔离 schema 无需清理真实知识库。"""
    yield


@pytest.fixture(scope="session", autouse=True)
def cleanup_test_sandboxes():
    """本文件不创建沙盒。"""
    yield


async def test_folder_upload_creates_real_tree_and_rolls_back_conflicts(monkeypatch):
    """并发上传复用目录；同名文件冲突不会留下半条目录链。"""
    schema = f"pytest_folder_upload_{uuid.uuid4().hex[:12]}"
    admin_engine = create_async_engine(os.environ["POSTGRES_URL"])
    async with admin_engine.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_async_engine(os.environ["POSTGRES_URL"], connect_args={"server_settings": {"search_path": schema}})
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def session_context():
        async with sessions() as session:
            async with session.begin():
                yield session

    monkeypatch.setattr(
        repository_module,
        "pg_manager",
        SimpleNamespace(get_async_session_context=session_context),
    )
    executor = SimpleNamespace(_file_meta_to_record_data=KnowledgeBase._file_meta_to_record_data)

    async def add_file(name: str, source_path: str) -> dict:
        """执行真实元数据构建和目录写入。"""
        item = f"minio://knowledgebases/kb-folder/upload/{name}"
        return await KnowledgeBase.add_file_record(
            executor,
            "kb-folder",
            item,
            params={
                "content_hashes": {item: f"hash-{name}"},
                "file_sizes": {item: 4},
                "source_path": source_path,
            },
            operator_id="uploader",
            additional_params={},
        )

    try:
        async with engine.begin() as connection:
            await connection.run_sync(
                lambda conn: Base.metadata.create_all(
                    conn, tables=[KnowledgeBaseRecord.__table__, KnowledgeFile.__table__]
                )
            )
        async with session_context() as session:
            session.add(KnowledgeBaseRecord(kb_id="kb-folder", name="Files", kb_type="milvus"))

        first, second = await asyncio.gather(
            add_file("a.md", "shared/sub/a.md"),
            add_file("b.md", "shared/sub/b.md"),
        )
        assert first["filename"] == "a.md"
        assert second["filename"] == "b.md"
        assert first["parent_id"] == second["parent_id"]

        root_rows, total = await repository_module.KnowledgeFileRepository().list_documents(kb_id="kb-folder")
        assert total == 1
        assert root_rows[0].filename == "shared"
        assert root_rows[0].is_virtual_folder is False

        async with session_context() as session:
            records = list((await session.scalars(select(KnowledgeFile))).all())
            folders = [record for record in records if record.is_folder]
            assert len(folders) == 2
            root = next(record for record in folders if record.filename == "shared")
            child = next(record for record in folders if record.filename == "sub")
            assert root.parent_id is None
            assert child.parent_id == root.file_id
            assert child.file_id == first["parent_id"]
            assert all(record.created_by == "uploader" for record in folders)
            assert all(record.created_at is not None for record in folders)
            session.add(
                KnowledgeFile(
                    file_id="blocking-file",
                    kb_id="kb-folder",
                    filename="blocked",
                    parent_id=root.file_id,
                    is_folder=False,
                )
            )

        with pytest.raises(ValueError, match="invalid folder segments"):
            await add_file("c.md", "shared/new/../c.md")

        with pytest.raises(ValueError, match="文件夹名称冲突"):
            await add_file("d.md", "shared/blocked/d.md")

        with pytest.raises(ValueError, match="文件名与文件夹冲突"):
            await add_file("sub", "shared/sub")

        async with session_context() as session:
            records = list((await session.scalars(select(KnowledgeFile))).all())
            assert len(records) == 5
            assert not any(record.filename == "new" for record in records)

        async with session_context() as session:
            session.add(
                KnowledgeFile(
                    file_id="legacy-file",
                    kb_id="kb-folder",
                    filename="legacy/old.md",
                    is_folder=False,
                )
            )
        with pytest.raises(ValueError, match="旧文件夹正在后台整理"):
            await add_file("new.md", "legacy/new.md")

        async with session_context() as session:
            session.add(
                KnowledgeFile(
                    file_id="partly-migrated-file",
                    kb_id="kb-folder",
                    filename="shared/old.md",
                    is_folder=False,
                )
            )
        with pytest.raises(ValueError, match="旧文件夹正在后台整理"):
            await add_file("new.md", "shared/new.md")

        async def allow_documents(_kb_id: str, _operation: str) -> None:
            """本测试只隔离权限和知识库类型查询。"""

        async def add_from_http(kb_id: str, item: str, *, params: dict, operator_id: str) -> dict:
            """HTTP 入口调用同一个真实目录持久化用例。"""
            return await KnowledgeBase.add_file_record(
                executor,
                kb_id,
                item,
                params=params,
                operator_id=operator_id,
                additional_params={},
            )

        monkeypatch.setattr(knowledge_router, "_ensure_database_supports_documents", allow_documents)
        monkeypatch.setattr(knowledge_router.knowledge_base, "add_file_record", add_from_http)
        app = FastAPI()
        app.include_router(knowledge_router.knowledge, prefix="/api")
        app.dependency_overrides[knowledge_router.require_knowledge_base_upload] = lambda: SimpleNamespace(
            uid="uploader"
        )
        http_item = "minio://knowledgebases/kb-folder/upload/http.md"
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/api/knowledge/databases/kb-folder/documents/add",
                json={
                    "items": [http_item],
                    "params": {
                        "content_hashes": {http_item: "hash-http"},
                        "file_sizes": {http_item: 4},
                        "source_paths": {http_item: "http/deep/http.md"},
                    },
                },
            )
            assert response.status_code == 200, response.text
            assert response.json()["status"] == "success"
            file_id = response.json()["items"][0]["file_id"]

        async with session_context() as session:
            file_record = await session.scalar(select(KnowledgeFile).where(KnowledgeFile.file_id == file_id))
            assert file_record.filename == "http.md"
            parent = await session.scalar(select(KnowledgeFile).where(KnowledgeFile.file_id == file_record.parent_id))
            assert parent.filename == "deep"
            assert parent.is_folder is True

            session.add_all(
                [
                    KnowledgeFile(file_id="trash-one", kb_id="kb-folder", filename="trash/a.md", is_folder=False),
                    KnowledgeFile(file_id="trash-two", kb_id="kb-folder", filename="trash/sub/b.md", is_folder=False),
                    KnowledgeFile(
                        file_id="trash-other", kb_id="kb-folder", filename="trash-other/c.md", is_folder=False
                    ),
                    KnowledgeFile(
                        file_id="trash-nested-parent",
                        kb_id="kb-folder",
                        filename="nested",
                        is_folder=True,
                    ),
                    KnowledgeFile(
                        file_id="trash-nested-file",
                        kb_id="kb-folder",
                        parent_id="trash-nested-parent",
                        filename="trash/c.md",
                        is_folder=False,
                    ),
                ]
            )

        async def delete_record(_kb_id: str, target_file_id: str) -> None:
            """用真实 repository 删除模拟 executor 已完成外部资源清理。"""
            await repository_module.KnowledgeFileRepository().delete(target_file_id)

        async def load_record(_kb_id: str, target_file_id: str) -> dict:
            """从隔离 schema 读取删除前元数据。"""
            async with session_context() as session:
                record = await session.scalar(select(KnowledgeFile).where(KnowledgeFile.file_id == target_file_id))
                return {"path": record.path or "", "filename": record.filename}

        deleted_objects: list[str] = []
        removed_mindmap: list[str] = []
        lock_entered = asyncio.Event()
        lock_release = asyncio.Event()

        async def delete_objects(_kb_id: str, target_file_id: str, _file_path: str) -> None:
            """记录真实删除用例调用的对象清理边界。"""
            deleted_objects.append(target_file_id)
            if target_file_id == "locktest-file":
                lock_entered.set()
                await lock_release.wait()

        async def remove_mindmap(_kb_id: str, target_file_id: str, _filename: str) -> None:
            """记录真实删除用例调用的导图清理边界。"""
            removed_mindmap.append(target_file_id)

        executor.delete_file = delete_record
        executor._load_file_meta = load_record
        monkeypatch.setattr(file_delete, "delete_document_storage_objects", delete_objects)
        monkeypatch.setattr(mindmap_utils, "remove_file_from_mindmap", remove_mindmap)

        async def delete_from_http(kb_id: str, parent_id: str | None, path_prefix: str) -> int:
            """HTTP 入口调用真实虚拟目录范围选择与删除。"""
            return await KnowledgeBase.delete_virtual_folder(executor, kb_id, parent_id, path_prefix)

        monkeypatch.setattr(knowledge_router.knowledge_base, "delete_virtual_folder", delete_from_http)
        app.dependency_overrides[knowledge_router.require_knowledge_base_delete_document] = lambda: SimpleNamespace(
            uid="uploader"
        )
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            invalid = await client.request(
                "DELETE",
                "/api/knowledge/databases/kb-folder/virtual-folders",
                json={"parent_id": None, "path_prefix": "../trash/"},
            )
            assert invalid.status_code == 400
            deleted = await client.request(
                "DELETE",
                "/api/knowledge/databases/kb-folder/virtual-folders",
                json={"parent_id": None, "path_prefix": "trash/"},
            )
            assert deleted.status_code == 200, deleted.text
            assert deleted.json()["deleted_count"] == 2
            assert set(deleted_objects) == {"trash-one", "trash-two"}
            assert set(removed_mindmap) == {"trash-one", "trash-two"}

        async with session_context() as session:
            remaining = set((await session.scalars(select(KnowledgeFile.file_id))).all())
            assert "trash-one" not in remaining
            assert "trash-two" not in remaining
            assert {"trash-other", "trash-nested-file"} <= remaining

            session.add_all(
                [
                    KnowledgeFile(
                        file_id="unknown-type",
                        kb_id="kb-folder",
                        filename="uncertain/unknown.md",
                        is_folder=None,
                    ),
                    KnowledgeFile(
                        file_id="known-type",
                        kb_id="kb-folder",
                        filename="uncertain/known.md",
                        is_folder=False,
                    ),
                ]
            )
            await session.flush()
            await session.execute(text("UPDATE knowledge_files SET is_folder = NULL WHERE file_id = 'unknown-type'"))

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            uncertain = await client.request(
                "DELETE",
                "/api/knowledge/databases/kb-folder/virtual-folders",
                json={"parent_id": None, "path_prefix": "uncertain/"},
            )
            assert uncertain.status_code == 400

        async with session_context() as session:
            remaining = set((await session.scalars(select(KnowledgeFile.file_id))).all())
            assert {"unknown-type", "known-type"} <= remaining

            session.add(
                KnowledgeFile(
                    file_id="locktest-file",
                    kb_id="kb-folder",
                    filename="locktest/file.md",
                    is_folder=False,
                )
            )

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            pending_delete = asyncio.create_task(
                client.request(
                    "DELETE",
                    "/api/knowledge/databases/kb-folder/virtual-folders",
                    json={"parent_id": None, "path_prefix": "locktest/"},
                )
            )
            try:
                await asyncio.wait_for(lock_entered.wait(), timeout=5)
                async with session_context() as session:
                    can_take_migration_lock = await session.scalar(
                        select(func.pg_try_advisory_xact_lock(func.hashtext("kb-folder")))
                    )
                    assert can_take_migration_lock is False
            finally:
                lock_release.set()
            locked_delete = await pending_delete
            assert locked_delete.status_code == 200, locked_delete.text
    finally:
        await engine.dispose()
        async with admin_engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin_engine.dispose()
