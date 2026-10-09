"""Unit tests for DashboardService and Thread analytics."""

from __future__ import annotations

from datetime import datetime, timedelta
import pytest
import pytest_asyncio
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from yuxi.services.dashboard_service import DashboardService
from yuxi.storage.postgres.models_business import (
    Agent,
    AgentRun,
    Project,
    SubagentThread,
    Base,
    Conversation,
    ConversationStats,
    Department,
    Message,
    MessageFeedback,
    ToolCall,
    User,
)
from yuxi.utils.datetime_utils import utc_now_naive

pytestmark = [pytest.mark.asyncio, pytest.mark.unit]


@pytest_asyncio.fixture()
async def dashboard_db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        dept = Department(name="Engineering")
        superadmin = User(
            username="Super Admin",
            uid="uid-superadmin",
            password_hash="$argon2id$placeholder",
            role="superadmin",
            department=dept,
        )
        user1 = User(
            username="Alice",
            uid="uid-alice",
            password_hash="$argon2id$placeholder",
            role="user",
            department=dept,
        )
        user2 = User(
            username="Bob",
            uid="uid-bob",
            password_hash="$argon2id$placeholder",
            role="user",
            department=dept,
        )
        deleted_user = User(
            username="Deleted User",
            uid="uid-deleted",
            password_hash="$argon2id$placeholder",
            role="user",
            department=dept,
            is_deleted=1,
        )

        agent1 = Agent(
            slug="agent-helper",
            backend_id="b-1",
            name="Helper Agent",
            share_config={},
        )
        agent2 = Agent(
            slug="agent-coder",
            backend_id="b-2",
            name="Coder Agent",
            share_config={},
        )

        now = utc_now_naive()
        yesterday = now - timedelta(days=1)

        conv1 = Conversation(
            thread_id="thread-101",
            project_id="p-1",
            uid="uid-alice",
            agent_id="agent-helper",
            title="Alice Helper Query",
            status="active",
            is_pinned=True,
            created_at=yesterday,
            updated_at=now,
        )
        conv2 = Conversation(
            thread_id="thread-102",
            project_id="p-2",
            uid="uid-bob",
            agent_id="agent-coder",
            title="Bob Coder Task",
            status="active",
            is_pinned=False,
            created_at=now,
            updated_at=now,
        )
        conv3 = Conversation(
            thread_id="thread-103",
            project_id="p-3",
            uid="uid-alice",
            agent_id="agent-coder",
            title="Alice Python Debug",
            status="archived",
            is_pinned=False,
            created_at=yesterday,
            updated_at=yesterday,
        )
        deleted_conversation = Conversation(
            thread_id="thread-deleted",
            project_id="p-deleted",
            uid="uid-alice",
            agent_id="agent-helper",
            title="Deleted conversation",
            status="deleted",
            created_at=yesterday,
            updated_at=now,
        )
        deleted_user_conversation = Conversation(
            thread_id="thread-deleted-user",
            project_id="p-deleted-user",
            uid="uid-deleted",
            agent_id="agent-helper",
            title="Deleted user conversation",
            status="active",
            created_at=yesterday,
            updated_at=now,
        )
        missing_agent_conversation = Conversation(
            thread_id="thread-missing-agent",
            project_id="p-missing-agent",
            uid="uid-alice",
            agent_id="removed-agent",
            title="Removed agent conversation",
            status="active",
            created_at=yesterday,
            updated_at=now,
        )
        subagent_conversation = Conversation(
            thread_id="thread-subagent",
            project_id="p-subagent",
            uid="uid-alice",
            agent_id="agent-helper",
            title="Subagent conversation",
            status="subagent",
            created_at=yesterday,
            updated_at=now,
        )

        stats1 = ConversationStats(conversation=conv1, message_count=4, total_tokens=1200)
        stats2 = ConversationStats(conversation=conv2, message_count=8, total_tokens=3500)
        stats3 = ConversationStats(conversation=conv3, message_count=1, total_tokens=300)

        msg1 = Message(conversation=conv1, role="user", content="Hello", created_at=yesterday)
        msg2 = Message(conversation=conv1, role="assistant", content="Hi there!", created_at=yesterday)
        msg3 = Message(conversation=conv2, role="user", content="Write code", created_at=now)
        msg4 = Message(
            conversation=conv2,
            role="assistant",
            content="Here is code",
            created_at=now,
            extra_metadata={"usage_metadata": {"input_tokens": 5, "output_tokens": 3}},
        )
        hidden_model_audit = Message(
            conversation=conv2,
            role="assistant",
            content="Intermediate model output",
            message_type="model_audit",
            operation_id="model-audit-1",
            execution_status="completed",
            created_at=now,
            extra_metadata={"usage_metadata": {"input_tokens": 100, "output_tokens": 100}},
        )
        hidden_tool_audit = Message(
            conversation=conv2,
            role="tool",
            content="Intermediate tool output",
            message_type="tool_audit",
            operation_id="tool-audit-1",
            started_at=now,
            sequence=2,
            execution_status="completed",
            created_at=now,
            extra_metadata={"usage_metadata": {"input_tokens": 100, "output_tokens": 100}},
        )
        removed_agent_message = Message(
            conversation=missing_agent_conversation,
            role="assistant",
            content="Historical removed agent output",
            created_at=now,
        )

        tool1 = ToolCall(message=msg4, tool_name="bash", status="success", created_at=now)
        removed_agent_tool = ToolCall(
            message=removed_agent_message,
            tool_name="legacy_tool",
            status="success",
            created_at=now,
        )

        feedback1 = MessageFeedback(message=msg2, uid="uid-alice", rating="like", created_at=yesterday)
        hidden_model_feedback = MessageFeedback(
            message=hidden_model_audit,
            uid="uid-bob",
            rating="dislike",
            created_at=now,
        )
        hidden_tool_feedback = MessageFeedback(
            message=hidden_tool_audit,
            uid="uid-bob",
            rating="like",
            created_at=now,
        )
        removed_agent_feedback = MessageFeedback(
            message=removed_agent_message,
            uid="uid-alice",
            rating="dislike",
            created_at=now,
        )

        db.add_all(
            [
                dept,
                superadmin,
                user1,
                user2,
                deleted_user,
                agent1,
                agent2,
                conv1,
                conv2,
                conv3,
                deleted_conversation,
                deleted_user_conversation,
                missing_agent_conversation,
                subagent_conversation,
                stats1,
                stats2,
                stats3,
                msg1,
                msg2,
                msg3,
                msg4,
                hidden_model_audit,
                hidden_tool_audit,
                removed_agent_message,
                tool1,
                removed_agent_tool,
                feedback1,
                hidden_model_feedback,
                hidden_tool_feedback,
                removed_agent_feedback,
            ]
        )
        for conversation in (
            conv1,
            conv2,
            conv3,
            deleted_conversation,
            deleted_user_conversation,
            missing_agent_conversation,
            subagent_conversation,
        ):
            db.add(
                Project(
                    id=conversation.project_id,
                    uid=conversation.uid,
                    name=conversation.title,
                    status="deleted" if conversation.status == "deleted" else "active",
                    selection_status="selectable",
                    directory_mode="managed",
                    workdir_path=f"projects/{conversation.project_id}",
                )
            )
        await db.commit()
        yield db
    await engine.dispose()


async def test_dashboard_service_basic_stats(dashboard_db):
    service = DashboardService(dashboard_db)
    stats = await service.get_basic_stats()

    assert stats["total_conversations"] == 7
    assert stats["current_resources"]["conversations"] == 3
    assert stats["total_messages"] == 5
    assert stats["total_users"] == 3
    assert stats["feedback_stats"]["total_feedbacks"] == 2
    assert stats["feedback_stats"]["satisfaction_rate"] == 50.0

    tool_stats = await service.get_tool_call_stats()
    assert tool_stats["total_calls"] == 2

    user_stats = await service.get_user_activity_stats()
    assert len(user_stats["daily_active_users"]) == 120
    assert user_stats["daily_active_users"][0]["date"] < user_stats["daily_active_users"][-1]["date"]

    feedbacks = await service.get_feedbacks()
    assert len(feedbacks) == 2


async def test_agent_analytics_omits_removed_top_performers_contract(dashboard_db):
    """智能体统计保留概览字段且不再生成 TOP 5 排行。"""
    analytics = await DashboardService(dashboard_db).get_agent_analytics()

    assert set(analytics) == {
        "total_agents",
        "agent_conversation_counts",
        "agent_satisfaction_rates",
        "agent_tool_usage",
        "agent_names",
    }
    assert analytics["total_agents"] == 2
    assert analytics["agent_names"] == {
        "agent-helper": "Helper Agent",
        "agent-coder": "Coder Agent",
        "removed-agent": "removed-agent（已删除）",
    }
    coder_satisfaction = next(
        item for item in analytics["agent_satisfaction_rates"] if item["agent_id"] == "agent-coder"
    )
    assert coder_satisfaction == {
        "agent_id": "agent-coder",
        "satisfaction_rate": 100,
        "total_feedbacks": 0,
    }


async def test_dashboard_service_thread_analytics(dashboard_db):
    service = DashboardService(dashboard_db)
    analytics = await service.get_thread_analytics(time_range="all", include_subagents=False)

    summary = analytics["summary"]
    assert summary["total_threads"] == 6
    assert summary["active_threads"] >= 1
    assert summary["pinned_threads"] == 1
    assert summary["total_messages"] == 5
    assert summary["total_tokens"] == 5000
    assert summary["avg_messages_per_thread"] > 0
    assert summary["avg_tokens_per_thread"] > 0

    assert len(analytics["daily_trends"]) == 30

    depth = analytics["depth_distribution"]
    assert depth["1-2 条"] == 3  # actual Message rows, not stale ConversationStats
    assert depth["0 条"] == 3
    assert depth["6-10 条"] == 0

    agents = analytics["agent_distribution"]
    assert len(agents) == 3
    coder_stat = next(a for a in agents if a["agent_id"] == "agent-coder")
    assert coder_stat["thread_count"] == 2
    assert coder_stat["agent_name"] == "Coder Agent"

    with_subagents = await service.get_thread_analytics(time_range="7days", include_subagents=True)
    assert with_subagents["summary"]["total_threads"] == 7
    helper_with_subagent = next(
        item for item in with_subagents["agent_distribution"] if item["agent_id"] == "agent-helper"
    )
    assert helper_with_subagent["thread_count"] == 4

    top_users = analytics["top_users"]
    assert len(top_users) >= 2
    alice_stat = next(u for u in top_users if u["uid"] == "uid-alice")
    assert alice_stat["username"] == "Alice"
    assert alice_stat["thread_count"] == 4

    assert analytics["status_distribution"]["active"] == 4
    assert analytics["status_distribution"]["archived"] == 1

    coder_only = await service.get_thread_analytics(time_range="7days", agent_id="agent-coder")
    assert coder_only["summary"]["total_threads"] == 2
    assert coder_only["summary"]["total_messages"] == 2
    assert [item["agent_id"] for item in coder_only["agent_distribution"]] == ["agent-coder"]
    assert coder_only["status_distribution"] == {"active": 1, "archived": 1}
    assert {item["uid"] for item in coder_only["top_users"]} == {"uid-alice", "uid-bob"}


async def test_thread_analytics_groups_daily_trends_by_shanghai_date(dashboard_db):
    service = DashboardService(dashboard_db)
    fixed_now = datetime(2026, 8, 24, 1, 0)
    baseline = await service.repo.get_thread_analytics(time_range="7days", now=fixed_now)
    baseline_by_date = {item["date"]: item for item in baseline["daily_trends"]}

    boundary_conversation = Conversation(
        thread_id="thread-shanghai-boundary",
        project_id="p-boundary",
        uid="uid-alice",
        agent_id="agent-helper",
        title="Shanghai boundary",
        status="active",
        created_at=datetime(2026, 8, 23, 16, 30),
        updated_at=datetime(2026, 8, 23, 16, 30),
    )
    boundary_message = Message(
        conversation=boundary_conversation,
        role="user",
        content="After Shanghai midnight",
        created_at=datetime(2026, 8, 23, 16, 30),
    )
    dashboard_db.add_all([boundary_conversation, boundary_message])
    await dashboard_db.commit()

    analytics = await service.repo.get_thread_analytics(time_range="7days", now=fixed_now)
    trend_by_date = {item["date"]: item for item in analytics["daily_trends"]}

    assert trend_by_date["2026-08-24"]["new_threads"] == baseline_by_date["2026-08-24"]["new_threads"] + 1
    assert trend_by_date["2026-08-24"]["active_threads"] == baseline_by_date["2026-08-24"]["active_threads"] + 1
    assert trend_by_date["2026-08-24"]["message_count"] == baseline_by_date["2026-08-24"]["message_count"] + 1


async def test_thread_analytics_query_count_does_not_grow_with_time_range(dashboard_db):
    service = DashboardService(dashboard_db)
    engine = dashboard_db.bind.sync_engine
    statement_counts = []
    current_count = 0

    def count_statement(*_args):
        nonlocal current_count
        current_count += 1

    event.listen(engine, "before_cursor_execute", count_statement)
    try:
        await service.get_thread_analytics(time_range="7days")
        statement_counts.append(current_count)
        current_count = 0
        await service.get_thread_analytics(time_range="90days")
        statement_counts.append(current_count)
    finally:
        event.remove(engine, "before_cursor_execute", count_statement)

    assert statement_counts[0] == statement_counts[1]
    assert statement_counts[0] <= 12


async def test_dashboard_service_list_conversations_search(dashboard_db):
    service = DashboardService(dashboard_db)

    all_convs = await service.list_conversations(limit=20)
    assert all_convs["total"] == 7
    assert len(all_convs["items"]) == 7
    assert any(item["status"] == "deleted" for item in all_convs["items"])
    deleted_convs = await service.list_conversations(status="deleted", limit=20)
    assert deleted_convs["total"] == 1
    assert deleted_convs["items"][0]["thread_id"] == "thread-deleted"
    deleted_item = next(item for item in all_convs["items"] if item["thread_id"] == "thread-deleted-user")
    missing_agent_item = next(item for item in all_convs["items"] if item["thread_id"] == "thread-missing-agent")
    assert deleted_item["user_deleted"] is True
    assert missing_agent_item["agent_deleted"] is True
    assert all(item["created_at"].endswith("Z") for item in all_convs["items"])
    assert all(item["updated_at"].endswith("Z") for item in all_convs["items"])

    search_result = await service.list_conversations(search="Python")
    assert search_result["total"] == 1
    assert search_result["items"][0]["thread_id"] == "thread-103"
    assert search_result["items"][0]["username"] == "Alice"
    assert search_result["items"][0]["agent_name"] == "Coder Agent"

    active_only = await service.list_conversations(status="active")
    assert active_only["total"] == 4
    assert len(active_only["items"]) == 4

    options = await service.get_conversation_filter_options()
    assert next(item for item in options["users"] if item["uid"] == "uid-deleted")["is_deleted"] is True
    assert next(item for item in options["agents"] if item["agent_id"] == "removed-agent")["is_deleted"] is True


async def test_dashboard_service_conversation_detail(dashboard_db):
    service = DashboardService(dashboard_db)
    detail = await service.get_conversation_detail("thread-102")

    assert detail is not None
    assert detail["thread_id"] == "thread-102"
    assert detail["total_tokens"] == 3500
    assert detail["user_deleted"] is False
    assert detail["agent_deleted"] is False
    assert detail["created_at"].endswith("Z")
    assert detail["updated_at"].endswith("Z")
    assert len(detail["messages"]) == 2
    assert all(message["created_at"].endswith("Z") for message in detail["messages"])
    assistant_msg = next(m for m in detail["messages"] if m["role"] == "assistant")
    assert "tool_calls" in assistant_msg
    assert assistant_msg["tool_calls"][0]["tool_name"] == "bash"


async def test_conversation_tokens_use_runs_and_expose_missing_usage(dashboard_db):
    """审计累加同会话 Run，忽略旧汇总并区分真实零和未知。"""
    from sqlalchemy import select
    from yuxi.storage.postgres.models_business import AgentRun

    conversation = (
        await dashboard_db.execute(select(Conversation).where(Conversation.thread_id == "thread-102"))
    ).scalar_one()
    for index, usage in enumerate(
        [
            {"total": {"total_tokens": 120}, "complete": True, "usage_reported_call_count": 1},
            {"total": {"total_tokens": 80}, "complete": True, "usage_reported_call_count": 1},
        ]
    ):
        dashboard_db.add(
            AgentRun(
                id=f"usage-run-{index}",
                conversation_id=conversation.id,
                conversation_thread_id=conversation.thread_id,
                runtime_scope_id=conversation.thread_id,
                agent_slug=conversation.agent_id,
                uid=conversation.uid,
                status="completed",
                request_id=f"usage-request-{index}",
                token_usage=usage,
            )
        )
    await dashboard_db.commit()
    service = DashboardService(dashboard_db)
    detail = await service.get_conversation_detail(conversation.thread_id)
    item = (await service.list_conversations(search="thread-102"))["items"][0]
    assert detail["total_tokens"] == item["total_tokens"] == 200
    assert item["token_usage_complete"] is True

    run = await dashboard_db.get(AgentRun, "usage-run-1")
    run.token_usage = {"available": False}
    await dashboard_db.commit()
    item = (await service.list_conversations(search="thread-102"))["items"][0]
    assert item["total_tokens"] == 120
    assert item["token_usage_complete"] is False

    run = await dashboard_db.get(AgentRun, "usage-run-0")
    run.token_usage = {"total": {"total_tokens": 0}, "complete": False, "usage_reported_call_count": 0}
    await dashboard_db.commit()
    item = (await service.list_conversations(search="thread-102"))["items"][0]
    assert item["total_tokens"] is None
    assert item["token_usage_complete"] is False

    run.token_usage = {"total": {"total_tokens": 0}, "complete": True, "usage_reported_call_count": 1}
    await dashboard_db.delete(await dashboard_db.get(AgentRun, "usage-run-1"))
    await dashboard_db.commit()
    item = (await service.list_conversations(search="thread-102"))["items"][0]
    assert item["total_tokens"] == 0
    assert item["token_usage_complete"] is True


async def test_history_is_unchanged_by_project_deletion_and_removed_dimensions(dashboard_db):
    """软删除、注销及智能体移除只影响当前资源和标签，不减少历史事实。"""
    from sqlalchemy import select
    from yuxi.repositories.project_repository import ProjectRepository

    service = DashboardService(dashboard_db)
    before = await service.get_basic_stats()
    before_threads = await service.get_thread_analytics(time_range="all")
    before_tools = await service.get_tool_call_stats()
    project = await dashboard_db.get(Project, "p-2")
    await ProjectRepository(dashboard_db).soft_delete_with_conversations(project, deleted_at=utc_now_naive())
    user = await dashboard_db.scalar(select(User).where(User.uid == "uid-bob"))
    user.is_deleted = 1
    await dashboard_db.delete(await dashboard_db.scalar(select(Agent).where(Agent.slug == "agent-coder")))
    await dashboard_db.commit()

    after = await service.get_basic_stats()
    assert after["current_resources"]["projects"] == before["current_resources"]["projects"] - 1
    assert after["current_resources"]["conversations"] < before["current_resources"]["conversations"]
    for key in ("total_conversations", "total_messages", "feedback_stats"):
        assert after[key] == before[key]
    assert (await service.get_thread_analytics(time_range="all"))["summary"] == before_threads["summary"]
    assert (await service.get_tool_call_stats())["total_calls"] == before_tools["total_calls"]
    item = (await service.list_conversations(project_id="p-2"))["items"][0]
    assert item["status"] == "deleted"
    assert item["project_name"] == "Bob Coder Task"
    assert item["project_deleted"] and item["user_deleted"] and item["agent_deleted"]
    assert (await service.get_conversation_detail(item["thread_id"]))["project_deleted"]


async def test_period_list_and_charts_share_facts_and_filters(dashboard_db):
    """旧会话的新执行纳入当期，旧消息和无法分期的汇总不能混入。"""
    from sqlalchemy import select

    repo = DashboardService(dashboard_db).repo
    now = datetime(2026, 10, 9, 1)
    conversation = await dashboard_db.scalar(select(Conversation).where(Conversation.thread_id == "thread-102"))
    conversation.created_at = datetime(2020, 1, 1)
    for msg in (await dashboard_db.scalars(select(Message).where(Message.conversation_id == conversation.id))).all():
        msg.created_at = datetime(2020, 1, 1)
    for index, (when, usage) in enumerate(
        [
            (datetime(2020, 1, 1), {"total": {"total_tokens": 900}, "complete": True}),
            (datetime(2026, 10, 8, 17), {"total": {"total_tokens": 42}, "complete": True}),
            (datetime(2026, 10, 8, 18), {"available": False}),
        ]
    ):
        dashboard_db.add(
            AgentRun(
                id=f"period-{index}",
                request_id=f"period-{index}",
                conversation_id=conversation.id,
                conversation_thread_id=conversation.thread_id,
                runtime_scope_id=conversation.thread_id,
                agent_slug=conversation.agent_id,
                uid=conversation.uid,
                status="completed",
                started_at=when,
                created_at=when,
                token_usage=usage,
            )
        )
    dashboard_db.add(
        Message(conversation_id=conversation.id, role="user", content="new", created_at=datetime(2026, 10, 8, 17))
    )
    await dashboard_db.commit()
    filters = dict(
        time_range="7days",
        now=now,
        project_id="p-2",
        uid="uid-bob",
        agent_id="agent-coder",
        search="Coder",
        status="all",
    )
    listing = await repo.list_conversations(**filters)
    analytics = await repo.get_thread_analytics(**filters)
    assert listing["total"] == analytics["summary"]["total_threads"] == 1
    assert listing["items"][0]["message_count"] == analytics["summary"]["total_messages"] == 1
    assert listing["items"][0]["total_tokens"] == analytics["summary"]["total_tokens"] == 42
    assert listing["items"][0]["token_usage_complete"] is False
    assert analytics["summary"]["token_usage_complete"] is False
    assert sum(day["message_count"] for day in analytics["daily_trends"]) == 1
    assert sum(day["new_threads"] for day in analytics["daily_trends"]) == 0
    assert analytics["agent_distribution"][0]["message_count"] == 1
    assert analytics["top_users"][0]["message_count"] == 1
    assert (await repo.list_conversations(**{**filters, "status": "deleted"}))["total"] == 0
    assert (await repo.get_thread_analytics(**{**filters, "uid": "uid-alice"}))["summary"]["total_threads"] == 0


async def test_deleted_subagent_remains_excluded_when_toggle_is_off(dashboard_db):
    """持久子线程关系在项目删除覆盖 status 后仍可区分子会话。"""
    from sqlalchemy import select

    parent = await dashboard_db.scalar(select(Conversation).where(Conversation.thread_id == "thread-101"))
    child = await dashboard_db.scalar(select(Conversation).where(Conversation.thread_id == "thread-subagent"))
    dashboard_db.add(
        SubagentThread(
            uid=child.uid,
            parent_conversation_id=parent.id,
            child_conversation_id=child.id,
            child_thread_id=child.thread_id,
            subagent_slug=child.agent_id,
            created_by_run_id="historical-parent",
        )
    )
    child.status = "deleted"
    await dashboard_db.commit()
    service = DashboardService(dashboard_db)
    listing = await service.list_conversations(include_subagents=False)
    assert child.thread_id not in {item["thread_id"] for item in listing["items"]}
    assert (await service.list_conversations(status="subagent"))["total"] == 1
    assert (await service.get_thread_analytics(time_range="all", include_subagents=False))["summary"][
        "total_threads"
    ] == listing["total"]


async def test_unstarted_requests_do_not_make_recorded_usage_incomplete(dashboard_db):
    """未开始的待执行与已取消记录不代表未知模型消耗。"""
    from sqlalchemy import select

    conversation = await dashboard_db.scalar(select(Conversation).where(Conversation.thread_id == "thread-102"))
    for index, (status, usage) in enumerate(
        [
            ("completed", {"total": {"total_tokens": 42}, "complete": True}),
            ("pending", {}),
            ("cancelled", {}),
        ]
    ):
        dashboard_db.add(
            AgentRun(
                id=f"unstarted-{index}",
                request_id=f"unstarted-{index}",
                conversation_id=conversation.id,
                conversation_thread_id=conversation.thread_id,
                runtime_scope_id=conversation.thread_id,
                agent_slug=conversation.agent_id,
                uid=conversation.uid,
                status=status,
                token_usage=usage,
            )
        )
    await dashboard_db.commit()
    item = (await DashboardService(dashboard_db).list_conversations(project_id="p-2"))["items"][0]
    assert item["total_tokens"] == 42
    assert item["token_usage_complete"] is True
