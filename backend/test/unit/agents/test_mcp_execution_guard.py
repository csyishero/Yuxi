"""MCP 工具装配后的实时授权与启停复核。"""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Annotated

import pytest
import pytest_asyncio
from langchain_core.messages import ToolMessage
from langchain_core.tools import InjectedToolArg, StructuredTool
from langgraph.prebuilt.tool_node import _get_all_injected_args
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import yuxi.agents.middlewares.skills as skills_middleware
import yuxi.agents.mcp.service as mcp_service
import yuxi.agents.toolkits.service as tool_service
from yuxi.agents.middlewares.skills import SkillsMiddleware
from yuxi.agents.toolkits.service import resolve_configured_runtime_tools
from yuxi.permissions import ResourcePermissionDenied
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import MCPServer, User


@pytest_asyncio.fixture
async def mcp_execution_db(monkeypatch):
    """为每次工具调用提供新的数据库会话，以观察已提交的权限变化。"""

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(User.__table__.create)
        await connection.run_sync(MCPServer.__table__.create)

    factory = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def session_context():
        async with factory() as session:
            yield session

    monkeypatch.setattr(pg_manager, "get_async_session_context", session_context)
    async with factory() as session:
        user = User(username="Alice", uid="alice", password_hash="hash", role="user", department_id=None)
        server = MCPServer(
            slug="charts",
            name="Charts",
            transport="streamable_http",
            url="https://example.com/mcp",
            enabled=1,
            created_by="admin",
            updated_by="admin",
            share_config=None,
        )
        session.add_all([user, server])
        await session.commit()
        yield session, server

    await engine.dispose()


def _mcp_tool(calls: list[tuple[str, object | None]]) -> StructuredTool:
    """模拟 adapter 返回的、带 runtime 注入和 artifact 的 MCP 工具。"""

    async def remote_call(
        runtime: Annotated[object | None, InjectedToolArg()] = None,
        **arguments,
    ):
        calls.append((arguments["query"], runtime))
        return f"result:{arguments['query']}", {"source": "mcp"}

    return StructuredTool(
        name="chart_tool",
        description="Generate chart",
        args_schema={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
        coroutine=remote_call,
        response_format="content_and_artifact",
        metadata={"id": "mcp__charts__chartTool"},
    )


@pytest.mark.asyncio
async def test_configured_mcp_tool_rechecks_permission_after_assembly(monkeypatch, mcp_execution_db):
    session, server = mcp_execution_db
    calls = []
    cached_tool = _mcp_tool(calls)

    async def load_tools(slug):
        assert slug == "charts"
        return [cached_tool]

    monkeypatch.setattr(mcp_service, "get_enabled_mcp_tools", load_tools)
    monkeypatch.setattr(tool_service, "get_tool_instances_by_category", lambda _category: [])
    context = SimpleNamespace(uid="alice", tools=[], mcps=["charts"], _skill_runtime_snapshot={})
    (guarded_tool,) = await resolve_configured_runtime_tools(context)

    assert guarded_tool is not cached_tool
    assert guarded_tool.name == cached_tool.name
    assert guarded_tool.args_schema == cached_tool.args_schema
    assert guarded_tool.metadata == cached_tool.metadata
    assert guarded_tool.response_format == "content_and_artifact"
    assert _get_all_injected_args(guarded_tool).runtime == "runtime"

    runtime = object()
    allowed = await guarded_tool.ainvoke({"query": "first", "runtime": runtime})
    assert allowed == "result:first"
    assert calls == [("first", runtime)]

    message = await guarded_tool.ainvoke(
        {"type": "tool_call", "name": "chart_tool", "args": {"query": "artifact"}, "id": "call-1"}
    )
    assert isinstance(message, ToolMessage)
    assert message.content == "result:artifact"
    assert message.artifact == {"source": "mcp"}

    server.share_config = {
        "version": 2,
        "read_scope": {"access_level": "global"},
        "edit_scope": None,
        "manage_scope": None,
    }
    await session.commit()
    with pytest.raises(ResourcePermissionDenied):
        await guarded_tool.ainvoke({"query": "revoked"})
    assert calls == [("first", runtime), ("artifact", None)]
    assert cached_tool.coroutine is not guarded_tool.coroutine


@pytest.mark.asyncio
async def test_configured_mcp_tool_rechecks_server_and_tool_switches(monkeypatch, mcp_execution_db):
    session, server = mcp_execution_db
    calls = []

    async def load_tools(_slug):
        return [_mcp_tool(calls)]

    monkeypatch.setattr(mcp_service, "get_enabled_mcp_tools", load_tools)
    monkeypatch.setattr(tool_service, "get_tool_instances_by_category", lambda _category: [])
    (guarded_tool,) = await resolve_configured_runtime_tools(
        SimpleNamespace(uid="alice", tools=[], mcps=["charts"], _skill_runtime_snapshot={})
    )

    server.enabled = 0
    await session.commit()
    with pytest.raises(ResourcePermissionDenied):
        await guarded_tool.ainvoke({"query": "disabled-server"})

    server.enabled = 1
    server.disabled_tools = ["chart_tool"]
    await session.commit()
    with pytest.raises(ResourcePermissionDenied):
        await guarded_tool.ainvoke({"query": "disabled-tool"})

    server.disabled_tools = []
    server.transport = "stdio"
    await session.commit()
    with pytest.raises(ResourcePermissionDenied):
        await guarded_tool.ainvoke({"query": "unsafe-transport"})
    assert calls == []


@pytest.mark.asyncio
async def test_skill_mcp_dependency_rechecks_permission_after_activation(monkeypatch, mcp_execution_db):
    session, server = mcp_execution_db
    calls = []
    cached_tool = _mcp_tool(calls)

    async def load_tools(_slug):
        return [cached_tool]

    monkeypatch.setattr(skills_middleware, "get_enabled_mcp_tools", load_tools)
    context = SimpleNamespace(
        uid="alice",
        tools=[],
        mcps=[],
        _skill_runtime_snapshot={
            "effective_skills": ["report"],
            "preloaded_skills": ["report"],
            "runtime_skills": {
                "report": {
                    "name": "report",
                    "description": "",
                    "path": "/home/gem/skills/report/SKILL.md",
                    "tools": [],
                    "mcps": ["charts"],
                    "skills": [],
                }
            },
        },
    )

    class Request:
        def __init__(self, tools):
            self.runtime = SimpleNamespace(context=context)
            self.state = {}
            self.tools = tools

        def override(self, *, tools):
            return Request(tools)

    captured = []

    async def handler(request):
        captured.extend(request.tools)
        return "ok"

    middleware = SkillsMiddleware(enable_skills_prompt=False)
    assert await middleware.awrap_model_call(Request([]), handler) == "ok"
    (guarded_tool,) = captured
    assert guarded_tool is context._active_skill_mcp_tools["chart_tool"]
    assert await guarded_tool.ainvoke({"query": "before"}) == "result:before"

    server.enabled = 0
    await session.commit()
    with pytest.raises(ResourcePermissionDenied):
        await guarded_tool.ainvoke({"query": "after"})
    assert calls == [("before", None)]
