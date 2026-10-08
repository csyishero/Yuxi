"""Project HTTP 适配层。"""

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from server.utils.auth_middleware import get_db, get_required_user
from yuxi.services.project_service import (
    create_project_view,
    delete_project_view,
    get_project_workdir_deletion_view,
    list_project_workdir_deletions_view,
    list_projects_view,
    rename_project_view,
    retry_project_workdir_deletion_view,
)
from yuxi.storage.postgres.models_business import User

projects = APIRouter(prefix="/projects", tags=["projects"])


class ProjectWorkdirCreate(BaseModel):
    """Project Workdir 创建意图。"""

    model_config = ConfigDict(extra="forbid")

    mode: str = "managed"
    path: str | None = None


class ProjectCreate(BaseModel):
    """独立 Project 创建请求。"""

    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(..., min_length=1, max_length=128)
    name: str
    workdir: ProjectWorkdirCreate


class ProjectUpdate(BaseModel):
    """Project 可修改字段。"""

    model_config = ConfigDict(extra="forbid")

    name: str


@projects.get("")
async def list_projects(
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    """列出当前用户可选择的 Project。"""
    return await list_projects_view(uid=str(current_user.uid), db=db)


@projects.post("")
async def create_project(
    payload: ProjectCreate,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    """创建拥有专属目录的 Project。"""
    return await create_project_view(
        uid=str(current_user.uid),
        request_id=payload.request_id,
        name=payload.name,
        directory_mode=payload.workdir.mode,
        workdir_path=payload.workdir.path,
        db=db,
    )


@projects.get("/workdir-deletions")
async def list_project_workdir_deletions(
    current_user: User = Depends(get_required_user),
):
    """恢复当前用户的项目文件夹清理状态。"""
    return await list_project_workdir_deletions_view(uid=str(current_user.uid))


@projects.put("/{project_id}")
async def rename_project(
    project_id: str,
    payload: ProjectUpdate,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    """重命名当前用户的 Project。"""
    return await rename_project_view(uid=str(current_user.uid), project_id=project_id, name=payload.name, db=db)


@projects.delete("/{project_id}")
async def delete_project(
    project_id: str,
    delete_workdir: bool = Query(False),
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    """删除 Project；显式要求时异步清理其专属目录。"""
    return await delete_project_view(
        uid=str(current_user.uid), project_id=project_id, db=db, delete_workdir=delete_workdir
    )


@projects.get("/{project_id}/workdir-deletion")
async def get_project_workdir_deletion(
    project_id: str,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await get_project_workdir_deletion_view(uid=str(current_user.uid), project_id=project_id, db=db)


@projects.post("/{project_id}/workdir-deletion/retry")
async def retry_project_workdir_deletion(
    project_id: str,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await retry_project_workdir_deletion_view(uid=str(current_user.uid), project_id=project_id, db=db)
