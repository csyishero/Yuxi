"""市场条目的管理员治理、受众依赖校验和可逆投影撤销。"""

import json

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.agents.mcp.service import requires_mcp_stdio_migration
from yuxi.agents.skills.market_repository import MarketSkillRepository
from yuxi.agents.skills.service import stage_market_projection_revocation
from yuxi.permissions import normalize_permission_config
from yuxi.services.operation_log_service import log_operation
from yuxi.storage.postgres.models_business import (
    Department,
    MarketSkillInstall,
    MCPServer,
    Skill,
    SkillShareRequest,
    User,
)
from yuxi.utils.datetime_utils import utc_now_naive


class MarketConflictError(ValueError):
    """页面版本已过期，拒绝覆盖当前治理状态。"""


class MarketScopeValidationError(ValueError):
    """只携带受控范围错误码和可选依赖身份。"""

    def __init__(self, code: str, *, dependency_type: str | None = None, dependency_slug: str | None = None):
        if code not in {
            "invalid_scope",
            "target_department_required",
            "target_department_not_found",
            "dependency_unavailable",
            "dependency_scope",
        }:
            raise ValueError("无效范围错误码")
        if dependency_type not in {None, "tool", "mcp", "skill"}:
            raise ValueError("无效依赖类型")
        self.code = code
        self.dependency_type = dependency_type
        self.dependency_slug = dependency_slug
        super().__init__(code)


async def list_manageable_market_skills(
    db: AsyncSession,
    *,
    operator: User,
    query: str = "",
    slug: str | None = None,
    status: str | None = None,
    scope: str | None = None,
    offset: int = 0,
    limit: int = 20,
) -> tuple[list[dict], int]:
    """返回当前管理员授权范围内的治理清单分页与总数。"""
    repository = MarketSkillRepository(db)
    total = await repository.count_manageable(operator, query=query, slug=slug, status=status, scope=scope)
    items = await repository.manageable_page(
        operator, query=query, slug=slug, status=status, scope=scope, offset=offset, limit=limit
    )
    return [await _management_dict(db, item) for item in items], total


async def get_manageable_market_skill(db: AsyncSession, *, slug: str, operator: User) -> dict:
    """返回当前管理范围内的详情，包括下架状态。"""
    item = await MarketSkillRepository(db).manageable_by_slug(operator, slug)
    if item is None:
        raise ValueError("市场 Skill 不存在")
    return await _management_dict(db, item)


async def unpublish_market_skill(db: AsyncSession, *, slug: str, expected_revision: int, operator: User) -> dict:
    """在一个事务中下架、失效旧申请并撤销所有历史投影。"""
    try:
        item = await _locked_listing(db, slug, expected_revision, operator)
        before = await _management_dict(db, item)
        pending = (
            await db.scalars(
                select(SkillShareRequest)
                .where(
                    SkillShareRequest.owner_uid == item.owner_uid,
                    SkillShareRequest.personal_slug == item.personal_slug,
                    SkillShareRequest.channel == "market",
                    SkillShareRequest.status == "pending",
                )
                .order_by(SkillShareRequest.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).all()
        now = utc_now_naive()
        for request in pending:
            request.status = "rejected"
            request.review_note = "条目已下架，需重新提交"
            request.reviewer_uid = str(operator.uid)
            request.reviewed_at = now
        item.is_published = False
        item.publication_generation += 1
        item.state_revision += 1
        item.updated_at = now
        return await _commit_policy(db, item, operator, before, retained_uids=set(), operation="skill_market_unpublish")
    except BaseException:
        await db.rollback()
        raise


async def change_market_skill_scope(
    db: AsyncSession, *, slug: str, scope: str, department_id: int | None, expected_revision: int, operator: User
) -> dict:
    """仅超级管理员可在校验完整目标受众后调整当前范围。"""
    if operator.role != "superadmin":
        raise PermissionError("仅超级管理员可调整市场范围")
    try:
        item = await _locked_listing(db, slug, expected_revision, operator)
        if scope not in {"global", "department"}:
            raise MarketScopeValidationError("invalid_scope")
        if scope == "department":
            if department_id is None:
                raise MarketScopeValidationError("target_department_required")
            if await db.get(Department, department_id) is None:
                raise MarketScopeValidationError("target_department_not_found")
        else:
            department_id = None
        await _validate_target_dependencies(db, item, scope, department_id)
        before = await _management_dict(db, item)
        item.scope = scope
        item.department_id = department_id
        item.state_revision += 1
        item.updated_at = utc_now_naive()
        users = (await db.scalars(select(User))).all()
        retained = {
            str(user.uid)
            for user in users
            if not user.is_deleted
            and (scope == "global" or user.role == "superadmin" or user.department_id == department_id)
        }
        return await _commit_policy(db, item, operator, before, retained_uids=retained, operation="skill_market_scope")
    except BaseException:
        await db.rollback()
        raise


async def _locked_listing(db, slug, expected_revision, operator):
    """在当前授权范围取条目锁并比较防 ABA 的版本。"""
    item = await MarketSkillRepository(db).manageable_by_slug(operator, slug, for_update=True)
    if item is None:
        raise ValueError("市场 Skill 不存在")
    if item.state_revision != expected_revision:
        raise MarketConflictError("市场状态已变化，请刷新后重试")
    if not item.is_published:
        raise ValueError("市场 Skill 已下架")
    return item


async def _management_dict(db, item):
    """仅在管理授权检查后组装当前作者和版本信息。"""
    release = await MarketSkillRepository(db).release(item.latest_release_id)
    author = await db.scalar(select(User).where(User.uid == item.owner_uid))
    return {
        "slug": item.slug,
        "name": release.name if release else item.slug,
        "is_published": item.is_published,
        "scope": item.scope,
        "department_id": item.department_id,
        "publication_generation": item.publication_generation,
        "state_revision": item.state_revision,
        "latest_version": release.version if release else None,
        "author_uid": item.owner_uid,
        "author_name": author.username if author else None,
        "status": "published" if item.is_published else "unpublished",
    }


async def _commit_policy(db, item, operator, before, *, retained_uids, operation):
    """先撤销文件，后在 UID 锁内提交策略和日志。"""
    repo = MarketSkillRepository(db)
    releases = await repo.releases(item.id)
    installers = set(
        (await db.scalars(select(MarketSkillInstall.uid).where(MarketSkillInstall.market_skill_id == item.id))).all()
    )
    after = await _management_dict(db, item)
    # flush 失败可能自动释放事务锁，必须在触碰文件之前完成所有数据库写入。
    await log_operation(db, operator.id, operation, json.dumps({"before": before, "after": after}, ensure_ascii=False))
    async with stage_market_projection_revocation(
        db, release_ids={release.id for release in releases}, installer_uids=installers, retained_uids=retained_uids
    ):
        await db.commit()
    return after


async def _validate_target_dependencies(db, item, scope, department_id):
    """检查本批次全部版本的存储权限，不能借用管理员自身的权限。"""
    from yuxi.agents.toolkits.service import get_tool_metadata

    releases = await MarketSkillRepository(db).releases(item.id)
    dependencies = {kind: set() for kind in ("tool", "mcp", "skill")}
    for release in releases:
        if release.version >= item.active_from_version:
            for kind in dependencies:
                dependencies[kind].update(getattr(release, f"{kind}_dependencies") or [])
    tools = {tool["slug"] for tool in get_tool_metadata()} if dependencies["tool"] else set()
    for kind, slugs in dependencies.items():
        for slug in sorted(slugs):
            if kind == "tool":
                if slug not in tools:
                    raise MarketScopeValidationError(
                        "dependency_unavailable", dependency_type=kind, dependency_slug=slug
                    )
                continue
            model = MCPServer if kind == "mcp" else Skill
            dependency = await db.scalar(
                select(model).where(model.slug == slug).execution_options(populate_existing=True)
            )
            if (
                dependency is None
                or not dependency.enabled
                or (kind == "mcp" and requires_mcp_stdio_migration(dependency))
            ):
                raise MarketScopeValidationError("dependency_unavailable", dependency_type=kind, dependency_slug=slug)
            if kind == "mcp" and dependency.share_config is None:
                continue
            try:
                config = normalize_permission_config(dependency.share_config)
            except ValueError as error:
                raise MarketScopeValidationError(
                    "dependency_scope", dependency_type=kind, dependency_slug=slug
                ) from error
            scopes = [config.get("read_scope")]
            if kind == "mcp":
                scopes.append(config.get("edit_scope"))
            if not all(_covers_target(stored, scope, department_id) for stored in scopes):
                raise MarketScopeValidationError("dependency_scope", dependency_type=kind, dependency_slug=slug)


def _covers_target(stored, scope, department_id):
    """全公司或完整目标部门授权才覆盖受众，逐人授权永远不足。"""
    if not stored:
        return False
    if stored.get("access_level") == "global":
        return True
    return (
        scope == "department"
        and stored.get("access_level") == "department"
        and department_id in (stored.get("department_ids") or [])
    )
