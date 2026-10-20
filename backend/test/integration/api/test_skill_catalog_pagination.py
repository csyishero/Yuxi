"""技能目录分页与单项权限边界。"""

from __future__ import annotations

import json
import os
import uuid

import asyncpg
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from yuxi.agents.skills.repository import SkillRepository
from yuxi.agents.skills.service import user_can_access_skill, user_can_manage_skill
from yuxi.storage.postgres.models_business import Skill, User


@pytest.mark.integration
@pytest.mark.asyncio
async def test_shared_skill_pages_only_count_authorized_items(test_client, standard_user):
    """授权后分页能取到旧记录，禁用的本人记录可管理，私有他人记录不可见。"""
    suffix = uuid.uuid4().hex[:10]
    uid = str(standard_user["user"]["uid"])
    headers = standard_user["headers"]
    slugs = [f"pytest-page-{suffix}-{name}" for name in ("one", "two", "owned", "hidden")]
    visible_scope = {
        "version": 2,
        "read_scope": {"access_level": "user", "department_ids": [], "user_uids": [uid]},
        "manage_scope": None,
    }
    hidden_scope = {
        "version": 2,
        "read_scope": {"access_level": "user", "department_ids": [], "user_uids": ["another-user"]},
        "manage_scope": None,
    }
    conn = await asyncpg.connect(os.environ["POSTGRES_URL"].replace("+asyncpg", ""))
    try:
        for index, slug in enumerate(slugs):
            await conn.execute(
                """
                INSERT INTO skills
                    (slug, name, description, category, source_type, tool_dependencies,
                     mcp_dependencies, skill_dependencies, dir_path, share_config, enabled,
                     created_by, updated_by)
                VALUES ($1, $1, 'pagination fixture', 'other', $5, '[]'::jsonb,
                        '[]'::jsonb, '[]'::jsonb, $1, $2::jsonb, $3, $4, $4)
                """,
                slug,
                json.dumps(hidden_scope if index == 3 else visible_scope),
                index != 2,
                uid if index == 2 else "another-user",
                "builtin" if index == 1 else "upload",
            )

        found = []
        for offset in range(3):
            response = await test_client.get(
                "/api/system/skills",
                headers=headers,
                params={"search": f"pytest-page-{suffix}", "limit": 1, "offset": offset},
            )
            assert response.status_code == 200, response.text
            payload = response.json()
            assert payload["total"] == 3
            assert len(payload["data"]) == 1
            found.append(payload["data"][0]["slug"])
        assert set(found) == set(slugs[:3])

        cards = await test_client.get(
            "/api/skills",
            headers=headers,
            params={"source": "shared", "search": f"pytest-page-{suffix}", "limit": 1, "offset": 2},
        )
        assert cards.status_code == 200, cards.text
        assert cards.json()["total"] == 3
        assert cards.json()["has_more"] is False
        assert len(cards.json()["data"]) == 1

        builtin = await test_client.get(
            "/api/skills",
            headers=headers,
            params={"source": "shared", "shared_kind": "builtin", "search": f"pytest-page-{suffix}", "limit": 1},
        )
        assert builtin.status_code == 200, builtin.text
        assert builtin.json()["total"] == 1
        assert [item["slug"] for item in builtin.json()["data"]] == [slugs[1]]

        shared_pages = []
        for offset in (0, 1):
            shared = await test_client.get(
                "/api/skills",
                headers=headers,
                params={
                    "source": "shared",
                    "shared_kind": "shared",
                    "search": f"pytest-page-{suffix}",
                    "limit": 1,
                    "offset": offset,
                },
            )
            assert shared.status_code == 200, shared.text
            assert shared.json()["total"] == 2
            shared_pages.extend(item["slug"] for item in shared.json()["data"])
        assert set(shared_pages) == {slugs[0], slugs[2]}

        denied = await test_client.get(f"/api/system/skills/{slugs[3]}/details", headers=headers)
        assert denied.status_code == 404
        older = await test_client.get(f"/api/system/skills/{slugs[0]}/details", headers=headers)
        assert older.status_code == 200
        assert older.json()["data"]["slug"] == slugs[0]
    finally:
        await conn.execute("DELETE FROM skills WHERE slug = ANY($1::text[])", slugs)
        await conn.close()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_skill_page_matches_historical_scope_normalization(test_client, standard_user):
    """旧记录的重复或字符串部门 ID 与省略的全局范围仍按权限函数可见，v1 配置不泄漏。"""
    suffix = uuid.uuid4().hex[:10]
    department_id = int(standard_user["user"]["department_id"])
    slugs = [f"pytest-historic-{suffix}-{kind}" for kind in ("department", "global", "v1")]
    scopes = [
        {
            "version": 2,
            "read_scope": {
                "access_level": "department",
                "department_ids": [str(department_id), department_id, department_id],
            },
            "manage_scope": None,
        },
        {"version": 2, "read_scope": {}, "manage_scope": None},
        {"access_level": "global", "department_ids": [], "user_uids": []},
    ]
    conn = await asyncpg.connect(os.environ["POSTGRES_URL"].replace("+asyncpg", ""))
    try:
        for slug, scope in zip(slugs, scopes, strict=True):
            await conn.execute(
                """
                INSERT INTO skills
                    (slug, name, description, category, source_type, tool_dependencies,
                     mcp_dependencies, skill_dependencies, dir_path, share_config, enabled,
                     created_by, updated_by)
                VALUES ($1, $1, 'historical scope fixture', 'other', 'upload', '[]'::jsonb,
                        '[]'::jsonb, '[]'::jsonb, $1, $2::jsonb, true, $3, $3)
                """,
                slug,
                json.dumps(scope),
                "another-user",
            )

        response = await test_client.get(
            "/api/system/skills",
            headers=standard_user["headers"],
            params={"search": f"pytest-historic-{suffix}", "limit": 10},
        )

        assert response.status_code == 200, response.text
        assert response.json()["total"] == 2
        assert {item["slug"] for item in response.json()["data"]} == set(slugs[:2])
    finally:
        await conn.execute("DELETE FROM skills WHERE slug = ANY($1::text[])", slugs)
        await conn.close()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_department_admin_page_agrees_with_python_permission_for_old_arrays(standard_user):
    """部门管理员对禁用技能的分页结果与权限函数一致，重复 ID 不改变独占范围。"""
    suffix = uuid.uuid4().hex[:10]
    department_id = int(standard_user["user"]["department_id"])
    slugs = [f"pytest-admin-page-{suffix}-{kind}" for kind in ("exclusive", "multi")]
    scopes = [
        {
            "version": 2,
            "owner_department_id": str(department_id),
            "read_scope": {
                "access_level": "department",
                "department_ids": [department_id, str(department_id), department_id],
            },
            "manage_scope": None,
        },
        {
            "version": 2,
            "owner_department_id": department_id,
            "read_scope": {
                "access_level": "department",
                "department_ids": [department_id, department_id + 1000],
            },
            "manage_scope": None,
        },
    ]
    conn = await asyncpg.connect(os.environ["POSTGRES_URL"].replace("+asyncpg", ""))
    engine = create_async_engine(os.environ["POSTGRES_URL"])
    try:
        for slug, scope in zip(slugs, scopes, strict=True):
            await conn.execute(
                """
                INSERT INTO skills
                    (slug, name, description, category, source_type, tool_dependencies,
                     mcp_dependencies, skill_dependencies, dir_path, share_config, enabled,
                     created_by, updated_by)
                VALUES ($1, $1, 'admin scope fixture', 'other', 'upload', '[]'::jsonb,
                        '[]'::jsonb, '[]'::jsonb, $1, $2::jsonb, false, 'another-user', 'another-user')
                """,
                slug,
                json.dumps(scope),
            )

        admin = User(uid="department-admin", username="department-admin", password_hash="x", role="admin")
        admin.department_id = department_id
        async with async_sessionmaker(engine, expire_on_commit=False)() as db:
            items, total = await SkillRepository(db).page_visible(
                admin, limit=10, offset=0, search=f"pytest-admin-page-{suffix}"
            )
            all_rows = (await db.scalars(select(Skill).where(Skill.slug.in_(slugs)))).all()
            expected = {
                item.slug
                for item in all_rows
                if user_can_manage_skill(admin, item) or (item.enabled and user_can_access_skill(admin, item))
            }

        assert expected == {slugs[0]}
        assert total == 1
        assert {item.slug for item in items} == expected
    finally:
        await conn.execute("DELETE FROM skills WHERE slug = ANY($1::text[])", slugs)
        await conn.close()
        await engine.dispose()
