"""会话迁入已有项目时的归属、运行状态与文件不变量。"""

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from yuxi.repositories.conversation_repository import ConversationRepository
from yuxi.repositories.project_repository import ProjectRepository
from yuxi.services import conversation_service, project_service
from yuxi.storage.postgres.models_business import (
    AgentRun,
    AgentRunRequest,
    Base,
    Conversation,
    Message,
    Project,
    SubagentThread,
    User,
)
from yuxi.workspace.paths import ensure_bound_user_workdir, user_workdir_host_dir

pytestmark = [pytest.mark.asyncio, pytest.mark.unit]


@pytest_asyncio.fixture
async def case(monkeypatch, tmp_path):
    """创建两个真实文件目录和带持久子会话的项目。"""
    monkeypatch.setattr("yuxi.workspace.paths.get_user_data_dir", lambda: tmp_path)
    monkeypatch.setattr(project_service, "_lock_project_workdir_changes", AsyncMock())
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as db:
            source_id, target_id = str(uuid4()), str(uuid4())
            source = Project(
                id=source_id,
                uid="owner",
                selection_status="implicit",
                directory_mode="managed",
                workdir_path=f"projects/{source_id}",
                idempotency_key="thread:create-root",
            )
            target = Project(
                id=target_id,
                uid="owner",
                name="目标",
                selection_status="selectable",
                directory_mode="managed",
                workdir_path=f"projects/{target_id}",
            )
            db.add_all([User(uid="owner", username="owner", password_hash="unused"), source, target])
            await db.flush()
            root = Conversation(
                thread_id="root",
                uid="owner",
                project_id=source_id,
                agent_id="agent",
                title="历史会话",
                status="active",
                creation_request_id="create-root",
            )
            child = Conversation(
                thread_id="child", uid="owner", project_id=source_id, agent_id="subagent", status="archived"
            )
            db.add_all([root, child])
            await db.flush()
            db.add_all(
                [
                    SubagentThread(
                        uid="owner",
                        parent_conversation_id=root.id,
                        child_conversation_id=child.id,
                        child_thread_id="child",
                        subagent_slug="subagent",
                        created_by_run_id="historic-run",
                    ),
                    Message(conversation_id=root.id, role="user", content="原有消息"),
                ]
            )
            await db.commit()
            ensure_bound_user_workdir("owner", source.workdir_path)
            ensure_bound_user_workdir("owner", target.workdir_path)
            source_dir = user_workdir_host_dir("owner", source.workdir_path)
            target_dir = user_workdir_host_dir("owner", target.workdir_path)
            (source_dir / "old.txt").write_bytes(b"original bytes")
            yield db, source, target, root, child, source_dir, target_dir
    finally:
        await engine.dispose()


async def test_moves_root_and_child_preserving_history_and_selected_file(case):
    """迁移只改变当前归属并复制所选字节；历史和源目录保留。"""
    db, source, target, root, child, source_dir, target_dir = case
    result = await conversation_service.move_conversation_project_view(
        thread_id="root",
        request_id="move-selected-file",
        target_project_id=target.id,
        files=[{"source_path": "/old.txt", "destination_path": "/renamed.txt"}],
        current_uid="owner",
        db=db,
    )

    assert result["thread"]["id"] == "root"
    assert result["thread"]["project_id"] == target.id
    assert result["thread"]["workdir_path"] == target.workdir_path
    assert set(result["moved_thread_ids"]) == {"root", "child"}
    assert result["imported_files"] == [
        {"source_path": "/old.txt", "destination_path": "/renamed.txt", "size": len(b"original bytes")}
    ]
    await db.refresh(root)
    await db.refresh(child)
    assert root.project_id == child.project_id == target.id
    assert root.extra_metadata["project_moves"] == child.extra_metadata["project_moves"]
    assert root.extra_metadata["project_moves"][0]["source_workdir_path"] == source.workdir_path
    assert (await db.scalars(select(Message.content).where(Message.conversation_id == root.id))).all() == ["原有消息"]
    assert (source_dir / "old.txt").read_bytes() == (target_dir / "renamed.txt").read_bytes()
    assert await ProjectRepository(db).has_historical_workdir_references(source)
    with pytest.raises(HTTPException, match="仍有已迁出会话引用此目录"):
        await project_service._validate_managed_directory_deletion(project=source, repository=ProjectRepository(db))

    conversation_service._require_matching_thread_creation_intent(root, target, agent_slug="agent", project_id=None)
    with pytest.raises(HTTPException, match="其他 Conversation 创建意图"):
        conversation_service._require_matching_thread_creation_intent(
            root, target, agent_slug="agent", project_id=target.id
        )


async def test_move_request_replay_returns_committed_result_without_copying_again(case):
    """提交成功但 HTTP 响应丢失后，相同请求可返回已提交的迁移事实。"""
    db, _source, target, root, _child, _source_dir, target_dir = case
    files = [{"source_path": "/old.txt", "destination_path": "/copy.txt"}]
    first = await conversation_service.move_conversation_project_view(
        thread_id="root", target_project_id=target.id, files=files, current_uid="owner", db=db, request_id="move-1"
    )
    replay = await conversation_service.move_conversation_project_view(
        thread_id="root", target_project_id=target.id, files=files, current_uid="owner", db=db, request_id="move-1"
    )

    assert replay["thread"]["project_id"] == target.id
    assert replay["imported_files"] == first["imported_files"]
    assert replay["moved_thread_ids"] == first["moved_thread_ids"]
    assert (target_dir / "copy.txt").read_bytes() == b"original bytes"
    await db.refresh(root)
    assert len(root.extra_metadata["project_moves"]) == 1

    with pytest.raises(HTTPException, match="request_id 已用于其他会话迁移意图"):
        await conversation_service.move_conversation_project_view(
            thread_id="root", target_project_id=target.id, files=[], current_uid="owner", db=db, request_id="move-1"
        )


@pytest.mark.parametrize("activity", ["running", "queued", "cleanup", "interrupted"])
async def test_unsettled_work_blocks_move_without_import(case, activity):
    """会话树任一未结束工作都阻止改绑和文件导入。"""
    db, source, target, root, child, _source_dir, target_dir = case
    source_id = source.id
    if activity == "queued":
        message = Message(conversation_id=child.id, role="user", content="pending")
        db.add(message)
        await db.flush()
        db.add(
            AgentRunRequest(
                request_id="queued",
                uid="owner",
                agent_slug="subagent",
                conversation_thread_id="child",
                input_message_id=message.id,
                status="queued",
            )
        )
    else:
        db.add(
            AgentRun(
                id="run",
                request_id="run-request",
                conversation_id=child.id,
                conversation_thread_id="child",
                runtime_scope_id="child",
                uid="owner",
                agent_slug="subagent",
                run_type="chat",
                status="completed" if activity == "cleanup" else activity,
                runtime_cleanup_pending=activity == "cleanup",
            )
        )
    await db.commit()
    with pytest.raises(HTTPException) as error:
        await conversation_service.move_conversation_project_view(
            thread_id="root",
            request_id=f"move-blocked-{activity}",
            target_project_id=target.id,
            files=[{"source_path": "/old.txt", "destination_path": "/old.txt"}],
            current_uid="owner",
            db=db,
        )
    assert error.value.status_code == 409
    await db.rollback()
    await db.refresh(root)
    await db.refresh(child)
    assert root.project_id == child.project_id == source_id
    assert not (target_dir / "old.txt").exists()


async def test_destination_conflict_and_foreign_target_do_not_move(case):
    """同名文件和他人目标项目均在产生副作用前拒绝。"""
    db, source, target, root, _child, _source_dir, target_dir = case
    source_id = source.id
    (target_dir / "old.txt").write_bytes(b"keep")
    with pytest.raises(HTTPException) as error:
        await conversation_service.move_conversation_project_view(
            thread_id="root",
            request_id="move-conflict",
            target_project_id=target.id,
            files=[{"source_path": "/old.txt", "destination_path": "/old.txt"}],
            current_uid="owner",
            db=db,
        )
    assert error.value.status_code == 409
    assert error.value.detail["code"] == "file_conflicts"
    assert (target_dir / "old.txt").read_bytes() == b"keep"
    await db.rollback()

    with pytest.raises(HTTPException) as error:
        await conversation_service.move_conversation_project_view(
            thread_id="root",
            request_id="move-foreign",
            target_project_id="other-user-project",
            files=[],
            current_uid="owner",
            db=db,
        )
    assert error.value.status_code == 404
    await db.refresh(root)
    assert root.project_id == source_id


async def test_database_failure_removes_only_newly_imported_file(case, monkeypatch):
    """数据库提交失败时撤销本次导入，原文件不变。"""
    db, source, target, root, _child, source_dir, target_dir = case
    source_id = source.id
    monkeypatch.setattr(db, "commit", AsyncMock(side_effect=RuntimeError("commit failed")))
    with pytest.raises(RuntimeError, match="commit failed"):
        await conversation_service.move_conversation_project_view(
            thread_id="root",
            request_id="move-db-failure",
            target_project_id=target.id,
            files=[{"source_path": "/old.txt", "destination_path": "/copy.txt"}],
            current_uid="owner",
            db=db,
        )
    assert not (target_dir / "copy.txt").exists()
    assert (source_dir / "old.txt").read_bytes() == b"original bytes"
    await db.refresh(root)
    assert root.project_id == source_id


async def test_lost_commit_response_does_not_delete_committed_file(case, monkeypatch):
    """提交已成功但调用方收到错误时，以持久 move_id 判断文件归属。"""
    db, _source, target, root, _child, _source_dir, target_dir = case
    commit = db.commit

    async def committed_then_lost_response():
        await commit()
        raise RuntimeError("commit response lost")

    monkeypatch.setattr(db, "commit", committed_then_lost_response)
    with pytest.raises(RuntimeError, match="commit response lost"):
        await conversation_service.move_conversation_project_view(
            thread_id="root",
            request_id="move-lost-commit-response",
            target_project_id=target.id,
            files=[{"source_path": "/old.txt", "destination_path": "/copy.txt"}],
            current_uid="owner",
            db=db,
        )
    await db.refresh(root)
    assert root.project_id == target.id
    assert (target_dir / "copy.txt").read_bytes() == b"original bytes"


async def test_new_conversation_cannot_supply_project_move_history(case):
    """仓储边界不接受由调用方伪造的历史目录授权记录。"""
    db, source, _target, _root, _child, _source_dir, _target_dir = case
    conversation = await ConversationRepository(db).add_conversation(
        uid="owner",
        agent_id="agent",
        thread_id="forged-history",
        project_id=source.id,
        metadata={"project_moves": [{"source_project_id": "forged"}], "safe": "value"},
    )
    assert conversation.extra_metadata == {"safe": "value", "attachments": []}
