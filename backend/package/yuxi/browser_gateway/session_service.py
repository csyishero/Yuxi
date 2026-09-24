"""Browser current-device assertion 的短期 Redis 状态。"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass

from yuxi.storage.redis import get_async_redis_client

from .config import load_browser_gateway_settings

ASSERTION_PREFIX = "browser:run-assertion"
BINDING_READY_PREFIX = "browser:run-binding-ready"
BINDING_READY_TTL_SECONDS = 1800


class BrowserRunAssertionConflict(RuntimeError):
    """表示该 Run 已有待消费断言或已经完成设备绑定。"""


@dataclass(frozen=True)
class BrowserRunScope:
    """标识 assertion 必须匹配的 Yuxi Run 作用域。"""

    uid: str
    run_id: str
    thread_id: str


def _assertion_key(scope: BrowserRunScope) -> str:
    return f"{ASSERTION_PREFIX}:{scope.uid}:{scope.run_id}"


def _ready_key(scope: BrowserRunScope) -> str:
    return f"{BINDING_READY_PREFIX}:{scope.uid}:{scope.run_id}"


async def save_current_device_assertion(scope: BrowserRunScope, assertion: str) -> int:
    """以 first-writer-wins 方式保存断言，禁止观察同一 Run 的其他页面覆盖。"""
    normalized = str(assertion or "").strip()
    if not 16 <= len(normalized) <= 4096:
        raise ValueError("current-device assertion 长度不合法")
    settings = load_browser_gateway_settings()
    redis = await get_async_redis_client()
    value = json.dumps(
        {"assertion": normalized, "uid": scope.uid, "run_id": scope.run_id, "thread_id": scope.thread_id},
        ensure_ascii=False,
    )
    accepted = await redis.eval(
        """
        if redis.call('EXISTS', KEYS[1]) == 1 or redis.call('EXISTS', KEYS[2]) == 1 then
            return 0
        end
        redis.call('SET', KEYS[1], ARGV[1], 'EX', ARGV[2])
        return 1
        """,
        2,
        _assertion_key(scope),
        _ready_key(scope),
        value,
        settings.assertion_ttl_seconds,
    )
    if int(accepted or 0) != 1:
        raise BrowserRunAssertionConflict("当前 Run 已提交浏览器设备断言")
    return settings.assertion_ttl_seconds


async def load_current_device_assertion(
    scope: BrowserRunScope,
    *,
    wait_seconds: float = 0,
    poll_interval: float = 0.2,
) -> str | None:
    """读取匹配 Run 的 assertion；必要时为前端提交留出有限等待窗口。"""
    redis = await get_async_redis_client()
    loop = asyncio.get_running_loop()
    deadline = loop.time() + max(0, wait_seconds)
    while True:
        raw = await redis.get(_assertion_key(scope))
        if raw:
            try:
                payload = json.loads(raw)
            except (TypeError, json.JSONDecodeError):
                await redis.delete(_assertion_key(scope))
                return None
            if (
                payload.get("uid") == scope.uid
                and payload.get("run_id") == scope.run_id
                and payload.get("thread_id") == scope.thread_id
                and isinstance(payload.get("assertion"), str)
            ):
                return payload["assertion"]
            await redis.delete(_assertion_key(scope))
            return None
        remaining = deadline - loop.time()
        if remaining <= 0:
            return None
        await asyncio.sleep(min(poll_interval, remaining))


async def delete_current_device_assertion(scope: BrowserRunScope) -> None:
    """删除已经交给 Gateway 消费的一次性 assertion。"""
    redis = await get_async_redis_client()
    await redis.delete(_assertion_key(scope))


async def mark_browser_binding_ready(scope: BrowserRunScope) -> None:
    """记录该 Run 已在 Gateway 建立绑定，避免后续调用等待 assertion。"""
    redis = await get_async_redis_client()
    await redis.set(_ready_key(scope), "1", ex=BINDING_READY_TTL_SECONDS)


async def is_browser_binding_ready(scope: BrowserRunScope) -> bool:
    """判断本地是否已观察到 Gateway Run 绑定成功。"""
    redis = await get_async_redis_client()
    return bool(await redis.get(_ready_key(scope)))
