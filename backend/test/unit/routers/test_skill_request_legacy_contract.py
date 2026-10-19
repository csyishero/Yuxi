"""Skill 审核列表同时支持旧数组响应和显式分页响应。"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.routers.skill_market_router import market_router
from server.routers.skill_router import user_skills
from server.utils.auth_middleware import get_db, get_required_user
from yuxi.storage.postgres.models_business import User


@pytest.mark.parametrize(
    ("path", "patch_target"),
    [
        ("/api/skills/share-requests", "server.routers.skill_router.list_skill_share_requests"),
        ("/api/skill-market/requests", "server.routers.skill_market_router.list_market_requests"),
    ],
)
def test_request_list_preserves_legacy_array_when_limit_is_absent(monkeypatch, path, patch_target):
    calls = []

    async def fake_list(_db, *, operator, mine_only, status, slug, offset, limit):
        calls.append((operator.uid, mine_only, status, slug, offset, limit))
        return {"items": [{"id": "r1"}, {"id": "r2"}], "total": 2, "offset": offset, "limit": limit}

    monkeypatch.setattr(patch_target, fake_list)
    app = FastAPI()
    app.include_router(user_skills, prefix="/api")
    app.include_router(market_router, prefix="/api")

    async def fake_db():
        return None

    async def fake_user():
        return User(uid="owner", username="owner", password_hash="x", role="user", department_id=3)

    app.dependency_overrides[get_db] = fake_db
    app.dependency_overrides[get_required_user] = fake_user
    client = TestClient(app)

    legacy = client.get(path, params={"mine_only": "true", "status": "approved", "slug": "demo"})
    assert legacy.status_code == 200, legacy.text
    assert legacy.json()["data"] == [{"id": "r1"}, {"id": "r2"}]
    assert calls[-1] == ("owner", True, "approved", "demo", 0, None)

    page = client.get(path, params={"mine_only": "true", "status": "approved", "slug": "demo", "offset": 1, "limit": 1})
    assert page.status_code == 200, page.text
    assert page.json()["data"] == {"items": [{"id": "r1"}, {"id": "r2"}], "total": 2, "offset": 1, "limit": 1}
    assert calls[-1] == ("owner", True, "approved", "demo", 1, 1)
