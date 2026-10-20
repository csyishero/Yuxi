"""技能个人空间的精确来源总数。"""

from types import SimpleNamespace

import pytest
from yuxi.services import skill_catalog_summary_service as summary
from yuxi.storage.postgres.models_business import User


def _card(
    slug: str,
    *,
    scope: str = "shared",
    source_type: str = "upload",
    name: str | None = None,
    description: str = "",
    enabled: bool = True,
    shadowed: bool = False,
    related_market_slug: str | None = None,
):
    return SimpleNamespace(
        slug=slug,
        name=name or slug,
        description=description,
        source_scope=scope,
        source_type=source_type,
        enabled=enabled,
        shadowed_by_personal=shadowed,
        related_market_slug=related_market_slug,
    )


def _user() -> User:
    return User(uid="member", username="member", password_hash="x", role="user", department_id=3)


def _shared_page(monkeypatch, *, total: int, search: str, personal_slugs: set[str], installed_slugs: set[str]):
    """校验 summary 把完整遮盖条件交给共享目录的真实 SQL 入口。"""

    class FakeRepo:
        def __init__(self, _db):
            pass

        async def page_visible(self, user, **kwargs):
            assert user.uid == "member"
            assert kwargs == {
                "limit": 1,
                "offset": 0,
                "search": search,
                "shared_kind": "shared",
                "catalog_all": True,
                "shadowed_personal_slugs": personal_slugs,
                "installed_market_slugs": installed_slugs,
            }
            return [], total

    monkeypatch.setattr(summary, "SkillRepository", FakeRepo)


@pytest.mark.asyncio
async def test_summary_counts_full_catalog_once_per_distinct_visible_entry(monkeypatch):
    """跨来源同源折叠后，全部数与完整个人空间卡片数量一致。"""
    cards = [
        _card("mine", scope="personal"),
        _card("shadow", scope="personal"),
        _card("mine-share", related_market_slug="other-market"),
        _card("older-share", related_market_slug="third-market"),
        _card("newer-share", related_market_slug="third-market"),
        _card("shadow", shadowed=True),
        _card("team-only"),
        _card("built-in", scope="builtin", source_type="builtin"),
        _card("image-gen", scope="builtin", source_type="builtin"),
        _card("disabled", enabled=False),
    ]
    installs = [
        {"slug": "my-market", "own_personal_slug": "mine", "installed_version": 1, "visible": True},
        {"slug": "third-market", "own_personal_slug": None, "installed_version": 1, "visible": True},
        {"slug": "hidden-market", "own_personal_slug": None, "installed_version": 1, "visible": False},
    ]

    async def fake_cards(_db, user):
        assert user.uid == "member"
        return cards

    async def fake_installs(_db, *, user, search=""):
        assert user.uid == "member"
        assert search == ""
        return installs

    async def fake_own_installs(_db, *, user, personal_slugs):
        assert user.uid == "member"
        assert personal_slugs == {"mine", "shadow"}
        return {"mine": installs[0]}

    monkeypatch.setattr(summary, "list_skill_cards_for_user", fake_cards)
    monkeypatch.setattr(summary, "list_user_market_installs", fake_installs)
    monkeypatch.setattr(summary, "personal_market_install_summaries", fake_own_installs)
    _shared_page(
        monkeypatch,
        total=2,
        search="",
        personal_slugs={"mine", "shadow"},
        installed_slugs={"my-market", "third-market"},
    )

    result = await summary.personal_space_skill_counts(None, _user())

    # 本人 2 项 + 外部安装 2 项 + 团队 2 项 + 系统 1 项。
    # 同名个人覆盖、同源共享版本及已安装的共享版本均不可重复计数。
    assert result == {"created_total": 2, "all_total": 7}


@pytest.mark.asyncio
async def test_summary_applies_search_to_sources_before_dedup(monkeypatch):
    """搜索总数先按各来源筛选，再折叠同源个人安装。"""
    cards = [
        _card("report", scope="personal", description="年度预算"),
        _card("other-personal", scope="personal", description="设备清单"),
        _card("team-report", description="预算审核"),
        _card("report-shared", description="预算模板", related_market_slug="visible-report"),
        _card("team-other", description="审核流程"),
    ]
    own_install = {"slug": "visible-report", "own_personal_slug": "report", "installed_version": 1, "visible": True}

    async def fake_cards(_db, _user):
        return cards

    async def fake_installs(_db, *, user, search=""):
        assert user.uid == "member"
        assert search == "预算"
        return []

    async def fake_own_installs(_db, *, user, personal_slugs):
        assert user.uid == "member"
        assert personal_slugs == {"report"}
        return {"report": own_install}

    monkeypatch.setattr(summary, "list_skill_cards_for_user", fake_cards)
    monkeypatch.setattr(summary, "list_user_market_installs", fake_installs)
    monkeypatch.setattr(summary, "personal_market_install_summaries", fake_own_installs)
    _shared_page(
        monkeypatch,
        total=1,
        search="预算",
        personal_slugs={"report", "other-personal"},
        installed_slugs={"visible-report"},
    )

    result = await summary.personal_space_skill_counts(None, _user(), search="预算")

    assert result == {"created_total": 1, "all_total": 2}


@pytest.mark.asyncio
async def test_summary_counts_own_install_when_only_market_copy_matches_search(monkeypatch):
    """本人安装命中搜索而个人原件未命中时，全部仍保留安装入口。"""
    cards = [
        _card("report", scope="personal", description="年度预算"),
        _card("report-shared", description="图像模板", related_market_slug="visible-image"),
    ]
    install = {"slug": "visible-image", "own_personal_slug": "report", "installed_version": 1, "visible": True}

    async def fake_cards(_db, _user):
        return cards

    async def fake_installs(_db, *, user, search=""):
        assert user.uid == "member"
        assert search == "图像"
        return [install]

    async def fake_own_installs(_db, *, user, personal_slugs):
        assert user.uid == "member"
        assert personal_slugs == set()
        return {}

    monkeypatch.setattr(summary, "list_skill_cards_for_user", fake_cards)
    monkeypatch.setattr(summary, "list_user_market_installs", fake_installs)
    monkeypatch.setattr(summary, "personal_market_install_summaries", fake_own_installs)
    _shared_page(
        monkeypatch,
        total=0,
        search="图像",
        personal_slugs={"report"},
        installed_slugs={"visible-image"},
    )

    result = await summary.personal_space_skill_counts(None, _user(), search="图像")

    assert result == {"created_total": 0, "all_total": 1}


@pytest.mark.asyncio
async def test_summary_passes_search_to_shared_catalog_count(monkeypatch):
    """个人文件搜索可跨字段，共享来源计数委托数据库搜索。"""
    cards = [
        _card("my-alpha", scope="personal", name="beta"),
        _card("team-alpha", name="beta"),
        _card("team-match", name="alpha beta"),
    ]

    async def fake_cards(_db, _user):
        return cards

    async def fake_installs(_db, *, user, search=""):
        assert user.uid == "member"
        assert search == "alpha beta"
        return []

    async def fake_own_installs(_db, *, user, personal_slugs):
        assert user.uid == "member"
        assert personal_slugs == {"my-alpha"}
        return {}

    monkeypatch.setattr(summary, "list_skill_cards_for_user", fake_cards)
    monkeypatch.setattr(summary, "list_user_market_installs", fake_installs)
    monkeypatch.setattr(summary, "personal_market_install_summaries", fake_own_installs)
    _shared_page(
        monkeypatch,
        total=1,
        search="alpha beta",
        personal_slugs={"my-alpha"},
        installed_slugs=set(),
    )

    result = await summary.personal_space_skill_counts(None, _user(), search="alpha beta")

    assert result == {"created_total": 1, "all_total": 2}
