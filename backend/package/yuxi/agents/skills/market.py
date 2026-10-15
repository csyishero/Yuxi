"""内部 Skill 市场的审核、发现和个人安装。"""

from __future__ import annotations

import asyncio
import shutil
import uuid
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.agents.skills.market_repository import MarketSkillRepository
from yuxi.agents.skills.repository import SkillRepository
from yuxi.agents.skills.service import (
    ResolvedSkill,
    build_skill_tree,
    compute_skill_dir_hash,
    copy_skill_snapshot,
    is_skill_text_path,
    list_personal_skills,
    lock_skill_slug_allocation,
    parse_skill_dir_metadata,
    resolve_skill_relative_path,
    user_can_access_skill,
    validate_skill_dependencies,
)
from yuxi.agents.skills.sharing import authorized_share_snapshot_path, submit_skill_share_request
from yuxi.config import get_skill_data_dir
from yuxi.repositories.department_repository import DepartmentRepository
from yuxi.storage.postgres.models_business import (
    MarketSkill,
    MarketSkillInstall,
    MarketSkillLike,
    MarketSkillRelease,
    Skill,
    SkillShareRequest,
    User,
)
from yuxi.utils.datetime_utils import format_utc_datetime, utc_now_naive


def market_projection_slug(release_id: str) -> str:
    """为不可变版本生成独立的沙盒目录名。"""
    return f"market-release-{release_id.replace('-', '')}"


def market_skill_visible(item: MarketSkill, user: User) -> bool:
    """判断条目是否在用户当前组织范围内。"""
    return (
        user.role == "superadmin"
        or item.scope == "global"
        or (item.scope == "department" and user.department_id is not None and item.department_id == user.department_id)
    )


async def submit_market_skill(
    db: AsyncSession, *, slug: str, application_reason: str, operator: User
) -> SkillShareRequest:
    """把个人 Skill 的当前内容作为市场审核快照提交。"""
    return await submit_skill_share_request(
        db, slug=slug, application_reason=application_reason, operator=operator, channel="market"
    )


async def review_market_skill(
    db: AsyncSession,
    *,
    request_id: str,
    approved: bool,
    scope: str,
    note: str,
    operator: User,
) -> SkillShareRequest:
    """管理员验证快照后发布不可变版本或拒绝申请。"""
    if operator.role not in {"admin", "superadmin"}:
        raise ValueError("无权审核市场申请")
    item = await db.scalar(select(SkillShareRequest).where(SkillShareRequest.id == request_id).with_for_update())
    if (
        item is None
        or item.channel != "market"
        or (
            operator.role == "admin"
            and (operator.department_id is None or item.owner_department_id != operator.department_id)
        )
    ):
        raise ValueError("市场申请不存在")
    if item.status != "pending":
        raise ValueError("市场申请已审核")

    if not approved:
        item.status = "rejected"
        item.review_note = note.strip() or None
        item.reviewer_uid = str(operator.uid)
        item.reviewed_at = utc_now_naive()
        await db.commit()
        await db.refresh(item)
        return item

    if scope not in {"department", "global"}:
        raise ValueError("请选择发布范围")
    if scope == "global" and operator.role != "superadmin":
        raise ValueError("仅超级管理员可以向全公司发布")
    if scope == "department" and item.owner_department_id is None:
        raise ValueError("申请人没有所属部门")

    snapshot = await authorized_share_snapshot_path(db, request_id=request_id, operator=operator)
    parsed = await asyncio.to_thread(parse_skill_dir_metadata, snapshot)
    if parsed["slug"] != item.personal_slug:
        raise ValueError("审核快照的 Skill slug 已变化")

    repo = MarketSkillRepository(db)
    await lock_skill_slug_allocation(db)
    listing = await repo.by_owner_source_for_update(item.owner_uid, item.personal_slug)
    expected_department = item.owner_department_id if scope == "department" else None
    if listing is not None and (listing.scope != scope or listing.department_id != expected_department):
        raise ValueError("已有市场条目的发布范围不可在新版本中修改")
    if listing is None:
        slug = item.personal_slug
        for suffix in range(1, 100):
            if await SkillRepository(db).get_by_slug(slug) is None and await repo.by_slug_for_update(slug) is None:
                break
            slug = f"{item.personal_slug[:110]}-{suffix + 1}"
        else:
            raise ValueError("无法分配市场 Skill 标识")
    else:
        slug = listing.slug

    share_config = {
        "version": 2,
        "read_scope": {
            "access_level": scope,
            "department_ids": [item.owner_department_id] if scope == "department" else [],
            "user_uids": [],
        },
        "manage_scope": None,
    }
    parent = Skill(slug=slug, share_config=share_config, created_by=item.owner_uid, enabled=True)
    available = {skill.slug: skill for skill in await SkillRepository(db).list_enabled()}
    tools, mcps, skills = await validate_skill_dependencies(
        db=db,
        user=operator,
        parent=parent,
        tool_dependencies=parsed["tool_dependencies"],
        mcp_dependencies=parsed["mcp_dependencies"],
        skill_dependencies=parsed["skill_dependencies"],
        available_skills=available,
    )

    release_id = str(uuid.uuid4())
    relative = Path("market") / "releases" / release_id
    published = get_skill_data_dir() / relative
    published.parent.mkdir(parents=True, exist_ok=True)
    staging = published.with_name(f".{release_id}.tmp")
    moved = False
    commit_started = False
    try:
        metadata = await asyncio.to_thread(
            copy_skill_snapshot, snapshot, staging, expected_slug=item.personal_slug, final_slug=slug
        )
        staging.rename(published)
        moved = True
        if listing is None:
            listing = MarketSkill(
                id=str(uuid.uuid4()),
                slug=slug,
                owner_uid=item.owner_uid,
                personal_slug=item.personal_slug,
                scope=scope,
                department_id=item.owner_department_id if scope == "department" else None,
                created_at=utc_now_naive(),
                updated_at=utc_now_naive(),
            )
            db.add(listing)
            await db.flush()
            version = 1
        else:
            latest = await repo.release(listing.latest_release_id)
            version = latest.version + 1
        release = MarketSkillRelease(
            id=release_id,
            market_skill_id=listing.id,
            version=version,
            name=metadata["name"],
            description=metadata["description"],
            dir_path=relative.as_posix(),
            content_hash=await asyncio.to_thread(compute_skill_dir_hash, published),
            tool_dependencies=tools,
            mcp_dependencies=mcps,
            skill_dependencies=skills,
            request_id=request_id,
            created_at=utc_now_naive(),
        )
        db.add(release)
        listing.latest_release_id = release_id
        listing.updated_at = utc_now_naive()
        item.status = "approved"
        item.review_note = note.strip() or None
        item.reviewer_uid = str(operator.uid)
        item.reviewed_at = utc_now_naive()
        item.published_slug = slug
        item.department_ids = [listing.department_id] if listing.department_id is not None else []
        item.read_scope = share_config["read_scope"]
        commit_started = True
        await db.commit()
        await db.refresh(item)
        return item
    except BaseException:
        await db.rollback()
        if moved and not commit_started:
            shutil.rmtree(published, ignore_errors=True)
        raise
    finally:
        shutil.rmtree(staging, ignore_errors=True)


async def list_market_skills(
    db: AsyncSession,
    *,
    user: User,
    query: str = "",
    sort: str = "recent",
    installed_only: bool = False,
    offset: int = 0,
    limit: int = 100,
) -> list[dict]:
    """搜索当前用户可见条目，并附上个人状态和可信指标。"""
    if sort not in {"recent", "installs", "likes"}:
        raise ValueError("无效的排序方式")
    repo = MarketSkillRepository(db)
    cards = []
    for listing in await repo.visible(
        user, query=query, sort=sort, installed_only=installed_only, offset=offset, limit=limit
    ):
        cards.append(await _market_card(db, repo, listing, user))
    return cards


async def list_market_rankings(
    db: AsyncSession,
    *,
    user: User,
    metric: str,
    scope: str,
    department_id: int | None = None,
    offset: int = 0,
    limit: int = 100,
) -> list[dict]:
    """按全公司或当前部门的活跃用户安装、点赞数列出排行榜。"""
    if metric not in {"installs", "likes"}:
        raise ValueError("无效的排行榜指标")
    if scope not in {"company", "department"}:
        raise ValueError("无效的排行榜范围")
    if scope == "company" and department_id is not None:
        raise ValueError("全公司排行榜不能指定部门")

    if scope == "department":
        department_id = department_id if department_id is not None else user.department_id
        if department_id is None:
            raise ValueError("当前用户未绑定部门，无法查看本部门排行榜")
        if user.role != "superadmin" and department_id != user.department_id:
            raise ValueError("无权查看其他部门排行榜")
        if await DepartmentRepository(db).get_by_id(department_id) is None:
            raise ValueError("部门不存在")

    repo = MarketSkillRepository(db)
    return [
        await _market_card(db, repo, listing, user, metrics=(install_count, like_count))
        for listing, install_count, like_count in await repo.ranked_visible(
            user, metric=metric, department_id=department_id, offset=offset, limit=limit
        )
    ]


async def get_market_skill(db: AsyncSession, *, slug: str, user: User) -> dict:
    """读取可见条目的详情与版本列表。"""
    repo = MarketSkillRepository(db)
    listing = await repo.visible_by_slug(user, slug)
    if listing is None:
        raise ValueError("市场 Skill 不存在")
    card = await _market_card(db, repo, listing, user)
    card["releases"] = [
        {"version": release.version, "created_at": format_utc_datetime(release.created_at)}
        for release in await repo.releases(listing.id)
    ]
    return card


async def _market_card(
    db: AsyncSession,
    repo: MarketSkillRepository,
    listing: MarketSkill,
    user: User,
    *,
    metrics: tuple[int, int] | None = None,
) -> dict:
    """组合当前版本、作者、指标和用户安装状态。"""
    release = await repo.release(listing.latest_release_id)
    install = await repo.install(str(user.uid), listing.id)
    installed_release = await repo.release(install.release_id) if install is not None else None
    install_count, like_count = metrics if metrics is not None else await repo.metrics(listing.id)
    owner = await db.scalar(select(User.username).where(User.uid == listing.owner_uid))
    return {
        "slug": listing.slug,
        "name": release.name,
        "description": release.description,
        "owner_uid": listing.owner_uid,
        "owner_name": owner or listing.owner_uid,
        "scope": listing.scope,
        "department_id": listing.department_id,
        "latest_version": release.version,
        "installed_version": installed_release.version if installed_release is not None else None,
        "upgrade_available": installed_release is not None and installed_release.version < release.version,
        "install_count": install_count,
        "likes_count": like_count,
        "liked": await repo.liked(str(user.uid), listing.id),
        "tool_dependencies": release.tool_dependencies or [],
        "mcp_dependencies": release.mcp_dependencies or [],
        "skill_dependencies": release.skill_dependencies or [],
        "updated_at": format_utc_datetime(listing.updated_at),
    }


async def install_market_skill(db: AsyncSession, *, slug: str, user: User, upgrade: bool = False) -> dict:
    """安装或手动升级到条目当前审核版本。"""
    repo = MarketSkillRepository(db)
    listing = await repo.visible_by_slug(user, slug)
    if listing is None:
        raise ValueError("市场 Skill 不存在")
    current = await repo.install(str(user.uid), listing.id)
    if upgrade and current is None:
        raise ValueError("请先安装市场 Skill")
    if not upgrade and current is not None:
        return await get_market_skill(db, slug=slug, user=user)
    release = await repo.release(listing.latest_release_id)
    if release.tool_dependencies or release.mcp_dependencies or release.skill_dependencies:
        available = {
            item.slug: item for item in await SkillRepository(db).list_enabled() if user_can_access_skill(user, item)
        }
        await validate_skill_dependencies(
            db=db,
            user=user,
            parent=Skill(slug=slug, enabled=True),
            tool_dependencies=release.tool_dependencies or [],
            mcp_dependencies=release.mcp_dependencies or [],
            skill_dependencies=release.skill_dependencies or [],
            available_skills=available,
            check_scope=False,
        )
    if current is None:
        if await SkillRepository(db).get_by_slug(slug) is not None or any(
            personal.slug == slug for personal in await list_personal_skills(str(user.uid))
        ):
            raise ValueError("已有同名 Skill，无法安装")
        current = MarketSkillInstall(
            uid=str(user.uid),
            market_skill_id=listing.id,
            release_id=listing.latest_release_id,
            installed_at=utc_now_naive(),
            updated_at=utc_now_naive(),
        )
        db.add(current)
    else:
        current.release_id = listing.latest_release_id
        current.updated_at = utc_now_naive()
    await db.commit()
    return await get_market_skill(db, slug=slug, user=user)


async def toggle_market_like(db: AsyncSession, *, slug: str, user: User) -> dict:
    """按用户唯一键切换点赞。"""
    repo = MarketSkillRepository(db)
    listing = await repo.visible_by_slug(user, slug)
    if listing is None:
        raise ValueError("市场 Skill 不存在")
    existing = await db.get(MarketSkillLike, (str(user.uid), listing.id))
    if existing is None:
        db.add(MarketSkillLike(uid=str(user.uid), market_skill_id=listing.id, created_at=utc_now_naive()))
    else:
        await db.delete(existing)
    await db.commit()
    return await get_market_skill(db, slug=slug, user=user)


async def list_installed_market_skills(db: AsyncSession, user: User) -> list[ResolvedSkill]:
    """只把用户当前可见且已安装的版本提供给 Agent。"""
    repo = MarketSkillRepository(db)
    result = []
    for install in await repo.installs_for_user(str(user.uid)):
        listing = await db.get(MarketSkill, install.market_skill_id)
        if listing is None or not market_skill_visible(listing, user):
            continue
        release = await repo.release(install.release_id)
        if release is None or release.market_skill_id != listing.id:
            continue
        result.append(
            ResolvedSkill(
                id=listing.id,
                slug=listing.slug,
                name=release.name,
                description=release.description,
                source_type="market",
                source_scope="market",
                source_dir=get_skill_data_dir() / release.dir_path,
                enabled=True,
                created_by=listing.owner_uid,
                share_config={
                    "version": 2,
                    "read_scope": {
                        "access_level": listing.scope,
                        "department_ids": [listing.department_id] if listing.department_id else [],
                        "user_uids": [],
                    },
                    "manage_scope": None,
                },
                tool_dependencies=release.tool_dependencies or [],
                mcp_dependencies=release.mcp_dependencies or [],
                skill_dependencies=release.skill_dependencies or [],
                version=str(release.version),
                content_hash=release.content_hash,
                projection_slug=market_projection_slug(release.id),
            )
        )
    return result


async def market_projection_sources(db: AsyncSession, user: User) -> dict[str, str]:
    """保留已安装条目的历史版本目录供运行中的 Agent 使用。"""
    repo = MarketSkillRepository(db)
    sources = {}
    for install in await repo.installs_for_user(str(user.uid)):
        listing = await db.get(MarketSkill, install.market_skill_id)
        if listing is None or not market_skill_visible(listing, user):
            continue
        installed_release = await repo.release(install.release_id)
        if installed_release is None or installed_release.market_skill_id != listing.id:
            continue
        for release in await repo.releases(listing.id):
            if release.version <= installed_release.version:
                sources[market_projection_slug(release.id)] = str(get_skill_data_dir() / release.dir_path)
    return sources


async def market_skill_file(db: AsyncSession, *, slug: str, user: User, path: str = "SKILL.md") -> str:
    """读取当前审核版本中的文本文件，供详情页预览。"""
    repo = MarketSkillRepository(db)
    listing = await repo.visible_by_slug(user, slug)
    if listing is None:
        raise ValueError("市场 Skill 不存在")
    release = await repo.release(listing.latest_release_id)
    root = get_skill_data_dir() / release.dir_path
    target, _ = resolve_skill_relative_path(root, path)
    if not target.is_file() or not is_skill_text_path(target):
        raise ValueError("文件不存在或不支持文本预览")
    return await asyncio.to_thread(target.read_text, encoding="utf-8")


async def market_skill_tree(db: AsyncSession, *, slug: str, user: User) -> list[dict]:
    """列出当前审核版本的文件结构。"""
    repo = MarketSkillRepository(db)
    listing = await repo.visible_by_slug(user, slug)
    if listing is None:
        raise ValueError("市场 Skill 不存在")
    release = await repo.release(listing.latest_release_id)
    root = get_skill_data_dir() / release.dir_path
    return await asyncio.to_thread(build_skill_tree, root, root)
