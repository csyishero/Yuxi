from __future__ import annotations

from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from yuxi.agents.mcp import service as mcp_service
from yuxi.storage.postgres import manager as postgres_manager
from yuxi.storage.postgres.models_business import Department, MCPServer, User


class _AsyncSessionContext:
    def __init__(self, db):
        self.db = db

    async def __aenter__(self):
        return self.db

    async def __aexit__(self, *_args):
        return False


class _FailingSessionContext:
    async def __aenter__(self):
        raise RuntimeError("database-secret-must-not-be-swallowed")

    async def __aexit__(self, *_args):
        return False


@pytest_asyncio.fixture
async def mcp_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Department.__table__.create)
        await conn.run_sync(User.__table__.create)
        await conn.run_sync(MCPServer.__table__.create)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session

    await engine.dispose()


class _FakeClient:
    def __init__(self, tools):
        self._tools = tools

    async def get_tools(self):
        return self._tools


async def test_mcp_new_server_is_private_and_sharing_requires_management(mcp_session):
    mcp_session.add_all(
        [
            Department(id=1, name="研发"),
            Department(id=2, name="市场"),
            User(uid="user-1", username="Alice", password_hash="x", department_id=1),
            User(uid="user-2", username="Other", password_hash="x", department_id=2),
        ]
    )
    await mcp_session.commit()
    server = await mcp_service.create_mcp_server(
        mcp_session,
        slug="new-private",
        name="New private",
        transport="streamable_http",
        url="https://example.com/mcp",
        created_by="admin",
    )
    assert server.share_config == {"version": 2, "read_scope": None, "edit_scope": None, "manage_scope": None}
    server_slug = server.slug

    user = SimpleNamespace(uid="user-1", username="Alice", role="user", department_id=1)
    with pytest.raises(mcp_service.MCPServerNotFoundError, match="does not exist"):
        await mcp_service.update_mcp_share_config(
            mcp_session,
            slug=server_slug,
            share_config={
                "version": 2,
                "read_scope": {"access_level": "global"},
                "edit_scope": None,
                "manage_scope": None,
            },
            operator=user,
        )
    await mcp_session.rollback()

    admin = SimpleNamespace(uid="admin", username="admin", role="admin", department_id=1)
    updated = await mcp_service.update_mcp_share_config(
        mcp_session,
        slug=server_slug,
        share_config={
            "version": 2,
            "read_scope": {"access_level": "global"},
            "edit_scope": {"access_level": "global"},
            "manage_scope": {"access_level": "user", "user_uids": ["user-1"]},
        },
        operator=admin,
    )
    assert updated.updated_by == "admin"
    assert await mcp_service.get_usable_mcp_server_slugs(mcp_session, user) == {"new-private"}

    with pytest.raises(PermissionError, match="全员授权"):
        await mcp_service.update_mcp_share_config(
            mcp_session,
            slug=server_slug,
            share_config={
                "version": 2,
                "read_scope": {"access_level": "user", "user_uids": ["user-1"]},
                "edit_scope": {"access_level": "user", "user_uids": ["user-1"]},
                "manage_scope": {"access_level": "user", "user_uids": ["user-1"]},
            },
            operator=user,
        )
    await mcp_session.rollback()

    await mcp_service.update_mcp_share_config(
        mcp_session,
        slug=server_slug,
        share_config={
            "version": 2,
            "read_scope": {"access_level": "department", "department_ids": [1]},
            "edit_scope": {"access_level": "user", "user_uids": ["user-1"]},
            "manage_scope": {"access_level": "user", "user_uids": ["user-1"]},
        },
        operator=admin,
    )
    with pytest.raises(PermissionError, match="只能给本部门授权"):
        await mcp_service.update_mcp_share_config(
            mcp_session,
            slug=server_slug,
            share_config={
                "version": 2,
                "read_scope": {"access_level": "user", "user_uids": ["user-2"]},
                "edit_scope": None,
                "manage_scope": None,
            },
            operator=user,
        )
    await mcp_session.rollback()

    delegated = await mcp_service.update_mcp_share_config(
        mcp_session,
        slug=server_slug,
        share_config={
            "version": 2,
            "read_scope": {"access_level": "user", "user_uids": ["user-1"]},
            "edit_scope": {"access_level": "user", "user_uids": ["user-1"]},
            "manage_scope": {"access_level": "user", "user_uids": ["user-1"]},
        },
        operator=user,
    )
    assert delegated.updated_by == "Alice"

    outsider = SimpleNamespace(uid="other", username="Other", role="user", department_id=2)
    assert await mcp_service.get_usable_mcp_server_slugs(mcp_session, outsider) == set()


async def test_mcp_sharing_rejects_use_without_view(mcp_session):
    server = await mcp_service.create_mcp_server(
        mcp_session,
        slug="new-private",
        name="New private",
        transport="streamable_http",
        url="https://example.com/mcp",
        created_by="admin",
    )
    admin = SimpleNamespace(uid="admin", username="admin", role="admin", department_id=1)
    server_slug = server.slug

    with pytest.raises(ValueError, match="查看范围"):
        await mcp_service.update_mcp_share_config(
            mcp_session,
            slug=server_slug,
            share_config={
                "version": 2,
                "read_scope": None,
                "edit_scope": {"access_level": "global"},
                "manage_scope": None,
            },
            operator=admin,
        )
    await mcp_session.rollback()

    reloaded = await mcp_service.get_mcp_server(mcp_session, server_slug)
    assert reloaded.share_config["read_scope"] is None

    for member_scope in (
        {"access_level": "department", "department_ids": 1},
        {"access_level": "user", "user_uids": 1},
    ):
        with pytest.raises(ValueError, match="必须是数组"):
            await mcp_service.update_mcp_share_config(
                mcp_session,
                slug=server_slug,
                share_config={
                    "version": 2,
                    "read_scope": member_scope,
                    "edit_scope": None,
                    "manage_scope": None,
                },
                operator=admin,
            )
        await mcp_session.rollback()

    with pytest.raises(ValueError, match="只能授予指定用户"):
        await mcp_service.update_mcp_share_config(
            mcp_session,
            slug=server_slug,
            share_config={
                "version": 2,
                "read_scope": {"access_level": "global"},
                "edit_scope": {"access_level": "global"},
                "manage_scope": {"access_level": "global"},
            },
            operator=admin,
        )


async def test_ensure_builtin_mcp_servers_removes_retired_system_servers(monkeypatch, mcp_session):
    mcp_session.add_all(
        [
            MCPServer(
                slug="sequentialthinking",
                name="sequentialthinking",
                transport="streamable_http",
                url="https://example.com/sequentialthinking/mcp",
                enabled=1,
                created_by="system",
                updated_by="system",
            ),
            MCPServer(
                slug="mcp-server-chart",
                name="Mcp Server Chart",
                transport="stdio",
                command="npx",
                args=["-y", "@antv/mcp-server-chart"],
                enabled=1,
                created_by="system",
                updated_by="system",
            ),
        ]
    )
    await mcp_session.commit()

    monkeypatch.setattr(
        postgres_manager.pg_manager,
        "get_async_session_context",
        lambda: _AsyncSessionContext(mcp_session),
    )

    await mcp_service.ensure_builtin_mcp_servers_in_db()

    remaining = await mcp_session.scalars(
        select(MCPServer).where(MCPServer.slug.in_(("sequentialthinking", "mcp-server-chart")))
    )
    assert list(remaining) == []


async def test_new_builtin_mcp_is_private_without_rewriting_existing_sharing(monkeypatch, mcp_session):
    monkeypatch.setattr(
        mcp_service,
        "_DEFAULT_MCP_SERVERS",
        {"builtin-demo": {"name": "Built-in demo", "transport": "streamable_http", "url": "https://example.com/mcp"}},
    )
    monkeypatch.setattr(mcp_service, "_BUILTIN_MCP_SERVER_SLUGS", ("builtin-demo",))
    monkeypatch.setattr(
        postgres_manager.pg_manager,
        "get_async_session_context",
        lambda: _AsyncSessionContext(mcp_session),
    )

    await mcp_service.ensure_builtin_mcp_servers_in_db()
    server = await mcp_session.scalar(select(MCPServer).where(MCPServer.slug == "builtin-demo"))
    assert server.share_config == {"version": 2, "read_scope": None, "edit_scope": None, "manage_scope": None}

    server.share_config = None
    await mcp_session.commit()
    await mcp_service.ensure_builtin_mcp_servers_in_db()
    await mcp_session.refresh(server)
    assert server.share_config is None


async def test_ensure_builtin_mcp_servers_preserves_user_server_with_retired_slug(monkeypatch, mcp_session):
    user_server = MCPServer(
        slug="mcp-server-chart",
        name="用户自定义 MCP",
        description="user managed",
        transport="streamable_http",
        url="https://example.com/mcp",
        enabled=1,
        created_by="admin",
        updated_by="admin",
    )
    mcp_session.add(user_server)
    await mcp_session.commit()

    monkeypatch.setattr(
        postgres_manager.pg_manager,
        "get_async_session_context",
        lambda: _AsyncSessionContext(mcp_session),
    )

    await mcp_service.ensure_builtin_mcp_servers_in_db()

    server = await mcp_session.scalar(select(MCPServer).where(MCPServer.slug == "mcp-server-chart"))
    assert server is not None
    assert server.created_by == "admin"


async def test_ensure_builtin_mcp_servers_disables_legacy_user_stdio(monkeypatch, mcp_session):
    legacy_server = MCPServer(
        slug="legacy-stdio",
        name="历史 stdio",
        transport="stdio",
        command="python3",
        enabled=1,
        created_by="system",
        updated_by="system",
    )
    mcp_session.add(legacy_server)
    await mcp_session.commit()

    monkeypatch.setattr(
        postgres_manager.pg_manager,
        "get_async_session_context",
        lambda: _AsyncSessionContext(mcp_session),
    )

    await mcp_service.ensure_builtin_mcp_servers_in_db()

    await mcp_session.refresh(legacy_server)
    assert legacy_server.enabled == 0


async def test_builtin_mcp_initialization_propagates_failure_to_entrypoint(monkeypatch):
    monkeypatch.setattr(
        postgres_manager.pg_manager,
        "get_async_session_context",
        lambda: _FailingSessionContext(),
    )

    with pytest.raises(RuntimeError, match="must-not-be-swallowed"):
        await mcp_service.ensure_builtin_mcp_servers_in_db()


async def test_runtime_configs_exclude_user_created_stdio_servers(mcp_session):
    mcp_session.add_all(
        [
            MCPServer(
                slug="mcp-server-chart",
                name="内置 stdio",
                transport="stdio",
                command="tampered-command",
                args=["--unsafe"],
                enabled=1,
                created_by="system",
                updated_by="system",
            ),
            MCPServer(
                slug="user-stdio",
                name="用户 stdio",
                transport="stdio",
                command="python3",
                args=["-c", "print('unsafe')"],
                enabled=1,
                created_by="admin",
                updated_by="admin",
            ),
            MCPServer(
                slug="forged-system-stdio",
                name="伪造系统 stdio",
                transport="stdio",
                command="python3",
                args=["-c", "print('unsafe')"],
                enabled=1,
                created_by="system",
                updated_by="system",
            ),
            MCPServer(
                slug="remote-http",
                name="远程 HTTP",
                transport="streamable_http",
                url="https://example.com/mcp",
                command="python3",
                args=["-c", "print('stale')"],
                enabled=1,
                created_by="admin",
                updated_by="admin",
            ),
        ]
    )
    await mcp_session.commit()

    configs = await mcp_service._load_enabled_mcp_server_configs(db=mcp_session)
    slugs = await mcp_service.get_enabled_mcp_server_slugs(db=mcp_session)

    assert set(configs) == {"remote-http"}
    assert set(slugs) == {"remote-http"}
    assert "command" not in configs["remote-http"]
    assert "args" not in configs["remote-http"]


async def test_create_mcp_server_rejects_user_created_stdio(mcp_session):
    with pytest.raises(ValueError, match="stdio"):
        await mcp_service.create_mcp_server(
            mcp_session,
            slug="unsafe-mcp",
            name="Unsafe MCP",
            transport="stdio",
            created_by="admin",
        )

    server = await mcp_session.scalar(select(MCPServer).where(MCPServer.slug == "unsafe-mcp"))
    assert server is None


async def test_create_remote_mcp_server_allows_retired_builtin_slug(mcp_session):
    server = await mcp_service.create_mcp_server(
        mcp_session,
        slug="mcp-server-chart",
        name="管理员远程 MCP",
        transport="streamable_http",
        url="https://example.com/mcp",
        created_by="admin",
    )

    assert server.created_by == "admin"
    assert server.transport == "streamable_http"


async def test_update_retired_builtin_record_migrates_to_remote_transport(mcp_session):
    server = MCPServer(
        slug="mcp-server-chart",
        name="内置 stdio",
        transport="stdio",
        command="trusted-command",
        enabled=1,
        created_by="system",
        updated_by="system",
    )
    mcp_session.add(server)
    await mcp_session.commit()

    await mcp_service.update_mcp_server(
        mcp_session,
        slug="mcp-server-chart",
        transport="streamable_http",
        url="https://example.com/mcp",
        updated_by="admin",
    )

    await mcp_session.refresh(server)
    assert server.transport == "streamable_http"
    assert server.url == "https://example.com/mcp"
    assert server.command is None


async def test_update_legacy_stdio_requires_remote_url(mcp_session):
    legacy_server = MCPServer(
        slug="legacy-stdio",
        name="历史 stdio",
        transport="stdio",
        command="python3",
        enabled=0,
        created_by="admin",
        updated_by="admin",
    )
    mcp_session.add(legacy_server)
    await mcp_session.commit()

    with pytest.raises(ValueError, match="url 必填"):
        await mcp_service.update_mcp_server(
            mcp_session,
            slug="legacy-stdio",
            transport="streamable_http",
            updated_by="admin",
        )

    await mcp_session.refresh(legacy_server)
    assert legacy_server.transport == "stdio"
    assert legacy_server.command == "python3"


async def test_get_enabled_mcp_tools_loads_latest_config_from_db(monkeypatch):
    captured: list[dict] = []

    async def fake_get_enabled_mcp_server_config(server_name: str, db=None):
        del db
        assert server_name == "demo"
        return {"transport": "stdio", "command": "demo", "disabled_tools": ["tool_b"]}

    async def fake_get_mcp_tools(server_name: str, additional_servers=None, disabled_tools=None, **kwargs):
        del kwargs
        captured.append(
            {
                "server_name": server_name,
                "additional_servers": additional_servers,
                "disabled_tools": list(disabled_tools or []),
            }
        )
        return ["tool-a"]

    monkeypatch.setattr(mcp_service, "get_enabled_mcp_server_config", fake_get_enabled_mcp_server_config)
    monkeypatch.setattr(mcp_service, "get_mcp_tools", fake_get_mcp_tools)

    tools = await mcp_service.get_enabled_mcp_tools("demo")

    assert tools == ["tool-a"]
    assert captured == [
        {
            "server_name": "demo",
            "additional_servers": {"demo": {"transport": "stdio", "command": "demo", "disabled_tools": ["tool_b"]}},
            "disabled_tools": ["tool_b"],
        }
    ]


async def test_get_mcp_tools_rebuilds_cache_when_config_hash_changes(monkeypatch):
    mcp_service.clear_mcp_cache()

    configs = [
        {"transport": "stdio", "command": "demo-v1", "disabled_tools": []},
        {"transport": "stdio", "command": "demo-v2", "disabled_tools": []},
    ]
    build_calls: list[str] = []

    async def fake_get_enabled_mcp_server_config(server_name: str, db=None):
        del db
        assert server_name == "demo"
        return configs[0]

    async def fake_get_mcp_client(server_configs):
        config = server_configs["demo"]
        build_calls.append(config["command"])
        tool = SimpleNamespace(name=f"tool_for_{config['command']}", metadata={})
        return _FakeClient([tool])

    monkeypatch.setattr(mcp_service, "get_enabled_mcp_server_config", fake_get_enabled_mcp_server_config)
    monkeypatch.setattr(mcp_service, "get_mcp_client", fake_get_mcp_client)

    tools_v1_first = await mcp_service.get_mcp_tools("demo")
    tools_v1_second = await mcp_service.get_mcp_tools("demo")

    configs[0] = configs[1]
    tools_v2 = await mcp_service.get_mcp_tools("demo")

    assert [tool.name for tool in tools_v1_first] == ["tool_for_demo-v1"]
    assert [tool.name for tool in tools_v1_second] == ["tool_for_demo-v1"]
    assert [tool.name for tool in tools_v2] == ["tool_for_demo-v2"]
    assert build_calls == ["demo-v1", "demo-v2"]

    mcp_service.clear_mcp_cache()


async def test_get_tools_from_all_servers_loads_names_from_db_once(monkeypatch):
    server_configs = {
        "alpha": {"transport": "stdio", "command": "cmd-a", "disabled_tools": []},
        "beta": {"transport": "stdio", "command": "cmd-b", "disabled_tools": []},
    }
    calls: list[tuple[str, dict[str, dict]]] = []

    async def fake_load_enabled_mcp_server_configs(*, names=None, db=None):
        del names, db
        return server_configs

    async def fake_get_mcp_tools(server_name: str, additional_servers=None, **kwargs):
        del kwargs
        calls.append((server_name, additional_servers or {}))
        return [server_name]

    monkeypatch.setattr(mcp_service, "_load_enabled_mcp_server_configs", fake_load_enabled_mcp_server_configs)
    monkeypatch.setattr(mcp_service, "get_mcp_tools", fake_get_mcp_tools)

    tools = await mcp_service.get_tools_from_all_servers()

    assert tools == ["alpha", "beta"]
    assert calls == [
        ("alpha", server_configs),
        ("beta", server_configs),
    ]


async def test_get_mcp_tools_sets_handle_tool_error(monkeypatch):
    mcp_service.clear_mcp_cache()

    config = {"transport": "stdio", "command": "demo-tool", "disabled_tools": []}

    async def fake_get_enabled_mcp_server_config(server_name: str, db=None):
        del db
        return config

    async def fake_get_mcp_client(server_configs):
        tool = SimpleNamespace(name="demo_tool", metadata={})
        return _FakeClient([tool])

    monkeypatch.setattr(mcp_service, "get_enabled_mcp_server_config", fake_get_enabled_mcp_server_config)
    monkeypatch.setattr(mcp_service, "get_mcp_client", fake_get_mcp_client)

    tools = await mcp_service.get_mcp_tools("demo")
    assert len(tools) == 1
    assert tools[0].handle_tool_error is True

    mcp_service.clear_mcp_cache()
