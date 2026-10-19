"""旧调用保持返回结构，新页面显式使用分页接口。"""

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.routers import dashboard_router
from server.utils.auth_middleware import get_db, get_superadmin_user


def test_feedback_legacy_array_and_explicit_page(monkeypatch):
    calls = []
    item = {
        "id": 1,
        "uid": "user-1",
        "username": "user",
        "avatar": None,
        "rating": "like",
        "reason": None,
        "created_at": "2026-01-01T00:00:00Z",
        "message_content": "reply",
        "conversation_title": "chat",
        "agent_id": "agent",
    }

    class Service:
        def __init__(self, db, *, scope):
            pass

        async def get_feedbacks(self, **kwargs):
            calls.append(kwargs)
            return {"items": [item], "total": 2, "limit": kwargs.get("limit"), "offset": kwargs.get("offset", 0)}

    monkeypatch.setattr(dashboard_router, "DashboardService", Service)
    app = FastAPI()
    app.include_router(dashboard_router.dashboard, prefix="/api")
    app.dependency_overrides[get_db] = lambda: None
    app.dependency_overrides[get_superadmin_user] = lambda: SimpleNamespace(uid="admin", role="superadmin")

    with TestClient(app) as client:
        legacy = client.get("/api/dashboard/feedbacks")
        page = client.get("/api/dashboard/feedbacks/page?limit=1&offset=1")

    assert legacy.status_code == 200 and legacy.json() == [item]
    assert page.status_code == 200 and page.json()["total"] == 2
    assert calls == [
        {"rating": None, "agent_id": None, "limit": None, "offset": 0},
        {"rating": None, "agent_id": None, "limit": 1, "offset": 1},
    ]


@pytest.mark.asyncio
async def test_conversation_detail_without_page_keeps_legacy_full_history(monkeypatch):
    calls = []

    class Service:
        def __init__(self, db, *, scope):
            pass

        async def get_conversation_detail(self, thread_id, *, limit, offset):
            calls.append((thread_id, limit, offset))
            return {"thread_id": thread_id, "messages": [{"id": i} for i in range(75)]}

    monkeypatch.setattr(dashboard_router, "DashboardService", Service)
    await dashboard_router.get_conversation_detail(
        "thread-1", scope="all", db=None, current_user=SimpleNamespace(uid="admin")
    )
    await dashboard_router.get_conversation_detail(
        "thread-1", scope="all", limit=50, offset=0, db=None, current_user=SimpleNamespace(uid="admin")
    )
    assert calls == [("thread-1", None, None), ("thread-1", 50, 0)]
