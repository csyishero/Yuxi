"""Project 创建、选择与目录生命周期用例。"""

from __future__ import annotations

import asyncio
import re
import uuid

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from yuxi.repositories.conversation_repository import ConversationRepository
from yuxi.repositories.project_repository import ProjectRepository
from yuxi.repositories.task_repository import TaskRepository
from yuxi.services.task_service import Tasker, TaskContext
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import Project
from yuxi.utils.datetime_utils import utc_now_naive
from yuxi.workspace.paths import (
    allocate_default_user_workdir_path,
    allocate_named_user_workdir_path,
    ensure_bound_user_workdir,
    normalize_managed_workdir_path,
)
from yuxi.workspace.filesystem import Workspace

MAX_PROJECT_NAME_LENGTH = 255


async def _lock_project_workdir_changes(*, db, uid: str) -> None:
    """串行化同一用户的目录分配与清理。"""
    await db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 0))"),
        {"lock_key": f"project-workdir:{uid}"},
    )


def _normalize_project_name(name: str | None, *, required: bool) -> str | None:
    """规范化并校验 Project 名称。"""
    normalized_name = (name or "").strip()
    if required and not normalized_name:
        raise HTTPException(status_code=422, detail="项目名称不能为空")
    if len(normalized_name) > MAX_PROJECT_NAME_LENGTH:
        raise HTTPException(status_code=422, detail="项目名称过长")
    return normalized_name or None


def _require_matching_creation_intent(project: Project, *, name: str) -> None:
    """要求已有 Project 仍有效且匹配当前幂等创建意图。"""
    if project.status == "deleted":
        raise HTTPException(status_code=409, detail="request_id 已用于已删除的 Project")
    if project.name != name or project.directory_mode != "managed":
        raise HTTPException(status_code=409, detail="request_id 已用于其他 Project 创建意图")


async def create_project_record(
    *,
    uid: str,
    name: str | None,
    directory_mode: str,
    selection_status: str,
    db,
    workdir_path: str | None = None,
    idempotency_key: str | None = None,
) -> Project:
    """在当前事务内创建 Project，但不提交或物化 managed 目录。"""
    normalized_name = _normalize_project_name(name, required=selection_status == "selectable")
    if directory_mode != "managed":
        raise HTTPException(status_code=422, detail="新建项目只支持 managed 目录")
    if selection_status not in {"implicit", "selectable"}:
        raise HTTPException(status_code=422, detail="selection_status 非法")

    project_id = str(uuid.uuid4())
    if workdir_path is not None:
        raise HTTPException(status_code=422, detail="managed Project 不接受 workdir_path")
    if selection_status == "selectable":
        await _lock_project_workdir_changes(db=db, uid=str(uid))
        repository = ProjectRepository(db)
        normalized_path = allocate_named_user_workdir_path(
            str(uid),
            project_id,
            normalized_name,
            reserved_paths=await repository.list_all_workdir_paths_for_user(str(uid)),
        )
        active_paths = await repository.list_active_workdir_paths_for_user(str(uid))
        if any(
            other == normalized_path
            or other.startswith(f"{normalized_path}/")
            or normalized_path.startswith(f"{other}/")
            for other in active_paths
        ):
            raise HTTPException(status_code=409, detail="已有项目绑定新目录的上层或下层路径")
    else:
        normalized_path = allocate_default_user_workdir_path(str(uid), project_id)

    project = Project(
        id=project_id,
        uid=str(uid),
        name=normalized_name,
        selection_status=selection_status,
        workdir_path=normalized_path,
        directory_mode=directory_mode,
        idempotency_key=idempotency_key.strip() if idempotency_key else None,
    )
    return await ProjectRepository(db).add(project)


async def create_implicit_project(*, uid: str, db, idempotency_key: str | None = None) -> Project:
    """为新 Conversation 创建 implicit managed Project。"""
    return await create_project_record(
        uid=uid,
        name=None,
        directory_mode="managed",
        selection_status="implicit",
        db=db,
        idempotency_key=idempotency_key,
    )


async def create_project_view(
    *, uid: str, request_id: str, name: str, directory_mode: str, workdir_path: str | None, db
) -> dict:
    """幂等创建 selectable Project。"""
    normalized_request_id = (request_id or "").strip()
    if not normalized_request_id:
        raise HTTPException(status_code=422, detail="request_id 不能为空")
    if directory_mode != "managed" or workdir_path is not None:
        raise HTTPException(status_code=422, detail="新建项目只支持自动创建专属目录")
    repository = ProjectRepository(db)
    existing = await repository.get_by_idempotency_key(normalized_request_id, str(uid))
    normalized_name = _normalize_project_name(name, required=True)
    if existing is not None:
        _require_matching_creation_intent(existing, name=normalized_name)
        ensure_bound_user_workdir(str(uid), existing.workdir_path)
        return existing.to_dict()

    try:
        project = await create_project_record(
            uid=uid,
            name=name,
            directory_mode=directory_mode,
            selection_status="selectable",
            workdir_path=None,
            db=db,
            idempotency_key=normalized_request_id,
        )
        await db.commit()
    except IntegrityError:
        await db.rollback()
        replay = await repository.get_by_idempotency_key(normalized_request_id, str(uid))
        if replay is None:
            raise HTTPException(status_code=409, detail="Project 创建冲突")
        _require_matching_creation_intent(replay, name=normalized_name)
        project = replay
    ensure_bound_user_workdir(str(uid), project.workdir_path)
    return project.to_dict()


async def list_projects_view(*, uid: str, db) -> list[dict]:
    """列出当前用户可选择的 Project。"""
    projects = await ProjectRepository(db).list_selectable_for_user(str(uid))
    return [project.to_dict() for project in projects]


async def rename_project_view(*, uid: str, project_id: str, name: str, db) -> dict:
    """重命名当前用户的 selectable Project。"""
    normalized_name = _normalize_project_name(name, required=True)
    repository = ProjectRepository(db)
    project = await repository.lock_active_selectable_for_user(project_id, str(uid))
    if project is None:
        raise HTTPException(status_code=404, detail="Project 不存在")

    project.name = normalized_name
    project.updated_at = utc_now_naive()
    await db.commit()
    await db.refresh(project)
    return project.to_dict()


async def _validate_managed_directory_deletion(*, project: Project, repository: ProjectRepository) -> None:
    if project.directory_mode != "managed":
        raise HTTPException(status_code=422, detail="关联已有目录的项目不能删除文件夹")
    try:
        normalized_path = normalize_managed_workdir_path(project.workdir_path)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail="项目目录不符合安全清理规则") from exc
    directory_name = normalized_path.split("/", 1)[1]
    if directory_name != project.id and not re.search(rf"_{re.escape(project.id[:8])}(?:-[1-9]\d*)?$", directory_name):
        raise HTTPException(status_code=409, detail="项目目录与项目 ID 不匹配，不能删除文件夹")
    if await repository.has_other_workdir_overlap(project):
        raise HTTPException(status_code=409, detail="项目目录与其他项目共用或重叠，不能删除文件夹")
    if await repository.has_active_project_work(project):
        raise HTTPException(status_code=409, detail="项目中仍有运行或排队的任务，请结束后重试")


async def delete_project_view(*, uid: str, project_id: str, db, delete_workdir: bool = False) -> dict:
    """软删除 Project 与 Conversation，可选择登记持久化目录清理任务。"""
    repository = ProjectRepository(db)
    if delete_workdir:
        await _lock_project_workdir_changes(db=db, uid=str(uid))
    project = await repository.lock_active_selectable_for_user(project_id, str(uid))
    if project is None:
        raise HTTPException(status_code=404, detail="Project 不存在")

    return await _delete_locked_project(project=project, repository=repository, db=db, delete_workdir=delete_workdir)


async def delete_implicit_project_view(*, uid: str, project_id: str, thread_id: str, db) -> dict:
    """删除独占 implicit Project 的会话，并登记文件夹清理任务。"""
    await _lock_project_workdir_changes(db=db, uid=str(uid))
    repository = ProjectRepository(db)
    project = await repository.lock_active_for_user(project_id, str(uid))
    if project is None:
        raise HTTPException(status_code=404, detail="对话文件夹所属项目不存在")
    if project.selection_status != "implicit":
        raise HTTPException(status_code=422, detail="项目内的对话不能单独删除共享文件夹")
    conversation = await ConversationRepository(db).lock_conversation_by_thread_id(thread_id)
    if (
        conversation is None
        or conversation.uid != str(uid)
        or conversation.project_id != project.id
        or conversation.status == "deleted"
    ):
        raise HTTPException(status_code=404, detail="对话线程不存在")
    if not await repository.has_exclusive_conversation(project, thread_id):
        raise HTTPException(status_code=409, detail="文件夹仍被其他会话使用，请取消删除文件夹选项")
    return await _delete_locked_project(project=project, repository=repository, db=db, delete_workdir=True)


async def _delete_locked_project(*, project: Project, repository: ProjectRepository, db, delete_workdir: bool) -> dict:
    """在调用方持有项目锁时提交删除事实，再投递目录清理任务。"""
    uid = str(project.uid)
    task = None
    tasker = Tasker()
    if delete_workdir:
        await _validate_managed_directory_deletion(project=project, repository=repository)
        task = await tasker.create_in_session(
            db,
            name="清理对话文件夹" if project.selection_status == "implicit" else f"清理项目文件夹：{project.name}",
            task_type="project_workdir_delete",
            payload={"uid": str(uid), "project_id": project.id},
        )

    deleted_conversations = await repository.soft_delete_with_conversations(
        project,
        deleted_at=utc_now_naive(),
    )
    await db.commit()
    if task is not None:
        await tasker.publish(task)
    return {
        "message": "删除成功",
        "project_id": project.id,
        "deleted_conversations": deleted_conversations,
        "workdir_delete_task_id": task.id if task else None,
    }


async def get_project_workdir_deletion_view(*, uid: str, project_id: str, db) -> dict:
    """用户只能读取自己项目的文件清理状态。"""
    project = await ProjectRepository(db).get_for_user(project_id, str(uid))
    if project is None:
        raise HTTPException(status_code=404, detail="Project 不存在")
    task = await Tasker().find_task_by_payload(
        task_type="project_workdir_delete", payload_match={"uid": str(uid), "project_id": project_id}
    )
    if task is None:
        raise HTTPException(status_code=404, detail="文件夹清理任务不存在")
    return {"task_id": task.id, "status": task.status, "error": task.error}


async def list_project_workdir_deletions_view(*, uid: str) -> list[dict]:
    """列出用户每个项目最新的目录清理任务，供响应丢失后恢复状态。"""
    tasks = await TaskRepository().list_project_workdir_deletions_for_user(str(uid))
    return [
        {"project_id": task.payload["project_id"], "task_id": task.id, "status": task.status, "error": task.error}
        for task in tasks
    ]


async def retry_project_workdir_deletion_view(*, uid: str, project_id: str, db) -> dict:
    await _lock_project_workdir_changes(db=db, uid=str(uid))
    repository = ProjectRepository(db)
    project = await repository.get_for_user(project_id, str(uid))
    if project is None or project.status != "deleted":
        raise HTTPException(status_code=404, detail="已删除的 Project 不存在")
    previous = await Tasker().find_task_by_payload(
        task_type="project_workdir_delete", payload_match={"uid": str(uid), "project_id": project_id}
    )
    if previous is None or previous.status not in {"failed", "cancelled"}:
        raise HTTPException(status_code=409, detail="只有清理未完成的项目可以重试")
    await _validate_managed_directory_deletion(project=project, repository=repository)
    tasker = Tasker()
    task = await tasker.create_in_session(
        db,
        name="重试清理对话文件夹" if project.selection_status == "implicit" else f"重试清理项目文件夹：{project.name}",
        task_type="project_workdir_delete",
        payload={"uid": str(uid), "project_id": project.id},
    )
    await db.commit()
    await tasker.publish(task)
    return {"task_id": task.id, "status": task.status}


async def run_project_workdir_delete(context: TaskContext) -> dict:
    """在用户级锁内重新核对所有权和活动执行，再以 no-follow 语义清理。"""
    uid = str(context.payload["uid"])
    project_id = str(context.payload["project_id"])
    await context.raise_if_cancelled()
    async with pg_manager.get_async_session_context() as db:
        await _lock_project_workdir_changes(db=db, uid=uid)
        repository = ProjectRepository(db)
        project = await repository.get_for_user(project_id, uid)
        if project is None or project.status != "deleted":
            raise ValueError("已删除的 Project 不存在")
        await _validate_managed_directory_deletion(project=project, repository=repository)
        path = normalize_managed_workdir_path(project.workdir_path)
        await _delete_project_workdir_and_wait(uid, path)
    return {"deleted_workdir": path}


async def _delete_project_workdir_and_wait(uid: str, path: str) -> None:
    """取消任务时仍等待文件线程结束，之后才能释放目录锁和任务状态。"""
    deletion = asyncio.create_task(asyncio.to_thread(Workspace(uid).delete_project_workdir, f"/{path}"))
    try:
        await asyncio.shield(deletion)
    except asyncio.CancelledError:
        while not deletion.done():
            try:
                await asyncio.shield(deletion)
            except asyncio.CancelledError:
                continue
        deletion.exception()
        raise
