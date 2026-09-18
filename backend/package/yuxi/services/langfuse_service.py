from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any
from urllib.parse import urlparse

from yuxi.utils.logging_config import logger

try:
    from langfuse import Langfuse
    from langfuse.langchain import CallbackHandler
except Exception:  # pragma: no cover - optional dependency during local test collection
    Langfuse = None  # type: ignore[assignment]
    CallbackHandler = None  # type: ignore[assignment]


_FALSE_VALUES = {"0", "false", "no", "off"}
_DEFAULT_LANGFUSE_BASE_URL = "https://cloud.langfuse.com"
_TRACE_NAMES = {
    "agent_chat_stream": "execute-agent-run",
    "agent_chat_resume": "resume-agent-run",
}
_REDACTED_VALUE = "[REDACTED]"


@dataclass(slots=True)
class LangfuseRunContext:
    callbacks: list[Any] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)
    trace_id: str | None = None


def is_langfuse_enabled() -> bool:
    enabled_raw = (os.getenv("LANGFUSE_ENABLED") or "true").strip().lower()
    if enabled_raw in _FALSE_VALUES:
        return False

    if Langfuse is None or CallbackHandler is None:
        return False

    return bool(os.getenv("LANGFUSE_PUBLIC_KEY") and os.getenv("LANGFUSE_SECRET_KEY"))


def _is_sensitive_key(key: object) -> bool:
    normalized = str(key).strip().lower().replace("-", "_")
    if normalized in {
        "accepted_prediction_tokens",
        "audio_input_tokens",
        "audio_output_tokens",
        "cache_creation_input_tokens",
        "cache_read_input_tokens",
        "cached_input_tokens",
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "prompt_tokens",
        "completion_tokens",
        "cached_tokens",
        "reasoning_tokens",
        "rejected_prediction_tokens",
    }:
        return False

    sensitive_markers = (
        "api_key",
        "apikey",
        "authorization",
        "cookie",
        "credential",
        "password",
        "passwd",
        "private_key",
        "secret",
    )
    return (
        any(marker in normalized for marker in sensitive_markers)
        or normalized == "token"
        or normalized.endswith(("_token", "_tokens"))
    )


def _mask_langfuse_data(data: Any) -> Any:
    """递归遮蔽发送到 Langfuse 的凭证字段，同时保留可诊断结构。"""
    if isinstance(data, dict):
        return {
            key: _REDACTED_VALUE if _is_sensitive_key(key) else _mask_langfuse_data(item) for key, item in data.items()
        }
    if isinstance(data, list):
        return [_mask_langfuse_data(item) for item in data]
    if isinstance(data, tuple):
        return tuple(_mask_langfuse_data(item) for item in data)
    if isinstance(data, str):
        stripped = data.strip()
        if stripped.lower().startswith("bearer "):
            return _REDACTED_VALUE
        if stripped.startswith(("{", "[")):
            try:
                parsed = json.loads(data)
            except (TypeError, ValueError):
                pass
            else:
                return json.dumps(_mask_langfuse_data(parsed), ensure_ascii=False)
    return data


@lru_cache(maxsize=1)
def get_langfuse_client() -> Langfuse | None:
    if not is_langfuse_enabled():
        return None

    kwargs: dict[str, Any] = {
        "public_key": os.getenv("LANGFUSE_PUBLIC_KEY"),
        "secret_key": os.getenv("LANGFUSE_SECRET_KEY"),
        "mask": _mask_langfuse_data,
    }
    host = os.getenv("LANGFUSE_BASE_URL")
    if host:
        kwargs["host"] = host
    environment = os.getenv("LANGFUSE_TRACING_ENVIRONMENT")
    if environment:
        kwargs["environment"] = environment
    release = os.getenv("LANGFUSE_RELEASE")
    if release:
        kwargs["release"] = release

    try:
        return Langfuse(**kwargs)
    except Exception as exc:
        logger.warning(f"初始化 Langfuse 客户端失败，将跳过 tracing: {exc}")
        return None


def build_trace_metadata(
    *,
    user_id: str,
    thread_id: str,
    agent_id: str,
    request_id: str,
    operation: str,
    backend_id: str | None = None,
    message_type: str | None = None,
    extra_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "langfuse_user_id": user_id,
        "langfuse_session_id": thread_id,
        "request_id": request_id,
        "thread_id": thread_id,
        "agent_id": agent_id,
        "operation": operation,
        "source": "yuxi",
        "feature": "chat",
    }

    if backend_id:
        metadata["backend_id"] = backend_id
    if message_type:
        metadata["message_type"] = message_type
    if extra_metadata:
        metadata.update(extra_metadata)

    return metadata


def build_trace_tags(
    *,
    agent_id: str,
    operation: str,
    message_type: str | None = None,
    extra_tags: list[str] | None = None,
) -> list[str]:
    tags = ["yuxi", "chat", operation, f"agent:{agent_id}"]
    if message_type:
        tags.append(f"message_type:{message_type}")
    for tag in extra_tags or []:
        if tag and tag not in tags:
            tags.append(tag)
    return tags


def build_run_context(
    *,
    user_id: str,
    thread_id: str,
    agent_id: str,
    request_id: str,
    operation: str,
    backend_id: str | None = None,
    message_type: str | None = None,
    extra_metadata: dict[str, Any] | None = None,
    extra_tags: list[str] | None = None,
) -> LangfuseRunContext:
    metadata = build_trace_metadata(
        user_id=user_id,
        thread_id=thread_id,
        agent_id=agent_id,
        request_id=request_id,
        operation=operation,
        backend_id=backend_id,
        message_type=message_type,
        extra_metadata=extra_metadata,
    )
    tags = build_trace_tags(
        agent_id=agent_id,
        operation=operation,
        message_type=message_type,
        extra_tags=extra_tags,
    )
    metadata["langfuse_trace_name"] = _TRACE_NAMES.get(operation, "execute-agent-run")
    metadata["langfuse_tags"] = list(tags)

    client = get_langfuse_client()
    if client is None or CallbackHandler is None:
        return LangfuseRunContext(metadata=metadata, tags=tags)

    trace_id = client.create_trace_id(seed=request_id)
    handler = CallbackHandler(
        public_key=os.getenv("LANGFUSE_PUBLIC_KEY"),
        trace_context={"trace_id": trace_id},
    )
    return LangfuseRunContext(callbacks=[handler], metadata=metadata, tags=tags, trace_id=trace_id)


def get_trace_info(run_context: LangfuseRunContext | None) -> dict[str, Any]:
    if run_context is None:
        return {}

    metadata = run_context.metadata or {}
    trace_id = run_context.trace_id
    if not trace_id and run_context.callbacks:
        trace_id = getattr(run_context.callbacks[0], "last_trace_id", None)

    if not trace_id:
        return {}

    trace_info = {
        "langfuse_trace_id": trace_id,
        "langfuse_user_id": metadata.get("langfuse_user_id"),
        "langfuse_session_id": metadata.get("langfuse_session_id"),
    }

    # Do not fetch trace_url on the request critical path. Langfuse resolves the
    # project id via a remote API call, which can add noticeable latency when the
    # base URL is slow or unreachable. If a trace URL is still needed, fetch it
    # later via get_trace_url_by_id_async() and patch message metadata asynchronously.
    return trace_info


def update_current_sandbox_timing(timing: dict[str, float]) -> bool:
    """把可信 Sandbox 阶段耗时附加到当前 Langfuse observation。"""
    client = get_langfuse_client()
    if client is None or not timing:
        return False

    try:
        client.update_current_span(metadata={"sandbox_timing": dict(timing)})
        return True
    except Exception as exc:
        logger.warning(f"更新 Langfuse Sandbox 计时失败，将保留本地计时: {type(exc).__name__}")
        return False


def submit_user_feedback_score(
    *,
    trace_id: str,
    feedback_id: int,
    message_id: int,
    conversation_id: int,
    uid: str,
    rating: str,
    reason: str | None = None,
) -> bool:
    client = get_langfuse_client()
    if client is None:
        return False

    value = 1 if rating == "like" else 0
    try:
        client.create_score(
            trace_id=trace_id,
            score_id=f"yuxi-message-feedback-{feedback_id}",
            name="user-feedback",
            value=value,
            data_type="BOOLEAN",
            comment=reason,
            metadata={
                "source": "yuxi",
                "feedback_id": feedback_id,
                "message_id": message_id,
                "conversation_id": conversation_id,
                "uid": uid,
                "rating": rating,
            },
        )
        client.flush()
        return True
    except Exception as exc:
        logger.warning(f"提交 Langfuse 用户反馈评分失败，将保留本地反馈: {exc}")
        return False


def _http_origin(url: str) -> tuple[str, str, int] | None:
    try:
        parsed_url = urlparse(url.strip())
        if parsed_url.scheme not in {"http", "https"} or not parsed_url.hostname:
            return None
        default_port = 443 if parsed_url.scheme == "https" else 80
        return parsed_url.scheme, parsed_url.hostname.casefold(), parsed_url.port or default_port
    except ValueError:
        return None


async def get_trace_url_by_id_async(trace_id: str, *, timeout: float = 5.0) -> str | None:
    """按 trace ID 惰性解析已配置 Langfuse 源站的页面 URL。"""
    trace_id = str(trace_id or "").strip()
    if not trace_id:
        return None

    client = get_langfuse_client()
    if client is None:
        return None

    try:
        trace_url = await asyncio.wait_for(
            asyncio.to_thread(client.get_trace_url, trace_id=trace_id),
            timeout=timeout,
        )
    except Exception as exc:
        logger.warning(f"解析 Langfuse trace URL 失败: {type(exc).__name__}")
        return None

    if not isinstance(trace_url, str):
        return None

    trace_url = trace_url.strip()
    configured_origin = _http_origin(os.getenv("LANGFUSE_BASE_URL") or _DEFAULT_LANGFUSE_BASE_URL)
    if configured_origin is None or _http_origin(trace_url) != configured_origin:
        logger.warning("Langfuse 返回了非配置源站的 trace URL，已拒绝")
        return None
    return trace_url


def flush_langfuse() -> None:
    client = get_langfuse_client()
    if client is None:
        return

    try:
        client.flush()
    except Exception as exc:
        logger.warning(f"刷新 Langfuse 事件失败: {exc}")
