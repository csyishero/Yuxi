"""Dashboard 统计读模型的数据访问层。"""

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import case, distinct, func, literal, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.repositories.agent_repository import AgentRepository
from yuxi.storage.minio.client import normalize_public_minio_url
from yuxi.storage.postgres.models_business import (
    AUDIT_MESSAGE_TYPES,
    Agent,
    AgentRun,
    Conversation,
    ConversationStats,
    Message,
    MessageFeedback,
    Project,
    SubagentThread,
    ToolCall,
    User,
)
from yuxi.utils.datetime_utils import UTC, format_utc_datetime, utc_now


class DashboardRepository:
    """集中封装 Dashboard 的跨表统计查询与读模型聚合。"""

    def __init__(self, db_session: AsyncSession, *, scope: str = "all"):
        self.db_session = db_session
        self.scope = scope

    def _scope_filter(self, conversation_id=Conversation.id):
        """统一当前有效归属；全部历史不按当前对象状态过滤。"""
        if self.scope == "all":
            return True
        valid = (
            select(Conversation.id)
            .join(Project, (Project.id == Conversation.project_id) & (Project.uid == Conversation.uid))
            .join(User, User.uid == Conversation.uid)
            .join(Agent, Agent.slug == Conversation.agent_id)
            .where(Conversation.status != "deleted", Project.status == "active", User.is_deleted == 0)
            .correlate(None)
        )
        return conversation_id.in_(valid)

    @staticmethod
    def _time_group_format(column: Any, time_range: str) -> Any:
        """生成使用上海时区显示的 PostgreSQL 时间分组表达式。"""
        if time_range == "14hours":
            return func.to_char(column + text("INTERVAL '8 hours'"), "YYYY-MM-DD HH24:00")
        if time_range == "14weeks":
            return func.to_char(column + text("INTERVAL '8 hours'"), "IYYY-IW")
        return func.to_char(column + text("INTERVAL '8 hours'"), "YYYY-MM-DD")

    def _shanghai_date_group(self, column: Any) -> Any:
        """按上海日历日生成 PostgreSQL/SQLite 兼容分组表达式。"""
        bind = self.db_session.bind
        if bind is not None and bind.dialect.name == "sqlite":
            return func.date(column, "+8 hours")
        return func.date(column + text("INTERVAL '8 hours'"))

    @staticmethod
    def _conversation_token_totals(conversation_ids: list[int] | None = None, *, start=None, end=None):
        """按会话汇总 Run 实测用量，保留缺失标记和无 Run 历史汇总。"""
        reported = AgentRun.token_usage["usage_reported_call_count"].as_integer() > 0
        complete = AgentRun.token_usage["complete"].as_boolean().is_(True)
        value = AgentRun.token_usage["total"]["total_tokens"].as_integer()
        run_totals = (
            select(
                AgentRun.conversation_id,
                func.sum(case((reported | complete, value), else_=None)).label("total_tokens"),
                func.min(case((complete & value.isnot(None), 1), else_=0)).label("complete"),
            )
            .where(
                AgentRun.conversation_id.in_(conversation_ids) if conversation_ids is not None else True,
                or_(AgentRun.started_at.isnot(None), AgentRun.status.notin_(("pending", "cancelled"))),
                func.coalesce(AgentRun.started_at, AgentRun.created_at) >= start if start else True,
                func.coalesce(AgentRun.started_at, AgentRun.created_at) <= end if end else True,
            )
            .group_by(AgentRun.conversation_id)
            .subquery()
        )
        has_runs = run_totals.c.conversation_id.isnot(None)
        legacy_tokens = func.nullif(ConversationStats.total_tokens, 0) if start is None else literal(None)
        return (
            select(
                Conversation.id.label("conversation_id"),
                case((has_runs, run_totals.c.total_tokens), else_=legacy_tokens).label("total_tokens"),
                case((has_runs, run_totals.c.complete == 1), else_=legacy_tokens.isnot(None)).label("complete"),
            )
            .where(Conversation.id.in_(conversation_ids) if conversation_ids is not None else True)
            .outerjoin(run_totals, Conversation.id == run_totals.c.conversation_id)
            .outerjoin(ConversationStats, Conversation.id == ConversationStats.conversation_id)
            .subquery()
        )

    async def get_conversation_token_usage(self, conversation_id: int) -> dict[str, Any]:
        """读取与会话列表一致的实测 Token 汇总。"""
        totals = self._conversation_token_totals([conversation_id])
        row = (
            await self.db_session.execute(
                select(totals.c.total_tokens, totals.c.complete).where(totals.c.conversation_id == conversation_id)
            )
        ).one()
        return {"total_tokens": row.total_tokens, "token_usage_complete": bool(row.complete)}

    @staticmethod
    def _audit_period(time_range: str, now: datetime | None = None):
        """给列表与图表提供相同的上海日历起点和 UTC 截止点。"""
        raw_now = now or utc_now()
        end = raw_now.astimezone(UTC).replace(tzinfo=None) if raw_now.tzinfo else raw_now
        if time_range == "all":
            return None, end
        days = {"7days": 7, "14days": 14, "30days": 30, "90days": 90}[time_range]
        local_start = (end + timedelta(hours=8)).replace(hour=0, minute=0, second=0, microsecond=0)
        return local_start - timedelta(days=days - 1, hours=8), end

    @staticmethod
    def _message_totals(*, start=None, end=None):
        """按事实消息计算条数及最近活动，排除内部审计消息。"""
        return (
            select(
                Message.conversation_id,
                func.count(Message.id).label("message_count"),
                func.max(Message.created_at).label("last_active_at"),
            )
            .where(
                or_(Message.message_type.is_(None), Message.message_type.notin_(AUDIT_MESSAGE_TYPES)),
                Message.created_at >= start if start else True,
                Message.created_at <= end if end else True,
            )
            .group_by(Message.conversation_id)
            .subquery()
        )

    def _audit_filters(
        self,
        *,
        start=None,
        end=None,
        uid=None,
        agent_id=None,
        project_id=None,
        status="all",
        search=None,
        include_subagents=True,
    ):
        """列表和图表共享归属与时间筛选，删除不丢失历史。"""
        filters = [self._scope_filter()]
        for value, column in (
            (uid, Conversation.uid),
            (agent_id, Conversation.agent_id),
            (project_id, Conversation.project_id),
        ):
            if value:
                filters.append(column == value)
        is_subagent = or_(
            Conversation.status == "subagent",
            select(SubagentThread.id)
            .where(SubagentThread.child_conversation_id == Conversation.id)
            .correlate(Conversation)
            .exists(),
        )
        if status == "subagent":
            filters.append(is_subagent)
        elif status != "all":
            filters.append(Conversation.status == status)
        if not include_subagents and status != "subagent":
            filters.append(~is_subagent)
        if search and search.strip():
            term = f"%{search.strip()}%"
            filters.append(
                or_(
                    Conversation.title.ilike(term),
                    Conversation.thread_id.ilike(term),
                    Conversation.uid.ilike(term),
                    User.username.ilike(term),
                )
            )
        if start:
            run_time = func.coalesce(AgentRun.started_at, AgentRun.created_at)
            filters.append(
                or_(
                    Conversation.created_at.between(start, end),
                    select(Message.id)
                    .where(Message.conversation_id == Conversation.id, Message.created_at.between(start, end))
                    .correlate(Conversation)
                    .exists(),
                    select(AgentRun.id)
                    .where(AgentRun.conversation_id == Conversation.id, run_time.between(start, end))
                    .correlate(Conversation)
                    .exists(),
                )
            )
        return filters

    async def list_conversations(
        self,
        *,
        uid: str | None = None,
        agent_id: str | None = None,
        status: str = "all",
        search: str | None = None,
        project_id: str | None = None,
        time_range: str = "all",
        include_subagents: bool = True,
        now: datetime | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        """分页查询 Dashboard 对话，并装配用户与 Agent 展示名称。"""
        start, end = self._audit_period(time_range, now)
        filters = self._audit_filters(
            start=start,
            end=end,
            uid=uid,
            agent_id=agent_id,
            project_id=project_id,
            status=status,
            search=search,
            include_subagents=include_subagents,
        )
        messages = self._message_totals(start=start, end=end)
        total_result = await self.db_session.execute(
            select(func.count(Conversation.id))
            .select_from(Conversation)
            .outerjoin(User, Conversation.uid == User.uid)
            .where(*filters)
        )
        rows = (
            await self.db_session.execute(
                select(Conversation, messages.c.message_count, User, Project)
                .outerjoin(messages, Conversation.id == messages.c.conversation_id)
                .outerjoin(Project, Conversation.project_id == Project.id)
                .outerjoin(User, Conversation.uid == User.uid)
                .where(*filters)
                .order_by(Conversation.created_at.desc(), Conversation.id.desc())
                .limit(limit)
                .offset(offset)
            )
        ).all()

        token_totals = self._conversation_token_totals(
            [conversation.id for conversation, _, _, _ in rows], start=start, end=end
        )
        usage_by_conversation = {
            row.conversation_id: row for row in (await self.db_session.execute(select(token_totals))).all()
        }
        agent_slugs = {conversation.agent_id for conversation, _, _, _ in rows if conversation.agent_id}
        agents_by_slug: dict[str, Agent] = {}
        if agent_slugs:
            agents = await AgentRepository(self.db_session).list_by_slugs(list(agent_slugs))
            agents_by_slug = {agent.slug: agent for agent in agents}

        items = []
        for conversation, message_count, user, project in rows:
            usage = usage_by_conversation[conversation.id]
            agent = agents_by_slug.get(conversation.agent_id)
            items.append(
                {
                    "thread_id": conversation.thread_id,
                    "uid": conversation.uid,
                    "username": user.username if user else conversation.uid,
                    "user_avatar": normalize_public_minio_url(user.avatar) if user and user.avatar else None,
                    "user_deleted": user is None or bool(user.is_deleted),
                    "agent_id": conversation.agent_id,
                    "agent_name": agent.name if agent else conversation.agent_id,
                    "agent_avatar": normalize_public_minio_url(agent.icon) if agent and agent.icon else None,
                    "agent_deleted": agent is None,
                    "title": conversation.title,
                    "status": conversation.status,
                    "is_pinned": bool(conversation.is_pinned),
                    "message_count": int(message_count or 0),
                    "project_id": conversation.project_id,
                    "project_name": project.name if project else None,
                    "project_deleted": project is None or project.status == "deleted",
                    "project_implicit": project is not None and project.selection_status == "implicit",
                    "total_tokens": usage.total_tokens,
                    "token_usage_complete": bool(usage.complete),
                    "created_at": format_utc_datetime(conversation.created_at) or "",
                    "updated_at": format_utc_datetime(conversation.updated_at) or "",
                }
            )
        return {
            "items": items,
            "total": int(total_result.scalar() or 0),
            "limit": limit,
            "offset": offset,
        }

    async def get_conversation_filter_options(self) -> dict[str, list[dict[str, Any]]]:
        """读取完整会话审计可用的用户与 Agent 筛选项。"""
        user_rows = (
            await self.db_session.execute(
                select(Conversation.uid, User.username, User.avatar, User.is_deleted)
                .select_from(Conversation)
                .outerjoin(User, Conversation.uid == User.uid)
                .where(self._scope_filter())
                .distinct()
            )
        ).all()
        agent_rows = (
            await self.db_session.execute(
                select(Conversation.agent_id, Agent.name, Agent.icon)
                .select_from(Conversation)
                .outerjoin(Agent, Conversation.agent_id == Agent.slug)
                .where(self._scope_filter())
                .distinct()
            )
        ).all()

        users = [
            {
                "uid": row.uid,
                "username": row.username or row.uid,
                "user_deleted": row.username is None or bool(row.is_deleted),
                "avatar": normalize_public_minio_url(row.avatar) if row.avatar else None,
                "is_deleted": row.username is None or bool(row.is_deleted),
            }
            for row in user_rows
        ]
        agents = [
            {
                "agent_id": row.agent_id,
                "agent_name": row.name or row.agent_id,
                "avatar": normalize_public_minio_url(row.icon) if row.icon else None,
                "is_deleted": row.name is None,
            }
            for row in agent_rows
        ]
        users.sort(key=lambda item: (item["is_deleted"], item["username"].lower()))
        agents.sort(key=lambda item: (item["is_deleted"], item["agent_name"].lower()))
        projects = (
            (
                await self.db_session.execute(
                    select(Project)
                    .where(
                        Project.id.in_(select(Conversation.project_id).where(self._scope_filter()))
                        if self.scope == "current"
                        else True
                    )
                    .order_by(Project.created_at.desc())
                )
            )
            .scalars()
            .all()
        )
        return {
            "users": users,
            "agents": agents,
            "projects": [
                {
                    "project_id": project.id,
                    "project_name": project.name or "无项目会话",
                    "uid": project.uid,
                    "is_deleted": project.status == "deleted",
                    "is_implicit": project.selection_status == "implicit",
                }
                for project in projects
            ],
        }

    async def get_conversation_audit_metadata(self, conversation: Conversation) -> dict[str, Any] | None:
        """读取会话关联用户与 Agent 的当前审计状态。"""
        row = (
            await self.db_session.execute(
                select(User, Agent, Project)
                .select_from(Conversation)
                .outerjoin(User, Conversation.uid == User.uid)
                .outerjoin(Agent, Conversation.agent_id == Agent.slug)
                .outerjoin(Project, Conversation.project_id == Project.id)
                .where(Conversation.id == conversation.id, self._scope_filter())
            )
        ).one_or_none()
        if row is None:
            return None
        user, agent, project = row
        return {
            "project_id": conversation.project_id,
            "project_name": project.name if project else None,
            "project_deleted": project is None or project.status == "deleted",
            "project_implicit": project is not None and project.selection_status == "implicit",
            "username": user.username if user else conversation.uid,
            "user_avatar": normalize_public_minio_url(user.avatar) if user and user.avatar else None,
            "user_deleted": user is None or bool(user.is_deleted),
            "agent_name": agent.name if agent else conversation.agent_id,
            "agent_avatar": normalize_public_minio_url(agent.icon) if agent and agent.icon else None,
            "agent_deleted": agent is None,
        }

    async def get_user_activity_stats(self, *, now: datetime | None = None) -> dict[str, Any]:
        """按用户消息发生时间统计历史活跃人数，注销不移除历史。"""
        _, end = self._audit_period("all", now)
        day = self._shanghai_date_group(Message.created_at)
        start = (end + timedelta(hours=8)).replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(
            days=119, hours=8
        )
        rows = (
            await self.db_session.execute(
                select(day.label("date"), func.count(distinct(Conversation.uid)).label("active_users"))
                .select_from(Message)
                .join(Conversation, Message.conversation_id == Conversation.id)
                .where(self._scope_filter(), Message.role == "user", Message.created_at.between(start, end))
                .group_by(day)
            )
        ).all()
        daily = {str(row.date): int(row.active_users) for row in rows}
        totals = (
            await self.db_session.execute(
                select(
                    func.count(distinct(Conversation.uid)),
                    func.count(
                        distinct(case((Message.created_at >= end - timedelta(days=1), Conversation.uid), else_=None))
                    ),
                )
                .select_from(Message)
                .join(Conversation, Message.conversation_id == Conversation.id)
                .where(
                    self._scope_filter(),
                    Message.role == "user",
                    Message.created_at.between(end - timedelta(days=30), end),
                )
            )
        ).one()
        return {
            "total_users": int(
                await self.db_session.scalar(
                    select(func.count(User.id)).where(User.is_deleted == 0 if self.scope == "current" else True)
                )
                or 0
            ),
            "active_users_24h": int(totals[1]),
            "active_users_30d": int(totals[0]),
            "daily_active_users": [
                {
                    "date": (start + timedelta(hours=8, days=i)).strftime("%Y-%m-%d"),
                    "active_users": daily.get((start + timedelta(hours=8, days=i)).strftime("%Y-%m-%d"), 0),
                }
                for i in range(120)
            ],
        }

    def _tool_executions(self):
        """已完成工具以 lifecycle 开始时间归属，旧记录保留原始时间。"""
        tool_id = Message.extra_metadata["compatibility_tool_call_id"].as_integer()
        audit = (
            select(tool_id.label("tool_id"), func.min(Message.started_at).label("started_at"))
            .where(Message.role == "tool", Message.message_type == "tool_audit", tool_id.isnot(None))
            .group_by(tool_id)
            .subquery()
        )
        return (
            select(
                ToolCall.id,
                ToolCall.tool_name,
                ToolCall.status,
                func.coalesce(audit.c.started_at, ToolCall.created_at).label("created_at"),
            )
            .join(Message, Message.id == ToolCall.message_id)
            .outerjoin(audit, audit.c.tool_id == ToolCall.id)
            .where(ToolCall.status.in_(("success", "error")), self._scope_filter(Message.conversation_id))
            .subquery()
        )

    async def get_tool_call_stats(self, *, now: datetime | None = None) -> dict[str, Any]:
        """统计完成的工具执行，pending 声明不作为失败或已执行次数。"""
        _, end = self._audit_period("all", now)
        calls = self._tool_executions().c
        counts = (
            await self.db_session.execute(
                select(func.count(calls.id), func.sum(case((calls.status == "success", 1), else_=0)))
            )
        ).one()
        total, successful = int(counts[0]), int(counts[1] or 0)
        most_used = (
            await self.db_session.execute(
                select(calls.tool_name, func.count(calls.id).label("count"))
                .group_by(calls.tool_name)
                .order_by(func.count(calls.id).desc(), calls.tool_name)
                .limit(10)
            )
        ).all()
        errors = (
            await self.db_session.execute(
                select(calls.tool_name, func.count(calls.id)).where(calls.status == "error").group_by(calls.tool_name)
            )
        ).all()
        start, _ = self._audit_period("7days", now)
        day = self._shanghai_date_group(calls.created_at)
        rows = (
            await self.db_session.execute(
                select(day.label("date"), func.count(calls.id).label("count"))
                .where(calls.created_at.between(start, end))
                .group_by(day)
            )
        ).all()
        daily = {str(row.date): int(row.count) for row in rows}
        return {
            "total_calls": total,
            "successful_calls": successful,
            "failed_calls": total - successful,
            "success_rate": round(successful / total * 100, 2) if total else 0,
            "most_used_tools": [{"tool_name": name, "count": count} for name, count in most_used],
            "tool_error_distribution": dict(errors),
            "daily_tool_calls": [
                {
                    "date": (start + timedelta(hours=8, days=i)).strftime("%Y-%m-%d"),
                    "call_count": daily.get((start + timedelta(hours=8, days=i)).strftime("%Y-%m-%d"), 0),
                }
                for i in range(7)
            ],
        }

    async def get_agent_analytics(self) -> dict[str, Any]:
        """汇总包含已移除智能体的历史使用情况。"""
        agents = list((await self.db_session.execute(select(Agent).order_by(Agent.name.asc()))).scalars().all())
        valid_filters = [self._scope_filter()]

        conversation_rows = (
            await self.db_session.execute(
                select(Conversation.agent_id, func.count(Conversation.id))
                .outerjoin(User, Conversation.uid == User.uid)
                .outerjoin(Agent, Conversation.agent_id == Agent.slug)
                .where(*valid_filters)
                .group_by(Conversation.agent_id)
            )
        ).all()
        conversation_counts = {agent_id: int(count or 0) for agent_id, count in conversation_rows}

        feedback_rows = (
            await self.db_session.execute(
                select(
                    Conversation.agent_id,
                    func.count(MessageFeedback.id).label("total"),
                    func.sum(case((MessageFeedback.rating == "like", 1), else_=0)).label("positive"),
                )
                .join(Message, MessageFeedback.message_id == Message.id)
                .join(Conversation, Message.conversation_id == Conversation.id)
                .outerjoin(User, Conversation.uid == User.uid)
                .outerjoin(Agent, Conversation.agent_id == Agent.slug)
                .where(
                    *valid_filters,
                    MessageFeedback.uid.in_(select(User.uid).where(User.is_deleted == 0).correlate(None))
                    if self.scope == "current"
                    else True,
                    or_(Message.message_type.is_(None), Message.message_type.notin_(AUDIT_MESSAGE_TYPES)),
                )
                .group_by(Conversation.agent_id)
            )
        ).all()
        feedback_by_agent = {row.agent_id: (int(row.total or 0), int(row.positive or 0)) for row in feedback_rows}

        tool_rows = (
            await self.db_session.execute(
                select(Conversation.agent_id, func.count(ToolCall.id))
                .join(Message, ToolCall.message_id == Message.id)
                .join(Conversation, Message.conversation_id == Conversation.id)
                .outerjoin(User, Conversation.uid == User.uid)
                .outerjoin(Agent, Conversation.agent_id == Agent.slug)
                .where(*valid_filters, ToolCall.status.in_(("success", "error")))
                .group_by(Conversation.agent_id)
            )
        ).all()
        tool_counts = {agent_id: int(count or 0) for agent_id, count in tool_rows}

        conversation_stats = []
        satisfaction_stats = []
        tool_usage = []
        agent_names = {agent.slug: agent.name for agent in agents}
        for slug in sorted(set(agent_names) | set(conversation_counts)):
            conversation_count = conversation_counts.get(slug, 0)
            total_feedbacks, positive_feedbacks = feedback_by_agent.get(slug, (0, 0))
            satisfaction_rate = round(positive_feedbacks / total_feedbacks * 100, 2) if total_feedbacks else 100
            conversation_stats.append({"agent_id": slug, "conversation_count": conversation_count})
            satisfaction_stats.append(
                {
                    "agent_id": slug,
                    "satisfaction_rate": satisfaction_rate,
                    "total_feedbacks": total_feedbacks,
                }
            )
            tool_usage.append({"agent_id": slug, "tool_usage_count": tool_counts.get(slug, 0)})

        return {
            "total_agents": len(agents),
            "agent_conversation_counts": conversation_stats,
            "agent_satisfaction_rates": satisfaction_stats,
            "agent_tool_usage": tool_usage,
            "agent_names": {
                slug: agent_names.get(slug, f"{slug}（已删除）") for slug in set(agent_names) | set(conversation_counts)
            },
        }

    async def get_basic_stats(self) -> dict[str, Any]:
        """分别读取历史累计事实与当前可用资源。"""
        total_conversations = await self.db_session.scalar(
            select(func.count(Conversation.id)).where(self._scope_filter())
        )
        visible_message = or_(Message.message_type.is_(None), Message.message_type.notin_(AUDIT_MESSAGE_TYPES))
        total_messages = await self.db_session.scalar(
            select(func.count(Message.id)).where(visible_message, self._scope_filter(Message.conversation_id))
        )
        feedback = (
            await self.db_session.execute(
                select(func.count(MessageFeedback.id), func.sum(case((MessageFeedback.rating == "like", 1), else_=0)))
                .join(Message, MessageFeedback.message_id == Message.id)
                .where(
                    visible_message,
                    self._scope_filter(Message.conversation_id),
                    MessageFeedback.uid.in_(select(User.uid).where(User.is_deleted == 0))
                    if self.scope == "current"
                    else True,
                )
            )
        ).one()
        current_users = await self.db_session.scalar(select(func.count(User.id)).where(User.is_deleted == 0))
        current_projects = await self.db_session.scalar(
            select(func.count(Project.id))
            .join(User, Project.uid == User.uid)
            .where(Project.status == "active", Project.selection_status == "selectable", User.is_deleted == 0)
        )
        current_conversations = await self.db_session.scalar(
            select(func.count(Conversation.id))
            .join(User, Conversation.uid == User.uid)
            .join(Agent, Conversation.agent_id == Agent.slug)
            .join(Project, Conversation.project_id == Project.id)
            .where(Conversation.status.in_(("active", "archived")), Project.status == "active", User.is_deleted == 0)
        )
        return {
            "total_conversations": int(total_conversations or 0),
            "total_messages": int(total_messages or 0),
            "total_users": int(current_users or 0),
            "current_resources": {
                "projects": int(current_projects or 0),
                "conversations": int(current_conversations or 0),
                "users": int(current_users or 0),
            },
            "feedback_stats": {
                "total_feedbacks": int(feedback[0] or 0),
                "satisfaction_rate": round((feedback[1] or 0) / feedback[0] * 100, 2) if feedback[0] else None,
            },
        }

    async def list_feedbacks(
        self, *, rating: str | None, agent_id: str | None
    ) -> list[tuple[MessageFeedback, Message, Conversation, User | None]]:
        """按可选评分和智能体过滤反馈关联数据。"""
        query = (
            select(MessageFeedback, Message, Conversation, User)
            .join(Message, MessageFeedback.message_id == Message.id)
            .join(Conversation, Message.conversation_id == Conversation.id)
            .outerjoin(User, MessageFeedback.uid == User.uid)
            .outerjoin(Agent, Conversation.agent_id == Agent.slug)
            .where(
                self._scope_filter(),
                MessageFeedback.uid.in_(select(User.uid).where(User.is_deleted == 0).correlate(None))
                if self.scope == "current"
                else True,
                or_(Message.message_type.is_(None), Message.message_type.notin_(AUDIT_MESSAGE_TYPES)),
            )
        )
        if rating and rating in {"like", "dislike"}:
            query = query.where(MessageFeedback.rating == rating)
        if agent_id:
            query = query.where(Conversation.agent_id == agent_id)
        query = query.order_by(MessageFeedback.created_at.desc())
        result = await self.db_session.execute(query)
        return list(result.all())

    async def get_call_timeseries(
        self, *, metric_type: str, time_range: str, now: datetime | None = None, local_now: datetime | None = None
    ) -> dict[str, Any]:
        """按实际执行事实及上海时间桶汇总，保留未知用量标记。"""
        _, end = self._audit_period("all", now or local_now)
        local_end = end + timedelta(hours=8)
        if time_range == "14hours":
            local_start = local_end.replace(minute=0, second=0, microsecond=0) - timedelta(hours=13)
            delta = timedelta(hours=1)
        elif time_range == "14weeks":
            local_start = local_end.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(
                days=local_end.weekday(), weeks=13
            )
            delta = timedelta(weeks=1)
        else:
            local_start = local_end.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=13)
            delta = timedelta(days=1)
        start = local_start - timedelta(hours=8)
        incomplete = 0
        if metric_type == "agents":
            event_time = AgentRun.started_at
            group = self._time_group_format(event_time, time_range)
            query = (
                select(
                    group.label("date"), AgentRun.agent_slug.label("category"), func.count(AgentRun.id).label("count")
                )
                .where(self._scope_filter(AgentRun.conversation_id), event_time.between(start, end))
                .group_by(group, AgentRun.agent_slug)
            )
            rows = (await self.db_session.execute(query)).all()
        elif metric_type == "models":
            event_time = func.coalesce(Message.started_at, Message.created_at)
            group = self._time_group_format(event_time, time_range)
            category = func.coalesce(
                Message.extra_metadata["response_metadata"]["model_name"].as_string(), "unknown_model"
            )
            rows = (
                await self.db_session.execute(
                    select(group.label("date"), category.label("category"), func.count(Message.id).label("count"))
                    .where(
                        self._scope_filter(Message.conversation_id),
                        Message.role == "assistant",
                        event_time.between(start, end),
                        or_(
                            Message.operation_id.isnot(None),
                            Message.message_type == "model_audit",
                            Message.extra_metadata["response_metadata"]["model_name"].as_string().isnot(None),
                        ),
                    )
                    .group_by(group, category)
                )
            ).all()
        elif metric_type == "tokens":
            # Run 用量是完整实测聚合；归入执行开始时间，不能从最终一条消息推算整次执行。
            event_time = func.coalesce(AgentRun.started_at, AgentRun.created_at)
            group = self._time_group_format(event_time, time_range)
            reported = or_(
                AgentRun.token_usage["usage_reported_call_count"].as_integer() > 0,
                AgentRun.token_usage["complete"].as_boolean().is_(True),
            )
            executed = or_(AgentRun.started_at.isnot(None), AgentRun.status.notin_(("pending", "cancelled")))
            rows = []
            for token_name in ("input_tokens", "output_tokens"):
                rows.extend(
                    (
                        await self.db_session.execute(
                            select(
                                group.label("date"),
                                literal(token_name).label("category"),
                                func.coalesce(
                                    func.sum(
                                        case(
                                            (reported, AgentRun.token_usage["total"][token_name].as_integer()),
                                            else_=None,
                                        )
                                    ),
                                    0,
                                ).label("count"),
                            )
                            .where(
                                self._scope_filter(AgentRun.conversation_id), executed, event_time.between(start, end)
                            )
                            .group_by(group)
                        )
                    ).all()
                )
            incomplete = int(
                await self.db_session.scalar(
                    select(func.count(AgentRun.id)).where(
                        self._scope_filter(AgentRun.conversation_id),
                        executed,
                        event_time.between(start, end),
                        or_(
                            AgentRun.token_usage["complete"].as_boolean().is_not(True),
                            AgentRun.token_usage["total"]["input_tokens"].as_integer().is_(None),
                            AgentRun.token_usage["total"]["output_tokens"].as_integer().is_(None),
                        ),
                    )
                )
                or 0
            )
        else:
            calls = self._tool_executions().c
            group = self._time_group_format(calls.created_at, time_range)
            rows = (
                await self.db_session.execute(
                    select(
                        group.label("date"),
                        calls.tool_name.label("category"),
                        func.count(calls.id).label("count"),
                    )
                    .where(calls.created_at.between(start, end))
                    .group_by(group, calls.tool_name)
                )
            ).all()
        categories = sorted({row.category for row in rows})
        if metric_type == "tokens":
            categories = ["input_tokens", "output_tokens"]
        time_data = {}
        for row in rows:
            time_data.setdefault(row.date, {})[row.category] = int(row.count or 0)
        data = []
        for i in range(14):
            local_time = local_start + delta * i
            date = local_time.strftime(
                "%Y-%m-%d %H:00" if time_range == "14hours" else "%G-%V" if time_range == "14weeks" else "%Y-%m-%d"
            )
            values = {category: time_data.get(date, {}).get(category, 0) for category in categories}
            data.append({"date": date, "data": values, "total": sum(values.values())})
        total = sum(item["total"] for item in data)
        peak = max(data, key=lambda item: item["total"])
        agent_names = None
        if metric_type == "agents":
            agents = await AgentRepository(self.db_session).list_by_slugs(categories) if categories else []
            names = {agent.slug: agent.name for agent in agents}
            agent_names = {slug: names.get(slug, f"{slug}（已删除）") for slug in categories}
        return {
            "data": data,
            "categories": categories,
            "total_count": total,
            "average_count": round(total / 14, 2),
            "peak_count": peak["total"],
            "peak_date": peak["date"],
            "agent_names": agent_names,
            "incomplete_usage_runs": incomplete,
            "token_usage_complete": incomplete == 0,
        }

    async def get_thread_analytics(
        self,
        *,
        time_range: str = "30days",
        agent_id: str | None = None,
        include_subagents: bool = True,
        uid: str | None = None,
        project_id: str | None = None,
        status: str = "all",
        search: str | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """统计会话（Thread）汇总、每日趋势、消息深度、Agent 与用户分布。"""
        query_start_time, query_now = self._audit_period(time_range, now)
        conversation_filters = self._audit_filters(
            start=query_start_time,
            end=query_now,
            uid=uid,
            agent_id=agent_id,
            project_id=project_id,
            status=status,
            search=search,
            include_subagents=include_subagents,
        )
        messages = self._message_totals(start=query_start_time, end=query_now)
        token_totals = self._conversation_token_totals(start=query_start_time, end=query_now)
        days = {"7days": 7, "14days": 14, "30days": 30, "90days": 90}.get(time_range, 30)
        trend_start = query_start_time or self._audit_period("30days", now)[0]
        local_start_day = trend_start + timedelta(hours=8)

        summary_result = await self.db_session.execute(
            select(
                func.count(Conversation.id).label("total_threads"),
                func.sum(case((Conversation.is_pinned.is_(True), 1), else_=0)).label("pinned_threads"),
            )
            .outerjoin(User, Conversation.uid == User.uid)
            .outerjoin(Agent, Conversation.agent_id == Agent.slug)
            .where(*conversation_filters)
        )
        summary_row = summary_result.one()

        message_summary_row = (
            await self.db_session.execute(
                select(
                    func.coalesce(func.sum(messages.c.message_count), 0).label("total_messages"),
                    func.count(messages.c.conversation_id).label("active_threads"),
                )
                .select_from(Conversation)
                .outerjoin(User, Conversation.uid == User.uid)
                .outerjoin(messages, Conversation.id == messages.c.conversation_id)
                .where(*conversation_filters)
            )
        ).one()
        usage_row = (
            await self.db_session.execute(
                select(
                    func.sum(token_totals.c.total_tokens).label("total_tokens"),
                    func.min(case((token_totals.c.complete.is_(True), 1), else_=0)).label("complete"),
                )
                .select_from(Conversation)
                .outerjoin(User, Conversation.uid == User.uid)
                .join(token_totals, Conversation.id == token_totals.c.conversation_id)
                .where(*conversation_filters)
            )
        ).one()
        total_tokens = usage_row.total_tokens

        total_threads = int(summary_row.total_threads or 0)
        active_threads = int(message_summary_row.active_threads or 0)
        pinned_threads = int(summary_row.pinned_threads or 0)
        total_messages = int(message_summary_row.total_messages or 0)

        new_thread_date = self._shanghai_date_group(Conversation.created_at)
        new_thread_rows = (
            await self.db_session.execute(
                select(new_thread_date.label("date"), func.count(Conversation.id).label("count"))
                .outerjoin(User, Conversation.uid == User.uid)
                .outerjoin(Agent, Conversation.agent_id == Agent.slug)
                .where(
                    *conversation_filters,
                    Conversation.created_at >= trend_start,
                    Conversation.created_at <= query_now,
                )
                .group_by(new_thread_date)
            )
        ).all()
        new_threads_by_date = {str(row.date): int(row.count or 0) for row in new_thread_rows}

        message_date = self._shanghai_date_group(Message.created_at)
        activity_query = (
            select(
                message_date.label("date"),
                func.count(distinct(Message.conversation_id)).label("active_threads"),
                func.count(Message.id).label("message_count"),
            )
            .select_from(Message)
            .join(Conversation, Message.conversation_id == Conversation.id)
            .outerjoin(User, Conversation.uid == User.uid)
            .outerjoin(Agent, Conversation.agent_id == Agent.slug)
            .where(
                Message.created_at >= trend_start,
                Message.created_at <= query_now,
                or_(Message.message_type.is_(None), Message.message_type.notin_(AUDIT_MESSAGE_TYPES)),
                *conversation_filters,
            )
            .group_by(message_date)
        )
        activity_rows = (await self.db_session.execute(activity_query)).all()
        activity_by_date = {
            str(row.date): {
                "active_threads": int(row.active_threads or 0),
                "message_count": int(row.message_count or 0),
            }
            for row in activity_rows
        }

        daily_trends = []
        for day_offset in range(days):
            date_key = (local_start_day + timedelta(days=day_offset)).strftime("%Y-%m-%d")
            activity = activity_by_date.get(date_key, {})
            daily_trends.append(
                {
                    "date": date_key,
                    "new_threads": new_threads_by_date.get(date_key, 0),
                    "active_threads": activity.get("active_threads", 0),
                    "message_count": activity.get("message_count", 0),
                }
            )

        # 3. 消息深度分布 (0条, 1-2条, 3-5条, 6-10条, 11-20条, 20+条)
        depth_query = (
            select(
                func.sum(case((func.coalesce(messages.c.message_count, 0) == 0, 1), else_=0)).label("d0"),
                func.sum(
                    case(
                        (
                            (func.coalesce(messages.c.message_count, 0) >= 1)
                            & (func.coalesce(messages.c.message_count, 0) <= 2),
                            1,
                        ),
                        else_=0,
                    )
                ).label("d1_2"),
                func.sum(
                    case(
                        (
                            (func.coalesce(messages.c.message_count, 0) >= 3)
                            & (func.coalesce(messages.c.message_count, 0) <= 5),
                            1,
                        ),
                        else_=0,
                    )
                ).label("d3_5"),
                func.sum(
                    case(
                        (
                            (func.coalesce(messages.c.message_count, 0) >= 6)
                            & (func.coalesce(messages.c.message_count, 0) <= 10),
                            1,
                        ),
                        else_=0,
                    )
                ).label("d6_10"),
                func.sum(
                    case(
                        (
                            (func.coalesce(messages.c.message_count, 0) >= 11)
                            & (func.coalesce(messages.c.message_count, 0) <= 20),
                            1,
                        ),
                        else_=0,
                    )
                ).label("d11_20"),
                func.sum(case((func.coalesce(messages.c.message_count, 0) > 20, 1), else_=0)).label("d20_plus"),
            )
            .select_from(Conversation)
            .outerjoin(messages, Conversation.id == messages.c.conversation_id)
            .outerjoin(User, Conversation.uid == User.uid)
            .outerjoin(Agent, Conversation.agent_id == Agent.slug)
            .where(*conversation_filters)
        )
        depth_row = (await self.db_session.execute(depth_query)).one()
        depth_distribution = {
            "0 条": int(depth_row.d0 or 0),
            "1-2 条": int(depth_row.d1_2 or 0),
            "3-5 条": int(depth_row.d3_5 or 0),
            "6-10 条": int(depth_row.d6_10 or 0),
            "11-20 条": int(depth_row.d11_20 or 0),
            "20+ 条": int(depth_row.d20_plus or 0),
        }

        # 4. 各智能体会话分布
        agent_group_query = (
            select(
                Conversation.agent_id,
                func.count(Conversation.id).label("thread_count"),
                func.coalesce(func.sum(messages.c.message_count), 0).label("message_count"),
                func.sum(token_totals.c.total_tokens).label("token_count"),
                func.min(case((token_totals.c.complete.is_(True), 1), else_=0)).label("token_complete"),
            )
            .select_from(Conversation)
            .join(token_totals, Conversation.id == token_totals.c.conversation_id)
            .outerjoin(messages, Conversation.id == messages.c.conversation_id)
            .outerjoin(User, Conversation.uid == User.uid)
            .outerjoin(Agent, Conversation.agent_id == Agent.slug)
            .where(*conversation_filters)
            .group_by(Conversation.agent_id)
            .order_by(func.count(Conversation.id).desc())
        )
        agent_rows = (await self.db_session.execute(agent_group_query)).all()
        agent_slugs = [row.agent_id for row in agent_rows if row.agent_id]
        agent_names_map = {}
        agent_avatars_map = {}
        if agent_slugs:
            agents = await AgentRepository(self.db_session).list_by_slugs(agent_slugs)
            agent_names_map = {a.slug: a.name for a in agents}
            agent_avatars_map = {a.slug: normalize_public_minio_url(a.icon) if a.icon else None for a in agents}

        agent_distribution = [
            {
                "agent_id": row.agent_id,
                "agent_name": agent_names_map.get(row.agent_id, f"{row.agent_id}（已删除）"),
                "agent_avatar": agent_avatars_map.get(row.agent_id),
                "thread_count": int(row.thread_count or 0),
                "message_count": int(row.message_count or 0),
                "token_count": row.token_count,
                "token_usage_complete": row.token_complete == 1,
                "avg_messages": round(int(row.message_count or 0) / int(row.thread_count or 1), 1),
            }
            for row in agent_rows
        ]

        # 5. 高频用户活跃排行
        user_group_query = (
            select(
                Conversation.uid,
                User.username,
                User.avatar,
                User.is_deleted,
                func.count(Conversation.id).label("thread_count"),
                func.coalesce(func.sum(messages.c.message_count), 0).label("message_count"),
                func.max(messages.c.last_active_at).label("last_active_at"),
            )
            .select_from(Conversation)
            .outerjoin(User, Conversation.uid == User.uid)
            .outerjoin(Agent, Conversation.agent_id == Agent.slug)
            .outerjoin(messages, Conversation.id == messages.c.conversation_id)
            .where(*conversation_filters)
            .group_by(Conversation.uid, User.username, User.avatar, User.is_deleted)
            .order_by(func.count(Conversation.id).desc())
            .limit(10)
        )
        user_rows = (await self.db_session.execute(user_group_query)).all()
        top_users = [
            {
                "uid": row.uid,
                "username": row.username or row.uid,
                "user_deleted": row.username is None or bool(row.is_deleted),
                "avatar": normalize_public_minio_url(row.avatar) if row.avatar else None,
                "thread_count": int(row.thread_count or 0),
                "message_count": int(row.message_count or 0),
                "last_active_at": row.last_active_at.isoformat() if row.last_active_at else None,
            }
            for row in user_rows
        ]

        # 6. 状态分布
        status_query = (
            select(Conversation.status, func.count(Conversation.id))
            .outerjoin(User, Conversation.uid == User.uid)
            .outerjoin(Agent, Conversation.agent_id == Agent.slug)
            .where(*conversation_filters)
            .group_by(Conversation.status)
        )
        status_rows = (await self.db_session.execute(status_query)).all()
        status_distribution = {row[0] or "unknown": int(row[1] or 0) for row in status_rows}

        return {
            "summary": {
                "total_threads": total_threads,
                "active_threads": active_threads,
                "total_messages": total_messages,
                "total_tokens": total_tokens,
                "avg_messages_per_thread": round(total_messages / total_threads, 1) if total_threads else 0.0,
                "avg_tokens_per_thread": round(total_tokens / total_threads, 0)
                if total_threads and total_tokens is not None
                else None,
                "token_usage_complete": usage_row.complete == 1,
                "pinned_threads": pinned_threads,
            },
            "daily_trends": daily_trends,
            "depth_distribution": depth_distribution,
            "agent_distribution": agent_distribution,
            "top_users": top_users,
            "status_distribution": status_distribution,
        }
