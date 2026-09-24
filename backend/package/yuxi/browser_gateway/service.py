"""Browser Gateway 配对与 Run 设备断言应用服务。"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.repositories.agent_run_repository import AgentRunRepository

from .client import BrowserGatewayClient, BrowserRuntimeIdentity
from .config import load_browser_gateway_settings
from .session_service import (
    BINDING_READY_TTL_SECONDS,
    BrowserRunScope,
    delete_current_device_assertion,
    mark_browser_binding_ready,
    save_current_device_assertion,
)


def get_browser_gateway_public_config() -> dict[str, Any]:
    """返回前端建立扩展桥接所需的非敏感配置。"""
    settings = load_browser_gateway_settings()
    if settings.enabled:
        settings.validate()
    return {
        "enabled": settings.enabled,
        "gateway_base_url": settings.public_url or None,
        "yuxi_origin": settings.yuxi_origin or None,
        "extension_id": settings.extension_id or None,
        "missing_configuration": list(settings.missing_fields),
        "protocol": 1,
    }


async def create_browser_pairing(*, uid: str) -> dict[str, Any]:
    """代理创建一次性配对令牌，避免把 Gateway JWT 暴露给网页。"""
    data = await BrowserGatewayClient().create_pairing(uid=uid)
    return {
        "pairing_token": data["pairingToken"],
        "expires_at": data.get("expiresAt"),
        "gateway_base_url": load_browser_gateway_settings().public_url,
    }


async def bind_browser_assertion_to_run(
    *,
    db: AsyncSession,
    uid: str,
    run_id: str,
    assertion: str,
) -> dict[str, Any] | None:
    """验证 Run 所有权并立即在 Gateway 建立 current-device 绑定。"""
    run = await AgentRunRepository(db).get_run_for_user(run_id, uid)
    if run is None:
        return None
    scope = BrowserRunScope(
        uid=str(uid),
        run_id=str(run.id),
        thread_id=str(run.conversation_thread_id),
    )
    await save_current_device_assertion(scope, assertion)
    identity = BrowserRuntimeIdentity(
        uid=scope.uid,
        run_id=scope.run_id,
        thread_id=scope.thread_id,
        request_id=str(uuid.uuid4()),
    )
    # 不等待模型首次选择 Browser Tool：assertion 只有 60 秒，Run 创建后应立即消费并固化绑定。
    await BrowserGatewayClient().call_tool(
        identity=identity,
        name="browser_current_device",
        arguments={},
        assertion=assertion,
    )
    await delete_current_device_assertion(scope)
    await mark_browser_binding_ready(scope)
    return {
        "run_id": scope.run_id,
        "thread_id": scope.thread_id,
        "binding_status": "ready",
        "expires_in": BINDING_READY_TTL_SECONDS,
    }
