"""内部 Skill 市场的 HTTP 入口。"""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from server.utils.auth_middleware import get_admin_user, get_db, get_required_user
from yuxi.agents.skills.market import (
    get_market_skill,
    install_market_skill,
    list_market_skills,
    market_skill_file,
    market_skill_tree,
    review_market_skill,
    submit_market_skill,
    toggle_market_like,
)
from yuxi.agents.skills.sharing import list_skill_share_requests
from yuxi.storage.postgres.models_business import User

market_router = APIRouter(prefix="/skill-market", tags=["skill-market"])


class MarketSubmission(BaseModel):
    """作者提交审核时填写的使用场景。"""

    application_reason: str = Field(..., min_length=1, max_length=1000)


class MarketReview(BaseModel):
    """管理员选择发布范围并记录审核意见。"""

    scope: str = Field("department")
    note: str = Field("", max_length=2000)


def _raise_market_error(exc: ValueError) -> None:
    """将业务拒绝映射为稳定的 HTTP 状态。"""
    message = str(exc)
    status = 404 if "不存在" in message else 403 if "无权" in message or "仅超级管理员" in message else 400
    raise HTTPException(status_code=status, detail=message) from exc


@market_router.get("")
async def list_market_route(
    query: str = Query("", max_length=100),
    sort: str = Query("recent"),
    installed_only: bool = Query(False),
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=100),
    user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    """搜索并排序当前用户可见的市场条目。"""
    try:
        return {
            "success": True,
            "data": await list_market_skills(
                db, user=user, query=query, sort=sort, installed_only=installed_only, offset=offset, limit=limit
            ),
        }
    except ValueError as exc:
        _raise_market_error(exc)


@market_router.get("/requests")
async def list_market_requests_route(user: User = Depends(get_required_user), db: AsyncSession = Depends(get_db)):
    """查看本人或授权范围内的市场审核申请。"""
    return {"success": True, "data": await list_skill_share_requests(db, operator=user, channel="market")}


@market_router.post("/personal/{slug}/submit")
async def submit_market_route(
    slug: str,
    payload: MarketSubmission,
    user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    """提交个人 Skill 的市场审核快照。"""
    try:
        item = await submit_market_skill(db, slug=slug, application_reason=payload.application_reason, operator=user)
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
