"""Project 持久化 Repository。"""

from datetime import datetime

from sqlalchemy import exists, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.storage.postgres.models_business import AgentRun, AgentRunRequest, Conversation, Project
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

    async def list_selectable_workdir_paths_for_user(self, uid: str) -> list[str]:
        """列出用户已选择 Project 的去重 Workdir 路径。"""
        result = await self.db.execute(
            select(Project.workdir_path)
            .where(
                Project.uid == str(uid),
                Project.selection_status == "selectable",
                Project.status == "active",
            )
            .distinct()
        )
        return list(result.scalars().all())

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
        """阻止清理仍共享或曾与其他项目同时共享的目录。"""
        path = project.workdir_path
        other_projects = (
            await self.db.scalars(select(Project).where(Project.uid == project.uid, Project.id != project.id))
        ).all()
        for other in other_projects:
            other_path = other.workdir_path
            if not (other_path == path or other_path.startswith(f"{path}/") or path.startswith(f"{other_path}/")):
                continue
            if (
                path.startswith(f"{other_path}/")
                and other.status == "deleted"
                and other.deleted_at is not None
                and project.created_at is not None
                and other.deleted_at < project.created_at
            ):
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
