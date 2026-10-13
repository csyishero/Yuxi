"""MCP 引用在 Agent 和 Skill HTTP 响应中的可见性。"""

from __future__ import annotations

import json
import os
import uuid

import asyncpg
import pytest

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


async def test_private_mcp_references_are_redacted_without_removing_saved_dependencies(
    test_client, admin_headers, standard_user
):
    """普通用户只读到获授权的引用，保存可见配置后私有旧引用仍在。"""

    suffix = uuid.uuid4().hex[:10]
    visible_slug = f"pytest-visible-mcp-{suffix}"
    hidden_slug = f"pytest-hidden-mcp-{suffix}"
    agent_slug = f"pytest-mcp-agent-{suffix}"
    skill_slug = f"pytest-mcp-skill-{suffix}"
    user_uid = str(standard_user["user"]["uid"])
    user_headers = standard_user["headers"]
    scope = {"access_level": "user", "department_ids": [], "user_uids": [user_uid]}
    conn = await asyncpg.connect(os.environ["POSTGRES_URL"].replace("+asyncpg", ""))
    try:
        for slug in (visible_slug, hidden_slug):
            created = await test_client.post(
                "/api/system/mcp-servers",
                headers=admin_headers,
                json={"slug": slug, "name": slug, "transport": "streamable_http", "url": "https://example.invalid/mcp"},
            )
            assert created.status_code == 200, created.text

        shared = await test_client.put(
            f"/api/system/mcp-servers/{visible_slug}/sharing",
            headers=admin_headers,
            json={
                "share_config": {
                    "version": 2,
                    "read_scope": scope,
                    "edit_scope": scope,
                    "manage_scope": None,
                }
            },
        )
        assert shared.status_code == 200, shared.text

        created_agent = await test_client.post(
            "/api/agent",
            headers=admin_headers,
            json={
                "name": agent_slug,
                "slug": agent_slug,
                "backend_id": "ChatbotAgent",
                "config_json": {"context": {"mcps": [visible_slug, hidden_slug]}},
                "share_config": {"version": 2, "read_scope": scope, "manage_scope": scope},
            },
        )
        assert created_agent.status_code == 200, created_agent.text

        await conn.execute(
            """
            INSERT INTO skills
                (slug, name, description, source_type, tool_dependencies, mcp_dependencies,
                 skill_dependencies, dir_path, share_config, enabled, created_by, updated_by)
            VALUES
                ($1, $1, 'MCP reference visibility fixture', 'upload', '[]'::jsonb, $2::jsonb,
                 '[]'::jsonb, $1, $3::jsonb, true, 'pytest', 'pytest')
            """,
            skill_slug,
            json.dumps([visible_slug, hidden_slug]),
            json.dumps({"version": 2, "read_scope": scope, "manage_scope": scope}),
        )

        admin_agent = await test_client.get(f"/api/agent/{agent_slug}", headers=admin_headers)
        assert admin_agent.status_code == 200, admin_agent.text
        assert admin_agent.json()["agent"]["config_json"]["context"]["mcps"] == [visible_slug, hidden_slug]

        for path in ("/api/agent", f"/api/agent/{agent_slug}"):
            response = await test_client.get(path, headers=user_headers)
            assert response.status_code == 200, response.text
            agents = response.json().get("agents") or [response.json()["agent"]]
            item = next(agent for agent in agents if agent["slug"] == agent_slug)
            assert item["config_json"]["context"]["mcps"] == [visible_slug]

        for path in ("/api/skills", "/api/skills/accessible", "/api/system/skills"):
            response = await test_client.get(path, headers=user_headers)
            assert response.status_code == 200, response.text
            item = next(skill for skill in response.json()["data"] if skill["slug"] == skill_slug)
            assert item["mcp_dependencies"] == [visible_slug]

        saved_agent = await test_client.put(
            f"/api/agent/{agent_slug}",
            headers=user_headers,
            json={"config_json": {"context": {"mcps": [visible_slug]}}},
        )
        assert saved_agent.status_code == 200, saved_agent.text
        assert saved_agent.json()["agent"]["config_json"]["context"]["mcps"] == [visible_slug]
        agent_config = json.loads(await conn.fetchval("SELECT config_json FROM agents WHERE slug = $1", agent_slug))
        assert agent_config["context"]["mcps"] == [visible_slug, hidden_slug]

        read_only = await test_client.put(
            f"/api/system/mcp-servers/{visible_slug}/sharing",
            headers=admin_headers,
            json={"share_config": {"version": 2, "read_scope": scope, "edit_scope": None, "manage_scope": None}},
        )
        assert read_only.status_code == 200, read_only.text
        cleared_visible = await test_client.put(
            f"/api/agent/{agent_slug}",
            headers=user_headers,
            json={"config_json": {"context": {"mcps": []}}},
        )
        assert cleared_visible.status_code == 200, cleared_visible.text
        assert cleared_visible.json()["agent"]["config_json"]["context"]["mcps"] == []
        agent_config = json.loads(await conn.fetchval("SELECT config_json FROM agents WHERE slug = $1", agent_slug))
        assert agent_config["context"]["mcps"] == [hidden_slug]
        restored = await test_client.put(
            f"/api/system/mcp-servers/{visible_slug}/sharing",
            headers=admin_headers,
            json={"share_config": {"version": 2, "read_scope": scope, "edit_scope": scope, "manage_scope": None}},
        )
        assert restored.status_code == 200, restored.text

        dynamic_all = await test_client.put(
            f"/api/agent/{agent_slug}",
            headers=user_headers,
            json={"config_json": {"context": {"mcps": None}}},
        )
        assert dynamic_all.status_code == 200, dynamic_all.text
        assert dynamic_all.json()["agent"]["config_json"]["context"]["mcps"] is None
        agent_config = json.loads(await conn.fetchval("SELECT config_json FROM agents WHERE slug = $1", agent_slug))
        assert agent_config["context"]["mcps"] is None

        saved_skill = await test_client.put(
            f"/api/system/skills/{skill_slug}/dependencies",
            headers=user_headers,
            json={"tool_dependencies": [], "mcp_dependencies": [visible_slug], "skill_dependencies": []},
        )
        assert saved_skill.status_code == 200, saved_skill.text
        assert saved_skill.json()["data"]["mcp_dependencies"] == [visible_slug]
        assert json.loads(await conn.fetchval("SELECT mcp_dependencies FROM skills WHERE slug = $1", skill_slug)) == [
            visible_slug,
            hidden_slug,
        ]

        denied = await test_client.put(
            f"/api/system/skills/{skill_slug}/dependencies",
            headers=user_headers,
            json={
                "tool_dependencies": [],
                "mcp_dependencies": [visible_slug, hidden_slug, "never-granted-mcp"],
                "skill_dependencies": [],
            },
        )
        assert denied.status_code == 400, denied.text
        assert json.loads(await conn.fetchval("SELECT mcp_dependencies FROM skills WHERE slug = $1", skill_slug)) == [
            visible_slug,
            hidden_slug,
        ]
    finally:
        await conn.execute("DELETE FROM skills WHERE slug = $1", skill_slug)
        await test_client.delete(f"/api/agent/{agent_slug}", headers=admin_headers)
        for slug in (visible_slug, hidden_slug):
            await test_client.delete(f"/api/system/mcp-servers/{slug}", headers=admin_headers)
        await conn.close()
