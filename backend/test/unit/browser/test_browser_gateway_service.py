from __future__ import annotations

import base64
from types import SimpleNamespace

import pytest
from langchain.tools import tool as langchain_tool
from langchain_core.tools import ToolException
from langgraph.prebuilt.tool_node import ToolRuntime

from yuxi.agents.toolkits.browser import tools as browser_tools
from yuxi.agents.toolkits.browser.tools import (
    BrowserExtractInput,
    BrowserListTabsInput,
    BrowserScreenshotInput,
    BrowserSnapshotInput,
    _runtime_identity,
)
from yuxi.browser_gateway import service
from yuxi.browser_gateway.client import BrowserGatewayUnavailable
from yuxi.browser_gateway import session_service


def test_browser_tool_schemas_never_expose_identity_fields():
    """模型可见参数中不得出现用户、Run、设备或 JWT 身份字段。"""
    forbidden = {"uid", "run_id", "thread_id", "request_id", "device_id", "jwt", "token"}
    for schema in (
        BrowserListTabsInput,
        BrowserSnapshotInput,
        BrowserExtractInput,
        BrowserScreenshotInput,
    ):
        properties = set(schema.model_json_schema().get("properties", {}))
        assert properties.isdisjoint(forbidden)


@pytest.mark.asyncio
async def test_parameterless_browser_tools_keep_injected_runtime(monkeypatch):
    """空模型会丢弃 ToolRuntime；注册后的无参工具必须保留注入参数。"""
    registered = {}

    def capture_tool(**kwargs):
        def decorate(func):
            instance = langchain_tool(
                name_or_callable=kwargs["name_or_callable"],
                description=kwargs["description"],
                args_schema=kwargs["args_schema"],
            )(func)
            registered[instance.name] = instance
            return instance

        return decorate

    async def fake_invoke(name, arguments, runtime):
        assert arguments == {}
        assert runtime is tool_runtime
        return name

    tool_runtime = ToolRuntime(
        state={},
        context=None,
        config={},
        stream_writer=lambda _value: None,
        tool_call_id="call-1",
        store=None,
    )
    monkeypatch.setattr(browser_tools, "_REGISTERED", False)
    monkeypatch.setattr(
        browser_tools,
        "load_browser_gateway_settings",
        lambda: SimpleNamespace(enabled=True, validate=lambda: None),
    )
    monkeypatch.setattr(browser_tools, "tool", capture_tool)
    monkeypatch.setattr(browser_tools, "_invoke_browser_tool", fake_invoke)

    browser_tools.register_browser_gateway_tools()

    for name in ("browser_whoami", "browser_current_device"):
        instance = registered[name]
        assert instance.tool_call_schema.model_json_schema().get("properties") == {}
        assert instance._injected_args_keys == frozenset({"runtime"})
        assert await instance.ainvoke({"runtime": tool_runtime}) == name


def test_runtime_identity_is_injected_from_tool_context():
    """Gateway 身份只能从 ToolRuntime context 构造。"""
    runtime = SimpleNamespace(
        context=SimpleNamespace(
            uid="user-1",
            run_id="run-1",
            thread_id="thread-1",
            request_id="request-1",
        )
    )

    identity, scope = _runtime_identity(runtime)

    assert identity.uid == scope.uid == "user-1"
    assert identity.run_id == scope.run_id == "run-1"
    assert identity.thread_id == scope.thread_id == "thread-1"
    assert identity.request_id == "request-1"


@pytest.mark.asyncio
async def test_bind_assertion_checks_run_ownership_before_redis(monkeypatch):
    """非本人 Run 不写 Redis；本人 Run 立即消费 assertion 建立 Gateway 绑定。"""
    saved = []
    invoked = []
    deleted = []
    ready = []

    class FakeRepository:
        def __init__(self, _db):
            pass

        async def get_run_for_user(self, run_id, uid):
            if uid != "owner" or run_id != "run-1":
                return None
            return SimpleNamespace(id="run-1", conversation_thread_id="thread-from-db")

    async def fake_save(scope, assertion):
        saved.append((scope, assertion))
        return 90

    class FakeClient:
        async def call_tool(self, **kwargs):
            invoked.append(kwargs)
            return {"device_id": "device-1"}

    async def fake_delete(scope):
        deleted.append(scope)

    async def fake_ready(scope):
        ready.append(scope)

    monkeypatch.setattr(service, "AgentRunRepository", FakeRepository)
    monkeypatch.setattr(service, "save_current_device_assertion", fake_save)
    monkeypatch.setattr(service, "BrowserGatewayClient", FakeClient)
    monkeypatch.setattr(service, "delete_current_device_assertion", fake_delete)
    monkeypatch.setattr(service, "mark_browser_binding_ready", fake_ready)

    denied = await service.bind_browser_assertion_to_run(
        db=object(), uid="other", run_id="run-1", assertion="assertion-value-long"
    )
    accepted = await service.bind_browser_assertion_to_run(
        db=object(), uid="owner", run_id="run-1", assertion="assertion-value-long"
    )

    assert denied is None
    assert saved[0][0].thread_id == "thread-from-db"
    assert saved[0][1] == "assertion-value-long"
    assert invoked[0]["name"] == "browser_current_device"
    assert invoked[0]["assertion"] == "assertion-value-long"
    assert invoked[0]["identity"].run_id == "run-1"
    assert deleted == ready == [saved[0][0]]
    assert accepted == {
        "run_id": "run-1",
        "thread_id": "thread-from-db",
        "binding_status": "ready",
        "expires_in": session_service.BINDING_READY_TTL_SECONDS,
    }


@pytest.mark.asyncio
async def test_immediate_binding_failure_keeps_assertion_for_tool_fallback(monkeypatch):
    """Gateway 网络失败时保留短期 assertion，随后 Tool 仍可完成同一 Run 的绑定。"""
    scope = SimpleNamespace(id="run-1", conversation_thread_id="thread-1")
    deleted = []
    ready = []

    class FakeRepository:
        def __init__(self, _db):
            pass

        async def get_run_for_user(self, _run_id, _uid):
            return scope

    class UnavailableClient:
        async def call_tool(self, **_kwargs):
            raise BrowserGatewayUnavailable("unavailable")

    async def fake_save(_scope, _assertion):
        return 50

    async def record_delete(value):
        deleted.append(value)

    async def record_ready(value):
        ready.append(value)

    monkeypatch.setattr(service, "AgentRunRepository", FakeRepository)
    monkeypatch.setattr(service, "BrowserGatewayClient", UnavailableClient)
    monkeypatch.setattr(service, "save_current_device_assertion", fake_save)
    monkeypatch.setattr(service, "delete_current_device_assertion", record_delete)
    monkeypatch.setattr(service, "mark_browser_binding_ready", record_ready)

    with pytest.raises(BrowserGatewayUnavailable):
        await service.bind_browser_assertion_to_run(
            db=object(), uid="user-1", run_id="run-1", assertion="assertion-value-long"
        )

    assert deleted == []
    assert ready == []


@pytest.mark.asyncio
async def test_gateway_network_failure_keeps_unconsumed_assertion(monkeypatch):
    """Assertion 尚未到达 Gateway 时保留到 TTL，允许同一 Run 安全重试。"""
    deleted = []
    runtime = SimpleNamespace(
        context=SimpleNamespace(uid="user-1", run_id="run-1", thread_id="thread-1", request_id="request-1")
    )

    class UnavailableClient:
        async def call_tool(self, **_kwargs):
            raise BrowserGatewayUnavailable("unavailable")

    async def not_ready(_scope):
        return False

    async def load_assertion(_scope, **_kwargs):
        return "assertion-value-long"

    async def record_delete(scope):
        deleted.append(scope)

    monkeypatch.setattr(browser_tools, "BrowserGatewayClient", UnavailableClient)
    monkeypatch.setattr(browser_tools, "is_browser_binding_ready", not_ready)
    monkeypatch.setattr(browser_tools, "load_current_device_assertion", load_assertion)
    monkeypatch.setattr(browser_tools, "delete_current_device_assertion", record_delete)

    with pytest.raises(ToolException, match="Browser Gateway 暂时不可用"):
        await browser_tools._invoke_browser_tool("browser_list_tabs", {}, runtime)

    assert deleted == []


@pytest.mark.asyncio
async def test_run_assertion_is_first_writer_wins(monkeypatch):
    """同一 Run 的待消费断言或 ready 标记存在时，后来的浏览器不能覆盖。"""
    calls = []

    class FakeRedis:
        accepted = 1

        async def eval(self, *args):
            calls.append(args)
            return self.accepted

    redis = FakeRedis()

    async def fake_redis():
        return redis

    monkeypatch.setattr(session_service, "get_async_redis_client", fake_redis)
    monkeypatch.setattr(
        session_service,
        "load_browser_gateway_settings",
        lambda: SimpleNamespace(assertion_ttl_seconds=90),
    )
    scope = session_service.BrowserRunScope(uid="user-1", run_id="run-1", thread_id="thread-1")

    assert await session_service.save_current_device_assertion(scope, "assertion-value-long") == 90
    assert calls[0][2].endswith(":user-1:run-1")
    assert calls[0][3].endswith(":user-1:run-1")

    redis.accepted = 0
    with pytest.raises(session_service.BrowserRunAssertionConflict):
        await session_service.save_current_device_assertion(scope, "other-assertion-value")


@pytest.mark.asyncio
async def test_browser_screenshot_returns_model_image_block(monkeypatch):
    """截图工具下载 Gateway artifact，而不是把不可访问的相对路径交给模型。"""
    png = b"\x89PNG\r\n\x1a\nimage"
    runtime = SimpleNamespace(
        context=SimpleNamespace(uid="user-1", run_id="run-1", thread_id="thread-1", request_id="request-1")
    )

    async def fake_invoke(_name, _arguments, _runtime):
        return {
            "artifact": {
                "contentType": "image/png",
                "size": len(png),
                "downloadPath": "/api/v1/artifacts/artifact-1",
            }
        }

    class FakeClient:
        async def download_artifact(self, **_kwargs):
            return png

    monkeypatch.setattr(browser_tools, "_invoke_browser_tool", fake_invoke)
    monkeypatch.setattr(browser_tools, "BrowserGatewayClient", FakeClient)

    result = await browser_tools._browser_screenshot(1, runtime)

    assert result[0]["type"] == "text"
    assert result[1]["type"] == "image"
    assert base64.b64decode(result[1]["base64"]) == png
