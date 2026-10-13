"""MCP 工具执行前的权限复核。"""

from __future__ import annotations

from inspect import signature
from typing import Annotated, Any

from langchain_core.tools import InjectedToolArg, StructuredTool

from yuxi.agents.mcp.service import get_mcp_server, is_builtin_mcp_server
from yuxi.permissions import ResourcePermission, ResourcePermissionDenied, resolve_mcp_permission
from yuxi.repositories.user_repository import UserRepository
from yuxi.storage.postgres.manager import pg_manager


async def require_mcp_tool_execution(uid: str, server_slug: str, tool_name: str) -> None:
    """每次调用都从 PostgreSQL 读取当前用户、服务和工具状态。"""

    if not uid:
        raise ResourcePermissionDenied("缺少 MCP 工具调用身份")

    async with pg_manager.get_async_session_context() as db:
        user = await UserRepository(db).get_by_uid_with_db(db, uid)
        server = await get_mcp_server(db, server_slug)

        allowed_transport = server is not None and (
            server.transport in {"sse", "streamable_http"}
            or (server.transport == "stdio" and is_builtin_mcp_server(server))
        )
        if (
            user is None
            or user.is_deleted != 0
            or server is None
            or server.enabled != 1
            or not allowed_transport
            or tool_name in (server.disabled_tools or [])
            or resolve_mcp_permission(user, server) not in {ResourcePermission.EDIT, ResourcePermission.MANAGE}
        ):
            raise ResourcePermissionDenied("MCP 服务或工具当前不可使用")


def guard_mcp_tool(tool: StructuredTool, *, uid: str, server_slug: str) -> StructuredTool:
    """包装当前 Run 的工具副本，不修改跨 Run 共享的 MCP 缓存对象。"""

    if not isinstance(tool, StructuredTool) or tool.coroutine is None:
        raise TypeError("MCP 工具必须是可异步执行的 StructuredTool")

    tool_name = tool.name
    original_call = tool.coroutine
    accepts_runtime = "runtime" in signature(original_call).parameters

    async def guarded_call(
        runtime: Annotated[object | None, InjectedToolArg()] = None,
        **arguments: Any,
    ) -> Any:
        """在发起 MCP 远端调用前复核授权。"""

        await require_mcp_tool_execution(uid, server_slug, tool_name)
        if accepts_runtime:
            return await original_call(runtime=runtime, **arguments)
        return await original_call(**arguments)

    return tool.model_copy(update={"coroutine": guarded_call, "func": None})
