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
