"""内部 Skill 市场的 PostgreSQL 查询。"""

from sqlalchemy import func, or_, select, true, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

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
            return true()
        if user.department_id is None:
            return MarketSkill.scope == "global"
        return or_(
            MarketSkill.scope == "global",
            (MarketSkill.scope == "department") & (MarketSkill.department_id == user.department_id),
        )

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
        statement = select(MarketSkill).where(MarketSkill.latest_release_id.is_not(None), self._visible_filter(user))
        if query.strip() or category is not None:
            statement = statement.join(MarketSkillRelease, MarketSkillRelease.id == MarketSkill.latest_release_id)
        if category is not None:
            statement = statement.where(MarketSkillRelease.category == category)
        if query.strip():
            pattern = f"%{query.strip()}%"
            statement = statement.where(
                or_(
                    MarketSkill.slug.ilike(pattern),
                    MarketSkillRelease.name.ilike(pattern),
                    MarketSkillRelease.description.ilike(pattern),
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
        return await self.db.scalar(select(MarketSkill).where(MarketSkill.slug == slug).with_for_update())

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

    async def installs_for_user(self, uid: str) -> list[MarketSkillInstall]:
        """列出用户所有安装。"""
        return list((await self.db.scalars(select(MarketSkillInstall).where(MarketSkillInstall.uid == uid))).all())

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
