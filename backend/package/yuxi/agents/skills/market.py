"""内部 Skill 市场的审核、发现和个人安装。"""

from __future__ import annotations

import asyncio
import shutil
import uuid
from pathlib import Path

from sqlalchemy import and_, func, or_, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.agents.skills.market_repository import MarketSkillRepository
from yuxi.agents.skills.repository import SkillRepository
from yuxi.agents.skills.service import (
    ResolvedSkill,
    approved_shared_origins,
    build_skill_tree,
    compute_skill_dir_hash,
    copy_skill_snapshot,
    get_personal_skill,
    is_skill_text_path,
    list_personal_skills,
    lock_skill_slug_allocation,
    normalize_skill_category,
    parse_skill_dir_metadata,
    project_skill_markdown_mcp_dependencies,
    refresh_user_skill_projection_async,
    resolve_skill_relative_path,
    set_skill_dir_category,
    user_can_access_skill,
    validate_skill_dependencies,
)
from yuxi.agents.skills.sharing import (
    authorized_share_snapshot_path,
    list_skill_share_requests,
    submit_skill_share_request,
)
from yuxi.config import get_skill_data_dir
from yuxi.repositories.department_repository import DepartmentRepository
from yuxi.services.mcp_reference_service import get_visible_mcp_reference_slugs, project_mcp_references
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
    db: AsyncSession, *, slug: str, application_reason: str, operator: User, category: str | None = None
) -> SkillShareRequest:
    """把个人 Skill 的当前内容作为市场审核快照提交。"""
    return await submit_skill_share_request(
        db, slug=slug, application_reason=application_reason, operator=operator, channel="market", category=category
    )


async def list_market_requests(
    db: AsyncSession,
    *,
    operator: User,
    mine_only: bool = False,
    status: str | None = None,
    slug: str | None = None,
    limit: int | None = 20,
    offset: int = 0,
    request_ids: list[str] | None = None,
) -> dict:
    """读取市场申请并附上发布范围与版本；limit=None 兼容旧版完整列表。"""
    page = await list_skill_share_requests(
        db,
        operator=operator,
        channel="market",
        mine_only=mine_only,
        status=status,
        slug=slug,
        limit=limit,
        offset=offset,
        request_ids=request_ids,
    )
    requests = page["items"]
    sources = {(item["owner_uid"], item["personal_slug"]) for item in requests}
    if not sources:
        return page

    rows = await db.execute(
        select(
            MarketSkill.owner_uid,
            MarketSkill.personal_slug,
            MarketSkill.scope,
            MarketSkill.department_id,
            MarketSkillRelease.version,
        )
        .outerjoin(MarketSkillRelease, MarketSkillRelease.id == MarketSkill.latest_release_id)
        .where(tuple_(MarketSkill.owner_uid, MarketSkill.personal_slug).in_(sources))
    )
    existing = {
        (owner_uid, slug): (scope, department_id, version)
        for owner_uid, slug, scope, department_id, version in rows.all()
    }
    published_rows = await db.execute(
        select(MarketSkillRelease.request_id, MarketSkillRelease.version).where(
            MarketSkillRelease.request_id.in_([item["id"] for item in requests])
        )
    )
    published_versions = dict(published_rows.all())
    for item in requests:
        scope, department_id, current_version = existing.get(
            (item["owner_uid"], item["personal_slug"]), (None, None, None)
        )
        item["existing_market_scope"] = scope
        item["existing_market_department_id"] = department_id
        item["current_market_version"] = current_version
        item["published_version"] = published_versions.get(item["id"])
    return page


async def list_personal_publication_records(
    db: AsyncSession, *, operator: User, limit: int = 20, offset: int = 0
) -> dict:
    """按个人技能分组分页，摘要只取各渠道最近申请和最近批准。"""
    activity = func.coalesce(SkillShareRequest.reviewed_at, SkillShareRequest.created_at)
    groups = (
        select(
            SkillShareRequest.personal_slug.label("slug"),
            func.max(activity).label("last_activity"),
        )
        .where(
            SkillShareRequest.owner_uid == operator.uid,
            SkillShareRequest.channel.in_(("shared", "market")),
        )
        .group_by(SkillShareRequest.personal_slug)
        .subquery()
    )
    total = await db.scalar(select(func.count()).select_from(groups)) or 0
    rows = (
        await db.execute(
            select(groups.c.slug, groups.c.last_activity)
            .order_by(groups.c.last_activity.desc(), groups.c.slug)
            .limit(limit)
            .offset(offset)
        )
    ).all()
    slugs = [slug for slug, _ in rows]
    activities = {slug: format_utc_datetime(last_activity) for slug, last_activity in rows}
    if not slugs:
        return {
            "slugs": [],
            "activities": {},
            "shared_requests": [],
            "market_requests": [],
            "total": total,
            "limit": limit,
            "offset": offset,
        }

    newest = func.row_number().over(
        partition_by=(SkillShareRequest.personal_slug, SkillShareRequest.channel),
        order_by=(SkillShareRequest.created_at.desc(), SkillShareRequest.id.desc()),
    )
    newest_status = func.row_number().over(
        partition_by=(SkillShareRequest.personal_slug, SkillShareRequest.channel, SkillShareRequest.status),
        order_by=(SkillShareRequest.created_at.desc(), SkillShareRequest.id.desc()),
    )
    ranked = (
        select(
            SkillShareRequest.id.label("request_id"),
            SkillShareRequest.channel,
            SkillShareRequest.status,
            newest.label("newest"),
            newest_status.label("newest_status"),
        )
        .where(
            SkillShareRequest.owner_uid == operator.uid,
            SkillShareRequest.channel.in_(("shared", "market")),
            SkillShareRequest.personal_slug.in_(slugs),
        )
        .subquery()
    )
    selected = await db.execute(
        select(ranked.c.request_id, ranked.c.channel).where(
            or_(ranked.c.newest == 1, and_(ranked.c.status == "approved", ranked.c.newest_status == 1))
        )
    )
    selected_rows = selected.all()
    shared_ids = [request_id for request_id, channel in selected_rows if channel == "shared"]
    market_ids = [request_id for request_id, channel in selected_rows if channel == "market"]
    shared = await list_skill_share_requests(
        db, operator=operator, mine_only=True, request_ids=shared_ids, limit=max(1, len(shared_ids))
    )
    market = await list_market_requests(
        db, operator=operator, mine_only=True, request_ids=market_ids, limit=max(1, len(market_ids))
    )
    return {
        "slugs": slugs,
        "activities": activities,
        "shared_requests": shared["items"],
        "market_requests": market["items"],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


async def review_market_skill(
    db: AsyncSession,
    *,
    request_id: str,
    approved: bool,
    scope: str | None,
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

    if scope is not None and scope not in {"department", "global"}:
        raise ValueError("请选择发布范围")

    snapshot = await authorized_share_snapshot_path(db, request_id=request_id, operator=operator)
    parsed = await asyncio.to_thread(parse_skill_dir_metadata, snapshot)
    if parsed["slug"] != item.personal_slug:
        raise ValueError("审核快照的 Skill slug 已变化")

    repo = MarketSkillRepository(db)
    await lock_skill_slug_allocation(db)
    listing = await repo.by_owner_source_for_update(item.owner_uid, item.personal_slug)
    if scope is None:
        scope = listing.scope if listing is not None else "department"
    if scope == "global" and operator.role != "superadmin":
        raise ValueError("仅超级管理员可以向全公司发布")
    if scope == "department" and listing is None and item.owner_department_id is None:
        raise ValueError("申请人没有所属部门")
    if (
        listing is not None
        and listing.scope == "department"
        and listing.department_id != item.owner_department_id
        and operator.role != "superadmin"
    ):
        raise ValueError("仅超级管理员可以批准跨部门新版本")
    expected_department = None
    if scope == "department":
        expected_department = listing.department_id if listing is not None else item.owner_department_id
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
            "department_ids": [expected_department] if scope == "department" else [],
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
        metadata = await asyncio.to_thread(set_skill_dir_category, staging, item.category)
        staging.rename(published)
        moved = True
        if listing is None:
            listing = MarketSkill(
                id=str(uuid.uuid4()),
                slug=slug,
                owner_uid=item.owner_uid,
                personal_slug=item.personal_slug,
                scope=scope,
                department_id=expected_department,
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
            category=item.category,
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
    category: str | None = None,
) -> list[dict]:
    """搜索当前用户可见条目，并附上个人状态和可信指标。"""
    if sort not in {"recent", "installs", "likes"}:
        raise ValueError("无效的排序方式")
    if category is not None:
        category = normalize_skill_category(category)
    repo = MarketSkillRepository(db)
    listings = await repo.visible(
        user, query=query, sort=sort, installed_only=installed_only, offset=offset, limit=limit, category=category
    )
    shared_by_source = await _available_shared_slugs_by_source(db, user=user, listings=listings)
    install_conflicts = await _market_install_conflicts(db, user=user, listings=listings)
    visible_mcps = await get_visible_mcp_reference_slugs(db, user) if listings else None
    cards = []
    for listing in listings:
        cards.append(
            await _market_card(
                db,
                repo,
                listing,
                user,
                visible_mcp_slugs=visible_mcps,
                shared_available_slug=shared_by_source.get((listing.owner_uid, listing.personal_slug)),
                install_conflict=install_conflicts[listing.slug],
            )
        )
    return cards


async def page_market_skills(
    db: AsyncSession,
    *,
    user: User,
    query: str = "",
    sort: str = "recent",
    installed_only: bool = False,
    offset: int = 0,
    limit: int = 100,
    category: str | None = None,
) -> tuple[list[dict], int]:
    """返回市场当前页和同一授权筛选条件下的准确总数。"""
    cards = await list_market_skills(
        db,
        user=user,
        query=query,
        sort=sort,
        installed_only=installed_only,
        offset=offset,
        limit=limit,
        category=category,
    )
    normalized_category = normalize_skill_category(category) if category is not None else None
    total = await MarketSkillRepository(db).count_visible(
        user, query=query, category=normalized_category, installed_only=installed_only
    )
    return cards, total


async def list_user_market_installs(db: AsyncSession, *, user: User, search: str = "") -> list[dict]:
    """列出本人已安装版本的安全摘要，包括现时可见状态。"""
    items = await MarketSkillRepository(db).installed_releases_for_user(str(user.uid), user=user, search=search)
    return _market_install_summaries(items, user=user)


async def personal_market_install_summaries(
    db: AsyncSession, *, user: User, personal_slugs: set[str]
) -> dict[str, dict]:
    """仅为当前个人技能页查本人同源安装，并复用安装列表的权限裁剪。"""
    rows = await MarketSkillRepository(db).installed_releases_for_personal_slugs(str(user.uid), personal_slugs)
    return {
        card["own_personal_slug"]: card
        for card in _market_install_summaries(rows, user=user)
        if card["own_personal_slug"] is not None
    }


async def page_user_market_installs(
    db: AsyncSession, *, user: User, offset: int, limit: int, search: str = "", catalog_all: bool = False
) -> tuple[list[dict], int]:
    """按页返回本人已安装版本，并保持隐藏条目的安全摘要。"""
    catalog_filters = {}
    if catalog_all:
        own_slugs = await MarketSkillRepository(db).installed_personal_slugs_for_user(str(user.uid))
        needle = search.strip().casefold()
        matching_originals = set()
        for slug in own_slugs:
            try:
                personal = await get_personal_skill(str(user.uid), slug)
            except ValueError:
                continue
            if not needle or needle in f"{personal.slug} {personal.name} {personal.description}".casefold():
                matching_originals.add(slug)
        catalog_filters = {"exclude_own_personal_slugs": matching_originals}

    items, total = await MarketSkillRepository(db).page_installed_releases_for_user(
        str(user.uid), user=user, offset=offset, limit=limit, search=search, **catalog_filters
    )
    cards = _market_install_summaries(items, user=user)
    for card in cards:
        card["own_personal"] = None
        if personal_slug := card["own_personal_slug"]:
            try:
                personal = await get_personal_skill(str(user.uid), personal_slug)
            except ValueError:
                continue
            card["own_personal"] = {
                "id": personal.id,
                "slug": personal.slug,
                "name": personal.name,
                "description": personal.description,
                "category": personal.category,
                "source_type": personal.source_type,
                "source_scope": personal.source_scope,
                "enabled": personal.enabled,
            }
    return cards, total


def _market_install_summaries(items: list[tuple[MarketSkill, MarketSkillRelease]], *, user: User) -> list[dict]:
    """将安装指针转为按当前可见范围裁剪的页面摘要。"""
    cards = []
    for listing, release in items:
        detail_available = bool(user.department_id) and listing.latest_release_id is not None
        visible = detail_available and market_skill_visible(listing, user)
        own_personal_slug = listing.personal_slug if listing.owner_uid == str(user.uid) else None
        cards.append(
            {
                "slug": listing.slug,
                "name": release.name if visible else listing.slug,
                "description": release.description if visible else "",
                "category": (release.category or "other") if visible else None,
                "installed_version": release.version,
                "owner_uid": listing.owner_uid if visible or own_personal_slug else None,
                "personal_slug": listing.personal_slug if visible or own_personal_slug else None,
                "own_personal_slug": own_personal_slug,
                "visible": visible,
            }
        )
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
    ranked = await repo.ranked_visible(user, metric=metric, department_id=department_id, offset=offset, limit=limit)
    install_conflicts = await _market_install_conflicts(db, user=user, listings=[item for item, _, _ in ranked])
    visible_mcps = await get_visible_mcp_reference_slugs(db, user) if ranked else None
    return [
        await _market_card(
            db,
            repo,
            listing,
            user,
            visible_mcp_slugs=visible_mcps,
            metrics=(install_count, like_count),
            install_conflict=install_conflicts[listing.slug],
        )
        for listing, install_count, like_count in ranked
    ]


async def get_market_skill(db: AsyncSession, *, slug: str, user: User) -> dict:
    """读取可见条目的详情与版本列表。"""
    repo = MarketSkillRepository(db)
    listing = await repo.visible_by_slug(user, slug)
    if listing is None:
        raise ValueError("市场 Skill 不存在")
    visible_mcps = await get_visible_mcp_reference_slugs(db, user)
    shared_by_source = await _available_shared_slugs_by_source(db, user=user, listings=[listing])
    install_conflicts = await _market_install_conflicts(db, user=user, listings=[listing])
    card = await _market_card(
        db,
        repo,
        listing,
        user,
        visible_mcp_slugs=visible_mcps,
        shared_available_slug=shared_by_source.get((listing.owner_uid, listing.personal_slug)),
        install_conflict=install_conflicts[listing.slug],
    )
    card["releases"] = [
        {"version": release.version, "created_at": format_utc_datetime(release.created_at)}
        for release in await repo.releases(listing.id)
    ]
    return card


async def _available_shared_slugs_by_source(
    db: AsyncSession, *, user: User, listings: list[MarketSkill]
) -> dict[tuple[str, str], str]:
    """仅按当前市场页原件查找用户能用且没有被个人技能覆盖的共享副本。"""
    sources = {(item.owner_uid, item.personal_slug) for item in listings}
    if not sources:
        return {}
    current_or_legacy = or_(
        Skill.id == SkillShareRequest.published_skill_id,
        and_(SkillShareRequest.published_skill_id.is_(None), Skill.slug == SkillShareRequest.published_slug),
    )
    rows = await db.scalars(
        select(Skill)
        .join(SkillShareRequest, current_or_legacy)
        .where(
            tuple_(SkillShareRequest.owner_uid, SkillShareRequest.personal_slug).in_(sources),
            SkillShareRequest.channel == "shared",
            SkillShareRequest.status == "approved",
            Skill.source_type == "personal_share",
            Skill.enabled.is_(True),
        )
    )
    candidates = list({item.id: item for item in rows.all()}.values())
    origins = await approved_shared_origins(db, candidates)
    latest: dict[tuple[str, str], Skill] = {}
    for skill in candidates:
        source = origins.get(skill.slug)
        if source not in sources or not user_can_access_skill(user, skill):
            continue
        try:
            await get_personal_skill(str(user.uid), skill.slug)
        except ValueError:
            pass
        else:
            continue
        if source not in latest or skill.id > latest[source].id:
            latest[source] = skill
    return {source: skill.slug for source, skill in latest.items()}


async def _market_install_conflicts(
    db: AsyncSession, *, user: User, listings: list[MarketSkill]
) -> dict[str, str | None]:
    """按当前市场页标识区分个人同名和数据库保留标识，不读取共享技能内容。"""
    slugs = {item.slug for item in listings}
    if not slugs:
        return {}
    reserved = set((await db.scalars(select(Skill.slug).where(Skill.slug.in_(slugs)))).all())

    async def personal_exists(slug: str) -> bool:
        try:
            await get_personal_skill(str(user.uid), slug)
        except ValueError:
            return False
        return True

    personal = dict(zip(slugs, await asyncio.gather(*(personal_exists(slug) for slug in slugs)), strict=True))
    return {slug: "personal" if personal[slug] else "reserved" if slug in reserved else None for slug in slugs}


async def _market_card(
    db: AsyncSession,
    repo: MarketSkillRepository,
    listing: MarketSkill,
    user: User,
    *,
    visible_mcp_slugs: set[str] | None,
    metrics: tuple[int, int] | None = None,
    shared_available_slug: str | None = None,
    install_conflict: str | None,
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
        "category": release.category or "other",
        "owner_uid": listing.owner_uid,
        "owner_name": owner or listing.owner_uid,
        "own_personal_slug": listing.personal_slug if listing.owner_uid == str(user.uid) else None,
        "shared_available_slug": shared_available_slug,
        "install_conflict": install_conflict,
        "scope": listing.scope,
        "department_id": listing.department_id,
        "latest_version": release.version,
        "installed_version": installed_release.version if installed_release is not None else None,
        "upgrade_available": installed_release is not None and installed_release.version < release.version,
        "install_count": install_count,
        "likes_count": like_count,
        "liked": await repo.liked(str(user.uid), listing.id),
        "tool_dependencies": release.tool_dependencies or [],
        "mcp_dependencies": project_mcp_references(list(release.mcp_dependencies or []), visible_mcp_slugs),
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
    if (
        current is None
        and listing.owner_uid == str(user.uid)
        and any(personal.slug == listing.personal_slug for personal in await list_personal_skills(str(user.uid)))
    ):
        raise ValueError("个人原件已可使用，无需安装自己发布的市场 Skill")
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
        if any(personal.slug == slug for personal in await list_personal_skills(str(user.uid))):
            raise ValueError("已有同名个人技能，无法安装市场版")
        if await SkillRepository(db).get_by_slug(slug) is not None:
            raise ValueError("技能标识已被占用，无法安装市场版")
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


async def uninstall_market_skill(db: AsyncSession, *, slug: str, user: User) -> dict:
    """移除当前用户的市场安装指针，保留发布版本与其他用户安装。"""
    removed = await MarketSkillRepository(db).uninstall_by_slug(str(user.uid), slug)
    await db.commit()
    await refresh_user_skill_projection_async(str(user.uid))
    return {"slug": slug, "uninstalled": removed}


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
                category=release.category or "other",
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
    if not target.is_file():
        raise ValueError("文件不存在")
    if not is_skill_text_path(target):
        raise ValueError("仅支持预览文本文件")
    try:
        content = await asyncio.to_thread(target.read_text, encoding="utf-8")
    except UnicodeError as exc:
        raise ValueError("仅支持预览 UTF-8 文本文件") from exc
    if target != (root / "SKILL.md").resolve() or user.role in {"admin", "superadmin"}:
        return content
    if not content.startswith("---"):
        raise ValueError("SKILL.md frontmatter 无效")
    visible_mcps = await get_visible_mcp_reference_slugs(db, user)
    return project_skill_markdown_mcp_dependencies(content, visible_mcps)


async def market_skill_tree(db: AsyncSession, *, slug: str, user: User) -> list[dict]:
    """列出当前审核版本的文件结构。"""
    repo = MarketSkillRepository(db)
    listing = await repo.visible_by_slug(user, slug)
    if listing is None:
        raise ValueError("市场 Skill 不存在")
    release = await repo.release(listing.latest_release_id)
    root = get_skill_data_dir() / release.dir_path
    return await asyncio.to_thread(build_skill_tree, root, root)
