"""Skills 管理路由"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from server.utils.auth_middleware import get_admin_user, get_db, get_required_user
from yuxi.agents.skills.service import (
    confirm_personal_skill_install_draft,
    confirm_skill_install_draft,
    create_personal_skill_node,
    create_skill_node,
    delete_skill,
    delete_skill_node,
    delete_skills_batch,
    delete_personal_skill,
    delete_personal_skill_node,
    discard_skill_install_draft,
    export_skill_zip,
    export_personal_skill_zip,
    get_allowed_skill_access_levels,
    get_manageable_personal_skill_owner,
    get_manageable_skill_or_raise,
    get_skill_dependency_options,
    get_skill_tree,
    get_personal_skill_tree,
    init_builtin_skills,
    is_builtin_skill,
    list_accessible_skills,
    list_skill_cards_for_user,
    list_personal_skill_catalog,
    list_skills,
    list_visible_skills_for_management,
    prepare_remote_skill_install,
    prepare_skill_upload,
    read_personal_skill_file,
    read_skill_file,
    update_skill_dependencies,
    update_personal_skill_dependencies,
    update_skill_enabled,
    update_skill_file,
    update_personal_skill_file,
    update_skill_share_config,
    user_can_manage_skill,
)
from yuxi.agents.skills.sharing import (
    approve_skill_share_request,
    export_skill_share_snapshot,
    list_skill_share_snapshot_tree,
    list_skill_share_requests,
    read_skill_share_snapshot,
    reject_skill_share_request,
    publish_personal_skill_directly,
    submit_skill_share_request,
)
from yuxi.permissions import resolve_skill_permission
from yuxi.agents.skills.remote_install import list_remote_skills, search_remote_skills
from yuxi.storage.postgres.models_business import User
from yuxi.utils.logging_config import logger

skills = APIRouter(prefix="/system/skills", tags=["skills"])
user_skills = APIRouter(prefix="/skills", tags=["skills"])


class ShareConfigPayload(BaseModel):
    share_config: dict | None = Field(None, description="共享权限配置")


class SkillEnabledUpdateRequest(BaseModel):
    enabled: bool = Field(..., description="是否启用")


class SkillNodeCreateRequest(BaseModel):
    path: str = Field(..., description="相对 skill 根目录的路径")
    is_dir: bool = Field(False, description="是否创建目录")
    content: str | None = Field("", description="文件内容（仅文件创建时生效）")


class SkillFileUpdateRequest(BaseModel):
    path: str = Field(..., description="相对 skill 根目录的路径")
    content: str = Field(..., description="文件内容")


class SkillDependenciesUpdateRequest(BaseModel):
    tool_dependencies: list[str] = Field(default_factory=list, description="依赖的内置工具列表")
    mcp_dependencies: list[str] = Field(default_factory=list, description="依赖的 MCP 服务列表")
    skill_dependencies: list[str] = Field(default_factory=list, description="依赖的其他 skill slug 列表")


class RemoteSkillSourceRequest(BaseModel):
    source: str = Field(..., description="远程 Skill 来源，如 owner/repo 或允许的 HTTPS URL")


class RemoteSkillPrepareRequest(RemoteSkillSourceRequest):
    skills: list[str] = Field(..., description="需要安装的 skill 名称列表")


class RemoteSkillSearchRequest(BaseModel):
    query: str = Field(..., description="搜索关键字")


class SkillBatchDeleteRequest(BaseModel):
    slugs: list[str] = Field(..., max_length=50, description="需要批量删除的 skill slug 列表，最多支持 50 个")


class _DraftConfirmRequestBase(BaseModel):
    slugs: list[str] | None = Field(None, description="本次确认安装的 Skill slug")


class SkillDraftConfirmRequest(_DraftConfirmRequestBase):
    share_config: dict | None = Field(None, description="共享权限配置")


class PersonalSkillDraftConfirmRequest(_DraftConfirmRequestBase):
    pass


class SkillShareReviewRequest(BaseModel):
    department_ids: list[int] = Field(default_factory=list, description="批准发布的部门 ID")
    read_scope: dict | None = Field(None, description="指定部门或人员的读取范围")
    note: str = Field("", max_length=2000, description="审核意见")


def _raise_from_value_error(e: ValueError) -> None:
    message = str(e)
    status_code = 404 if "不存在" in message or "无权" in message else 400
    raise HTTPException(status_code=status_code, detail=message)


def _cleanup_export_file(path: str) -> None:
    try:
        Path(path).unlink(missing_ok=True)
    except Exception as e:
        logger.warning(f"Failed to cleanup exported skill archive '{path}': {e}")


def _summarize_results(results: list[dict]) -> dict[str, int]:
    return {
        "total": len(results),
        "success": sum(1 for item in results if item.get("success")),
        "failed": sum(1 for item in results if not item.get("success")),
    }


def _serialize_skill_for_user(item, user: User) -> dict:
    data = item.to_dict()
    data["can_manage"] = user_can_manage_skill(user, item)
    data["effective_permission"] = resolve_skill_permission(user, item).value
    data["is_builtin"] = is_builtin_skill(item)
    return data


@user_skills.get("")
async def list_skill_cards_route(
    audience_search: str | None = Query(None, max_length=100),
    audience_department_id: int | None = Query(None, ge=1),
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        items = await list_skill_cards_for_user(
            db,
            current_user,
            audience_search=audience_search,
            audience_department_id=audience_department_id,
        )
        return {
            "success": True,
            "data": [_serialize_skill_for_user(item, current_user) for item in items],
            "allowed_access_levels": get_allowed_skill_access_levels(current_user),
        }
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to list Skill cards: {e}")
        raise HTTPException(status_code=500, detail="获取 Skill 列表失败")


@user_skills.get("/personal-catalog")
async def list_personal_skill_catalog_route(
    department_id: int | None = Query(None, ge=1),
    owner_search: str | None = Query(None, max_length=100),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """管理员按授权部门和人员浏览个人 Skill。"""
    try:
        data = await list_personal_skill_catalog(
            db,
            operator=current_user,
            department_id=department_id,
            owner_search=owner_search,
            offset=offset,
            limit=limit,
        )
        return {"success": True, "data": data}
    except ValueError as exc:
        _raise_from_value_error(exc)


@user_skills.get("/personal-catalog/{owner_uid}/{slug}/file")
async def read_personal_skill_catalog_file_route(
    owner_uid: str,
    slug: str,
    path: str = Query(..., description="相对 Skill 根目录的文本路径"),
    current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """管理员预览授权部门内有效用户的个人 Skill 文件。"""
    try:
        await get_manageable_personal_skill_owner(db, operator=current_user, owner_uid=owner_uid)
        return {"success": True, "data": await read_personal_skill_file(owner_uid, slug, path)}
    except (ValueError, PermissionError) as exc:
        _raise_from_value_error(ValueError(str(exc)))


@user_skills.post("/personal-catalog/{owner_uid}/{slug}/publish")
async def publish_personal_skill_directly_route(
    owner_uid: str,
    slug: str,
    payload: SkillShareReviewRequest,
    current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """管理员将权限范围内的个人 Skill 快照直接发布为共享副本。"""
    try:
        item = await publish_personal_skill_directly(
            db,
            owner_uid=owner_uid,
            slug=slug,
            department_ids=payload.department_ids,
            read_scope=payload.read_scope,
            note=payload.note,
            operator=current_user,
        )
        return {"success": True, "data": item.to_dict()}
    except ValueError as exc:
        _raise_from_value_error(exc)


@user_skills.get("/accessible")
async def list_accessible_skills_route(
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        items = await list_accessible_skills(db, current_user)
        return {"success": True, "data": [_serialize_skill_for_user(item, current_user) for item in items]}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to list accessible skills: {e}")
        raise HTTPException(status_code=500, detail="获取可访问 Skills 失败")


@user_skills.post("/import/prepare")
async def prepare_skill_upload_route(
    file: UploadFile = File(...),
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        data = await prepare_skill_upload(
            db,
            filename=file.filename or "",
            file_bytes=await file.read(),
            operator=current_user,
        )
        return {"success": True, "data": data}
    except ValueError as e:
        _raise_from_value_error(e)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to prepare skill upload: {e}")
        raise HTTPException(status_code=500, detail="解析上传 Skill 失败")


@user_skills.post("/remote/list")
async def list_remote_skills_route(
    payload: RemoteSkillSourceRequest,
    _current_user: User = Depends(get_required_user),
):
    try:
        return {"success": True, "data": await list_remote_skills(payload.source)}
    except ValueError as e:
        _raise_from_value_error(e)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to list remote skills from '{payload.source}': {e}")
        raise HTTPException(status_code=500, detail="获取远程 skills 列表失败")


@user_skills.post("/remote/search")
async def search_remote_skills_route(
    payload: RemoteSkillSearchRequest, _current_user: User = Depends(get_required_user)
):
    try:
        return {"success": True, "data": await search_remote_skills(payload.query)}
    except ValueError as e:
        _raise_from_value_error(e)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to search remote skills with query '{payload.query}': {e}")
        raise HTTPException(status_code=500, detail="搜索远程 skills 失败")


@user_skills.post("/remote/prepare")
async def prepare_remote_skills_route(
    payload: RemoteSkillPrepareRequest,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        data = await prepare_remote_skill_install(
            db,
            source=payload.source,
            skills=payload.skills,
            operator=current_user,
        )
        return {"success": True, "data": data}
    except ValueError as e:
        _raise_from_value_error(e)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to prepare remote skills from '{payload.source}': {e}")
        raise HTTPException(status_code=500, detail="解析远程 Skills 失败")


@user_skills.post("/install-drafts/{draft_id}/confirm")
async def confirm_skill_install_draft_route(
    draft_id: str,
    payload: SkillDraftConfirmRequest,
    current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        results = await confirm_skill_install_draft(
            db,
            draft_id=draft_id,
            share_config=payload.share_config,
            slugs=payload.slugs,
            operator=current_user,
        )
        return {"success": True, "data": results, "summary": _summarize_results(results)}
    except ValueError as e:
        _raise_from_value_error(e)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to confirm skill install draft '{draft_id}': {e}")
        raise HTTPException(status_code=500, detail="确认安装 Skill 失败")


@user_skills.post("/personal/install-drafts/{draft_id}/confirm")
async def confirm_personal_skill_install_draft_route(
    draft_id: str,
    payload: PersonalSkillDraftConfirmRequest,
    current_user: User = Depends(get_required_user),
):
    try:
        results = await confirm_personal_skill_install_draft(
            draft_id=draft_id,
            slugs=payload.slugs,
            operator=current_user,
        )
        return {"success": True, "data": results, "summary": _summarize_results(results)}
    except ValueError as e:
        _raise_from_value_error(e)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to confirm personal Skill draft '{draft_id}': {e}")
        raise HTTPException(status_code=500, detail="确认安装个人 Skill 失败")


@user_skills.post("/personal/{slug}/share-request")
async def submit_skill_share_request_route(
    slug: str,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    """提交当前用户个人 Skill 的共享申请。"""
    try:
        item = await submit_skill_share_request(db, slug=slug, operator=current_user)
        return {"success": True, "data": item.to_dict()}
    except ValueError as e:
        _raise_from_value_error(e)
    except Exception as e:
        logger.error(f"Failed to submit Skill share request for '{slug}': {e}")
        raise HTTPException(status_code=500, detail="提交共享申请失败")


@user_skills.get("/share-requests")
async def list_skill_share_requests_route(
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    """列出当前用户可见的共享申请。"""
    items = await list_skill_share_requests(db, operator=current_user)
    return {"success": True, "data": [item.to_dict() for item in items]}


@user_skills.get("/share-requests/{request_id}/snapshot")
async def read_skill_share_snapshot_route(
    request_id: str,
    path: str = Query("SKILL.md", description="审核快照内的文本文件路径"),
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    """读取提交时的审核快照。"""
    try:
        content = await read_skill_share_snapshot(db, request_id=request_id, operator=current_user, relative_path=path)
        return {"success": True, "data": {"content": content}}
    except ValueError as e:
        _raise_from_value_error(e)


@user_skills.get("/share-requests/{request_id}/tree")
async def list_skill_share_snapshot_tree_route(
    request_id: str,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    """列出审核快照内的所有文件。"""
    try:
        tree = await list_skill_share_snapshot_tree(db, request_id=request_id, operator=current_user)
        return {"success": True, "data": tree}
    except ValueError as e:
        _raise_from_value_error(e)


@user_skills.get("/share-requests/{request_id}/export")
async def export_skill_share_snapshot_route(
    request_id: str,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """向可审核的管理员下载完整快照。"""
    try:
        path, filename = await export_skill_share_snapshot(db, request_id=request_id, operator=current_user)
        background_tasks.add_task(_cleanup_export_file, path)
        return FileResponse(path=path, media_type="application/zip", filename=filename)
    except ValueError as e:
        _raise_from_value_error(e)


@user_skills.post("/share-requests/{request_id}/approve")
async def approve_skill_share_request_route(
    request_id: str,
    payload: SkillShareReviewRequest,
    current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """由管理员批准部门或人员范围并发布共享副本。"""
    try:
        item = await approve_skill_share_request(
            db,
            request_id=request_id,
            department_ids=payload.department_ids,
            read_scope=payload.read_scope,
            note=payload.note,
            operator=current_user,
        )
        return {"success": True, "data": item.to_dict()}
    except ValueError as e:
        _raise_from_value_error(e)


@user_skills.post("/share-requests/{request_id}/reject")
async def reject_skill_share_request_route(
    request_id: str,
    payload: SkillShareReviewRequest,
    current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """由管理员驳回申请并保留审核记录。"""
    try:
        item = await reject_skill_share_request(
            db,
            request_id=request_id,
            note=payload.note,
            operator=current_user,
        )
        return {"success": True, "data": item.to_dict()}
    except ValueError as e:
        _raise_from_value_error(e)


@user_skills.get("/personal/{slug}/tree")
async def get_personal_skill_tree_route(slug: str, current_user: User = Depends(get_required_user)):
    """列出当前用户个人 Skill 的完整文件树。"""
    try:
        return {"success": True, "data": await get_personal_skill_tree(str(current_user.uid), slug)}
    except (ValueError, PermissionError) as exc:
        _raise_from_value_error(ValueError(str(exc)))


@user_skills.get("/personal/{slug}/export")
async def export_personal_skill_route(
    slug: str,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_required_user),
):
    """导出当前用户个人 Skill。"""
    try:
        export_path, download_name = await export_personal_skill_zip(str(current_user.uid), slug)
        background_tasks.add_task(_cleanup_export_file, export_path)
        return FileResponse(path=export_path, media_type="application/zip", filename=download_name)
    except (ValueError, PermissionError) as exc:
        _raise_from_value_error(ValueError(str(exc)))


@user_skills.get("/personal/{slug}/file")
async def read_personal_skill_file_route(
    slug: str,
    path: str = Query(..., description="相对 Skill 根目录的文件路径"),
    current_user: User = Depends(get_required_user),
):
    try:
        return {
            "success": True,
            "data": await read_personal_skill_file(str(current_user.uid), slug, path),
        }
    except (ValueError, PermissionError) as e:
        _raise_from_value_error(ValueError(str(e)))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to read personal Skill file '{slug}/{path}': {e}")
        raise HTTPException(status_code=500, detail="读取个人 Skill 文件失败")


@user_skills.post("/personal/{slug}/file")
async def create_personal_skill_node_route(
    slug: str,
    payload: SkillNodeCreateRequest,
    current_user: User = Depends(get_required_user),
):
    """在当前用户个人 Skill 内新建文本文件或目录。"""
    try:
        await create_personal_skill_node(
            str(current_user.uid), slug, payload.path, is_dir=payload.is_dir, content=payload.content or ""
        )
        return {"success": True}
    except (ValueError, PermissionError) as exc:
        _raise_from_value_error(ValueError(str(exc)))


@user_skills.put("/personal/{slug}/file")
async def update_personal_skill_file_route(
    slug: str,
    payload: SkillFileUpdateRequest,
    current_user: User = Depends(get_required_user),
):
    """更新当前用户个人 Skill 的已有文本文件。"""
    try:
        await update_personal_skill_file(str(current_user.uid), slug, payload.path, payload.content)
        return {"success": True}
    except (ValueError, PermissionError) as exc:
        _raise_from_value_error(ValueError(str(exc)))


@user_skills.put("/personal/{slug}/dependencies")
async def update_personal_skill_dependencies_route(
    slug: str,
    payload: SkillDependenciesUpdateRequest,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    """仅允许本人配置个人 Skill 依赖。"""
    try:
        item = await update_personal_skill_dependencies(
            db,
            slug=slug,
            tool_dependencies=payload.tool_dependencies,
            mcp_dependencies=payload.mcp_dependencies,
            skill_dependencies=payload.skill_dependencies,
            operator=current_user,
        )
        return {"success": True, "data": {**item.to_dict(), "can_manage": True}}
    except (ValueError, PermissionError) as exc:
        _raise_from_value_error(ValueError(str(exc)))


@user_skills.delete("/personal/{slug}/file")
async def delete_personal_skill_node_route(
    slug: str,
    path: str = Query(..., description="相对 Skill 根目录的文件或目录路径"),
    current_user: User = Depends(get_required_user),
):
    """删除当前用户个人 Skill 的指定文件或目录。"""
    try:
        await delete_personal_skill_node(str(current_user.uid), slug, path)
        return {"success": True}
    except (ValueError, PermissionError) as exc:
        _raise_from_value_error(ValueError(str(exc)))


@user_skills.delete("/personal/{slug}")
async def delete_personal_skill_route(
    slug: str,
    current_user: User = Depends(get_required_user),
):
    try:
        await delete_personal_skill(str(current_user.uid), slug)
        return {"success": True}
    except (ValueError, PermissionError) as e:
        _raise_from_value_error(ValueError(str(e)))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to delete personal Skill '{slug}': {e}")
        raise HTTPException(status_code=500, detail="删除个人 Skill 失败")


@user_skills.delete("/install-drafts/{draft_id}")
async def discard_skill_install_draft_route(draft_id: str, current_user: User = Depends(get_required_user)):
    try:
        await discard_skill_install_draft(draft_id=draft_id, operator=current_user)
        return {"success": True}
    except ValueError as e:
        _raise_from_value_error(e)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to discard skill install draft '{draft_id}': {e}")
        raise HTTPException(status_code=500, detail="取消安装 Skill 失败")


@skills.get("")
async def list_skills_route(
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        items = await list_visible_skills_for_management(db, current_user)
        return {
            "success": True,
            "data": [_serialize_skill_for_user(item, current_user) for item in items],
            "allowed_access_levels": get_allowed_skill_access_levels(current_user),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to list manageable skills: {e}")
        raise HTTPException(status_code=500, detail="获取技能列表失败")


@skills.get("/dependency-options")
async def get_skill_dependency_options_route(
    slug: str | None = Query(None, description="当前 Skill slug"),
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        if slug:
            await get_manageable_skill_or_raise(db, current_user, slug)
        return {"success": True, "data": await get_skill_dependency_options(db, current_user, slug)}
    except ValueError as e:
        _raise_from_value_error(e)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get skill dependency options: {e}")
        raise HTTPException(status_code=500, detail="获取 skill 依赖选项失败")


@skills.get("/builtin")
async def list_builtin_skills_route(
    _current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        items = [item for item in await list_skills(db) if item.source_type == "builtin"]
        return {"success": True, "data": [item.to_dict() for item in items]}
    except ValueError as e:
        _raise_from_value_error(e)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to list builtin skills: {e}")
        raise HTTPException(status_code=500, detail="获取内置 skill 列表失败")


@skills.post("/builtin/sync")
async def sync_builtin_skills_route(
    current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        items = await init_builtin_skills(db, created_by=current_user.uid)
        return {"success": True, "data": [item.to_dict() for item in items]}
    except ValueError as e:
        _raise_from_value_error(e)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to sync builtin skills: {e}")
        raise HTTPException(status_code=500, detail="同步内置 skill 失败")


@skills.put("/{slug}/share-config")
async def update_skill_share_config_route(
    slug: str,
    payload: ShareConfigPayload,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        item = await update_skill_share_config(db, slug=slug, share_config=payload.share_config, operator=current_user)
        return {"success": True, "data": _serialize_skill_for_user(item, current_user)}
    except ValueError as e:
        _raise_from_value_error(e)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update skill share config '{slug}': {e}")
        raise HTTPException(status_code=500, detail="更新 Skill 共享范围失败")


@skills.put("/{slug}/enabled")
async def update_skill_enabled_route(
    slug: str,
    payload: SkillEnabledUpdateRequest,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        item = await update_skill_enabled(db, slug=slug, enabled=payload.enabled, operator=current_user)
        return {"success": True, "data": _serialize_skill_for_user(item, current_user)}
    except ValueError as e:
        _raise_from_value_error(e)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update skill enabled '{slug}': {e}")
        raise HTTPException(status_code=500, detail="更新 Skill 启用状态失败")


@skills.get("/{slug}/tree")
async def get_skill_tree_route(
    slug: str,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        return {"success": True, "data": await get_skill_tree(db, slug=slug, operator=current_user)}
    except ValueError as e:
        _raise_from_value_error(e)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get skill tree '{slug}': {e}")
        raise HTTPException(status_code=500, detail="获取技能目录树失败")


@skills.get("/{slug}/file")
async def get_skill_file_route(
    slug: str,
    path: str = Query(..., description="相对 skill 根目录路径"),
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        return {
            "success": True,
            "data": await read_skill_file(db, slug=slug, relative_path=path, operator=current_user),
        }
    except ValueError as e:
        _raise_from_value_error(e)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to read skill file '{slug}/{path}': {e}")
        raise HTTPException(status_code=500, detail="读取技能文件失败")


@skills.post("/{slug}/file")
async def create_skill_file_route(
    slug: str,
    payload: SkillNodeCreateRequest,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        await create_skill_node(
            db,
            slug=slug,
            relative_path=payload.path,
            is_dir=payload.is_dir,
            content=payload.content,
            updated_by=current_user.uid,
            operator=current_user,
        )
        return {"success": True}
    except ValueError as e:
        _raise_from_value_error(e)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to create skill node '{slug}/{payload.path}': {e}")
        raise HTTPException(status_code=500, detail="创建技能文件失败")


@skills.put("/{slug}/file")
async def update_skill_file_route(
    slug: str,
    payload: SkillFileUpdateRequest,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        await update_skill_file(
            db,
            slug=slug,
            relative_path=payload.path,
            content=payload.content,
            updated_by=current_user.uid,
            operator=current_user,
        )
        return {"success": True}
    except ValueError as e:
        _raise_from_value_error(e)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update skill file '{slug}/{payload.path}': {e}")
        raise HTTPException(status_code=500, detail="更新技能文件失败")


@skills.put("/{slug}/dependencies")
async def update_skill_dependencies_route(
    slug: str,
    payload: SkillDependenciesUpdateRequest,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        item = await update_skill_dependencies(
            db,
            slug=slug,
            tool_dependencies=payload.tool_dependencies,
            mcp_dependencies=payload.mcp_dependencies,
            skill_dependencies=payload.skill_dependencies,
            operator=current_user,
        )
        return {"success": True, "data": _serialize_skill_for_user(item, current_user)}
    except ValueError as e:
        _raise_from_value_error(e)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update skill dependencies '{slug}': {e}")
        raise HTTPException(status_code=500, detail="更新 skill 依赖失败")


@skills.delete("/{slug}/file")
async def delete_skill_file_route(
    slug: str,
    path: str = Query(..., description="相对 skill 根目录路径"),
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        await delete_skill_node(db, slug=slug, relative_path=path, operator=current_user)
        return {"success": True}
    except ValueError as e:
        _raise_from_value_error(e)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to delete skill file '{slug}/{path}': {e}")
        raise HTTPException(status_code=500, detail="删除技能文件失败")


@skills.get("/{slug}/export")
async def export_skill_route(
    slug: str,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        export_path, download_name = await export_skill_zip(db, slug=slug, operator=current_user)
        background_tasks.add_task(_cleanup_export_file, export_path)
        return FileResponse(path=export_path, media_type="application/zip", filename=download_name)
    except ValueError as e:
        _raise_from_value_error(e)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to export skill '{slug}': {e}")
        raise HTTPException(status_code=500, detail="导出技能失败")


@skills.delete("/{slug}")
async def delete_skill_route(
    slug: str,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        await delete_skill(db, slug=slug, operator=current_user)
        return {"success": True}
    except ValueError as e:
        _raise_from_value_error(e)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to delete skill '{slug}': {e}")
        raise HTTPException(status_code=500, detail="删除技能失败")


@skills.post("/delete-batch")
async def delete_skills_batch_route(
    payload: SkillBatchDeleteRequest,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        results = await delete_skills_batch(db, slugs=payload.slugs, operator=current_user)
        return {"success": True, "data": results, "summary": _summarize_results(results)}
    except ValueError as e:
        _raise_from_value_error(e)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to delete skills batch: {e}")
        raise HTTPException(status_code=500, detail="批量删除技能失败")
