"""独立对话原地转项目的状态与文件不变量。"""

from uuid import uuid4
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from yuxi.services import project_service as svc
from yuxi.storage.postgres.models_business import (
    Base,
    User,
    Project,
    Conversation,
    AgentRun,
    SubagentThread,
    Message,
    AgentRunRequest,
)
from yuxi.workspace.paths import ensure_bound_user_workdir, user_workspace_dir

pytestmark = [pytest.mark.asyncio, pytest.mark.unit]


@pytest_asyncio.fixture
async def case(monkeypatch, tmp_path):
    """使用真实查询和临时文件，仅替换 SQLite 不支持的 PG 用户锁。"""
    monkeypatch.setattr("yuxi.workspace.paths.get_user_data_dir", lambda: tmp_path)
    monkeypatch.setattr(svc, "_lock_project_workdir_changes", AsyncMock())
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as db:
        project_id = str(uuid4())
        project = Project(
            id=project_id,
            uid="owner",
            selection_status="implicit",
            directory_mode="managed",
            workdir_path=f"projects/2026-10-09_12-00-00_{project_id[:8]}",
        )
        db.add(User(uid="owner", username="owner", password_hash="unused"))
        db.add(project)
        await db.flush()
        conversation = Conversation(
            thread_id="root", uid="owner", project_id=project_id, agent_id="agent", title="历史", status="active"
        )
        db.add(conversation)
        await db.commit()
        ensure_bound_user_workdir("owner", project.workdir_path)
        directory = user_workspace_dir("owner") / project.workdir_path
        (directory / "report.txt").write_bytes(b"original\x00content")
        yield db, project, conversation, directory
    await engine.dispose()


async def test_promote_keeps_ids_paths_files_and_retry_name(case):
    """成功只提升项目可见性，重试不会改名或创建新项目。"""
    db, project, conversation, directory = case
    before = (project.id, project.workdir_path, conversation.project_id, directory.stat().st_ino)
    first = await svc.promote_conversation_project_view(uid="owner", thread_id="root", name=" 新项目 ", db=db)
    second = await svc.promote_conversation_project_view(uid="owner", thread_id="root", name="重试不同名称", db=db)
    assert first["id"] == second["id"] == project.id
    assert second["name"] == "新项目" and second["selection_status"] == "selectable"
    assert before == (project.id, project.workdir_path, conversation.project_id, directory.stat().st_ino)
    assert (directory / "report.txt").read_bytes() == b"original\x00content"
    assert len((await db.scalars(select(Project))).all()) == 1


@pytest.mark.parametrize(
    "reason",
    [
        "foreign",
        "deleted",
        "project_deleted",
        "linked",
        "root_directory",
        "wrong_id",
        "shared",
        "overlap",
        "busy",
        "queued",
        "cleanup",
        "missing",
        "blank",
        "subagent",
    ],
)
async def test_rejected_promotion_leaves_original_state(case, reason):
    """每个拒绝条件保持隐藏状态和原文件字节。"""
    db, project, conversation, directory = case
    uid, name = "owner", "新项目"
    if reason == "foreign":
        uid = "other"
    elif reason == "deleted":
        conversation.status = "deleted"
    elif reason == "project_deleted":
        project.status = "deleted"
    elif reason == "linked":
        project.directory_mode = "linked"
    elif reason == "root_directory":
        project.workdir_path = "projects"
    elif reason == "wrong_id":
        project.workdir_path = "projects/example_aaaaaaaa"
        ensure_bound_user_workdir("owner", project.workdir_path)
    elif reason == "shared":
        db.add(Conversation(thread_id="another", uid="owner", project_id=project.id, agent_id="agent", status="active"))
    elif reason == "overlap":
        db.add(
            Project(
                id=str(uuid4()),
                uid="owner",
                name="旧项目",
                selection_status="selectable",
                directory_mode="linked",
                workdir_path=project.workdir_path,
            )
        )
    elif reason in {"busy", "cleanup"}:
        db.add(
            AgentRun(
                id="run",
                request_id="request",
                conversation_id=conversation.id,
                conversation_thread_id="root",
                runtime_scope_id="root",
                uid="owner",
                agent_slug="agent",
                status="running" if reason == "busy" else "completed",
                runtime_cleanup_pending=reason == "cleanup",
            )
        )
    elif reason == "queued":
        message = Message(conversation_id=conversation.id, role="user", content="pending")
        db.add(message)
        await db.flush()
        db.add(
            AgentRunRequest(
                request_id="queued",
                uid="owner",
                agent_slug="agent",
                conversation_thread_id="root",
                input_message_id=message.id,
                status="queued",
            )
        )
    elif reason == "missing":
        (directory / "report.txt").unlink()
        directory.rmdir()
    elif reason == "blank":
        name = " "
    elif reason == "subagent":
        conversation.status = "subagent"
    await db.commit()
    with pytest.raises(HTTPException) as error:
        await svc.promote_conversation_project_view(uid=uid, thread_id="root", name=name, db=db)
    expected = {
        "foreign": (404, "对话不存在"),
        "deleted": (404, "对话或项目已删除"),
        "project_deleted": (404, "对话或项目已删除"),
        "linked": (422, "旧共用或非独立目录不支持直接转为项目"),
        "root_directory": (422, "旧共用或非独立目录不支持直接转为项目"),
        "wrong_id": (422, "旧共用或非独立目录不支持直接转为项目"),
        "shared": (409, "此目录存在其他普通会话，或当前是子会话，不能直接转为项目"),
        "subagent": (409, "此目录存在其他普通会话，或当前是子会话，不能直接转为项目"),
        "overlap": (409, "目录存在共享绑定或其他任务占用，不能直接转为项目"),
        "busy": (409, "对话仍有运行、排队或清理任务，请结束后再转为项目"),
        "queued": (409, "对话仍有运行、排队或清理任务，请结束后再转为项目"),
        "cleanup": (409, "对话仍有运行、排队或清理任务，请结束后再转为项目"),
        "missing": (409, "原对话目录不存在或不可用，未转换项目"),
        "blank": (422, "项目名称不能为空"),
    }
    assert (error.value.status_code, error.value.detail) == expected[reason]
    await db.rollback()
    await db.refresh(project)
    assert project.selection_status == "implicit" and project.name is None
    if reason != "missing":
        assert (directory / "report.txt").read_bytes() == b"original\x00content"
    else:
        assert not directory.exists()


async def test_project_promotion_preserves_real_subagent_binding(case):
    """持久子会话仍属于同一项目，不能作为独立转换入口。"""
    db, project, parent, directory = case
    child = Conversation(thread_id="child", uid="owner", project_id=project.id, agent_id="agent", status="archived")
    db.add(child)
    await db.flush()
    db.add(
        SubagentThread(
            uid="owner",
            parent_conversation_id=parent.id,
            child_conversation_id=child.id,
            child_thread_id="child",
            subagent_slug="agent",
            created_by_run_id="old-run",
        )
    )
    await db.commit()
    with pytest.raises(HTTPException):
        await svc.promote_conversation_project_view(uid="owner", thread_id="child", name="错误入口", db=db)
    result = await svc.promote_conversation_project_view(uid="owner", thread_id="root", name="正确入口", db=db)
    assert result["id"] == child.project_id == parent.project_id
    assert (directory / "report.txt").read_bytes() == b"original\x00content"


async def test_promoted_conversation_keeps_original_creation_intent(case):
    """提升后的原始创建重试仍有效，其他请求和 Agent 不可借用此绑定。"""
    from yuxi.services.conversation_service import _require_matching_thread_creation_intent

    db, project, conversation, _ = case
    conversation.creation_request_id = "original-create"
    project.idempotency_key = "thread:original-create"
    await db.commit()
    await svc.promote_conversation_project_view(uid="owner", thread_id="root", name="项目", db=db)
    _require_matching_thread_creation_intent(conversation, project, agent_slug="agent", project_id=None)
    with pytest.raises(HTTPException, match="其他 Conversation 创建意图"):
        _require_matching_thread_creation_intent(conversation, project, agent_slug="different", project_id=None)
    conversation.creation_request_id = "another-create"
    with pytest.raises(HTTPException, match="其他 Conversation 创建意图"):
        _require_matching_thread_creation_intent(conversation, project, agent_slug="agent", project_id=None)
