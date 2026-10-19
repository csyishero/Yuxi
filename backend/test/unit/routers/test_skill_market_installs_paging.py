"""市场安装列表的分页 HTTP 契约。"""

from fastapi import FastAPI
from fastapi.testclient import TestClient
from yuxi.storage.postgres.models_business import User

from server.routers.skill_market_router import market_router
from server.utils.auth_middleware import get_current_user, get_db


def test_market_installs_route_forwards_page_and_reports_next_page(monkeypatch):
    """页面能请求后续安装记录，而不依赖全量返回。"""
    app = FastAPI()
    app.include_router(market_router, prefix="/api")

    async def fake_db():
        return None

    async def fake_user():
        return User(uid="viewer", username="viewer", password_hash="x", role="user")

    async def fake_page(_db, *, user, offset, limit, search):
        assert (user.uid, offset, limit, search) == ("viewer", 2, 1, "older")
        return [{"slug": "older-skill"}], 5

    app.dependency_overrides[get_db] = fake_db
    app.dependency_overrides[get_current_user] = fake_user
    monkeypatch.setattr("server.routers.skill_market_router.page_user_market_installs", fake_page)

    response = TestClient(app).get("/api/skill-market/-/installs?offset=2&limit=1&search=older")

    assert response.status_code == 200, response.text
    assert response.json() == {
        "success": True,
        "data": [{"slug": "older-skill"}],
        "total": 5,
        "has_more": True,
        "limit": 1,
        "offset": 2,
    }


def test_market_installs_without_paging_keeps_legacy_complete_response(monkeypatch):
    """旧客户端没有传页码时仍能看到全部本人安装。"""
    app = FastAPI()
    app.include_router(market_router, prefix="/api")

    async def fake_db():
        return None

    async def fake_user():
        return User(uid="viewer", username="viewer", password_hash="x", role="user")

    async def fake_all(_db, *, user):
        assert user.uid == "viewer"
        return [{"slug": f"skill-{index}"} for index in range(31)]

    app.dependency_overrides[get_db] = fake_db
    app.dependency_overrides[get_current_user] = fake_user
    monkeypatch.setattr("server.routers.skill_market_router.list_user_market_installs", fake_all, raising=False)

    response = TestClient(app).get("/api/skill-market/-/installs")

    assert response.status_code == 200, response.text
    assert len(response.json()["data"]) == 31
