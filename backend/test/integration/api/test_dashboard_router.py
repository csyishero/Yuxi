"""
Integration tests for dashboard router endpoints.
"""

from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from yuxi.storage.postgres.models_business import Conversation

from test.live_api_cleanup import make_test_conversation_metadata, make_test_conversation_title

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


async def _set_conversation_statuses(subagent_thread_id: str, deleted_thread_id: str) -> None:
    """使用绑定当前测试事件循环的一次性引擎写入状态事实。"""
    engine = create_async_engine(os.environ["POSTGRES_URL"])
    try:
        session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)
        async with session_factory() as db:
            await db.execute(
                update(Conversation).where(Conversation.thread_id == subagent_thread_id).values(status="subagent")
            )
            await db.execute(
                update(Conversation).where(Conversation.thread_id == deleted_thread_id).values(status="deleted")
            )
            await db.commit()
    finally:
        await engine.dispose()


async def test_dashboard_requires_authentication(test_client):
    response = await test_client.get("/api/dashboard/conversations")
    assert response.status_code == 401


async def test_standard_user_is_forbidden(test_client, standard_user):
    response = await test_client.get("/api/dashboard/conversations", headers=standard_user["headers"])
    assert response.status_code == 403


async def test_admin_can_fetch_conversations(test_client, admin_headers):
    response = await test_client.get("/api/dashboard/conversations", headers=admin_headers)
    assert response.status_code == 200, response.text
    data = response.json()
    assert set(data) == {"items", "total", "limit", "offset"}
    assert isinstance(data["items"], list)
    assert data["total"] >= len(data["items"])


async def test_admin_can_fetch_conversation_filter_options(test_client, admin_headers):
    response = await test_client.get("/api/dashboard/conversations/options", headers=admin_headers)

    assert response.status_code == 200, response.text
    data = response.json()
    assert set(data) == {"users", "agents", "projects"}
    assert all("is_deleted" in item for item in data["users"])
    assert all("is_deleted" in item for item in data["agents"])


async def test_dashboard_rejects_invalid_query_ranges(test_client, admin_headers):
    responses = [
        await test_client.get("/api/dashboard/stats/threads?time_range=365days", headers=admin_headers),
        await test_client.get("/api/dashboard/conversations?limit=0", headers=admin_headers),
        await test_client.get("/api/dashboard/conversations?offset=-1", headers=admin_headers),
        await test_client.get("/api/dashboard/stats?scope=invalid", headers=admin_headers),
        await test_client.get("/api/dashboard/conversations?scope=invalid", headers=admin_headers),
    ]

    assert [response.status_code for response in responses] == [422, 422, 422, 422, 422]


async def test_agent_stats_http_omits_removed_top_performers_contract(test_client, admin_headers):
    """智能体统计 HTTP 契约保留概览字段且不再发布 TOP 5 排行。"""
    response = await test_client.get("/api/dashboard/stats/agents", headers=admin_headers)

    assert response.status_code == 200, response.text
    assert set(response.json()) == {
        "total_agents",
        "agent_conversation_counts",
        "agent_satisfaction_rates",
        "agent_tool_usage",
        "agent_names",
    }


async def test_admin_can_fetch_stats(test_client, admin_headers):
    """Test that the timeseries stats endpoint returns consistent values."""
    response = await test_client.get(
        "/api/dashboard/stats/calls/timeseries?type=models&time_range=14days",
        headers=admin_headers,
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["total_count"] >= 0
    assert len(data["data"]) == 14
    assert isinstance(data["categories"], list)


async def test_knowledge_stats_matches_runtime_capability(test_client, admin_headers):
    response = await test_client.get("/api/dashboard/stats/knowledge", headers=admin_headers)

    assert response.status_code == 200, response.text
    assert set(response.json()) == {
        "total_databases",
        "total_files",
        "total_nodes",
        "total_storage_size",
        "databases_by_type",
        "file_type_distribution",
    }


async def test_admin_can_fetch_thread_analytics(test_client, admin_headers):
    """Test that thread analytics endpoint returns complete statistics schema."""
    response = await test_client.get(
        "/api/dashboard/stats/threads?time_range=30days",
        headers=admin_headers,
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert "summary" in data
    assert "daily_trends" in data
    assert "depth_distribution" in data
    assert "agent_distribution" in data
    assert "top_users" in data
    assert "status_distribution" in data
    assert len(data["daily_trends"]) == 30
    assert data["summary"]["total_threads"] >= 0


async def test_dashboard_http_applies_subagent_and_deleted_conversation_scopes(test_client, admin_headers):
    default_agent = await test_client.get("/api/agent/default", headers=admin_headers)
    assert default_agent.status_code == 200, default_agent.text
    agent = default_agent.json()["agent"]
    agent_id = str(agent.get("slug") or agent["agent_id"])
    marker = f"dashboard-scope-{uuid.uuid4().hex[:10]}"

    async def analytics(*, include_subagents: bool) -> dict:
        response = await test_client.get(
            "/api/dashboard/stats/threads",
            params={
                "time_range": "30days",
                "agent_id": agent_id,
                "include_subagents": str(include_subagents).lower(),
            },
            headers=admin_headers,
        )
        assert response.status_code == 200, response.text
        return response.json()

    baseline_default = await analytics(include_subagents=False)
    baseline_including_subagents = await analytics(include_subagents=True)
    thread_ids = []
    for status in ("active", "subagent", "deleted"):
        response = await test_client.post(
            "/api/chat/thread",
            headers=admin_headers,
            json={
                "agent_id": agent_id,
                "title": make_test_conversation_title(f"{marker}-{status}"),
                "metadata": make_test_conversation_metadata(marker),
            },
        )
        assert response.status_code == 200, response.text
        thread_ids.append(str(response.json().get("thread_id") or response.json()["id"]))

    await _set_conversation_statuses(thread_ids[1], thread_ids[2])

    default_scope = await analytics(include_subagents=False)
    subagent_scope = await analytics(include_subagents=True)
    assert default_scope["summary"]["total_threads"] == baseline_default["summary"]["total_threads"] + 2
    assert subagent_scope["summary"]["total_threads"] == baseline_including_subagents["summary"]["total_threads"] + 3

    default_audit = await test_client.get(
        "/api/dashboard/conversations",
        params={"search": marker, "limit": 10},
        headers=admin_headers,
    )
    deleted_audit = await test_client.get(
        "/api/dashboard/conversations",
        params={"search": marker, "status": "deleted", "limit": 10},
        headers=admin_headers,
    )
    assert default_audit.status_code == 200, default_audit.text
    assert deleted_audit.status_code == 200, deleted_audit.text
    assert {item["thread_id"] for item in default_audit.json()["items"]} == set(thread_ids)
    assert {item["thread_id"] for item in deleted_audit.json()["items"]} == {thread_ids[2]}


async def test_admin_can_fetch_feedbacks(test_client, admin_headers):
    """Test that feedback endpoint returns 200 and handles the User join correctly."""
    response = await test_client.get("/api/dashboard/feedbacks", headers=admin_headers)
    assert response.status_code == 200, f"feedbacks failed: {response.text}"
    assert isinstance(response.json(), list)


async def test_dashboard_http_reads_run_token_totals(test_client, admin_headers):
    """真实 HTTP 返回 PostgreSQL 同会话 Run 用量和缺失标记。"""
    from sqlalchemy import select
    from yuxi.storage.postgres.models_business import AgentRun, ConversationStats

    default_agent = await test_client.get("/api/agent/default", headers=admin_headers)
    assert default_agent.status_code == 200
    agent = default_agent.json()["agent"]
    agent_id = str(agent.get("slug") or agent["agent_id"])
    marker = f"dashboard-usage-{uuid.uuid4().hex[:10]}"
    response = await test_client.post(
        "/api/chat/thread",
        headers=admin_headers,
        json={
            "agent_id": agent_id,
            "title": make_test_conversation_title(marker),
            "metadata": make_test_conversation_metadata(marker),
        },
    )
    assert response.status_code == 200
    thread_id = str(response.json().get("thread_id") or response.json()["id"])
    engine = create_async_engine(os.environ["POSTGRES_URL"])
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as db:
            conversation = (
                await db.execute(select(Conversation).where(Conversation.thread_id == thread_id))
            ).scalar_one()
            await db.execute(
                update(ConversationStats)
                .where(ConversationStats.conversation_id == conversation.id)
                .values(total_tokens=9999)
            )
            for index, usage in enumerate(
                [
                    {"total": {"total_tokens": 120}, "complete": True, "usage_reported_call_count": 1},
                    {"total": {"total_tokens": 80}, "complete": True, "usage_reported_call_count": 1},
                ]
            ):
                db.add(
                    AgentRun(
                        id=f"{marker}-{index}",
                        request_id=f"{marker}-request-{index}",
                        conversation_id=conversation.id,
                        conversation_thread_id=thread_id,
                        runtime_scope_id=thread_id,
                        uid=conversation.uid,
                        agent_slug=agent_id,
                        status="completed",
                        token_usage=usage,
                    )
                )
            await db.commit()
            for expected, complete in [(200, True), (120, False), (None, False)]:
                listing = await test_client.get(
                    "/api/dashboard/conversations", headers=admin_headers, params={"search": marker}
                )
                detail = await test_client.get(f"/api/dashboard/conversations/{thread_id}", headers=admin_headers)
                assert listing.status_code == detail.status_code == 200
                item = listing.json()["items"][0]
                assert item["status"] == "active"
                assert item["total_tokens"] == detail.json()["total_tokens"] == expected
                assert item["token_usage_complete"] is complete
                assert detail.json()["token_usage_complete"] is complete
                run_id = f"{marker}-{1 if expected == 200 else 0}"
                await db.execute(update(AgentRun).where(AgentRun.id == run_id).values(token_usage={"available": False}))
                await db.commit()
    finally:
        await engine.dispose()


async def test_dashboard_history_survives_lifecycle_changes(test_client, admin_headers):
    """真实 PostgreSQL 与 HTTP 验证删除不改写历史，列表和图表同口径。"""
    from datetime import timedelta

    from sqlalchemy import delete, select
    from yuxi.repositories.project_repository import ProjectRepository
    from yuxi.storage.postgres.models_business import Agent, AgentRun, Department, Message, Project, ToolCall, User
    from yuxi.utils.datetime_utils import utc_now_naive

    marker = f"history-{uuid.uuid4().hex[:12]}"
    engine = create_async_engine(os.environ["POSTGRES_URL"])
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as db:
            now = utc_now_naive() - timedelta(seconds=5)
            department = Department(name=marker)
            db.add(department)
            await db.flush()
            user = User(uid=marker, username=marker, password_hash="unused", role="user", department_id=department.id)
            agent = Agent(slug=marker, backend_id=marker, name="历史统计测试", share_config={})
            db.add_all([user, agent])
            await db.flush()
            project = Project(
                id=marker,
                uid=marker,
                name="审计保留项目",
                selection_status="selectable",
                directory_mode="managed",
                workdir_path=f"projects/{marker}",
                status="active",
            )
            db.add(project)
            await db.flush()
            conversation = Conversation(
                thread_id=marker,
                project_id=marker,
                uid=marker,
                agent_id=marker,
                title=marker,
                status="active",
                created_at=now - timedelta(days=60),
                updated_at=now,
            )
            db.add(conversation)
            await db.flush()
            for index in range(2):
                run = AgentRun(
                    id=f"{marker}-{index}",
                    request_id=f"{marker}-{index}",
                    conversation_id=conversation.id,
                    conversation_thread_id=marker,
                    runtime_scope_id=marker,
                    uid=marker,
                    agent_slug=marker,
                    status="completed",
                    started_at=now,
                    created_at=now,
                    token_usage={
                        "complete": True,
                        "usage_reported_call_count": 1,
                        "total": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
                    },
                )
                db.add(run)
                await db.flush()
                message = Message(
                    conversation_id=conversation.id,
                    run_id=run.id,
                    role="assistant",
                    content="ok",
                    operation_id=f"model-{index}",
                    execution_status="completed",
                    started_at=now,
                    created_at=now,
                    extra_metadata={"response_metadata": {"model_name": marker}},
                )
                db.add(message)
                await db.flush()
                # 审批前声明可以属于前一个 Run，执行通过明确投影 ID 关联到 resume Run。
                if index == 1:
                    message.run_id = f"{marker}-0"
                tool = ToolCall(
                    message_id=message.id,
                    tool_name=marker,
                    langgraph_tool_call_id=f"tool-{index}",
                    status="success",
                    created_at=now - timedelta(days=60),
                )
                db.add(tool)
                await db.flush()
                db.add(
                    Message(
                        conversation_id=conversation.id,
                        run_id=run.id,
                        role="tool",
                        content="done",
                        message_type="tool_audit",
                        operation_id=f"tool-{index}",
                        execution_status="completed",
                        started_at=now,
                        created_at=now,
                        extra_metadata={"compatibility_tool_call_id": tool.id},
                    )
                )
            # queued, never started: cannot count as an Agent execution
            db.add(
                AgentRun(
                    id=f"{marker}-pending",
                    request_id=f"{marker}-pending",
                    conversation_id=conversation.id,
                    conversation_thread_id=marker,
                    runtime_scope_id=marker,
                    uid=marker,
                    agent_slug=marker,
                    status="pending",
                    created_at=now,
                    token_usage={"available": False},
                )
            )
            await db.commit()

            async def read(path, params=None):
                response = await test_client.get(f"/api/dashboard/{path}", headers=admin_headers, params=params)
                assert response.status_code == 200, response.text
                return response.json()

            filters = {"project_id": marker, "time_range": "7days"}
            before = await read("stats/threads", filters)
            assert before["summary"]["total_threads"] == 1
            assert before["summary"]["total_messages"] == 2
            assert before["summary"]["total_tokens"] == 30
            assert before["summary"]["token_usage_complete"] is True
            assert (await read("stats/threads", {**filters, "scope": "current"}))["summary"] == before["summary"]
            assert (await read("stats", {"scope": "current"}))["current_resources"] == (await read("stats"))[
                "current_resources"
            ]
            current_tokens_before = await read("stats/calls/timeseries", {"type": "tokens", "scope": "current"})
            totals = {}
            for kind, expected in [("agents", 2), ("models", 2), ("tools", 2)]:
                series = await read("stats/calls/timeseries", {"type": kind, "time_range": "14days"})
                totals[kind] = sum(bucket["data"].get(marker, 0) for bucket in series["data"])
                assert totals[kind] == expected
                current_series = await read("stats/calls/timeseries", {"type": kind, "scope": "current"})
                assert sum(bucket["data"].get(marker, 0) for bucket in current_series["data"]) == expected
                assert series["total_count"] == sum(bucket["total"] for bucket in series["data"])
            tokens_before = await read("stats/calls/timeseries", {"type": "tokens", "time_range": "14days"})

            await ProjectRepository(db).soft_delete_with_conversations(project, deleted_at=utc_now_naive())
            user.is_deleted = 1
            await db.delete(agent)
            await db.commit()
            assert (await read("stats/threads", {**filters, "scope": "current"}))["summary"]["total_threads"] == 0
            assert (await read("conversations", {**filters, "scope": "current", "status": "deleted"}))["total"] == 0
            current_detail = await test_client.get(
                f"/api/dashboard/conversations/{marker}", headers=admin_headers, params={"scope": "current"}
            )
            assert current_detail.status_code == 404
            current_options = await read("conversations/options", {"scope": "current"})
            assert all(item["project_id"] != marker for item in current_options["projects"])
            for kind in totals:
                series = await read("stats/calls/timeseries", {"type": kind, "scope": "current"})
                assert sum(bucket["data"].get(marker, 0) for bucket in series["data"]) == 0
            assert (await read("stats/calls/timeseries", {"type": "tokens", "scope": "current"}))[
                "total_count"
            ] == current_tokens_before["total_count"] - 30
            assert (await read("stats", {"scope": "current"}))["current_resources"] == (await read("stats"))[
                "current_resources"
            ]
            for path in ("stats/users", "stats/tools", "stats/agents", "feedbacks"):
                await read(path, {"scope": "current"})
            after = await read("stats/threads", filters)
            assert before["summary"] == after["summary"]
            assert before["daily_trends"] == after["daily_trends"]
            listing = await read("conversations", filters)
            assert listing["total"] == after["summary"]["total_threads"]
            item = listing["items"][0]
            assert item["message_count"] == 2 and item["total_tokens"] == 30
            assert item["project_name"] == "审计保留项目"
            assert item["status"] == "deleted"
            assert item["project_deleted"] and item["user_deleted"] and item["agent_deleted"]
            assert (await read(f"conversations/{marker}"))["project_deleted"]
            assert (await read("conversations", {**filters, "status": "active"}))["total"] == 0
            options = await read("conversations/options")
            assert next(project for project in options["projects"] if project["project_id"] == marker)["is_deleted"]
            for kind in totals:
                series = await read("stats/calls/timeseries", {"type": kind, "time_range": "14days"})
                assert sum(bucket["data"].get(marker, 0) for bucket in series["data"]) == totals[kind]
            assert (await read("stats/calls/timeseries", {"type": "tokens", "time_range": "14days"}))[
                "total_count"
            ] == tokens_before["total_count"]
    finally:
        async with factory() as db:
            ids = select(Conversation.id).where(Conversation.thread_id == marker)
            messages = select(Message.id).where(Message.conversation_id.in_(ids))
            await db.execute(delete(ToolCall).where(ToolCall.message_id.in_(messages)))
            await db.execute(delete(Message).where(Message.conversation_id.in_(ids)))
            await db.execute(delete(AgentRun).where(AgentRun.uid == marker))
            await db.execute(delete(Conversation).where(Conversation.thread_id == marker))
            await db.execute(delete(Project).where(Project.id == marker))
            await db.execute(delete(User).where(User.uid == marker))
            await db.execute(delete(Agent).where(Agent.slug == marker))
            await db.execute(delete(Department).where(Department.name == marker))
            await db.commit()
        await engine.dispose()


async def test_dashboard_period_buckets_use_shanghai_and_iso_year():
    """真实 PG 验证当天起点、小时起点和跨年 ISO 周，工具总数只含所选周期。"""
    from datetime import datetime

    from sqlalchemy import delete
    from yuxi.repositories.dashboard_repository import DashboardRepository
    from yuxi.storage.postgres.models_business import AgentRun

    engine = create_async_engine(os.environ["POSTGRES_URL"])
    marker = f"buckets-{uuid.uuid4().hex[:10]}"
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as db:
            for index, when in enumerate(
                [datetime(2025, 12, 28, 16, 0), datetime(2025, 12, 31, 16, 0), datetime(2026, 1, 1, 4, 0)]
            ):
                db.add(
                    AgentRun(
                        id=f"{marker}-{index}",
                        request_id=f"{marker}-{index}",
                        conversation_thread_id=marker,
                        runtime_scope_id=marker,
                        uid=marker,
                        agent_slug=marker,
                        status="completed",
                        started_at=when,
                        created_at=when,
                    )
                )
            await db.commit()
            repo = DashboardRepository(db)
            for period, bucket, expected in [
                ("14weeks", "2026-01", 3),
                ("14days", "2026-01-01", 2),
                ("14hours", "2026-01-01 00:00", 1),
            ]:
                stats = await repo.get_call_timeseries(
                    metric_type="agents", time_range=period, now=datetime(2026, 1, 1, 5, 30)
                )
                row = next(row for row in stats["data"] if row["date"] == bucket)
                assert row["data"][marker] == expected
                assert stats["total_count"] == sum(row["total"] for row in stats["data"])
            await db.execute(delete(AgentRun).where(AgentRun.uid == marker))
            await db.commit()
    finally:
        async with async_sessionmaker(engine)() as db:
            await db.execute(delete(AgentRun).where(AgentRun.uid == marker))
            await db.commit()
        await engine.dispose()
