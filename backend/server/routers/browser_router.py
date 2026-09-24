"""Yuxi Browser Extension 与 Browser MCP Gateway 接入路由。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from server.utils.auth_middleware import get_db, get_required_user
from yuxi.browser_gateway.client import BrowserGatewayError, BrowserGatewayUnavailable
from yuxi.browser_gateway.config import BrowserGatewayConfigurationError
from yuxi.browser_gateway.service import (
    bind_browser_assertion_to_run,
    create_browser_pairing,
    get_browser_gateway_public_config,
)
from yuxi.browser_gateway.session_service import BrowserRunAssertionConflict
from yuxi.storage.postgres.models_business import User

browser = APIRouter(prefix="/browser", tags=["browser"])


class CurrentDeviceAssertionInput(BaseModel):
    """网页从已信任 Extension 获取的一次性设备断言。"""

    assertion: str = Field(min_length=16, max_length=4096)


@browser.get("/config")
async def get_browser_config(_current_user: User = Depends(get_required_user)):
    """返回浏览器扩展接入的公开配置与启用状态。"""
    try:
        return get_browser_gateway_public_config()
    except BrowserGatewayConfigurationError as exc:
        raise HTTPException(status_code=503, detail={"code": "BROWSER_GATEWAY_CONFIG_INVALID"}) from exc


@browser.post("/pairings")
async def create_pairing(current_user: User = Depends(get_required_user)):
    """以当前登录用户身份创建一次性浏览器配对。"""
    try:
        return await create_browser_pairing(uid=str(current_user.uid))
    except BrowserGatewayConfigurationError as exc:
        raise HTTPException(status_code=503, detail={"code": "BROWSER_GATEWAY_NOT_CONFIGURED"}) from exc
    except BrowserGatewayError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"code": exc.code, "diagnostic_id": exc.diagnostic_id},
        ) from exc
    except BrowserGatewayUnavailable as exc:
        raise HTTPException(status_code=503, detail={"code": "BROWSER_GATEWAY_UNAVAILABLE"}) from exc


@browser.post("/runs/{run_id}/assertion")
async def bind_run_assertion(
    run_id: str,
    payload: CurrentDeviceAssertionInput,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    """校验 Run 归属并立即建立 Gateway current-device 绑定。"""
    try:
        result = await bind_browser_assertion_to_run(
            db=db,
            uid=str(current_user.uid),
            run_id=run_id,
            assertion=payload.assertion,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={"code": "INVALID_BROWSER_ASSERTION"}) from exc
    except BrowserRunAssertionConflict as exc:
        raise HTTPException(status_code=409, detail={"code": "BROWSER_RUN_ALREADY_BOUND"}) from exc
    except BrowserGatewayError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"code": exc.code, "diagnostic_id": exc.diagnostic_id},
        ) from exc
    except BrowserGatewayUnavailable as exc:
        raise HTTPException(status_code=503, detail={"code": "BROWSER_GATEWAY_UNAVAILABLE"}) from exc
    if result is None:
        raise HTTPException(status_code=404, detail="Agent Run 不存在")
    return result
