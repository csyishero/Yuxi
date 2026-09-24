"""通过 Browser MCP Gateway 访问当前 Run 绑定浏览器的只读工具。"""

from __future__ import annotations

import asyncio
import base64
from typing import Any, Literal

from langchain_core.tools import ToolException
from langchain_core.messages.content import create_image_block
from langgraph.prebuilt.tool_node import ToolRuntime
from pydantic import BaseModel, Field

from yuxi.agents.toolkits.registry import tool
from yuxi.browser_gateway.client import (
    BrowserGatewayClient,
    BrowserGatewayError,
    BrowserGatewayUnavailable,
    BrowserRuntimeIdentity,
)
from yuxi.browser_gateway.config import BrowserGatewayConfigurationError, load_browser_gateway_settings
from yuxi.browser_gateway.session_service import (
    BrowserRunScope,
    delete_current_device_assertion,
    is_browser_binding_ready,
    load_current_device_assertion,
    mark_browser_binding_ready,
)
from yuxi.utils import logger

_REGISTERED = False
_IDENTITY_ONLY_TOOL = "browser_whoami"
_CURRENT_DEVICE_REQUIRED = "CURRENT_DEVICE_REQUIRED"
# 空的 Pydantic 输入模型会丢弃注入的 ToolRuntime；自动推导模型又会把
# ToolRuntime 的 Callable 字段带入 JSON Schema。显式 JSON Schema 避开两者。
_EMPTY_BROWSER_ARGS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {},
    "additionalProperties": False,
}


class BrowserListTabsInput(BaseModel):
    """列出当前设备标签页的过滤参数。"""

    active_only: bool = Field(default=False, description="仅返回活动标签页")
    url_pattern: str | None = Field(default=None, max_length=512, description="可选 URL 子串过滤")


class BrowserSnapshotInput(BaseModel):
    """语义页面快照参数。"""

    tab_id: int = Field(ge=0, description="browser_list_tabs 返回的标签页 ID")
    mode: Literal["interactive", "visible", "document"] = Field(
        default="interactive", description="快照范围：交互元素、可见区域或整个文档"
    )
    max_nodes: int = Field(default=1200, ge=50, le=5000, description="最多返回的语义节点数")


class BrowserExtractInput(BaseModel):
    """从短期页面快照节点提取内容的参数。"""

    tab_id: int = Field(ge=0, description="生成该快照的标签页 ID")
    snapshot_id: str = Field(min_length=1, max_length=256, description="browser_snapshot 返回的快照 ID")
    ref: str = Field(min_length=1, max_length=256, description="快照元素的短期 ref")
    format: Literal["text", "table", "attributes"] = Field(default="text", description="提取格式")


class BrowserScreenshotInput(BaseModel):
    """受控截图参数。"""

    tab_id: int = Field(ge=0, description="需要截取的活动标签页 ID")


def _runtime_identity(runtime: ToolRuntime) -> tuple[BrowserRuntimeIdentity, BrowserRunScope]:
    """从不可由模型覆盖的 ToolRuntime 构建 Gateway 身份。"""
    context = getattr(runtime, "context", None)
    uid = str(getattr(context, "uid", "") or "").strip()
    run_id = str(getattr(context, "run_id", "") or "").strip()
    thread_id = str(getattr(context, "thread_id", "") or "").strip()
    request_id = str(getattr(context, "request_id", "") or "").strip()
    if not all((uid, run_id, thread_id, request_id)):
        raise ToolException("当前 Agent Run 缺少浏览器身份上下文")
    identity = BrowserRuntimeIdentity(uid=uid, run_id=run_id, thread_id=thread_id, request_id=request_id)
    return identity, BrowserRunScope(uid=uid, run_id=run_id, thread_id=thread_id)


async def _wait_for_ready(scope: BrowserRunScope, seconds: float = 1.5) -> bool:
    """等待并发首个工具完成一次性 assertion 消费。"""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + seconds
    while loop.time() < deadline:
        if await is_browser_binding_ready(scope):
            return True
        await asyncio.sleep(0.1)
    return await is_browser_binding_ready(scope)


def _public_tool_error(error: Exception) -> ToolException:
    """把 Gateway 细节收敛为 Agent 可操作且不含敏感内容的提示。"""
    if isinstance(error, BrowserGatewayUnavailable):
        return ToolException("Browser Gateway 暂时不可用，请稍后重试")
    if not isinstance(error, BrowserGatewayError):
        return ToolException("浏览器工具调用失败")
    messages = {
        "BROWSER_OFFLINE": "当前浏览器离线，请打开 Yuxi Browser Assistant 扩展并等待连接恢复",
        "CURRENT_DEVICE_REQUIRED": "当前 Run 尚未绑定浏览器，请在 Yuxi 的浏览器扩展页面完成配对后重试",
        "RUN_DEVICE_CONFLICT": "当前 Run 已绑定到其他浏览器设备，不能自动切换设备",
        "DOMAIN_NOT_ALLOWED": "目标网站尚未授权，请在目标标签页点击扩展并授权当前网站",
        "COMMAND_TIMEOUT": "浏览器命令执行超时，请保持目标标签页活动后重试",
        "INVALID_ARGUMENT": "浏览器工具参数无效，可能是标签页或页面快照已过期",
        "STALE_REF": "页面快照引用已过期，请重新调用 browser_snapshot",
        "SCREENSHOT_EXPIRED": "浏览器截图已过期，请重新调用 browser_screenshot",
    }
    return ToolException(messages.get(error.code, f"浏览器工具调用失败（{error.code}）"))


async def _invoke_browser_tool(name: str, arguments: dict[str, Any], runtime: ToolRuntime) -> Any:
    """完成 Run 绑定竞态处理后调用只读 Gateway 工具。"""
    identity, scope = _runtime_identity(runtime)
    client = BrowserGatewayClient()
    if name == _IDENTITY_ONLY_TOOL:
        try:
            return await client.call_tool(identity=identity, name=name, arguments=arguments)
        except (BrowserGatewayError, BrowserGatewayUnavailable) as exc:
            raise _public_tool_error(exc) from exc

    assertion = None
    assertion_reached_gateway = False
    try:
        if not await is_browser_binding_ready(scope):
            assertion = await load_current_device_assertion(scope)
        if assertion is None:
            try:
                result = await client.call_tool(identity=identity, name=name, arguments=arguments)
            except BrowserGatewayError as exc:
                if exc.code != _CURRENT_DEVICE_REQUIRED:
                    raise
                assertion = await load_current_device_assertion(scope, wait_seconds=8)
                if assertion is None:
                    raise exc
            else:
                await mark_browser_binding_ready(scope)
                return result

        try:
            result = await client.call_tool(
                identity=identity,
                name=name,
                arguments=arguments,
                assertion=assertion,
            )
        except BrowserGatewayError as exc:
            assertion_reached_gateway = True
            if exc.code == _CURRENT_DEVICE_REQUIRED and await _wait_for_ready(scope):
                result = await client.call_tool(identity=identity, name=name, arguments=arguments)
            else:
                raise
        else:
            assertion_reached_gateway = True
        await mark_browser_binding_ready(scope)
        return result
    except (BrowserGatewayError, BrowserGatewayUnavailable) as exc:
        raise _public_tool_error(exc) from exc
    finally:
        if assertion is not None and assertion_reached_gateway:
            await delete_current_device_assertion(scope)


async def _browser_whoami(runtime: ToolRuntime) -> Any:
    """返回 Gateway 从 JWT 验证得到的当前 Agent Run 身份。"""
    return await _invoke_browser_tool("browser_whoami", {}, runtime)


async def _browser_current_device(runtime: ToolRuntime) -> Any:
    """返回当前 Run 明确绑定的浏览器设备。"""
    return await _invoke_browser_tool("browser_current_device", {}, runtime)


async def _browser_list_tabs(
    active_only: bool = False,
    url_pattern: str | None = None,
    runtime: ToolRuntime = None,
) -> Any:
    """列出当前 Run 绑定浏览器中的标签页。"""
    arguments: dict[str, Any] = {"active_only": active_only}
    if url_pattern:
        arguments["url_pattern"] = url_pattern
    return await _invoke_browser_tool("browser_list_tabs", arguments, runtime)


async def _browser_snapshot(
    tab_id: int,
    mode: Literal["interactive", "visible", "document"] = "interactive",
    max_nodes: int = 1200,
    runtime: ToolRuntime = None,
) -> Any:
    """获取目标标签页的短期语义快照和元素 ref。"""
    return await _invoke_browser_tool(
        "browser_snapshot",
        {"tab_id": tab_id, "mode": mode, "max_nodes": max_nodes},
        runtime,
    )


async def _browser_extract(
    tab_id: int,
    snapshot_id: str,
    ref: str,
    format: Literal["text", "table", "attributes"] = "text",
    runtime: ToolRuntime = None,
) -> Any:
    """从语义快照的指定 ref 提取脱敏文本、表格或属性。"""
    return await _invoke_browser_tool(
        "browser_extract",
        {"tab_id": tab_id, "snapshot_id": snapshot_id, "ref": ref, "format": format},
        runtime,
    )


async def _browser_screenshot(tab_id: int, runtime: ToolRuntime = None) -> Any:
    """截取目标活动标签页，下载短期 artifact 并作为模型可消费的图片返回。"""
    result = await _invoke_browser_tool("browser_screenshot", {"tab_id": tab_id}, runtime)
    artifact = result.get("artifact") if isinstance(result, dict) else None
    if not isinstance(artifact, dict):
        raise ToolException("Browser Gateway 返回了无效截图描述")
    identity, _scope = _runtime_identity(runtime)
    try:
        image_bytes = await BrowserGatewayClient().download_artifact(identity=identity, artifact=artifact)
    except (BrowserGatewayError, BrowserGatewayUnavailable) as exc:
        raise _public_tool_error(exc) from exc
    return [
        {"type": "text", "text": "已获取当前浏览器标签页截图。"},
        create_image_block(base64=base64.b64encode(image_bytes).decode("ascii"), mime_type="image/png"),
    ]


def register_browser_gateway_tools() -> None:
    """配置完整时注册只读浏览器工具；重复调用保持幂等。"""
    global _REGISTERED
    if _REGISTERED:
        return
    try:
        settings = load_browser_gateway_settings()
        if not settings.enabled:
            return
        settings.validate()
    except BrowserGatewayConfigurationError as exc:
        logger.warning(f"Browser Gateway tools disabled: {exc}")
        return

    definitions = (
        (
            _browser_whoami,
            "browser_whoami",
            "验证并返回当前 Agent Run 的浏览器身份。身份完全来自运行上下文，不接受模型传入。",
            _EMPTY_BROWSER_ARGS_SCHEMA,
            "浏览器身份",
        ),
        (
            _browser_current_device,
            "browser_current_device",
            "返回当前 Agent Run 已明确绑定的浏览器设备，不会自动选择同账号的其他设备。",
            _EMPTY_BROWSER_ARGS_SCHEMA,
            "当前浏览器",
        ),
        (
            _browser_list_tabs,
            "browser_list_tabs",
            "列出当前 Run 绑定浏览器的标签页。后续快照、提取和截图必须使用返回的 tab_id。",
            BrowserListTabsInput,
            "浏览器标签页",
        ),
        (
            _browser_snapshot,
            "browser_snapshot",
            "获取标签页的脱敏语义快照和短期元素 ref，支持 SPA、可访问 iframe 与 open shadow root。",
            BrowserSnapshotInput,
            "浏览器页面快照",
        ),
        (
            _browser_extract,
            "browser_extract",
            "从最新页面快照的 ref 提取脱敏文本、表格或属性；页面变化后应重新获取快照。",
            BrowserExtractInput,
            "浏览器内容提取",
        ),
        (
            _browser_screenshot,
            "browser_screenshot",
            "截取当前活动标签页并返回短期 artifact 元数据。目标标签页必须保持活动且已授权。",
            BrowserScreenshotInput,
            "浏览器截图",
        ),
    )
    for func, name, description, args_schema, display_name in definitions:
        tool(
            category="buildin",
            tags=["浏览器", "只读"],
            display_name=display_name,
            icon="browser",
            config_guide="请先在“智能体扩展 > 浏览器”中检测并配对 Yuxi Browser Assistant。",
            name_or_callable=name,
            description=description,
            args_schema=args_schema,
        )(func)
    _REGISTERED = True
