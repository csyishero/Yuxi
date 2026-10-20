"""市场安装列表的分页 HTTP 契约。"""

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pathlib import Path
import pytest
from yuxi.agents.skills import market, service as skill_service
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


def test_catalog_all_install_page_forwards_dedup_mode_and_requires_limit(monkeypatch):
    """全部视图使用独立安装分页模式，普通安装页参数保持原样。"""
    app = FastAPI()
    app.include_router(market_router, prefix="/api")

    async def fake_db():
        return None

    async def fake_user():
        return User(uid="viewer", username="viewer", password_hash="x", role="user")

    calls = []

    async def fake_page(_db, **kwargs):
        calls.append(kwargs)
        return [{"slug": "market-one"}], 1

    app.dependency_overrides[get_db] = fake_db
    app.dependency_overrides[get_current_user] = fake_user
    monkeypatch.setattr("server.routers.skill_market_router.page_user_market_installs", fake_page)
    client = TestClient(app)

    catalog = client.get("/api/skill-market/-/installs?catalog_all=true&limit=1")
    ordinary = client.get("/api/skill-market/-/installs?limit=1")

    assert catalog.status_code == 200, catalog.text
    assert ordinary.status_code == 200, ordinary.text
    assert catalog.json()["total"] == 1
    assert calls[0]["catalog_all"] is True
    assert "catalog_all" not in calls[1]
    assert client.get("/api/skill-market/-/installs?catalog_all=true").status_code == 422


@pytest.mark.asyncio
async def test_catalog_all_install_page_only_folds_valid_personal_origins_matching_search(monkeypatch, tmp_path: Path):
    """市场安装页只按本人有效且搜索命中的原件折叠，损坏原件不遮盖。"""
    owned = tmp_path / "owned"
    owned.mkdir()
    (owned / "SKILL.md").write_text("---\nname: owned\ndescription: budget report\n---\n# Skill\n", encoding="utf-8")
    broken = tmp_path / "broken"
    broken.mkdir()
    (broken / "SKILL.md").write_text("invalid", encoding="utf-8")
    monkeypatch.setattr(skill_service, "personal_skills_root", lambda _uid: tmp_path)

    class FakeRepo:
        def __init__(self, _db):
            pass

        async def installed_personal_slugs_for_user(self, uid):
            assert uid == "viewer"
            return {"owned", "broken"}

        async def page_installed_releases_for_user(
            self, uid, *, user, offset, limit, search, exclude_own_personal_slugs
        ):
            assert (uid, user.uid, offset, limit, search) == ("viewer", "viewer", 0, 1, "budget")
            assert exclude_own_personal_slugs == {"owned"}
            return [], 0

    monkeypatch.setattr(market, "MarketSkillRepository", FakeRepo)
    user = User(uid="viewer", username="viewer", password_hash="x", role="user", department_id=1)

    cards, total = await market.page_user_market_installs(
        None, user=user, offset=0, limit=1, search="budget", catalog_all=True
    )

    assert (cards, total) == ([], 0)
