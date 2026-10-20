"""个人技能空间的精确来源统计。"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.agents.skills.market import list_user_market_installs, personal_market_install_summaries
from yuxi.agents.skills.repository import RETIRED_BUILTIN_SKILL_SLUGS, SkillRepository
from yuxi.agents.skills.service import list_skill_cards_for_user
from yuxi.storage.postgres.models_business import User


async def personal_space_skill_counts(db: AsyncSession, user: User, *, search: str = "") -> dict[str, int]:
    """按完整授权来源计算当前用户个人空间的精确总数。"""
    cards = await list_skill_cards_for_user(db, user)
    needle = search.strip().casefold()

    def matches_personal(card) -> bool:
        return not needle or needle in f"{card.slug} {card.name} {card.description}".casefold()

    def matches_shared(card) -> bool:
        return not needle or any(needle in value.casefold() for value in (card.slug, card.name, card.description))

    created = [card for card in cards if card.source_scope == "personal" and matches_personal(card)]
    created_slugs = {card.slug for card in created}
    personal_slugs = {card.slug for card in cards if card.source_scope == "personal"}
    installs = await list_user_market_installs(db, user=user, search=search)

    # 个人原件可因名称匹配搜索，而它对应的市场条目没有命中搜索；
    # 全部视图仍用原件绑定的安装隐藏同源共享卡片。
    own_installs = await personal_market_install_summaries(db, user=user, personal_slugs=created_slugs)
    installed_market_slugs = {item["slug"] for item in [*installs, *own_installs.values()] if item["visible"]}
    private_total = len(created) + sum(1 for item in installs if item["own_personal_slug"] not in created_slugs)

    builtin_total = sum(
        1
        for card in cards
        if card.source_type == "builtin"
        and card.source_scope != "personal"
        and card.slug not in RETIRED_BUILTIN_SKILL_SLUGS
        and card.slug not in installed_market_slugs
        and card.enabled
        and not card.shadowed_by_personal
        and matches_shared(card)
    )
    _, shared_total = await SkillRepository(db).page_visible(
        user,
        limit=1,
        offset=0,
        search=search,
        shared_kind="shared",
        catalog_all=True,
        shadowed_personal_slugs=personal_slugs,
        installed_market_slugs=installed_market_slugs,
    )

    return {"created_total": len(created), "all_total": private_total + builtin_total + shared_total}
