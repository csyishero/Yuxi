from __future__ import annotations

from sqlalchemy import String, and_, case, cast, exists, false, func, or_, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from yuxi.storage.postgres.models_business import MarketSkill, Skill, SkillShareRequest, User
from yuxi.utils.datetime_utils import utc_now_naive


RETIRED_BUILTIN_SKILL_SLUGS = ("image-gen", "mysql-reporter")


class SkillRepository:
    def __init__(self, db_session: AsyncSession):
        self.db = db_session

    async def list_all(self) -> list[Skill]:
        result = await self.db.execute(select(Skill).order_by(Skill.updated_at.desc(), Skill.id.desc()))
        return list(result.scalars().all())

    async def list_enabled(self) -> list[Skill]:
        result = await self.db.execute(
            select(Skill).where(Skill.enabled.is_(True)).order_by(Skill.updated_at.desc(), Skill.id.desc())
        )
        return list(result.scalars().all())

    async def matching_shared_slugs(
        self, slugs: set[str], *, search: str = "", shared_kind: str = "shared"
    ) -> set[str]:
        """只查可能被同名个人原件覆盖的目录技能标识。"""
        if not slugs:
            return set()
        statement = select(Skill.slug).where(
            Skill.slug.in_(slugs),
            Skill.source_type == "builtin" if shared_kind == "builtin" else Skill.source_type != "builtin",
            Skill.enabled.is_(True),
        )
        if search.strip():
            needle = search.strip()
            statement = statement.where(
                or_(
                    Skill.slug.icontains(needle, autoescape=True),
                    Skill.name.icontains(needle, autoescape=True),
                    Skill.description.icontains(needle, autoescape=True),
                )
            )
        return set((await self.db.scalars(statement)).all())

    async def page_visible(
        self,
        user,
        *,
        limit: int,
        offset: int,
        search: str = "",
        audience_search: str | None = None,
        audience_department_id: int | None = None,
        shared_kind: str | None = None,
        exclude_retired_builtin: bool = False,
        catalog_all: bool = False,
        shadowed_personal_slugs: set[str] | None = None,
        installed_market_slugs: set[str] | None = None,
    ) -> tuple[list[Skill], int]:
        """在数据库中按读取和管理授权筛选共享 Skill，并返回稳定分页。"""
        config = cast(Skill.share_config, JSONB)

        def members(scope, field: str, alias: str):
            """安全展开历史配置中的数组，避免缺失字段或非数组使查询失败。"""
            raw = scope[field]
            safe_array = case((func.jsonb_typeof(raw) == "array", raw), else_=func.jsonb_build_array())
            return func.jsonb_array_elements_text(safe_array).table_valued("value").alias(alias)

        def department_scope_matches(scope, alias: str, *, exact: bool = False):
            """按权限函数的 int 转换语义匹配部门，并容忍重复及字符串 ID。"""
            elements = members(scope, "department_ids", alias)
            value = func.btrim(elements.c.value)
            department = int(user.department_id)
            matches_id = value.op("~")(rf"^\+?0*{department}$")
            has_match = exists(select(1).select_from(elements).where(matches_id))
            if exact:
                return and_(has_match, ~exists(select(1).select_from(elements).where(~matches_id)))
            valid_id = value.op("~")(r"^[+-]?[0-9]+$")
            return and_(has_match, ~exists(select(1).select_from(elements).where(~valid_id)))

        def scope_matches(key: str):
            scope = config[key]
            access_level = scope["access_level"].as_string()
            conditions = [
                or_(
                    access_level == "global",
                    and_(
                        func.jsonb_typeof(scope) == "object",
                        or_(access_level.is_(None), access_level == ""),
                    ),
                )
            ]
            if user.department_id is not None:
                conditions.append(
                    and_(
                        access_level == "department",
                        department_scope_matches(scope, f"{key}_departments"),
                    )
                )
            user_members = members(scope, "user_uids", f"{key}_users")
            conditions.append(
                and_(
                    access_level == "user",
                    exists(
                        select(1).select_from(user_members).where(func.btrim(user_members.c.value) == str(user.uid))
                    ),
                )
            )
            return or_(*conditions)

        if user.role == "superadmin":
            visibility = True
        else:
            read = scope_matches("read_scope")
            edit = scope_matches("edit_scope")
            manage = scope_matches("manage_scope")
            read_empty = config["read_scope"].as_string().is_(None)
            edit_empty = config["edit_scope"].as_string().is_(None)
            managed = or_(
                Skill.created_by == str(user.uid),
                and_(manage, or_(edit_empty, edit), or_(read_empty, read)),
            )
            if user.role == "admin":
                department_managed = False
                if user.department_id is not None:
                    department_managed = and_(
                        Skill.source_type.in_(("upload", "remote", "personal_share")),
                        config["owner_department_id"].as_string() == str(user.department_id),
                        config["read_scope"]["access_level"].as_string() == "department",
                        department_scope_matches(config["read_scope"], "owned_departments", exact=True),
                    )
                managed = or_(managed, department_managed)
            readable = or_(read, and_(read_empty, edit))
            authorized = and_(
                config["version"].as_string() == "2",
                or_(managed, and_(Skill.enabled.is_(True), readable)),
            )
            visibility = or_(Skill.source_type == "builtin", authorized) if user.role == "admin" else authorized

        filters = [visibility]
        if shared_kind == "builtin":
            filters.extend(
                (
                    Skill.source_type == "builtin",
                    Skill.enabled.is_(True),
                    Skill.slug.notin_(RETIRED_BUILTIN_SKILL_SLUGS),
                )
            )
        elif shared_kind == "shared":
            filters.extend((Skill.source_type != "builtin", Skill.enabled.is_(True)))
        if exclude_retired_builtin:
            filters.append(or_(Skill.source_type != "builtin", Skill.slug.notin_(RETIRED_BUILTIN_SKILL_SLUGS)))
            # 内置技能只有管理员可管理。普通用户即使恰好是历史记录的创建者，
            # 禁用后也不能经过下面的授权后处理，因此必须在 count/page 前排除。
            if user.role not in {"admin", "superadmin"}:
                filters.append(or_(Skill.source_type != "builtin", Skill.enabled.is_(True)))
        if search.strip():
            needle = search.strip()
            filters.append(
                or_(
                    Skill.slug.icontains(needle, autoescape=True),
                    Skill.name.icontains(needle, autoescape=True),
                    Skill.description.icontains(needle, autoescape=True),
                )
            )

        if audience_search or audience_department_id is not None:
            # 筛选目标用户的条件必须与卡片授权一起在 count/page 前执行。
            # 这里用 EXISTS 避免将整个用户表和共享技能表读入应用再切片。
            target_filters = [User.is_deleted == 0]
            if audience_department_id is not None:
                target_filters.append(User.department_id == audience_department_id)
            if audience_search:
                target_filters.append(
                    or_(
                        User.username.icontains(audience_search, autoescape=True),
                        User.uid.icontains(audience_search, autoescape=True),
                        User.phone_number.icontains(audience_search, autoescape=True),
                    )
                )

            read_scope = config["read_scope"]
            read_level = read_scope["access_level"].as_string()
            global_read = or_(
                read_level == "global",
                and_(
                    func.jsonb_typeof(read_scope) == "object",
                    or_(read_level.is_(None), read_level == ""),
                ),
            )

            department_members = members(read_scope, "department_ids", "audience_departments")
            department_value = func.btrim(department_members.c.value)
            department_matches = exists(
                select(1)
                .select_from(department_members)
                .where(
                    department_value.op("~")(
                        func.concat(r"^\+?0*", cast(User.department_id, String), "$"),
                    )
                )
                .correlate(Skill, User)
            )
            user_members = members(read_scope, "user_uids", "audience_users")
            user_matches = exists(
                select(1)
                .select_from(user_members)
                .where(func.btrim(user_members.c.value) == User.uid)
                .correlate(Skill, User)
            )

            audience_exists = exists(select(1).select_from(User).where(*target_filters).correlate(Skill))
            department_audience_exists = exists(
                select(1)
                .select_from(User)
                .where(*target_filters, User.department_id.is_not(None), department_matches)
                .correlate(Skill)
            )
            user_audience_exists = exists(
                select(1).select_from(User).where(*target_filters, user_matches).correlate(Skill)
            )
            filters.append(
                or_(
                    Skill.source_type == "builtin",
                    and_(global_read, audience_exists),
                    and_(read_level == "department", department_audience_exists),
                    and_(read_level == "user", user_audience_exists),
                )
            )

        where = and_(*filters)
        if catalog_all:
            if shared_kind not in {"shared", "builtin"}:
                raise ValueError("全部目录仅适用于共享或内置技能分类")
            if shadowed_personal_slugs:
                where = and_(where, Skill.slug.notin_(shadowed_personal_slugs))
            if shared_kind == "builtin":
                if installed_market_slugs:
                    where = and_(where, Skill.slug.notin_(installed_market_slugs))
                total = await self.db.scalar(select(func.count()).select_from(Skill).where(where))
                rows = await self.db.scalars(
                    select(Skill)
                    .where(where)
                    .order_by(Skill.updated_at.desc(), Skill.id.desc())
                    .offset(offset)
                    .limit(limit)
                )
                return list(rows.all()), int(total or 0)

            related_market_slug = self._catalog_related_market_slug(user)
            filtered = (
                select(
                    Skill.id.label("id"),
                    Skill.slug.label("slug"),
                    related_market_slug.label("related_market_slug"),
                )
                .where(where)
                .cte("catalog_filtered")
            )
            available = select(filtered)
            if installed_market_slugs:
                available = available.where(
                    func.coalesce(filtered.c.related_market_slug, filtered.c.slug).notin_(installed_market_slugs)
                )
            available = available.cte("catalog_available")
            origin_kind = case((available.c.related_market_slug.is_not(None), "market"), else_="shared")
            origin_slug = func.coalesce(available.c.related_market_slug, available.c.slug)
            ranked = select(
                available.c.id,
                func.row_number()
                .over(partition_by=(origin_kind, origin_slug), order_by=available.c.id.desc())
                .label("origin_rank"),
            ).cte("catalog_ranked")
            selected = ranked.c.origin_rank == 1
            total = await self.db.scalar(select(func.count()).select_from(ranked).where(selected))
            rows = await self.db.scalars(
                select(Skill)
                .join(ranked, ranked.c.id == Skill.id)
                .where(selected)
                .order_by(Skill.updated_at.desc(), Skill.id.desc())
                .offset(offset)
                .limit(limit)
            )
            return list(rows.all()), int(total or 0)

        total = await self.db.scalar(select(func.count()).select_from(Skill).where(where))
        rows = await self.db.scalars(
            select(Skill).where(where).order_by(Skill.updated_at.desc(), Skill.id.desc()).offset(offset).limit(limit)
        )
        return list(rows.all()), int(total or 0)

    @staticmethod
    def _catalog_related_market_slug(user: User):
        """按审核来源真实性和当前市场授权计算共享版本的同源条目。"""
        request = aliased(SkillShareRequest)
        other = aliased(SkillShareRequest)

        def matches_published_skill(item):
            return and_(
                item.content_hash.is_not(None),
                item.content_hash != "",
                item.name == Skill.name,
                item.description == Skill.description,
                item.reviewer_uid == Skill.created_by,
                Skill.created_at.is_not(None),
                item.reviewed_at.is_not(None),
                Skill.created_at >= item.created_at,
                Skill.created_at <= item.reviewed_at,
            )

        conflicting_request = exists(
            select(1)
            .select_from(other)
            .where(
                other.published_slug == Skill.slug,
                other.channel == "shared",
                other.status == "approved",
                or_(
                    other.owner_uid.is_distinct_from(request.owner_uid),
                    other.personal_slug.is_distinct_from(request.personal_slug),
                    other.content_hash.is_distinct_from(request.content_hash),
                    ~func.coalesce(matches_published_skill(other), false()),
                ),
            )
        ).correlate(Skill, request)

        market_visible = True if user.role == "superadmin" else MarketSkill.scope == "global"
        if user.role != "superadmin" and user.department_id is not None:
            market_visible = or_(
                market_visible,
                and_(MarketSkill.scope == "department", MarketSkill.department_id == user.department_id),
            )

        return (
            select(MarketSkill.slug)
            .select_from(request)
            .join(
                MarketSkill,
                and_(
                    MarketSkill.owner_uid == request.owner_uid,
                    MarketSkill.personal_slug == request.personal_slug,
                ),
            )
            .where(
                Skill.source_type == "personal_share",
                Skill.content_hash.is_not(None),
                Skill.content_hash != "",
                request.published_slug == Skill.slug,
                request.channel == "shared",
                request.status == "approved",
                func.coalesce(matches_published_skill(request), false()),
                ~conflicting_request,
                MarketSkill.latest_release_id.is_not(None),
                MarketSkill.is_published.is_(True),
                market_visible,
            )
            .limit(1)
            .correlate(Skill)
            .scalar_subquery()
        )

    async def get_by_slug(self, slug: str, *, for_update: bool = False) -> Skill | None:
        stmt = select(Skill).where(Skill.slug == slug)
        if for_update:
            stmt = stmt.with_for_update()
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def exists_slug(self, slug: str) -> bool:
        return (await self.get_by_slug(slug)) is not None

    async def create(
        self,
        *,
        slug: str,
        name: str,
        description: str,
        category: str = "other",
        source_type: str,
        tool_dependencies: list[str] | None,
        mcp_dependencies: list[str] | None,
        skill_dependencies: list[str] | None,
        dir_path: str,
        share_config: dict,
        enabled: bool = True,
        version: str | None = None,
        content_hash: str | None = None,
        created_by: str | None,
    ) -> Skill:
        now = utc_now_naive()
        item = Skill(
            slug=slug,
            name=name,
            description=description,
            category=category,
            source_type=source_type,
            tool_dependencies=tool_dependencies or [],
            mcp_dependencies=mcp_dependencies or [],
            skill_dependencies=skill_dependencies or [],
            dir_path=dir_path,
            version=version,
            content_hash=content_hash,
            share_config=share_config,
            enabled=enabled,
            created_by=created_by,
            updated_by=created_by,
            created_at=now,
            updated_at=now,
        )
        self.db.add(item)
        await self.db.flush()
        await self.db.refresh(item)
        return item

    async def update_builtin_install(
        self,
        item: Skill,
        *,
        version: str,
        content_hash: str,
        updated_by: str | None,
    ) -> Skill:
        item.version = version
        item.content_hash = content_hash
        item.source_type = "builtin"
        item.share_config = {
            "version": 2,
            "read_scope": {"access_level": "global", "department_ids": [], "user_uids": []},
            "manage_scope": None,
        }
        item.updated_by = updated_by
        item.updated_at = utc_now_naive()
        await self.db.flush()
        await self.db.refresh(item)
        return item

    async def update_dependencies(
        self,
        item: Skill,
        *,
        tool_dependencies: list[str],
        mcp_dependencies: list[str],
        skill_dependencies: list[str],
        updated_by: str | None,
    ) -> Skill:
        item.tool_dependencies = tool_dependencies
        item.mcp_dependencies = mcp_dependencies
        item.skill_dependencies = skill_dependencies
        item.updated_by = updated_by
        item.updated_at = utc_now_naive()
        await self.db.flush()
        await self.db.refresh(item)
        return item

    async def update_metadata(
        self,
        item: Skill,
        *,
        name: str,
        description: str,
        updated_by: str | None,
    ) -> Skill:
        item.name = name
        item.description = description
        item.updated_by = updated_by
        item.updated_at = utc_now_naive()
        await self.db.flush()
        await self.db.refresh(item)
        return item

    async def update_share_config(self, item: Skill, *, share_config: dict, updated_by: str | None) -> Skill:
        item.share_config = share_config
        item.updated_by = updated_by
        item.updated_at = utc_now_naive()
        await self.db.flush()
        await self.db.refresh(item)
        return item

    async def update_enabled(self, item: Skill, *, enabled: bool, updated_by: str | None) -> Skill:
        item.enabled = enabled
        item.updated_by = updated_by
        item.updated_at = utc_now_naive()
        await self.db.flush()
        await self.db.refresh(item)
        return item

    async def delete(self, item: Skill) -> None:
        await self.db.delete(item)
        await self.db.flush()
