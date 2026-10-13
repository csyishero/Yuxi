from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
from yuxi.agents.mcp.service import MCPServerNotFoundError
from yuxi.storage.postgres.models_business import User

from server.routers.mcp_router import mcp
from server.utils.auth_middleware import get_admin_user, get_db, get_required_user


def _build_app(*, allow_admin: bool = True) -> FastAPI:
    app = FastAPI()
    app.include_router(mcp, prefix="/api")

    async def fake_db():
        return None

    async def fake_admin_user():
        if not allow_admin:
            from fastapi import HTTPException

            raise HTTPException(status_code=403, detail="需要管理员权限")
        return User(
            username="admin",
            uid="admin",
            password_hash="x",
            role="admin",
        )

    async def fake_required_user():
        return User(
            username="admin" if allow_admin else "user",
            uid="admin" if allow_admin else "user",
            password_hash="x",
            role="admin" if allow_admin else "user",
            department_id=1,
        )

    app.dependency_overrides[get_db] = fake_db
    app.dependency_overrides[get_admin_user] = fake_admin_user
    app.dependency_overrides[get_required_user] = fake_required_user
    return app


def test_update_mcp_server_status(monkeypatch):
    captured = {}

    class DummyServer:
        def __init__(self, enabled):
            self.slug = "demo-mcp"
            self.transport = "streamable_http"
            self.enabled = enabled

        def to_dict(self):
            return {"name": "demo-mcp", "enabled": self.enabled}

    async def fake_set_server_enabled(db, name, enabled, updated_by=None):
        captured["name"] = name
        captured["enabled"] = enabled
        captured["updated_by"] = updated_by
        return enabled, DummyServer(enabled)

    monkeypatch.setattr("server.routers.mcp_router.set_server_enabled", fake_set_server_enabled)

    client = TestClient(_build_app())
    resp = client.put("/api/system/mcp-servers/demo-mcp/status", json={"enabled": False})
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload["success"] is True
    assert payload["enabled"] is False
    assert payload["data"]["enabled"] is False
    assert captured == {"name": "demo-mcp", "enabled": False, "updated_by": "admin"}


def test_update_mcp_server_status_not_found(monkeypatch):
    async def fake_set_server_enabled(db, name, enabled, updated_by=None):
        raise MCPServerNotFoundError(f"Server '{name}' does not exist")

    monkeypatch.setattr("server.routers.mcp_router.set_server_enabled", fake_set_server_enabled)

    client = TestClient(_build_app())
    resp = client.put("/api/system/mcp-servers/missing/status", json={"enabled": True})
    assert resp.status_code == 404, resp.text


def test_update_mcp_server_status_rejects_legacy_stdio(monkeypatch):
    async def fake_set_server_enabled(db, name, enabled, updated_by=None):
        raise ValueError("历史 stdio MCP 已被禁用")

    monkeypatch.setattr("server.routers.mcp_router.set_server_enabled", fake_set_server_enabled)

    client = TestClient(_build_app())
    resp = client.put("/api/system/mcp-servers/legacy-stdio/status", json={"enabled": True})
    assert resp.status_code == 400, resp.text


def test_get_mcp_servers_normal_user_is_stripped(monkeypatch):
    class DummyServer:
        def __init__(self):
            self.name = "test-mcp"
            self.slug = "test-mcp"
            self.description = "test mcp description"
            self.transport = "streamable_http"
            self.url = "http://localhost:8000"
            self.command = "python"
            self.args = ["-m", "mcp"]
            self.env = {"API_KEY": "secret"}
            self.headers = {"Auth": "Bearer secret"}
            self.enabled = 1

        def to_dict(self):
            return {
                "name": self.name,
                "description": self.description,
                "transport": self.transport,
                "url": self.url,
                "command": self.command,
                "args": self.args,
                "env": self.env,
                "headers": self.headers,
                "enabled": bool(self.enabled),
            }

    async def fake_get_all_mcp_servers(db):
        return [DummyServer()]

    monkeypatch.setattr("server.routers.mcp_router.get_all_mcp_servers", fake_get_all_mcp_servers)

    # 1. 管理员请求，应该返回全部字段
    client_admin = TestClient(_build_app(allow_admin=True))
    resp_admin = client_admin.get("/api/system/mcp-servers")
    assert resp_admin.status_code == 200
    data_admin = resp_admin.json()["data"][0]
    assert data_admin["url"] == "http://localhost:8000"
    assert data_admin["command"] == "python"
    assert data_admin["env"] == {"API_KEY": "secret"}

    # 2. 普通用户请求，敏感字段及一切非安全白名单字段应该被彻底脱敏
    client_user = TestClient(_build_app(allow_admin=False))
    resp_user = client_user.get("/api/system/mcp-servers")
    assert resp_user.status_code == 200
    data_user = resp_user.json()["data"][0]
    assert "url" not in data_user
    assert "command" not in data_user
    assert "env" not in data_user
    assert "headers" not in data_user
    assert "transport" not in data_user  # NOTE: 进一步验证连 transport 等配置层元数据也一并过滤
    assert data_user["name"] == "test-mcp"
    assert data_user["slug"] == "test-mcp"
    assert data_user["permission"] == "edit"
    assert data_user["can_manage_sharing"] is False
    assert data_user["description"] == "test mcp description"
    assert data_user["enabled"] is True


def test_mcp_list_and_detail_hide_private_servers_and_connection_secrets(monkeypatch):
    class DummyServer:
        slug = "scoped-mcp"
        name = "Scoped MCP"
        description = "description"
        icon = None
        tags = []
        transport = "streamable_http"
        enabled = 1
        share_config = {
            "version": 2,
            "read_scope": {"access_level": "user", "user_uids": ["user"]},
            "edit_scope": {"access_level": "user", "user_uids": ["user"]},
            "manage_scope": None,
        }

        def to_dict(self):
            return {"slug": self.slug, "url": "https://secret.example/mcp", "headers": {"Authorization": "secret"}}

    server = DummyServer()

    async def fake_list(_db):
        return [server]

    async def fake_get(_db, _slug):
        return server

    monkeypatch.setattr("server.routers.mcp_router.get_all_mcp_servers", fake_list)
    monkeypatch.setattr("server.routers.mcp_router.get_mcp_server", fake_get)
    client = TestClient(_build_app(allow_admin=False))

    listed = client.get("/api/system/mcp-servers")
    assert listed.status_code == 200
    assert listed.json()["data"] == [
        {
            "slug": "scoped-mcp",
            "name": "Scoped MCP",
            "description": "description",
            "icon": None,
            "enabled": True,
            "tags": [],
            "permission": "edit",
            "can_manage_sharing": False,
        }
    ]

    detail = client.get("/api/system/mcp-servers/scoped-mcp")
    assert detail.status_code == 200
    assert detail.json()["data"] == listed.json()["data"][0]
    assert "secret" not in detail.text

    server.share_config = {"version": 2, "read_scope": None, "edit_scope": None, "manage_scope": None}
    assert client.get("/api/system/mcp-servers").json()["data"] == []
    assert client.get("/api/system/mcp-servers/scoped-mcp").status_code == 404
    assert client.get("/api/system/mcp-servers/scoped-mcp/sharing/options").status_code == 404


def test_mcp_tools_require_use_scope_and_sharing_requires_manage(monkeypatch):
    class DummyServer:
        slug = "scoped-mcp"
        name = "Scoped MCP"
        description = None
        icon = None
        tags = []
        transport = "streamable_http"
        enabled = 1
        disabled_tools = []
        share_config = {
            "version": 2,
            "read_scope": {"access_level": "global"},
            "edit_scope": None,
            "manage_scope": None,
        }

        def to_dict(self):
            return {"url": "https://secret.example/mcp", "headers": {"Authorization": "secret"}}

    server = DummyServer()
    tool_calls = []

    async def fake_get(_db, _slug):
        return server

    async def fake_tools(_slug):
        tool_calls.append(_slug)
        return []

    monkeypatch.setattr("server.routers.mcp_router.get_mcp_server", fake_get)
    monkeypatch.setattr("server.routers.mcp_router.get_all_mcp_tools", fake_tools)
    client = TestClient(_build_app(allow_admin=False))

    assert client.get("/api/system/mcp-servers/scoped-mcp/tools").status_code == 403
    assert client.get("/api/system/mcp-servers/scoped-mcp/sharing/options").status_code == 403
    assert tool_calls == []

    server.share_config["edit_scope"] = {"access_level": "global"}
    tools = client.get("/api/system/mcp-servers/scoped-mcp/tools")
    assert tools.status_code == 200
    assert tools.json()["data"] == []
    assert tool_calls == ["scoped-mcp"]

    async def failing_tools(_slug):
        raise RuntimeError("Bearer secret https://secret.example/mcp")

    monkeypatch.setattr("server.routers.mcp_router.get_all_mcp_tools", failing_tools)
    failed = client.get("/api/system/mcp-servers/scoped-mcp/tools")
    assert failed.status_code == 500
    assert failed.json()["detail"] == "获取工具失败"
    assert "secret" not in failed.text


def test_mcp_delegated_manager_gets_sharing_without_connection_secrets(monkeypatch):
    from types import SimpleNamespace

    class DummyServer:
        slug = "shared-mcp"
        name = "Shared MCP"
        description = None
        icon = None
        tags = []
        transport = "streamable_http"
        enabled = 1
        share_config = {
            "version": 2,
            "read_scope": {"access_level": "global"},
            "edit_scope": {"access_level": "global"},
            "manage_scope": {"access_level": "user", "user_uids": ["user"]},
        }

        def to_dict(self):
            return {"url": "https://secret.example/mcp", "headers": {"Authorization": "secret"}}

    server = DummyServer()

    async def fake_get(_db, _slug):
        return server

    async def fake_department(_self, department_id):
        assert department_id == 1
        return SimpleNamespace(id=1, name="研发")

    async def fake_users(_self, *, limit, department_id):
        assert limit is None
        assert department_id == 1
        return [(SimpleNamespace(uid="user", username="Alice", role="user", department_id=1), "研发")]

    monkeypatch.setattr("server.routers.mcp_router.get_mcp_server", fake_get)
    monkeypatch.setattr("yuxi.agents.mcp.service.DepartmentRepository.get_by_id", fake_department)
    monkeypatch.setattr("yuxi.agents.mcp.service.UserRepository.list_with_department", fake_users)
    client = TestClient(_build_app(allow_admin=False))

    detail = client.get("/api/system/mcp-servers/shared-mcp")
    assert detail.status_code == 200
    data = detail.json()["data"]
    assert data["permission"] == "manage"
    assert data["can_manage_sharing"] is True
    assert "share_config" not in data
    assert "secret" not in detail.text

    options = client.get("/api/system/mcp-servers/shared-mcp/sharing/options")
    assert options.status_code == 200
    assert options.json() == {
        "success": True,
        "data": {
            "departments": [{"id": 1, "name": "研发"}],
            "users": [
                {"uid": "user", "username": "Alice", "role": "user", "department_id": 1, "department_name": "研发"}
            ],
            "share_config": {
                "version": 2,
                "read_scope": {"access_level": "global", "department_ids": [], "user_uids": []},
                "edit_scope": {"access_level": "global", "department_ids": [], "user_uids": []},
                "manage_scope": {"access_level": "user", "department_ids": [], "user_uids": ["user"]},
            },
            "protected_scopes": {"read_scope": True, "edit_scope": True, "manage_scope": False},
        },
    }


def test_create_mcp_server_rejects_extra_config_fields():
    client = TestClient(_build_app())
    resp = client.post(
        "/api/system/mcp-servers",
        json={
            "slug": "demo-mcp",
            "name": "Demo MCP",
            "transport": "streamable_http",
            "url": "https://example.com/mcp",
            "enabled": True,
        },
    )

    assert resp.status_code == 422, resp.text


def test_create_mcp_server_rejects_stdio_command():
    client = TestClient(_build_app())
    resp = client.post(
        "/api/system/mcp-servers",
        json={
            "slug": "unsafe-mcp",
            "name": "Unsafe MCP",
            "transport": "stdio",
            "command": "python3",
            "args": ["-c", "print('unsafe')"],
        },
    )

    assert resp.status_code == 422, resp.text


def test_update_mcp_server_rejects_extra_config_fields():
    client = TestClient(_build_app())
    resp = client.put(
        "/api/system/mcp-servers/demo-mcp",
        json={
            "name": "Demo MCP",
            "transport": "streamable_http",
            "url": "https://example.com/mcp",
            "slug": "renamed-mcp",
        },
    )

    assert resp.status_code == 422, resp.text


def test_update_builtin_mcp_server_rejects_connection_changes(monkeypatch):
    async def fake_update_mcp_server(db, slug, **kwargs):
        raise PermissionError("系统内置 MCP 的连接配置由代码管理，无法通过接口修改")

    monkeypatch.setattr("server.routers.mcp_router.update_mcp_server", fake_update_mcp_server)

    client = TestClient(_build_app())
    resp = client.put(
        "/api/system/mcp-servers/mcp-server-chart",
        json={
            "transport": "streamable_http",
            "url": "https://example.com/mcp",
        },
    )

    assert resp.status_code == 403, resp.text


def test_update_mcp_server_not_found(monkeypatch):
    async def fake_update_mcp_server(db, slug, **kwargs):
        raise MCPServerNotFoundError(f"Server '{slug}' does not exist")

    monkeypatch.setattr("server.routers.mcp_router.update_mcp_server", fake_update_mcp_server)

    client = TestClient(_build_app())
    resp = client.put(
        "/api/system/mcp-servers/missing",
        json={"name": "Missing MCP"},
    )

    assert resp.status_code == 404, resp.text
