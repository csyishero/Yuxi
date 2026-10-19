"""共享技能分页的 SQL 授权条件。"""

from types import SimpleNamespace

import pytest
from sqlalchemy.dialects import postgresql
from yuxi.agents.skills.repository import SkillRepository
from yuxi.storage.postgres.models_business import User


class _PageSession:
    def __init__(self):
        self.count_statement = None
        self.page_statement = None

    async def scalar(self, statement):
        self.count_statement = statement
        return 4

    async def scalars(self, statement):
        self.page_statement = statement
        return SimpleNamespace(all=lambda: [])


@pytest.mark.asyncio
async def test_department_admin_page_includes_exclusively_owned_department_skills():
    """部门管理员能翻到本部门专享且已禁用的技能。"""
    session = _PageSession()
    user = User(uid="admin", username="admin", password_hash="x", role="admin", department_id=7)

    items, total = await SkillRepository(session).page_visible(user, limit=2, offset=2)

    assert (items, total) == ([], 4)
    compiled_count = session.count_statement.compile(dialect=postgresql.dialect())
    count_sql = str(compiled_count)
    page_sql = str(session.page_statement.compile(dialect=postgresql.dialect()))
    assert "owner_department_id" in compiled_count.params.values()
    assert "department_ids" in compiled_count.params.values()
    assert "jsonb_array_elements_text" in count_sql
    assert "NOT (EXISTS" in count_sql
    assert "skills.enabled IS true" in count_sql
    assert "LIMIT" in page_sql and "OFFSET" in page_sql


@pytest.mark.asyncio
async def test_normal_user_page_never_gets_department_admin_manage_bypass():
    """普通用户仅凭所属部门不能管理禁用的部门技能。"""
    session = _PageSession()
    user = User(uid="member", username="member", password_hash="x", role="user", department_id=7)

    await SkillRepository(session).page_visible(user, limit=10, offset=0)

    compiled = session.count_statement.compile(dialect=postgresql.dialect())
    sql = str(compiled)
    assert "owner_department_id" not in compiled.params.values()
    assert "skills.created_by" in sql
    assert "skills.enabled IS true" in sql
    assert "jsonb_array_elements_text" in sql
    assert "version" in compiled.params.values()


@pytest.mark.asyncio
async def test_audience_filter_is_in_count_and_page_queries():
    """受众命中应在数据库 count 和 limit 之前完成，且不能包含已删除用户。"""
    session = _PageSession()
    admin = User(uid="admin", username="admin", password_hash="x", role="admin", department_id=7)

    await SkillRepository(session).page_visible(
        admin,
        limit=1,
        offset=1,
        audience_search="target",
        audience_department_id=7,
    )

    compiled_count = session.count_statement.compile(dialect=postgresql.dialect())
    count_sql = str(compiled_count)
    page_sql = str(session.page_statement.compile(dialect=postgresql.dialect()))
    assert "FROM users" in count_sql
    assert "users.is_deleted" in count_sql
    assert "users.department_id" in count_sql
    assert "users.username" in count_sql
    assert "jsonb_array_elements_text" in count_sql
    assert "FROM users" in page_sql
    assert "LIMIT" in page_sql and "OFFSET" in page_sql


@pytest.mark.asyncio
async def test_skill_search_treats_sql_wildcards_as_literal_characters():
    """百分号和下划线按技能名称文本匹配，不扩大分页总数。"""
    session = _PageSession()
    user = User(uid="member", username="member", password_hash="x", role="user", department_id=7)

    await SkillRepository(session).page_visible(user, limit=10, offset=0, search="%_")

    compiled = session.count_statement.compile(dialect=postgresql.dialect())
    assert "ESCAPE" in str(compiled)
    assert any("/%/_" in value for value in compiled.params.values() if isinstance(value, str))
