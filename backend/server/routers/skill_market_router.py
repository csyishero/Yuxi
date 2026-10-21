"""内部 Skill 市场的 HTTP 入口。"""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from server.utils.auth_middleware import (
    get_admin_user,
    get_current_user,
    get_db,
    get_required_user,
    get_superadmin_user,
)
from yuxi.agents.skills.market import (
    get_market_skill,
    install_market_skill,
    list_market_rankings,
    list_market_requests,
    list_user_market_installs,
    page_market_skills,
    page_user_market_installs,
    market_skill_file,
    market_skill_tree,
    review_market_skill,
    submit_market_skill,
    toggle_market_like,
    uninstall_market_skill,
)
from yuxi.storage.postgres.models_business import User
from yuxi.services.skill_market_governance_service import (
    MarketConflictError,
    MarketScopeValidationError,
    change_market_skill_scope,
    get_manageable_market_skill,
    list_manageable_market_skills,
    unpublish_market_skill,
)

market_router = APIRouter(prefix="/skill-market", tags=["skill-market"])


class MarketSubmission(BaseModel):
    """作者提交审核时填写的使用场景。"""

    application_reason: str = Field(..., min_length=1, max_length=1000)
    category: str | None = Field(None, description="市场技能分类")


class MarketReview(BaseModel):
    """管理员选择发布范围并记录审核意见。"""

    scope: str | None = Field(None)
    note: str = Field("", max_length=2000)


class MarketUnpublish(BaseModel):
    """下架时提交已读取的条目版本。"""

    expected_revision: int


class MarketScopeChange(BaseModel):
    """超级管理员指定目标受众与已读取的条目版本。"""

    scope: str
    department_id: int | None = None
    expected_revision: int


def _raise_market_error(exc: ValueError) -> None:
    """将业务拒绝映射为稳定的 HTTP 状态。"""
    message = str(exc)
    status = 404 if "不存在" in message else 403 if "无权" in message or "仅超级管理员" in message else 400
    raise HTTPException(status_code=status, detail=message) from exc


def _raise_governance_error(exc: ValueError | PermissionError) -> None:
    """将治理拒绝映射到稳定状态码及受控范围错误字段。"""
    if isinstance(exc, MarketConflictError):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if isinstance(exc, MarketScopeValidationError):
        detail = {"code": exc.code}
        if exc.dependency_type is not None:
            detail["dependency_type"] = exc.dependency_type
        if exc.dependency_slug is not None:
            detail["dependency_slug"] = exc.dependency_slug
        raise HTTPException(status_code=400, detail=detail) from exc
    if isinstance(exc, PermissionError):
        raise HTTPException(status_code=403, detail="需要管理员权限") from exc
    _raise_market_error(exc)


async def get_market_install_user(user: User | None = Depends(get_current_user)) -> User:
    """本人安装清理只要求登录，不要求当前绑定部门。"""
    if user is None:
        raise HTTPException(status_code=401, detail="请登录后再访问")
    return user


@market_router.get("")
async def list_market_route(
    query: str = Query("", max_length=100),
    category: str | None = Query(None),
    sort: str = Query("recent"),
    installed_only: bool = Query(False),
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=100),
    user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    """搜索并排序当前用户可见的市场条目。"""
    try:
        items, total = await page_market_skills(
            db,
            user=user,
            query=query,
            category=category,
            sort=sort,
            installed_only=installed_only,
            offset=offset,
            limit=limit,
        )
        return {
            "success": True,
            "data": items,
            "total": total,
            "has_more": offset + limit < total,
            "limit": limit,
            "offset": offset,
        }
    except ValueError as exc:
        _raise_market_error(exc)


@market_router.get("/requests")
async def list_market_requests_route(
    mine_only: bool = Query(False),
    status: Literal["pending", "approved", "rejected"] | None = Query(None),
    slug: str | None = Query(None, max_length=128),
    offset: int = Query(0, ge=0),
    limit: int | None = Query(None, ge=1, le=100),
    user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    """旧客户端返回完整数组；显式传 limit 时分页查看授权市场申请。"""
    page = await list_market_requests(
        db,
        operator=user,
        mine_only=mine_only,
        status=status,
        slug=slug,
        offset=offset if limit is not None else 0,
        limit=limit,
    )
    return {
        "success": True,
        "data": page if limit is not None else page["items"],
    }


@market_router.get("/-/installs")
async def list_user_market_installs_route(
    offset: int = Query(0, ge=0),
    limit: int | None = Query(None, ge=1, le=100),
    search: str = Query("", max_length=100),
    catalog_all: bool = Query(False),
    user: User = Depends(get_market_install_user),
    db: AsyncSession = Depends(get_db),
):
    """分页查看本人安装；旧客户端不传 limit 时保留完整摘要。"""
    if limit is None:
        if catalog_all:
            raise HTTPException(status_code=422, detail="catalog_all 仅适用于分页安装目录")
        return {"success": True, "data": await list_user_market_installs(db, user=user)}
    items, total = await page_user_market_installs(
        db, user=user, offset=offset, limit=limit, search=search, **({"catalog_all": True} if catalog_all else {})
    )
    return {
        "success": True,
        "data": items,
        "total": total,
        "has_more": offset + limit < total,
        "limit": limit,
        "offset": offset,
    }


@market_router.get("/-/rankings")
async def list_market_rankings_route(
    metric: str = Query("installs"),
    scope: str = Query("company"),
    department_id: int | None = Query(None, ge=1),
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=100),
    user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    """返回当前用户可见的市场技能排行。"""
    try:
        return {
            "success": True,
            "data": await list_market_rankings(
                db, user=user, metric=metric, scope=scope, department_id=department_id, offset=offset, limit=limit
            ),
        }
    except ValueError as exc:
        _raise_market_error(exc)


@market_router.get("/-/management")
async def list_market_management_route(user: User = Depends(get_admin_user), db: AsyncSession = Depends(get_db)):
    """列出当前管理员有权治理的条目，包括已下架条目。"""
    try:
        return {"success": True, "data": await list_manageable_market_skills(db, operator=user)}
    except (PermissionError, ValueError) as exc:
        _raise_governance_error(exc)


@market_router.get("/-/management/{slug}")
async def market_management_detail_route(
    slug: str, user: User = Depends(get_admin_user), db: AsyncSession = Depends(get_db)
):
    """读取当前管理员有权治理的条目详情。"""
    try:
        return {"success": True, "data": await get_manageable_market_skill(db, slug=slug, operator=user)}
    except (PermissionError, ValueError) as exc:
        _raise_governance_error(exc)


@market_router.post("/{slug}/unpublish")
async def unpublish_market_route(
    slug: str, payload: MarketUnpublish, user: User = Depends(get_admin_user), db: AsyncSession = Depends(get_db)
):
    """下架当前管理范围内的已发布条目。"""
    try:
        data = await unpublish_market_skill(db, slug=slug, expected_revision=payload.expected_revision, operator=user)
        return {"success": True, "data": data}
    except (PermissionError, ValueError) as exc:
        _raise_governance_error(exc)


@market_router.put("/{slug}/scope")
async def change_market_scope_route(
    slug: str, payload: MarketScopeChange, user: User = Depends(get_superadmin_user), db: AsyncSession = Depends(get_db)
):
    """超级管理员直接调整当前上架范围。"""
    try:
        data = await change_market_skill_scope(
            db,
            slug=slug,
            scope=payload.scope,
            department_id=payload.department_id,
            expected_revision=payload.expected_revision,
            operator=user,
        )
        return {"success": True, "data": data}
    except (PermissionError, ValueError) as exc:
        _raise_governance_error(exc)


@market_router.post("/personal/{slug}/submit")
async def submit_market_route(
    slug: str,
    payload: MarketSubmission,
    user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    """提交个人 Skill 的市场审核快照。"""
    try:
        item = await submit_market_skill(
            db, slug=slug, application_reason=payload.application_reason, category=payload.category, operator=user
        )
        return {"success": True, "data": item.to_dict()}
    except ValueError as exc:
        _raise_market_error(exc)


@market_router.post("/requests/{request_id}/approve")
async def approve_market_route(
    request_id: str,
    payload: MarketReview,
    user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """批准市场申请并发布一个不可变版本。"""
    try:
        item = await review_market_skill(
            db, request_id=request_id, approved=True, scope=payload.scope, note=payload.note, operator=user
        )
        return {"success": True, "data": item.to_dict()}
    except ValueError as exc:
        _raise_market_error(exc)


@market_router.post("/requests/{request_id}/reject")
async def reject_market_route(
    request_id: str,
    payload: MarketReview,
    user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """拒绝市场申请并保存审核意见。"""
    try:
        item = await review_market_skill(
            db, request_id=request_id, approved=False, scope=payload.scope, note=payload.note, operator=user
        )
        return {"success": True, "data": item.to_dict()}
    except ValueError as exc:
        _raise_market_error(exc)


@market_router.get("/{slug}")
async def market_detail_route(slug: str, user: User = Depends(get_required_user), db: AsyncSession = Depends(get_db)):
    """读取当前用户可见的条目详情。"""
    try:
        return {"success": True, "data": await get_market_skill(db, slug=slug, user=user)}
    except ValueError as exc:
        _raise_market_error(exc)


@market_router.get("/{slug}/tree")
async def market_tree_route(slug: str, user: User = Depends(get_required_user), db: AsyncSession = Depends(get_db)):
    """列出审核发布的文件树。"""
    try:
        return {"success": True, "data": await market_skill_tree(db, slug=slug, user=user)}
    except ValueError as exc:
        _raise_market_error(exc)


@market_router.get("/{slug}/file")
async def market_file_route(
    slug: str,
    path: str = Query("SKILL.md"),
    user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    """预览审核发布版本中的文本文件。"""
    try:
        return {"success": True, "data": {"content": await market_skill_file(db, slug=slug, user=user, path=path)}}
    except ValueError as exc:
        _raise_market_error(exc)


@market_router.post("/{slug}/install")
async def market_install_route(slug: str, user: User = Depends(get_required_user), db: AsyncSession = Depends(get_db)):
    """主动安装市场 Skill。"""
    try:
        return {"success": True, "data": await install_market_skill(db, slug=slug, user=user)}
    except ValueError as exc:
        _raise_market_error(exc)


@market_router.delete("/{slug}/install")
async def market_uninstall_route(
    slug: str, user: User = Depends(get_market_install_user), db: AsyncSession = Depends(get_db)
):
    """卸载本人安装的市场 Skill。"""
    return {"success": True, "data": await uninstall_market_skill(db, slug=slug, user=user)}


@market_router.post("/{slug}/upgrade")
async def market_upgrade_route(slug: str, user: User = Depends(get_required_user), db: AsyncSession = Depends(get_db)):
    """手动升级到最新审核版本。"""
    try:
        return {"success": True, "data": await install_market_skill(db, slug=slug, user=user, upgrade=True)}
    except ValueError as exc:
        _raise_market_error(exc)


@market_router.post("/{slug}/like")
async def market_like_route(slug: str, user: User = Depends(get_required_user), db: AsyncSession = Depends(get_db)):
    """切换个人点赞并返回数据库中的最新状态。"""
    try:
        return {"success": True, "data": await toggle_market_like(db, slug=slug, user=user)}
    except ValueError as exc:
        _raise_market_error(exc)
