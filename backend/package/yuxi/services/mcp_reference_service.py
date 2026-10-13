"""面向用户的 MCP 引用可见性投影。"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.agents.mcp.service import get_all_mcp_servers
from yuxi.permissions import ResourcePermission, resolve_mcp_permission
from yuxi.storage.postgres.models_business import User


async def get_visible_mcp_reference_slugs(db: AsyncSession, user: User) -> set[str] | None:
    """返回当前用户可查看的 MCP 标识；管理员无需过滤。"""

    if user.role in {"admin", "superadmin"}:
        return None
    return {
        server.slug
        for server in await get_all_mcp_servers(db)
        if resolve_mcp_permission(user, server) != ResourcePermission.NONE
    }


def project_mcp_references(references: list[str], visible_slugs: set[str] | None) -> list[str]:
    """仅在响应副本中隐去没有查看权限的 MCP 引用。"""

    if visible_slugs is None:
        return references
    return [slug for slug in references if slug in visible_slugs]
