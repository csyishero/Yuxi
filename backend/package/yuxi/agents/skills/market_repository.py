"""内部 Skill 市场的 PostgreSQL 查询。"""

from sqlalchemy import and_, case, delete, func, or_, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from yuxi.storage.postgres.models_business import (
    MarketSkill,
    MarketSkillInstall,
    MarketSkillLike,
    MarketSkillRelease,
    User,
)


class MarketSkillRepository:
    """按用户授权范围读写市场条目与安装事实。"""

    def __init__(self, db: AsyncSession):
        self.db = db

    @staticmethod
    def _visible_filter(user: User):
        """约束市场条目只能被当前用户所在组织看到。"""
        if user.role == "superadmin":
            return MarketSkill.is_published.is_(True)
        if user.department_id is None:
            return and_(MarketSkill.is_published.is_(True), MarketSkill.scope == "global")
        return and_(
            MarketSkill.is_published.is_(True),
            or_(
                MarketSkill.scope == "global",
                (MarketSkill.scope == "department") & (MarketSkill.department_id == user.department_id),
            ),
        )

    @staticmethod
    def _manageable_filter(operator: User):
        """管理权限仅跟随条目当前范围，包含已下架记录。"""
        if operator.role == "superadmin":
            return MarketSkill.latest_release_id.is_not(None)
        if operator.role != "admin":
            raise PermissionError("无权管理市场 Skill")
        return and_(
            MarketSkill.latest_release_id.is_not(None),
            MarketSkill.scope == "department",
            MarketSkill.department_id == operator.department_id,
            MarketSkill.department_id.is_not(None),
        )

    async def manageable(self, operator: User) -> list[MarketSkill]:
        """列出管理员当前范围内的稳定条目。"""
        return list(
            (
                await self.db.scalars(
                    select(MarketSkill)
                    .where(self._manageable_filter(operator))
                    .order_by(MarketSkill.updated_at.desc(), MarketSkill.id)
                )
            ).all()
        )

    async def manageable_by_slug(self, operator: User, slug: str, *, for_update: bool = False) -> MarketSkill | None:
        """在当前管理范围内读取条目，写入时刷新并锁定。"""
        statement = select(MarketSkill).where(MarketSkill.slug == slug, self._manageable_filter(operator))
        if for_update:
            statement = statement.with_for_update().execution_options(populate_existing=True)
        return await self.db.scalar(statement)

    def _filtered_visible_statement(
        self,
        user: User,
        *,
        query: str = "",
        category: str | None = None,
        slug: str | None = None,
        installed_only: bool = False,
    ):
        """复用授权与筛选条件，使列表和总数保持一致。"""
        statement = select(MarketSkill).where(MarketSkill.latest_release_id.is_not(None), self._visible_filter(user))
        search = query.strip()
        if search or category is not None:
            statement = statement.join(MarketSkillRelease, MarketSkillRelease.id == MarketSkill.latest_release_id)
        if category is not None:
            statement = statement.where(MarketSkillRelease.category == category)
        if search:
            statement = statement.where(
                or_(
                    MarketSkill.slug.icontains(search, autoescape=True),
                    MarketSkillRelease.name.icontains(search, autoescape=True),
                    MarketSkillRelease.description.icontains(search, autoescape=True),
                )
            )
        if slug is not None:
            statement = statement.where(MarketSkill.slug == slug)
        if installed_only:
            statement = statement.where(
                MarketSkill.id.in_(
                    select(MarketSkillInstall.market_skill_id).where(MarketSkillInstall.uid == str(user.uid))
                )
            )
        return statement

    async def count_visible(
        self,
        user: User,
        *,
        query: str = "",
        category: str | None = None,
        installed_only: bool = False,
    ) -> int:
        """计算当前用户授权和筛选后可见的市场条目数。"""
        statement = self._filtered_visible_statement(
            user, query=query, category=category, installed_only=installed_only
        )
        total = await self.db.scalar(statement.with_only_columns(func.count(MarketSkill.id)))
        return int(total or 0)

    async def visible(
        self,
        user: User,
        *,
        query: str = "",
        category: str | None = None,
        slug: str | None = None,
        sort: str = "recent",
        installed_only: bool = False,
        offset: int = 0,
        limit: int = 100,
    ) -> list[MarketSkill]:
        """只返回当前用户可见的市场条目。"""
        statement = self._filtered_visible_statement(
            user, query=query, category=category, slug=slug, installed_only=installed_only
        )
        if sort == "installs":
            count = (
                select(func.count())
                .select_from(MarketSkillInstall)
                .where(MarketSkillInstall.market_skill_id == MarketSkill.id)
                .correlate(MarketSkill)
                .scalar_subquery()
            )
            statement = statement.order_by(count.desc(), MarketSkill.updated_at.desc(), MarketSkill.id.desc())
        elif sort == "likes":
            count = (
                select(func.count())
                .select_from(MarketSkillLike)
                .where(MarketSkillLike.market_skill_id == MarketSkill.id)
                .correlate(MarketSkill)
                .scalar_subquery()
            )
            statement = statement.order_by(count.desc(), MarketSkill.updated_at.desc(), MarketSkill.id.desc())
        else:
            statement = statement.order_by(MarketSkill.updated_at.desc(), MarketSkill.id.desc())
        return list((await self.db.scalars(statement.offset(offset).limit(limit))).all())

    async def ranked_visible(
        self,
        user: User,
        *,
        metric: str,
        department_id: int | None,
        offset: int,
        limit: int,
    ) -> list[tuple[MarketSkill, int, int]]:
        """按活跃用户当前部门统计并排序可见的市场条目。"""

        def count_for(model):
            """计算符合当前榜单范围的用户行为数。"""
            statement = (
                select(func.count())
                .select_from(model)
                .join(User, User.uid == model.uid)
                .where(model.market_skill_id == MarketSkill.id, User.is_deleted == 0)
            )
            if department_id is not None:
                statement = statement.where(User.department_id == department_id)
            return statement.correlate(MarketSkill).scalar_subquery()

        installs = count_for(MarketSkillInstall)
        likes = count_for(MarketSkillLike)
        sort_count = installs if metric == "installs" else likes
        statement = (
            select(MarketSkill, installs, likes)
            .where(MarketSkill.latest_release_id.is_not(None), self._visible_filter(user), sort_count > 0)
            .order_by(sort_count.desc(), MarketSkill.updated_at.desc(), MarketSkill.id.desc())
            .offset(offset)
            .limit(limit)
        )
        return [
            (item, int(install_count), int(like_count))
            for item, install_count, like_count in (await self.db.execute(statement)).all()
        ]

    async def visible_by_slug(self, user: User, slug: str) -> MarketSkill | None:
        """在可见目录中按稳定 slug 定位条目。"""
        for item in await self.visible(user, slug=slug):
            if item.slug == slug:
                return item
        return None

    async def visible_by_slug_for_update(self, user: User, slug: str) -> MarketSkill | None:
        """先锁定当前可见条目，再允许修改用户安装确认。"""
        return await self.db.scalar(
            select(MarketSkill)
            .where(MarketSkill.slug == slug, MarketSkill.latest_release_id.is_not(None), self._visible_filter(user))
            .with_for_update()
            .execution_options(populate_existing=True)
        )

    async def visible_by_sources(self, user: User, sources: set[tuple[str, str]]) -> list[MarketSkill]:
        """不经目录分页，读取当前用户可见的指定个人来源。"""
        if not sources:
            return []
        statement = select(MarketSkill).where(
            MarketSkill.latest_release_id.is_not(None),
            self._visible_filter(user),
            tuple_(MarketSkill.owner_uid, MarketSkill.personal_slug).in_(sources),
        )
        return list((await self.db.scalars(statement)).all())

    async def visible_by_ids(self, user: User, ids: set[str]) -> list[MarketSkill]:
        """读取已安装候选的可见来源身份。"""
        if not ids:
            return []
        statement = select(MarketSkill).where(
            MarketSkill.id.in_(ids), MarketSkill.latest_release_id.is_not(None), self._visible_filter(user)
        )
        return list((await self.db.scalars(statement)).all())

    async def by_slug_for_update(self, slug: str) -> MarketSkill | None:
        """锁定稳定条目以串行发布新版本。"""
        return await self.db.scalar(
            select(MarketSkill)
            .where(MarketSkill.slug == slug)
            .with_for_update()
            .execution_options(populate_existing=True)
        )

    async def published_slug_exists(self, slug: str) -> bool:
        """确认共享安装前检测已发布的同名市场条目。"""
        return (
            await self.db.scalar(
                select(MarketSkill.id).where(
                    MarketSkill.slug == slug,
                    MarketSkill.latest_release_id.is_not(None),
                )
            )
        ) is not None

    async def by_owner_source_for_update(self, uid: str, personal_slug: str) -> MarketSkill | None:
        """定位作者同一原件已经发布的条目。"""
        return await self.db.scalar(
            select(MarketSkill)
            .where(MarketSkill.owner_uid == uid, MarketSkill.personal_slug == personal_slug)
            .order_by(MarketSkill.created_at)
            .with_for_update()
            .execution_options(populate_existing=True)
        )

    async def release(self, release_id: str) -> MarketSkillRelease | None:
        """读取指定不可变版本。"""
        return await self.db.get(MarketSkillRelease, release_id)

    async def releases(self, market_skill_id: str) -> list[MarketSkillRelease]:
        """列出条目的所有版本。"""
        return list(
            (
                await self.db.scalars(
                    select(MarketSkillRelease)
                    .where(MarketSkillRelease.market_skill_id == market_skill_id)
                    .order_by(MarketSkillRelease.version)
                )
            ).all()
        )

    async def install(self, uid: str, market_skill_id: str) -> MarketSkillInstall | None:
        """读取用户安装指针。"""
        return await self.db.get(MarketSkillInstall, (uid, market_skill_id))

    async def install_for_update(self, uid: str, market_skill_id: str) -> MarketSkillInstall | None:
        """在条目锁内锁定本人安装行。"""
        return await self.db.scalar(
            select(MarketSkillInstall)
            .where(MarketSkillInstall.uid == uid, MarketSkillInstall.market_skill_id == market_skill_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )

    async def uninstall_by_slug(self, uid: str, slug: str) -> bool:
        """仅删除当前用户对稳定市场条目的安装指针。"""
        market_skill_id = select(MarketSkill.id).where(MarketSkill.slug == slug).scalar_subquery()
        statement = (
            delete(MarketSkillInstall)
            .where(MarketSkillInstall.uid == uid, MarketSkillInstall.market_skill_id == market_skill_id)
            .returning(MarketSkillInstall.market_skill_id)
        )
        return (await self.db.scalar(statement)) is not None

    async def installs_for_user(self, uid: str) -> list[MarketSkillInstall]:
        """列出用户所有安装。"""
        return list((await self.db.scalars(select(MarketSkillInstall).where(MarketSkillInstall.uid == uid))).all())

    async def installed_personal_slugs_for_user(self, uid: str) -> set[str]:
        """读取本人已安装市场条目对应的个人原件标识。"""
        rows = await self.db.scalars(
            select(MarketSkill.personal_slug)
            .join(MarketSkillInstall, MarketSkillInstall.market_skill_id == MarketSkill.id)
            .where(MarketSkillInstall.uid == uid, MarketSkill.owner_uid == uid)
        )
        return set(rows.all())

    def _installed_releases_statement(self, uid: str, *, user: User | None = None, search: str = ""):
        """复用安装列表与摘要的搜索及可见性条件。"""
        statement = (
            select(MarketSkill, MarketSkillRelease, MarketSkillInstall)
            .select_from(MarketSkillInstall)
            .join(MarketSkill, MarketSkill.id == MarketSkillInstall.market_skill_id)
            .join(
                MarketSkillRelease,
                (MarketSkillRelease.id == MarketSkillInstall.release_id)
                & (MarketSkillRelease.market_skill_id == MarketSkill.id),
            )
            .where(MarketSkillInstall.uid == uid)
        )
        if search.strip():
            if user is None:
                raise ValueError("搜索安装技能需要当前用户")
            needle = search.strip()
            current_release = aliased(MarketSkillRelease, name="current_release")
            statement = statement.outerjoin(current_release, current_release.id == MarketSkill.latest_release_id)
            eligible = and_(
                MarketSkillInstall.confirmed_generation == MarketSkill.publication_generation,
                MarketSkillRelease.version >= MarketSkill.active_from_version,
            )
            # 搜索与摘要展示相同版本，旧批次内容不能通过匹配结果和总数泄露。
            searchable_name = case((eligible, MarketSkillRelease.name), else_=current_release.name)
            searchable_description = case((eligible, MarketSkillRelease.description), else_=current_release.description)
            visible_release = and_(
                MarketSkill.latest_release_id.is_not(None),
                self._visible_filter(user),
                or_(
                    searchable_name.icontains(needle, autoescape=True),
                    searchable_description.icontains(needle, autoescape=True),
                ),
            )
            if user.department_id is None:
                statement = statement.where(MarketSkill.slug.icontains(needle, autoescape=True))
            else:
                statement = statement.where(or_(MarketSkill.slug.icontains(needle, autoescape=True), visible_release))
        return statement

    async def installed_releases_for_user(
        self, uid: str, *, user: User | None = None, search: str = ""
    ) -> list[tuple[MarketSkill, MarketSkillRelease, MarketSkillInstall]]:
        """只读取本人安装对应的市场身份和不可变版本。"""
        statement = self._installed_releases_statement(uid, user=user, search=search).order_by(
            MarketSkillInstall.updated_at.desc(), MarketSkill.id.desc()
        )
        return [(listing, release, install) for listing, release, install in (await self.db.execute(statement)).all()]

    async def installed_releases_for_personal_slugs(
        self, uid: str, slugs: set[str]
    ) -> list[tuple[MarketSkill, MarketSkillRelease, MarketSkillInstall]]:
        """只关联当前个人技能页由本人发布并安装的市场版本。"""
        if not slugs:
            return []
        statement = (
            select(MarketSkill, MarketSkillRelease, MarketSkillInstall)
            .select_from(MarketSkillInstall)
            .join(MarketSkill, MarketSkill.id == MarketSkillInstall.market_skill_id)
            .join(
                MarketSkillRelease,
                (MarketSkillRelease.id == MarketSkillInstall.release_id)
                & (MarketSkillRelease.market_skill_id == MarketSkill.id),
            )
            .where(
                MarketSkillInstall.uid == uid,
                MarketSkill.owner_uid == uid,
                MarketSkill.personal_slug.in_(slugs),
            )
        )
        return [(listing, release, install) for listing, release, install in (await self.db.execute(statement)).all()]

    async def page_installed_releases_for_user(
        self,
        uid: str,
        *,
        user: User,
        offset: int,
        limit: int,
        search: str = "",
        exclude_own_personal_slugs: set[str] | None = None,
    ) -> tuple[list[tuple[MarketSkill, MarketSkillRelease, MarketSkillInstall]], int]:
        """只取本人当前页的安装版本，并计算精确总数。"""
        joined = self._installed_releases_statement(uid, user=user, search=search)
        if exclude_own_personal_slugs and user.department_id is not None:
            joined = joined.where(
                or_(
                    MarketSkill.owner_uid != uid,
                    MarketSkill.personal_slug.notin_(exclude_own_personal_slugs),
                    ~self._visible_filter(user),
                    MarketSkill.latest_release_id.is_(None),
                )
            )
        total = await self.db.scalar(joined.with_only_columns(func.count()))
        statement = (
            joined.order_by(MarketSkillInstall.updated_at.desc(), MarketSkill.id.desc()).offset(offset).limit(limit)
        )
        rows = await self.db.execute(statement)
        return [(listing, release, install) for listing, release, install in rows.all()], int(total or 0)

    async def metrics(self, market_skill_id: str) -> tuple[int, int]:
        """从唯一安装和点赞行计算排行指标。"""
        installs = await self.db.scalar(
            select(func.count())
            .select_from(MarketSkillInstall)
            .where(MarketSkillInstall.market_skill_id == market_skill_id)
        )
        likes = await self.db.scalar(
            select(func.count()).select_from(MarketSkillLike).where(MarketSkillLike.market_skill_id == market_skill_id)
        )
        return int(installs or 0), int(likes or 0)

    async def liked(self, uid: str, market_skill_id: str) -> bool:
        """判断用户是否已点赞。"""
        return await self.db.get(MarketSkillLike, (uid, market_skill_id)) is not None
