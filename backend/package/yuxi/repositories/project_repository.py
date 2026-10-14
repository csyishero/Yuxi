"""Project 持久化 Repository。"""

from datetime import datetime

from sqlalchemy import exists, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.storage.postgres.models_business import AgentRun, AgentRunRequest, Conversation, Project, SubagentThread
from yuxi.repositories.agent_run_repository import TERMINAL_RUN_STATUSES


class ProjectRepository:
    """读写当前用户的 Project 业务事实。"""

    def __init__(self, db_session: AsyncSession):
        self.db = db_session

    async def add(self, project: Project) -> Project:
        """新增 Project 并 flush。"""
        self.db.add(project)
        await self.db.flush()
        return project

    async def get_for_user(self, project_id: str, uid: str) -> Project | None:
        """按用户读取 Project。"""
        return await self.db.scalar(select(Project).where(Project.id == project_id, Project.uid == str(uid)))

    async def lock_active_for_user(self, project_id: str, uid: str) -> Project | None:
        """锁定当前用户的 active Project。"""
        return await self.db.scalar(
            select(Project)
            .where(
                Project.id == project_id,
                Project.uid == str(uid),
                Project.status == "active",
            )
            .with_for_update()
        )

    async def lock_active_selectable_for_user(self, project_id: str, uid: str) -> Project | None:
        """锁定当前用户可管理的 active selectable Project。"""
        return await self.db.scalar(
            select(Project)
            .where(
                Project.id == project_id,
                Project.uid == str(uid),
                Project.selection_status == "selectable",
                Project.status == "active",
            )
            .with_for_update()
        )

    async def get_by_idempotency_key(self, idempotency_key: str, uid: str) -> Project | None:
        """按用户和幂等键读取 Project。"""
        return await self.db.scalar(
            select(Project).where(Project.uid == str(uid), Project.idempotency_key == idempotency_key)
        )

    async def list_selectable_for_user(self, uid: str) -> list[Project]:
        """列出用户可选择的 Project。"""
        result = await self.db.execute(
            select(Project)
            .where(
                Project.uid == str(uid),
                Project.selection_status == "selectable",
                Project.status == "active",
            )
            .order_by(Project.updated_at.desc(), Project.id.desc())
        )
        return list(result.scalars().all())

    async def list_workspace_project_paths_for_user(self, uid: str) -> list[str]:
        """列出个人空间可见的项目目录及仍被迁移会话引用的旧目录。"""
        result = await self.db.execute(
            select(Project.workdir_path)
            .where(
                Project.uid == str(uid),
                Project.selection_status == "selectable",
                Project.status.in_(["active", "deleted"]),
                # 已删除的容器根绑定不再让所有匿名会话目录可见。
                or_(Project.status == "active", Project.workdir_path != "projects"),
            )
            .distinct()
        )
        visible = set(result.scalars().all())
        implicit_projects = (
            await self.db.execute(
                select(Project.id, Project.workdir_path).where(
                    Project.uid == str(uid),
                    Project.selection_status == "implicit",
                    Project.status.in_(["active", "deleted"]),
                    Project.workdir_path != "projects",
                )
            )
        ).all()
        if not implicit_projects:
            return list(visible)
        implicit_paths = dict(implicit_projects)
        histories = await self.db.scalars(
            select(Conversation.extra_metadata).where(Conversation.uid == str(uid), Conversation.status != "deleted")
        )
        for metadata in histories:
            for move in (metadata or {}).get("project_moves", []):
                if not isinstance(move, dict):
                    continue
                source_id = move.get("source_project_id")
                source_path = move.get("source_workdir_path")
                if (
                    isinstance(source_id, str)
                    and isinstance(source_path, str)
                    and implicit_paths.get(source_id) == source_path
                ):
                    visible.add(source_path)
        return list(visible)

    async def list_all_workdir_paths_for_user(self, uid: str) -> set[str]:
        """为新建目录避开已登记但尚未物化的 Project 路径。"""
        result = await self.db.scalars(select(Project.workdir_path).where(Project.uid == str(uid)))
        return set(result.all())

    async def list_active_workdir_paths_for_user(self, uid: str) -> set[str]:
        """返回当前仍能读取目录的 Project 路径。"""
        result = await self.db.scalars(
            select(Project.workdir_path).where(Project.uid == str(uid), Project.status == "active")
        )
        return set(result.all())

    async def has_other_workdir_overlap(self, project: Project) -> bool:
        """按 managed 子目录归属检查共享绑定，并锁住上层的工作准入。"""
        path = project.workdir_path
        other_projects = (
            await self.db.scalars(
                select(Project).where(Project.uid == project.uid, Project.id != project.id).order_by(Project.id)
            )
        ).all()
        for other in other_projects:
            other_path = other.workdir_path
            if not (other_path == path or other_path.startswith(f"{path}/") or path.startswith(f"{other_path}/")):
                continue
            if project.directory_mode == "managed" and path.startswith(f"{other_path}/"):
                # 新会话/子线程先锁 Project，普通请求锁 Conversation；持锁覆盖检查与文件清理。
                await self.db.execute(select(Project.id).where(Project.id == other.id).with_for_update())
                await self.db.execute(
                    select(Conversation.id)
                    .where(Conversation.project_id == other.id)
                    .order_by(Conversation.id)
                    .with_for_update()
                )
                if await self.has_active_project_work(other):
                    return True
                continue
            return True
        return False

    async def has_active_project_work(self, project: Project) -> bool:
        """清理前阻止未结束的 Run、运行时清理和排队请求。"""
        conversations = select(Conversation.id).where(
            Conversation.uid == project.uid, Conversation.project_id == project.id
        )
        threads = select(Conversation.thread_id).where(
            Conversation.uid == project.uid, Conversation.project_id == project.id
        )
        active_run = await self.db.scalar(
            select(
                exists().where(
                    AgentRun.uid == project.uid,
                    or_(AgentRun.conversation_id.in_(conversations), AgentRun.conversation_thread_id.in_(threads)),
                    or_(AgentRun.status.notin_(TERMINAL_RUN_STATUSES), AgentRun.runtime_cleanup_pending.is_(True)),
                )
            )
        )
        queued_request = await self.db.scalar(
            select(
                exists().where(
                    AgentRunRequest.uid == project.uid,
                    AgentRunRequest.conversation_thread_id.in_(threads),
                    AgentRunRequest.status == "queued",
                )
            )
        )
        return bool(active_run or queued_request)

    async def has_unsettled_conversation_work(
        self, *, uid: str, root_thread_id: str, conversations: list[Conversation]
    ) -> bool:
        """迁移前检查整棵会话树的运行、排队、清理和待回复中断。"""
        ids = [conversation.id for conversation in conversations]
        thread_ids = [conversation.thread_id for conversation in conversations]
        active_run = await self.db.scalar(
            select(
                exists().where(
                    AgentRun.uid == str(uid),
                    or_(
                        AgentRun.conversation_id.in_(ids),
                        AgentRun.conversation_thread_id.in_(thread_ids),
                        AgentRun.runtime_scope_id == root_thread_id,
                    ),
                    or_(AgentRun.status.notin_(TERMINAL_RUN_STATUSES), AgentRun.runtime_cleanup_pending.is_(True)),
                )
            )
        )
        queued_request = await self.db.scalar(
            select(
                exists().where(
                    AgentRunRequest.uid == str(uid),
                    AgentRunRequest.conversation_thread_id.in_(thread_ids),
                    AgentRunRequest.status == "queued",
                )
            )
        )
        latest_runs = (
            select(
                AgentRun.status,
                func.row_number()
                .over(
                    partition_by=AgentRun.conversation_thread_id,
                    order_by=(AgentRun.created_at.desc(), AgentRun.id.desc()),
                )
                .label("rn"),
            )
            .where(AgentRun.uid == str(uid), AgentRun.conversation_thread_id.in_(thread_ids))
            .subquery()
        )
        interrupted = await self.db.scalar(
            select(exists().where(latest_runs.c.rn == 1, latest_runs.c.status == "interrupted"))
        )
        return bool(active_run or queued_request or interrupted)

    async def has_historical_workdir_references(self, project: Project) -> bool:
        """防止清理仍被已迁出会话历史附件引用的旧目录。"""
        records = await self.db.execute(
            select(Conversation.project_id, Conversation.extra_metadata).where(
                Conversation.uid == project.uid,
                Conversation.status != "deleted",
                Conversation.project_id != project.id,
            )
        )
        for current_project_id, metadata in records:
            for move in (metadata or {}).get("project_moves", []):
                if (
                    isinstance(move, dict)
                    and move.get("source_project_id") == project.id
                    and move.get("source_workdir_path") == project.workdir_path
                ):
                    return True
        return False

    async def is_sole_root_conversation(self, project: Project, thread_id: str) -> bool:
        """只允许唯一普通会话提升项目，持久子会话关系不构成第二个入口。"""
        child = exists().where(SubagentThread.child_conversation_id == Conversation.id)
        roots = list(
            (
                await self.db.scalars(
                    select(Conversation.thread_id).where(
                        Conversation.project_id == project.id,
                        Conversation.status != "subagent",
                        ~child,
                    )
                )
            ).all()
        )
        return roots == [thread_id]

    async def has_exclusive_conversation(self, project: Project, thread_id: str) -> bool:
        """确认目录仅绑定指定会话，历史会话也计入共享关系。"""
        threads = (
            await self.db.scalars(select(Conversation.thread_id).where(Conversation.project_id == project.id))
        ).all()
        return threads == [thread_id]

    async def soft_delete_with_conversations(self, project: Project, *, deleted_at: datetime) -> int:
        """在调用方事务内软删除 Project 及其全部 Conversation。"""
        result = await self.db.execute(
            update(Conversation)
            .where(Conversation.uid == project.uid, Conversation.project_id == project.id)
            .values(status="deleted", updated_at=deleted_at)
        )
        project.status = "deleted"
        project.deleted_at = deleted_at
        project.updated_at = deleted_at
        await self.db.flush()
        return int(result.rowcount or 0)
