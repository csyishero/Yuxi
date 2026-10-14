"""智能体列表与详情返回创建人当前所属部门。"""

from __future__ import annotations

import uuid

import pytest

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


async def test_agent_cards_and_detail_show_creator_and_department(test_client, admin_headers, standard_user):
    """普通用户创建的智能体显示本人和部门，内置默认智能体保持系统身份。"""
    owner = standard_user["user"]
    slug = f"pytest-owner-label-{uuid.uuid4().hex[:10]}"
    hidden_slug = f"pytest-hidden-owner-{uuid.uuid4().hex[:10]}"
    departments_response = await test_client.get("/api/departments", headers=admin_headers)
    assert departments_response.status_code == 200, departments_response.text
    department_name = next(item["name"] for item in departments_response.json() if item["id"] == owner["department_id"])

    created = await test_client.post(
        "/api/agent",
        headers=standard_user["headers"],
        json={"name": "Pytest owner label", "slug": slug, "backend_id": "ChatbotAgent"},
    )
    assert created.status_code == 200, created.text
    hidden_created = None
    try:
        hidden_created = await test_client.post(
            "/api/agent",
            headers=admin_headers,
            json={"name": "Hidden admin agent", "slug": hidden_slug, "backend_id": "ChatbotAgent"},
        )
        assert hidden_created.status_code == 200, hidden_created.text

        expected = {
            "created_by": owner["uid"],
            "owner_name": owner["username"],
            "owner_is_system": False,
            "owner_department_id": owner["department_id"],
            "owner_department_name": department_name,
        }
        assert {key: created.json()["agent"][key] for key in expected} == expected

        listed = await test_client.get("/api/agent", headers=standard_user["headers"])
        assert listed.status_code == 200, listed.text
        card = next(item for item in listed.json()["agents"] if item["slug"] == slug)
        assert {key: card[key] for key in expected} == expected
        assert all(item["slug"] != hidden_slug for item in listed.json()["agents"])

        default = next(item for item in listed.json()["agents"] if item["slug"] == "default-chatbot")
        assert default["owner_name"] == "系统"
        assert default["owner_is_system"] is True
        assert default["owner_department_id"] is None
        assert default["owner_department_name"] is None

        detail = await test_client.get(f"/api/agent/{slug}", headers=standard_user["headers"])
        assert detail.status_code == 200, detail.text
        assert {key: detail.json()["agent"][key] for key in expected} == expected

        hidden_detail = await test_client.get(f"/api/agent/{hidden_slug}", headers=standard_user["headers"])
        assert hidden_detail.status_code == 404
    finally:
        deleted = await test_client.delete(f"/api/agent/{slug}", headers=standard_user["headers"])
        assert deleted.status_code == 200, deleted.text
        if hidden_created is not None and hidden_created.status_code == 200:
            hidden_deleted = await test_client.delete(f"/api/agent/{hidden_slug}", headers=admin_headers)
            assert hidden_deleted.status_code == 200, hidden_deleted.text


async def test_agent_card_resolves_share_department_names_for_regular_reader(test_client, admin_headers, standard_user):
    """普通成员的智能体列表返回各共享权限对应的部门名。"""
    department_id = standard_user["user"]["department_id"]
    departments = await test_client.get("/api/departments", headers=admin_headers)
    assert departments.status_code == 200, departments.text
    department_name = next(item["name"] for item in departments.json() if item["id"] == department_id)
    slug = f"pytest-agent-scope-label-{uuid.uuid4().hex[:10]}"
    share_config = {
        "version": 2,
        "read_scope": {"access_level": "department", "department_ids": [department_id]},
        "edit_scope": {"access_level": "department", "department_ids": [department_id]},
        "manage_scope": {"access_level": "department", "department_ids": [department_id]},
    }

    created = await test_client.post(
        "/api/agent",
        headers=admin_headers,
        json={"name": "Scope labels", "slug": slug, "share_config": share_config},
    )
    assert created.status_code == 200, created.text
    try:
        listed = await test_client.get("/api/agent", headers=standard_user["headers"])
        assert listed.status_code == 200, listed.text
        card = next(item for item in listed.json()["agents"] if item["slug"] == slug)
        assert card["read_department_names"] == [department_name]
        assert card["edit_department_names"] == [department_name]
        assert card["manage_department_names"] == [department_name]

        detail = await test_client.get(f"/api/agent/{slug}", headers=standard_user["headers"])
        assert detail.status_code == 200, detail.text
        assert detail.json()["agent"]["manage_department_names"] == [department_name]
    finally:
        deleted = await test_client.delete(f"/api/agent/{slug}", headers=admin_headers)
        assert deleted.status_code == 200, deleted.text
