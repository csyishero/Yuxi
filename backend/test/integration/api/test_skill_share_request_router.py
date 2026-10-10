"""个人 Skill 共享申请的真实 HTTP 生命周期。"""

from __future__ import annotations

import uuid

import pytest


@pytest.mark.integration
@pytest.mark.asyncio
async def test_personal_skill_share_requires_review_and_preserves_source(test_client, standard_user, admin_headers):
    """申请快照只由管理员发布，个人原件和来源记录继续存在。"""
    headers = standard_user["headers"]
    slug = f"pytest-share-{uuid.uuid4().hex[:8]}"
    original = f"---\nname: {slug}\ndescription: original review content\n---\n# Original\n"
    departments = await test_client.get("/api/departments", headers=admin_headers)
    assert departments.status_code == 200, departments.text
    department_id = standard_user["user"]["department_id"]
    assert department_id in {department["id"] for department in departments.json()}

    draft = await test_client.post(
        "/api/skills/import/prepare",
        headers=headers,
        files={"file": ("SKILL.md", original.encode(), "text/markdown")},
    )
    assert draft.status_code == 200, draft.text
    draft_id = draft.json()["data"]["draft_id"]
    installed = await test_client.post(
        f"/api/skills/personal/install-drafts/{draft_id}/confirm",
        headers=headers,
        json={"slugs": [slug]},
    )
    assert installed.status_code == 200, installed.text

    published_slug = None
    try:
        submitted = await test_client.post(f"/api/skills/personal/{slug}/share-request", headers=headers)
        assert submitted.status_code == 200, submitted.text
        request = submitted.json()["data"]
        assert request["status"] == "pending"
        assert request["owner_uid"] == standard_user["user"]["uid"]
        assert len(request["content_hash"]) == 64

        duplicate = await test_client.post(f"/api/skills/personal/{slug}/share-request", headers=headers)
        assert duplicate.status_code == 400, duplicate.text
        forbidden = await test_client.post(
            f"/api/skills/share-requests/{request['id']}/approve",
            headers=headers,
            json={"department_ids": [department_id]},
        )
        assert forbidden.status_code == 403, forbidden.text

        own_list = await test_client.get("/api/skills/share-requests", headers=headers)
        assert own_list.status_code == 200, own_list.text
        assert any(item["id"] == request["id"] for item in own_list.json()["data"])
        snapshot = await test_client.get(f"/api/skills/share-requests/{request['id']}/snapshot", headers=admin_headers)
        assert snapshot.status_code == 200, snapshot.text
        assert snapshot.json()["data"]["content"] == original

        approved = await test_client.post(
            f"/api/skills/share-requests/{request['id']}/approve",
            headers=admin_headers,
            json={"department_ids": [department_id], "note": "核验通过"},
        )
        assert approved.status_code == 200, approved.text
        assert approved.json()["data"]["status"] == "approved"
        published_slug = approved.json()["data"]["published_slug"]
        assert published_slug
        assert approved.json()["data"]["department_ids"] == [department_id]

        repeated = await test_client.post(
            f"/api/skills/share-requests/{request['id']}/approve",
            headers=admin_headers,
            json={"department_ids": [department_id]},
        )
        assert repeated.status_code == 400, repeated.text
        cards = await test_client.get("/api/skills", headers=headers)
        assert cards.status_code == 200, cards.text
        matching = [card for card in cards.json()["data"] if card["slug"] == slug]
        assert any(card["source_scope"] == "personal" for card in matching)
        assert any(card["slug"] == published_slug and card["source_scope"] == "shared" for card in cards.json()["data"])
    finally:
        if published_slug:
            await test_client.delete(f"/api/system/skills/{published_slug}", headers=admin_headers)
        await test_client.delete(f"/api/skills/personal/{slug}", headers=headers)
