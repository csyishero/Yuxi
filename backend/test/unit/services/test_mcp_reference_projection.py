"""MCP 引用响应投影和旧依赖保留的负向测试。"""

from __future__ import annotations

from importlib import import_module
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from yuxi.agents.skills.service import merge_existing_mcp_dependencies
from yuxi.repositories.agent_repository import merge_agent_config_json
from yuxi.services import agent_config_service
from yuxi.services import mcp_reference_service as references
from yuxi.storage.postgres.models_business import Skill, User

agent_routes = import_module("server.routers.agent_router")
skill_routes = import_module("server.routers.skill_router")


def _user(role: str = "user") -> User:
    return User(uid="viewer", username="viewer", password_hash="x", role=role, is_deleted=0)


@pytest.mark.asyncio
async def test_private_mcp_slug_is_absent_from_user_visible_set(monkeypatch):
    """没有 READ 的私有 MCP 不进入当前用户的可见引用集合。"""

    scope = {"access_level": "user", "department_ids": [], "user_uids": ["viewer"]}
    servers = [
        SimpleNamespace(
            slug="visible",
            share_config={"version": 2, "read_scope": scope, "edit_scope": None, "manage_scope": None},
        ),
        SimpleNamespace(
            slug="private",
            share_config={"version": 2, "read_scope": None, "edit_scope": None, "manage_scope": None},
        ),
        SimpleNamespace(slug="legacy", share_config=None),
    ]

    async def get_servers(_db):
        return servers

    monkeypatch.setattr(references, "get_all_mcp_servers", get_servers)
    assert await references.get_visible_mcp_reference_slugs(object(), _user()) == {"visible", "legacy"}
    assert await references.get_visible_mcp_reference_slugs(object(), _user("admin")) is None


@pytest.mark.asyncio
async def test_agent_and_skill_response_projection_does_not_mutate_stored_references(monkeypatch):
    """普通用户响应隐去私有 slug，源 Agent 与 Skill 配置保持原值。"""

    async def visible(_db, _user):
        return {"visible"}

    monkeypatch.setattr(agent_routes, "get_visible_mcp_reference_slugs", visible)
    source = {"context": {"mcps": ["visible", "private"]}}

    class Repo:
        db = object()

        async def serialize(self, _item, **_kwargs):
            return {"config_json": source}

    agent = await agent_routes._serialize_agent(Repo(), SimpleNamespace(backend_id="ChatbotAgent"), _user())
    assert agent["config_json"]["context"]["mcps"] == ["visible"]
    assert source["context"]["mcps"] == ["visible", "private"]

    skill = Skill(
        slug="shared",
        name="shared",
        description="shared",
        source_type="upload",
        dir_path="shared/shared",
        mcp_dependencies=["visible", "private"],
        share_config={
            "version": 2,
            "read_scope": {"access_level": "global", "department_ids": [], "user_uids": []},
            "manage_scope": None,
        },
        enabled=True,
        created_by="owner",
    )
    card = skill_routes._serialize_skill_for_user(skill, _user(), {"visible"})
    assert card["mcp_dependencies"] == ["visible"]
    assert skill.mcp_dependencies == ["visible", "private"]


def test_skill_save_preserves_old_private_reference_and_rejects_new_one():
    """提交过滤后的依赖仍保留旧私有引用，新增无权 MCP 会失败。"""

    assert merge_existing_mcp_dependencies(["visible", "private"], ["visible"], {"visible"}, {"visible"}) == [
        "visible",
        "private",
    ]
    with pytest.raises(ValueError, match="存在无效 MCP 依赖"):
        merge_existing_mcp_dependencies(["visible", "private"], ["visible", "new-private"], {"visible"}, {"visible"})


def test_read_only_mcp_reference_can_be_removed_but_new_one_cannot_be_added():
    """用户能移除可见的旧依赖；添加一个仅可查看的 MCP 仍失败。"""

    assert merge_existing_mcp_dependencies(
        ["editable", "read-only", "private"], ["editable"], {"editable"}, {"editable", "read-only"}
    ) == ["editable", "private"]
    with pytest.raises(ValueError, match="存在无效 MCP 依赖"):
        merge_existing_mcp_dependencies([], ["read-only"], set(), {"read-only"})


def test_agent_empty_mcp_patch_preserves_hidden_old_reference_for_user():
    """普通用户保存过滤后的空 MCP 配置不会清除旧私有引用。"""

    existing = {"context": {"mcps": ["visible", "private"]}}
    patch = {"context": {"mcps": []}}
    merged = merge_agent_config_json(
        existing,
        patch,
        resource_access={"mcps": {"visible"}},
        preserve_hidden_mcp_references=True,
    )
    assert merged["context"]["mcps"] == ["private"]
    assert existing["context"]["mcps"] == ["visible", "private"]
    assert merge_agent_config_json(existing, patch, resource_access={"mcps": {"visible"}})["context"]["mcps"] == []


def test_agent_null_mcp_patch_keeps_dynamic_all_semantics():
    """显式 null 继续表示动态选择全部当前可用 MCP。"""

    existing = {"context": {"mcps": ["visible", "private"]}}
    patch = {"context": {"mcps": None}}
    merged = merge_agent_config_json(
        existing,
        patch,
        resource_access={},
        preserve_hidden_mcp_references=True,
    )
    assert merged["context"]["mcps"] is None


def test_agent_read_only_mcp_reference_can_be_removed():
    """有查看权但无使用权的旧 MCP 仍可从配置中删除。"""

    merged = merge_agent_config_json(
        {"context": {"mcps": ["read-only", "private"]}},
        {"context": {"mcps": []}},
        resource_access={"mcps": set()},
        preserve_hidden_mcp_references=True,
        visible_mcp_references={"read-only"},
    )
    assert merged["context"]["mcps"] == ["private"]


@pytest.mark.asyncio
async def test_empty_mcp_patch_still_resolves_user_access(monkeypatch):
    """普通用户的空列表补丁也要取得真实权限集合供隐藏引用保留。"""

    resolver = AsyncMock(return_value={"mcps": [{"key": "visible"}]})
    monkeypatch.setattr(agent_config_service, "resolve_agent_resource_options", resolver)
    config, access = await agent_config_service.prepare_agent_config_write(
        {"context": {"mcps": []}},
        context_schema=None,
        db=object(),
        user=_user(),
    )
    assert config == {"context": {"mcps": []}}
    assert access == {"mcps": {"visible"}}
    resolver.assert_awaited_once()
