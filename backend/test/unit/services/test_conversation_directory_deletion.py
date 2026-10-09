"""无项目会话删除的默认行为和目录所有权保护。"""

from uuid import uuid4
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from yuxi.services import conversation_service, project_service
from yuxi.storage.postgres.models_business import AgentRun, Base, Conversation, Project, TaskRecord, User
from yuxi.workspace.paths import ensure_bound_user_workdir, user_workdir_host_dir

pytestmark = [pytest.mark.asyncio, pytest.mark.unit]


@pytest_asyncio.fixture()
async def case(monkeypatch, tmp_path):
    """为删除用例准备真实数据库记录和临时文件夹。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr("yuxi.workspace.paths.get_user_data_dir", lambda: tmp_path)
    monkeypatch.setattr(project_service, "_lock_project_workdir_changes", AsyncMock())
    monkeypatch.setattr(project_service.Tasker, "publish", AsyncMock())
    project_id = str(uuid4())
    try:
        async with sessions() as db:
            db.add(User(uid="user-1", username="user-1", password_hash="test"))
            project = Project(
                id=project_id,
                uid="user-1",
                selection_status="implicit",
                directory_mode="managed",
                workdir_path=f"projects/{project_id}",
            )
            db.add(project)
            await db.flush()
            conversation = Conversation(uid="user-1", thread_id=str(uuid4()), project_id=project_id, agent_id="main")
            db.add(conversation)
            await db.commit()
            ensure_bound_user_workdir("user-1", project.workdir_path)
            directory = user_workdir_host_dir("user-1", project.workdir_path)
            (directory / "marker.txt").write_text("keep until cleanup")
            yield db, project, conversation, directory
    finally:
        await engine.dispose()


async def test_default_delete_keeps_directory_and_creates_no_cleanup_task(case):
    db, project, conversation, directory = case
    await conversation_service.delete_thread_view(thread_id=conversation.thread_id, current_uid="user-1", db=db)
    await db.refresh(conversation)
    await db.refresh(project)
    assert conversation.status == "deleted"
    assert project.status == "active"
    assert (directory / "marker.txt").read_text() == "keep until cleanup"
    assert await db.scalar(select(TaskRecord)) is None


async def test_explicit_delete_commits_conversation_project_and_pending_task(case):
    db, project, conversation, directory = case
    result = await conversation_service.delete_thread_view(
        thread_id=conversation.thread_id, current_uid="user-1", db=db, delete_workdir=True
    )
    await db.refresh(conversation)
    await db.refresh(project)
    task = await db.get(TaskRecord, result["workdir_delete_task_id"])
    assert conversation.status == project.status == "deleted"
    assert task.status == "pending"
    assert task.payload == {"uid": "user-1", "project_id": project.id}
    assert result["project_id"] == project.id
    assert directory.is_dir()


@pytest.mark.parametrize("guard", ["selectable", "linked", "shared", "overlap", "running", "other-user"])
async def test_rejected_cleanup_preserves_conversation_and_files(case, guard):
    db, project, conversation, directory = case
    expected = 409
    uid = "user-1"
    if guard == "selectable":
        project.selection_status = "selectable"
        expected = 422
    elif guard == "linked":
        project.directory_mode = "linked"
        expected = 422
    elif guard == "shared":
        db.add(Conversation(uid=uid, thread_id=str(uuid4()), project_id=project.id, agent_id="main"))
    elif guard == "overlap":
        db.add(
            Project(
                id=str(uuid4()),
                uid=uid,
                workdir_path=project.workdir_path,
                directory_mode="linked",
                selection_status="selectable",
            )
        )
    elif guard == "running":
        db.add(
            AgentRun(
                id=str(uuid4()),
                uid=uid,
                conversation_thread_id=conversation.thread_id,
                conversation_id=conversation.id,
                runtime_scope_id=conversation.thread_id,
                agent_slug="main",
                status="running",
                request_id=str(uuid4()),
                run_type="chat",
                input_payload={},
            )
        )
    else:
        uid = "user-2"
        expected = 404
    await db.commit()
    with pytest.raises(HTTPException) as error:
        await conversation_service.delete_thread_view(
            thread_id=conversation.thread_id, current_uid=uid, db=db, delete_workdir=True
        )
    assert error.value.status_code == expected
    await db.refresh(project)
    await db.refresh(conversation)
    assert project.status == conversation.status == "active"
    assert (directory / "marker.txt").read_text() == "keep until cleanup"
    assert await db.scalar(select(TaskRecord)) is None


@pytest.mark.parametrize(
    "selection,mode,expected",
    [
        ("implicit", "managed", True),
        ("selectable", "managed", False),
        ("implicit", "linked", False),
    ],
)
async def test_thread_response_exposes_only_standalone_managed_cleanup(case, selection, mode, expected):
    db, project, conversation, _directory = case
    project.selection_status = selection
    project.directory_mode = mode
    await db.commit()
    result = await conversation_service.list_threads_view(agent_slug=None, db=db, current_uid="user-1", limit=20)
    assert result[0]["id"] == conversation.thread_id
    assert result[0]["can_delete_workdir"] is expected
