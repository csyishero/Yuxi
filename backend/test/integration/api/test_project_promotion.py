"""项目提升在真实 HTTP、PostgreSQL 和工作目录上的验证。"""

import asyncio
import os
from contextlib import asynccontextmanager
from uuid import uuid4

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from yuxi.storage.postgres.models_business import Agent, AgentRun, AgentRunRequest, Conversation, Message, Project
from yuxi.workspace.paths import ensure_bound_user_workdir, user_workspace_dir

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


@asynccontextmanager
async def promotion_fixture(uid):
    """仅创建和清理由随机 ID 标识的测试对话及目录。"""
    engine = create_async_engine(os.environ["POSTGRES_URL"])
    factory = async_sessionmaker(engine, expire_on_commit=False)
    project_id = str(uuid4())
    thread_id = f"promote-{uuid4().hex}"
    request_id = f"request-{thread_id}"
    path = f"projects/2026-10-09_12-00-00_{project_id[:8]}"
    directory = user_workspace_dir(uid) / path
    assert not directory.exists()
    try:
        async with factory() as db:
            db.add(
                Agent(
                    slug=thread_id,
                    backend_id="chatbot",
                    name="test-agent",
                    share_config={
                        "version": 2,
                        "read_scope": {"access_level": "user", "user_uids": [uid]},
                        "manage_scope": None,
                    },
                    created_by=uid,
                )
            )
            db.add(
                Project(
                    id=project_id,
                    uid=uid,
                    selection_status="implicit",
                    directory_mode="managed",
                    workdir_path=path,
                    idempotency_key=f"thread:{request_id}",
                )
            )
            await db.flush()
            conversation = Conversation(
                uid=uid,
                project_id=project_id,
                thread_id=thread_id,
                agent_id=thread_id,
                creation_request_id=request_id,
                status="active",
                title="promotion test",
            )
            db.add(conversation)
            await db.flush()
            db.add(Message(conversation_id=conversation.id, role="user", content="original-message"))
            await db.commit()
        ensure_bound_user_workdir(uid, path)
        (directory / "proof.txt").write_bytes(b"original-bytes")
        yield factory, project_id, thread_id, directory
    finally:
        async with factory() as db:
            ids = select(Conversation.id).where(Conversation.project_id == project_id)
            await db.execute(delete(AgentRunRequest).where(AgentRunRequest.conversation_thread_id == thread_id))
            await db.execute(delete(AgentRun).where(AgentRun.conversation_thread_id == thread_id))
            await db.execute(delete(Message).where(Message.conversation_id.in_(ids)))
            await db.execute(delete(Conversation).where(Conversation.project_id == project_id))
            await db.execute(delete(Project).where(Project.id == project_id))
            await db.execute(delete(Agent).where(Agent.slug == thread_id))
            await db.commit()
        if (directory / "proof.txt").exists():
            (directory / "proof.txt").unlink()
        if directory.exists():
            directory.rmdir()
        await engine.dispose()


async def test_promote_http_preserves_files_and_serializes_retries(test_client, standard_user, admin_headers):
    """普通用户只能提升自己的项目，并发重试保持单一所有权和安全删除语义。"""
    uid = standard_user["user"]["uid"]
    headers = standard_user["headers"]
    async with promotion_fixture(uid) as (factory, project_id, thread_id, directory):
        url = f"/api/projects/from-conversation/{thread_id}"
        assert (await test_client.post(url, json={"name": "项目"})).status_code == 401
        assert (await test_client.post(url, json={"name": "项目"}, headers=admin_headers)).status_code == 404
        async with factory() as db:
            await db.scalar(select(Project).where(Project.id == project_id).with_for_update())
            requests = [
                asyncio.create_task(test_client.post(url, json={"name": "项目"}, headers=headers)) for _ in range(2)
            ]
            done, _ = await asyncio.wait(requests, timeout=0.2)
            assert not done, "转换必须等待项目生命周期锁"
            await db.commit()
        responses = await asyncio.gather(*requests)
        assert [response.status_code for response in responses] == [200, 200]
        assert {response.json()["id"] for response in responses} == {project_id}
        path = responses[0].json()["workdir_path"]
        assert path.endswith(project_id[:8])
        replay = await test_client.post(url, json={"name": "不同名称"}, headers=headers)
        assert replay.json()["name"] == "项目"
        original_retry = await test_client.post(
            "/api/chat/thread", headers=headers, json={"agent_id": thread_id, "request_id": f"request-{thread_id}"}
        )
        assert original_retry.status_code == 200, original_retry.text
        assert original_retry.json()["id"] == thread_id
        assert original_retry.json()["can_delete_workdir"] is False
        async with factory() as db:
            project = await db.get(Project, project_id)
            conversation = await db.scalar(select(Conversation).where(Conversation.thread_id == thread_id))
            assert project.selection_status == "selectable"
            assert project.workdir_path == path and conversation.project_id == project_id
            assert (
                await db.scalars(select(Message.content).where(Message.conversation_id == conversation.id))
            ).all() == ["original-message"]
            assert (
                len((await db.scalars(select(Project).where(Project.workdir_path == path, Project.uid == uid))).all())
                == 1
            )
        assert (directory / "proof.txt").read_bytes() == b"original-bytes"
        # 原会话的文件删除选项即使从旧页面发来，也不能清理项目共享目录。
        response = await test_client.delete(
            f"/api/chat/thread/{thread_id}", headers=headers, params={"delete_workdir": True}
        )
        assert response.status_code == 422, response.text
        assert (await test_client.delete(f"/api/chat/thread/{thread_id}", headers=headers)).status_code == 200
        assert (directory / "proof.txt").read_bytes() == b"original-bytes"
        assert (await test_client.delete(f"/api/projects/{project_id}", headers=headers)).status_code == 200
        assert (directory / "proof.txt").read_bytes() == b"original-bytes"


async def test_promote_waiter_rechecks_deleted_project(test_client, standard_user):
    """等待锁期间删除项目，转换返回拒绝且不复活目录或项目。"""
    uid = standard_user["user"]["uid"]
    async with promotion_fixture(uid) as (factory, project_id, thread_id, directory):
        async with factory() as db:
            project = await db.scalar(select(Project).where(Project.id == project_id).with_for_update())
            pending = asyncio.create_task(
                test_client.post(
                    f"/api/projects/from-conversation/{thread_id}",
                    headers=standard_user["headers"],
                    json={"name": "不能复活"},
                )
            )
            done, _ = await asyncio.wait([pending], timeout=0.2)
            assert not done
            project.status = "deleted"
            await db.commit()
        response = await pending
        assert response.status_code == 404, response.text
        async with factory() as db:
            project = await db.get(Project, project_id)
            assert project.selection_status == "implicit" and project.status == "deleted"
        assert (directory / "proof.txt").read_bytes() == b"original-bytes"


async def test_promote_while_run_active_keeps_run_and_file_delete_guard(test_client, standard_user):
    """运行中的对话可转项目；同一运行仍保护项目文件夹不被删除。"""
    uid = standard_user["user"]["uid"]
    headers = standard_user["headers"]
    async with promotion_fixture(uid) as (factory, project_id, thread_id, directory):
        run_id = f"run-{uuid4().hex}"
        async with factory() as db:
            conversation = await db.scalar(select(Conversation).where(Conversation.thread_id == thread_id))
            db.add(
                AgentRun(
                    id=run_id,
                    request_id=f"active-{uuid4().hex}",
                    conversation_id=conversation.id,
                    conversation_thread_id=thread_id,
                    runtime_scope_id=thread_id,
                    agent_slug=thread_id,
                    uid=uid,
                    status="running",
                )
            )
            await db.commit()

        promoted = await test_client.post(
            f"/api/projects/from-conversation/{thread_id}", headers=headers, json={"name": "读书项目"}
        )
        assert promoted.status_code == 200, promoted.text
        assert promoted.json()["id"] == project_id
        assert promoted.json()["selection_status"] == "selectable"
        assert (directory / "proof.txt").read_bytes() == b"original-bytes"
        async with factory() as db:
            run = await db.get(AgentRun, run_id)
            conversation = await db.scalar(select(Conversation).where(Conversation.thread_id == thread_id))
            assert run.status == "running" and run.conversation_id == conversation.id
            assert conversation.project_id == project_id

        deletion = await test_client.delete(
            f"/api/projects/{project_id}", headers=headers, params={"delete_workdir": True}
        )
        assert deletion.status_code == 409, deletion.text
        assert deletion.json()["detail"] == "项目中仍有运行或排队的任务，请结束后重试"
        assert (directory / "proof.txt").read_bytes() == b"original-bytes"
