"""会话迁移在真实 HTTP、PostgreSQL 与工作目录上的验证。"""

import asyncio
import os
from contextlib import asynccontextmanager
from uuid import uuid4

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from yuxi.storage.postgres.models_business import (
    Agent,
    AgentRun,
    AgentRunRequest,
    Conversation,
    Message,
    Project,
    SubagentThread,
)
from yuxi.workspace.paths import ensure_bound_user_workdir, user_workdir_host_dir

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


@asynccontextmanager
async def move_fixture(uid):
    """为 HTTP 迁移创建可清理的源、目标项目与子会话。"""
    engine = create_async_engine(os.environ["POSTGRES_URL"])
    factory = async_sessionmaker(engine, expire_on_commit=False)
    source_id, target_id = str(uuid4()), str(uuid4())
    thread_id = f"move-{uuid4().hex}"
    child_thread_id = f"move-child-{uuid4().hex}"
    agent_slug = f"move-agent-{uuid4().hex}"
    source_path, target_path = f"projects/{source_id}", f"projects/{target_id}"
    source_dir = user_workdir_host_dir(uid, source_path)
    target_dir = user_workdir_host_dir(uid, target_path)
    assert not source_dir.exists() and not target_dir.exists()
    try:
        async with factory() as db:
            db.add(
                Agent(
                    slug=agent_slug,
                    backend_id="chatbot",
                    name="move test agent",
                    share_config={
                        "version": 2,
                        "read_scope": {"access_level": "user", "user_uids": [uid]},
                        "manage_scope": None,
                    },
                    created_by=uid,
                )
            )
            db.add_all(
                [
                    Project(
                        id=source_id,
                        uid=uid,
                        selection_status="implicit",
                        directory_mode="managed",
                        workdir_path=source_path,
                        idempotency_key=f"thread:{thread_id}",
                    ),
                    Project(
                        id=target_id,
                        uid=uid,
                        name="目标项目",
                        selection_status="selectable",
                        directory_mode="managed",
                        workdir_path=target_path,
                    ),
                ]
            )
            await db.flush()
            root = Conversation(
                uid=uid,
                project_id=source_id,
                thread_id=thread_id,
                agent_id=agent_slug,
                creation_request_id=thread_id,
                status="active",
                title="原会话",
            )
            child = Conversation(
                uid=uid, project_id=source_id, thread_id=child_thread_id, agent_id=agent_slug, status="archived"
            )
            db.add_all([root, child])
            await db.flush()
            db.add_all(
                [
                    SubagentThread(
                        uid=uid,
                        parent_conversation_id=root.id,
                        child_conversation_id=child.id,
                        child_thread_id=child_thread_id,
                        subagent_slug=agent_slug,
                        created_by_run_id="historical-run",
                    ),
                    Message(conversation_id=root.id, role="user", content="保留的消息"),
                ]
            )
            await db.commit()
        ensure_bound_user_workdir(uid, source_path)
        ensure_bound_user_workdir(uid, target_path)
        (source_dir / "original.txt").write_bytes(b"source bytes")
        yield factory, source_id, target_id, thread_id, child_thread_id, agent_slug, source_dir, target_dir
    finally:
        async with factory() as db:
            threads = [thread_id, child_thread_id]
            ids = select(Conversation.id).where(Conversation.thread_id.in_(threads))
            await db.execute(delete(AgentRunRequest).where(AgentRunRequest.conversation_thread_id.in_(threads)))
            await db.execute(delete(AgentRun).where(AgentRun.conversation_thread_id.in_(threads)))
            await db.execute(delete(SubagentThread).where(SubagentThread.child_thread_id == child_thread_id))
            await db.execute(delete(Message).where(Message.conversation_id.in_(ids)))
            await db.execute(delete(Conversation).where(Conversation.thread_id.in_(threads)))
            await db.execute(delete(Project).where(Project.id.in_([source_id, target_id])))
            await db.execute(delete(Agent).where(Agent.slug == agent_slug))
            await db.commit()
        for directory in (source_dir, target_dir):
            for file in directory.glob("*"):
                file.unlink()
            if directory.exists():
                directory.rmdir()
        await engine.dispose()


async def test_http_move_keeps_history_and_uses_target_directory(test_client, standard_user, admin_headers):
    """移动后 HTTP 历史和创建重试返回原线程，数据库归属及字节一致。"""
    uid = standard_user["user"]["uid"]
    headers = standard_user["headers"]
    async with move_fixture(uid) as (
        factory,
        source_id,
        target_id,
        thread_id,
        child_id,
        agent_slug,
        source_dir,
        target_dir,
    ):
        url = f"/api/projects/conversations/{thread_id}/move"
        payload = {
            "target_project_id": target_id,
            "request_id": f"move-{uuid4().hex}",
            "files": [{"source_path": "/original.txt", "destination_path": "/renamed.txt"}],
        }
        assert (await test_client.post(url, json=payload)).status_code == 401
        assert (await test_client.post(url, json=payload, headers=admin_headers)).status_code == 404
        assert (
            await test_client.post(
                url,
                json={"target_project_id": target_id, "files": payload["files"]},
                headers=headers,
            )
        ).status_code == 422
        response = await test_client.post(url, json=payload, headers=headers)
        assert response.status_code == 200, response.text
        assert response.json()["thread"]["project_id"] == target_id
        assert response.json()["thread"]["workdir_path"] == f"projects/{target_id}"
        assert response.json()["imported_files"][0]["destination_path"] == "/renamed.txt"
        replay = await test_client.post(url, json=payload, headers=headers)
        assert replay.status_code == 200, replay.text
        assert replay.json()["imported_files"] == response.json()["imported_files"]

        history = await test_client.get(f"/api/chat/thread/{thread_id}/history", headers=headers)
        assert history.status_code == 200, history.text
        assert history.json()["thread"]["project_id"] == target_id
        assert any(item["content"] == "保留的消息" for item in history.json()["history"])
        replay = await test_client.post(
            "/api/chat/thread", headers=headers, json={"agent_id": agent_slug, "request_id": thread_id}
        )
        assert replay.status_code == 200, replay.text
        assert replay.json()["id"] == thread_id

        async with factory() as db:
            conversations = (
                await db.scalars(select(Conversation).where(Conversation.thread_id.in_([thread_id, child_id])))
            ).all()
            assert {conversation.project_id for conversation in conversations} == {target_id}
            source_refs = {
                conversation.extra_metadata["project_moves"][0]["source_project_id"] for conversation in conversations
            }
            assert source_refs == {source_id}
        assert (source_dir / "original.txt").read_bytes() == (target_dir / "renamed.txt").read_bytes()


async def test_move_waits_for_conversation_lock_and_rechecks_queue(test_client, standard_user):
    """等待会话锁期间出现排队请求后，迁移拒绝且不复制文件。"""
    uid = standard_user["user"]["uid"]
    headers = standard_user["headers"]
    async with move_fixture(uid) as (
        factory,
        source_id,
        target_id,
        thread_id,
        _child_id,
        _slug,
        source_dir,
        target_dir,
    ):
        async with factory() as db:
            root = await db.scalar(select(Conversation).where(Conversation.thread_id == thread_id).with_for_update())
            pending = asyncio.create_task(
                test_client.post(
                    f"/api/projects/conversations/{thread_id}/move",
                    headers=headers,
                    json={
                        "target_project_id": target_id,
                        "request_id": f"move-queued-{uuid4().hex}",
                        "files": [{"source_path": "/original.txt", "destination_path": "/copy.txt"}],
                    },
                )
            )
            done, _ = await asyncio.wait([pending], timeout=0.2)
            assert not done
            message = Message(conversation_id=root.id, role="user", content="queued")
            db.add(message)
            await db.flush()
            db.add(
                AgentRunRequest(
                    request_id=f"queued-{uuid4().hex}",
                    uid=uid,
                    agent_slug=root.agent_id,
                    conversation_thread_id=thread_id,
                    input_message_id=message.id,
                    status="queued",
                )
            )
            await db.commit()
        response = await pending
        assert response.status_code == 409, response.text
        assert not (target_dir / "copy.txt").exists()
        assert (source_dir / "original.txt").read_bytes() == b"source bytes"
        async with factory() as db:
            root = await db.scalar(select(Conversation).where(Conversation.thread_id == thread_id))
            assert root.project_id == source_id


async def test_thread_settings_update_waits_for_move_and_keeps_move_history(test_client, standard_user):
    """并发更新线程设置不能以旧 JSON 覆盖已经提交的迁移记录。"""
    uid = standard_user["user"]["uid"]
    headers = standard_user["headers"]
    async with move_fixture(uid) as (
        factory,
        source_id,
        target_id,
        thread_id,
        _child_id,
        _slug,
        _source_dir,
        _target_dir,
    ):
        async with factory() as db:
            root = await db.scalar(select(Conversation).where(Conversation.thread_id == thread_id).with_for_update())
            pending = asyncio.create_task(
                test_client.put(
                    f"/api/chat/thread/{thread_id}",
                    headers=headers,
                    json={"tool_approval_mode": "always_trust"},
                )
            )
            done, _ = await asyncio.wait([pending], timeout=0.2)
            assert not done
            root.project_id = target_id
            root.extra_metadata = {
                "project_moves": [
                    {
                        "source_project_id": source_id,
                        "source_workdir_path": f"projects/{source_id}",
                        "move_id": "sentinel",
                    }
                ]
            }
            await db.commit()
        response = await pending
        assert response.status_code == 200, response.text
        assert response.json()["project_id"] == target_id
        async with factory() as db:
            saved = await db.scalar(select(Conversation).where(Conversation.thread_id == thread_id))
            assert saved.extra_metadata["project_moves"][0]["move_id"] == "sentinel"
            assert saved.extra_metadata["tool_approval_mode"] == "always_trust"
