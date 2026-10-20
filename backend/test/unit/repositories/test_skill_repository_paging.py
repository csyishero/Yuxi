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


@pytest.mark.asyncio
@pytest.mark.parametrize("shared_kind,operator", [("shared", "!="), ("builtin", "=")])
async def test_shared_kind_filters_count_and_page_before_offset(shared_kind, operator):
    """共享与内置分类在 SQL count 和 limit 前分开，不能用当前页再过滤。"""
    session = _PageSession()
    user = User(uid="member", username="member", password_hash="x", role="user", department_id=7)

    await SkillRepository(session).page_visible(user, limit=1, offset=1, shared_kind=shared_kind)

    count = session.count_statement.compile(dialect=postgresql.dialect())
    page = session.page_statement.compile(dialect=postgresql.dialect())
    assert f"skills.source_type {operator}" in str(count)
    assert f"skills.source_type {operator}" in str(page)
    assert "builtin" in count.params.values()
    assert "builtin" in page.params.values()
    assert "LIMIT" in str(page) and "OFFSET" in str(page)


@pytest.mark.asyncio
async def test_shared_kind_excludes_disabled_and_retired_builtins_from_count_and_page():
    """个人目录分页的总数与卡片一致，治理查询仍能包含停用技能。"""
    session = _PageSession()
    root = User(uid="root", username="root", password_hash="x", role="superadmin", department_id=7)

    for shared_kind in ("shared", "builtin"):
        await SkillRepository(session).page_visible(root, limit=1, offset=1, shared_kind=shared_kind)
        count = session.count_statement.compile(dialect=postgresql.dialect())
        page = session.page_statement.compile(dialect=postgresql.dialect())
        assert "skills.enabled IS true" in str(count)
        assert "skills.enabled IS true" in str(page)
        if shared_kind == "builtin":
            assert "skills.slug NOT IN" in str(count)
            assert any(
                set(value) == {"image-gen", "mysql-reporter"}
                for value in count.params.values()
                if isinstance(value, (list, tuple))
            )
        else:
            assert "skills.slug NOT IN" not in str(count)

    await SkillRepository(session).page_visible(root, limit=1, offset=1)
    governance = session.count_statement.compile(dialect=postgresql.dialect())
    assert "skills.enabled IS true" not in str(governance)
    assert "skills.slug NOT IN" not in str(governance)


@pytest.mark.asyncio
async def test_governance_filter_excludes_only_retired_builtins_from_count_and_page():
    """治理页过滤退役内置技能时，停用记录仍计入分页。"""
    session = _PageSession()
    root = User(uid="root", username="root", password_hash="x", role="superadmin", department_id=7)

    await SkillRepository(session).page_visible(root, limit=1, offset=2, exclude_retired_builtin=True)

    count = session.count_statement.compile(dialect=postgresql.dialect())
    page = session.page_statement.compile(dialect=postgresql.dialect())
    for statement in (count, page):
        assert "skills.source_type !=" in str(statement)
        assert "skills.slug NOT IN" in str(statement)
        assert "skills.enabled IS true" not in str(statement)
        assert any(
            set(value) == {"image-gen", "mysql-reporter"}
            for value in statement.params.values()
            if isinstance(value, (list, tuple))
        )


@pytest.mark.asyncio
async def test_governance_filter_excludes_disabled_builtin_for_demoted_creator():
    """普通用户即使是内置技能记录的创建者，也不能把已禁用内置技能计入页数。"""
    session = _PageSession()
    user = User(uid="former-admin", username="former-admin", password_hash="x", role="user", department_id=7)

    await SkillRepository(session).page_visible(user, limit=1, offset=0, exclude_retired_builtin=True)

    for statement in (session.count_statement, session.page_statement):
        sql = str(statement.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
        assert "skills.source_type != 'builtin' OR skills.enabled IS true" in sql


@pytest.mark.asyncio
async def test_catalog_all_filters_and_ranks_origins_before_count_and_offset():
    """全部共享目录的授权、搜索、原件与安装遮盖均在数据库分页前执行。"""
    session = _PageSession()
    user = User(uid="member", username="member", password_hash="x", role="user", department_id=7)

    await SkillRepository(session).page_visible(
        user,
        limit=1,
        offset=1,
        search="budget",
        shared_kind="shared",
        catalog_all=True,
        shadowed_personal_slugs={"mine"},
        installed_market_slugs={"installed"},
    )

    count = session.count_statement.compile(dialect=postgresql.dialect())
    page = session.page_statement.compile(dialect=postgresql.dialect())
    count_sql = str(count)
    page_sql = str(page)
    for sql in (count_sql, page_sql):
        assert "row_number() OVER" in sql
        assert "catalog_available.id DESC" in sql
        assert "skill_share_requests" in sql
        assert "market_skills" in sql
        assert "coalesce(catalog_filtered.related_market_slug, catalog_filtered.slug) NOT IN" in sql
        assert "skills.slug NOT IN" in sql
        assert "skills.enabled IS true" in sql
        assert "skills.slug ILIKE" in sql
    assert "LIMIT" in page_sql and "OFFSET" in page_sql
    assert "mine" in count.params.values() or ["mine"] in count.params.values()


@pytest.mark.asyncio
async def test_catalog_all_builtin_excludes_personal_and_installed_slugs_before_count_and_offset():
    """内置技能的全部视图在数据库计数与分页前去掉个人原件和可见安装遮盖。"""
    session = _PageSession()
    user = User(uid="member", username="member", password_hash="x", role="user", department_id=7)

    await SkillRepository(session).page_visible(
        user,
        limit=1,
        offset=1,
        shared_kind="builtin",
        catalog_all=True,
        shadowed_personal_slugs={"personal-builtin"},
        installed_market_slugs={"market-builtin"},
    )

    count = session.count_statement.compile(dialect=postgresql.dialect())
    page = session.page_statement.compile(dialect=postgresql.dialect())
    for compiled in (count, page):
        sql = str(compiled)
        assert "skills.source_type =" in sql
        assert "skills.slug NOT IN" in sql
        assert "skills.enabled IS true" in sql
        assert "row_number()" not in sql
        assert any("personal-builtin" in value for value in compiled.params.values() if isinstance(value, list))
        assert any("market-builtin" in value for value in compiled.params.values() if isinstance(value, list))
    assert "LIMIT" in str(page) and "OFFSET" in str(page)


@pytest.mark.asyncio
async def test_matching_shared_slugs_only_queries_candidate_names_and_search():
    """个人文件定点校验只从可能同名的共享技能取候选。"""
    session = _PageSession()

    await SkillRepository(session).matching_shared_slugs({"one", "two"}, search="budget")

    compiled = session.page_statement.compile(dialect=postgresql.dialect())
    sql = str(compiled)
    assert "skills.slug IN" in sql
    assert "skills.source_type !=" in sql
    assert "skills.enabled IS true" in sql
    assert "skills.description ILIKE" in sql
