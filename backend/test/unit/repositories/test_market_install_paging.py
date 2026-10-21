"""市场安装分页 SQL 的可见性边界。"""

from types import SimpleNamespace

import pytest
from sqlalchemy.dialects import postgresql
from yuxi.agents.skills.market_repository import MarketSkillRepository
from yuxi.storage.postgres.models_business import User


class _PageSession:
    def __init__(self):
        self.count_statement = None
        self.page_statement = None

    async def scalar(self, statement):
        self.count_statement = statement
        return 31

    async def execute(self, statement):
        self.page_statement = statement
        return SimpleNamespace(all=lambda: [])


@pytest.mark.asyncio
async def test_install_search_only_uses_visible_release_content():
    """隐藏安装仅允许按公开标识搜索，名称及描述受现时可见范围限制。"""
    session = _PageSession()
    viewer = User(uid="viewer", username="viewer", password_hash="x", role="user", department_id=1)

    items, total = await MarketSkillRepository(session).page_installed_releases_for_user(
        "viewer", user=viewer, offset=30, limit=30, search="report"
    )

    assert (items, total) == ([], 31)
    compiled_count = session.count_statement.compile(dialect=postgresql.dialect())
    count_sql = str(compiled_count)
    page_sql = str(session.page_statement.compile(dialect=postgresql.dialect()))
    assert "market_skills.latest_release_id IS NOT NULL" in count_sql
    assert "market_skills.scope" in count_sql
    assert "market_skill_releases.description" in count_sql
    assert "market_skills.slug" in count_sql
    assert "LIMIT" in page_sql and "OFFSET" in page_sql


@pytest.mark.asyncio
async def test_install_search_treats_sql_wildcards_as_literal_characters():
    """安装列表中的特殊字符搜索不扩大数据库计数。"""
    session = _PageSession()
    viewer = User(uid="viewer", username="viewer", password_hash="x", role="user", department_id=1)

    await MarketSkillRepository(session).page_installed_releases_for_user(
        "viewer", user=viewer, offset=0, limit=30, search="%_"
    )

    compiled = session.count_statement.compile(dialect=postgresql.dialect())
    assert "ESCAPE" in str(compiled)
    assert any("/%/_" in value for value in compiled.params.values() if isinstance(value, str))


@pytest.mark.asyncio
async def test_install_search_uses_generation_eligible_content():
    """旧批次安装不能通过搜索条件泄露旧快照文本。"""
    session = _PageSession()
    viewer = User(uid="viewer", username="viewer", password_hash="x", role="user", department_id=1)
    await MarketSkillRepository(session).page_installed_releases_for_user(
        "viewer", user=viewer, offset=0, limit=30, search="secret-old-description"
    )
    sql = str(session.count_statement.compile(dialect=postgresql.dialect()))
    assert "market_skill_installs.confirmed_generation = market_skills.publication_generation" in sql
    assert "market_skill_releases.version >= market_skills.active_from_version" in sql
    assert "CASE WHEN" in sql
    assert "current_release.description" in sql
    assert "market_skills.is_published IS true" in sql


@pytest.mark.asyncio
async def test_catalog_dedup_keeps_unavailable_own_install_visible():
    """失效安装没有原件绑定摘要，全部视图仍须保留它的卸载入口。"""
    session = _PageSession()
    viewer = User(uid="viewer", username="viewer", password_hash="x", role="user", department_id=1)
    await MarketSkillRepository(session).page_installed_releases_for_user(
        "viewer", user=viewer, offset=0, limit=30, exclude_own_personal_slugs={"my-original"}
    )
    sql = str(session.count_statement.compile(dialect=postgresql.dialect()))
    assert "NOT (market_skills.is_published IS true" in sql
    assert "market_skills.scope" in sql


@pytest.mark.asyncio
async def test_all_install_queries_keep_install_generation_record():
    """完整列表、个人原件关联与分页统一保留安装确认批次对象。"""
    listing, release, install = object(), object(), object()

    class Session(_PageSession):
        async def execute(self, statement):
            return SimpleNamespace(all=lambda: [(listing, release, install)])

    repo = MarketSkillRepository(Session())
    viewer = User(uid="viewer", username="viewer", password_hash="x", role="user", department_id=1)
    assert await repo.installed_releases_for_user("viewer", user=viewer) == [(listing, release, install)]
    assert await repo.installed_releases_for_personal_slugs("viewer", {"original"}) == [(listing, release, install)]
    rows, total = await repo.page_installed_releases_for_user("viewer", user=viewer, offset=0, limit=30)
    assert rows == [(listing, release, install)]
    assert total == 31
