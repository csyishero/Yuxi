"""Browser MCP Gateway 的短期 JWT 与 HTTP 协议客户端。"""

from __future__ import annotations

import time
import uuid
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx
import jwt
from jwt.algorithms import ECAlgorithm

from .config import BrowserGatewaySettings, load_browser_gateway_settings

MCP_PROTOCOL_VERSION = "2026-07-28"
MAX_SCREENSHOT_BYTES = 5 * 1024 * 1024
_ARTIFACT_PATH = re.compile(r"/api/v1/artifacts/[A-Za-z0-9_-]{1,128}")


@dataclass(frozen=True)
class BrowserRuntimeIdentity:
    """后端从认证或 ToolRuntime 中提取的不可变运行身份。"""

    uid: str
    run_id: str
    thread_id: str
    request_id: str


class BrowserGatewayError(RuntimeError):
    """表示 Gateway 返回的公开业务错误。"""

    def __init__(self, code: str, message: str, *, diagnostic_id: str | None = None):
        super().__init__(message)
        self.code = code
        self.diagnostic_id = diagnostic_id


class BrowserGatewayUnavailable(RuntimeError):
    """表示 Gateway 网络不可达或返回了无效协议。"""


class BrowserGatewayClient:
    """为每次请求签发 JWT，并封装配对与只读 MCP 调用。"""

    def __init__(
        self,
        settings: BrowserGatewaySettings | None = None,
        *,
        http_client: httpx.AsyncClient | None = None,
    ):
        self.settings = settings or load_browser_gateway_settings()
        self.settings.validate()
        self._http_client = http_client
        self._signing_key = ECAlgorithm.from_jwk(self.settings.private_jwk)

    def issue_token(self, identity: BrowserRuntimeIdentity, *, ttl_seconds: int = 60) -> str:
        """签发不超过五分钟的 request-scoped ES256 JWT。"""
        now = int(time.time())
        payload = {
            "iss": self.settings.issuer,
            "aud": self.settings.audience,
            "sub": identity.uid,
            "run_id": identity.run_id,
            "thread_id": identity.thread_id,
            "request_id": identity.request_id,
            "scope": ["browser:mcp"],
            "iat": now,
            "nbf": now - 5,
            "exp": now + min(max(ttl_seconds, 15), 300),
            "jti": str(uuid.uuid4()),
        }
        return jwt.encode(
            payload,
            self._signing_key,
            algorithm="ES256",
            headers={"kid": self.settings.key_id, "typ": "JWT"},
        )

    async def create_pairing(self, *, uid: str) -> dict[str, Any]:
        """以当前用户身份创建一次性配对令牌。"""
        request_id = str(uuid.uuid4())
        identity = BrowserRuntimeIdentity(
            uid=str(uid),
            run_id=f"pairing:{uuid.uuid4()}",
            thread_id="browser-pairing",
            request_id=request_id,
        )
        response = await self._request(
            "POST",
            "/api/v1/pairings",
            headers={
                "Authorization": f"Bearer {self.issue_token(identity)}",
                "Origin": self.settings.yuxi_origin,
                "X-Yuxi-Request-ID": request_id,
            },
        )
        data = self._response_data(response)
        token = data.get("pairingToken")
        if not isinstance(token, str) or not token:
            raise BrowserGatewayUnavailable("Browser Gateway 返回了无效配对响应")
        return data

    async def call_tool(
        self,
        *,
        identity: BrowserRuntimeIdentity,
        name: str,
        arguments: dict[str, Any],
        assertion: str | None = None,
    ) -> Any:
        """调用 Gateway 的单个只读 MCP 工具并返回 structuredContent.data。"""
        headers = {
            "Authorization": f"Bearer {self.issue_token(identity)}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "MCP-Protocol-Version": MCP_PROTOCOL_VERSION,
            "Mcp-Method": "tools/call",
            "Mcp-Name": name,
            "X-Yuxi-Request-ID": identity.request_id,
        }
        if assertion:
            headers["X-Yuxi-Current-Device-Assertion"] = assertion
        response = await self._request(
            "POST",
            "/mcp",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": identity.request_id,
                "method": "tools/call",
                "params": {
                    "name": name,
                    "arguments": arguments,
                    "_meta": {"io.modelcontextprotocol/protocolVersion": MCP_PROTOCOL_VERSION},
                },
            },
        )
        payload = self._response_json(response)
        error = payload.get("error")
        if isinstance(error, dict):
            details = error.get("data") if isinstance(error.get("data"), dict) else {}
            raise BrowserGatewayError(
                str(details.get("code") or "BROWSER_GATEWAY_ERROR"),
                str(error.get("message") or "Browser Gateway 调用失败"),
                diagnostic_id=str(details.get("diagnostic_id") or "") or None,
            )
        try:
            return payload["result"]["structuredContent"]["data"]
        except (KeyError, TypeError) as exc:
            raise BrowserGatewayUnavailable("Browser Gateway 返回了无效 MCP 响应") from exc

    async def download_artifact(self, *, identity: BrowserRuntimeIdentity, artifact: dict[str, Any]) -> bytes:
        """使用同一 Run 的短期 JWT 下载 Gateway 截图，并执行大小与 PNG 校验。"""
        path = artifact.get("downloadPath")
        content_type = artifact.get("contentType")
        declared_size = artifact.get("size")
        if not isinstance(path, str) or not isinstance(content_type, str):
            raise BrowserGatewayUnavailable("Browser Gateway 返回了无效截图描述")
        parsed = urlsplit(path)
        if (
            parsed.scheme
            or parsed.netloc
            or parsed.query
            or parsed.fragment
            or not _ARTIFACT_PATH.fullmatch(parsed.path)
        ):
            raise BrowserGatewayUnavailable("Browser Gateway 返回了不安全的截图路径")
        if content_type.split(";", 1)[0].strip().lower() != "image/png":
            raise BrowserGatewayUnavailable("Browser Gateway 截图类型无效")
        if not isinstance(declared_size, int) or not 0 < declared_size <= MAX_SCREENSHOT_BYTES:
            raise BrowserGatewayUnavailable("Browser Gateway 截图大小无效")
        response = await self._request(
            "GET",
            parsed.path,
            headers={
                "Authorization": f"Bearer {self.issue_token(identity)}",
                "Accept": "image/png",
                "X-Yuxi-Request-ID": identity.request_id,
            },
        )
        if response.status_code != 200:
            raise BrowserGatewayError("SCREENSHOT_EXPIRED", "Browser screenshot is no longer available")
        body = response.content
        response_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if (
            response_type != "image/png"
            or len(body) != declared_size
            or len(body) > MAX_SCREENSHOT_BYTES
            or not body.startswith(b"\x89PNG\r\n\x1a\n")
        ):
            raise BrowserGatewayUnavailable("Browser Gateway 返回了无效截图内容")
        return body

    async def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        """执行 HTTP 请求；网络细节不携带令牌进入日志或异常文本。"""
        try:
            if self._http_client is not None:
                return await self._http_client.request(method, f"{self.settings.internal_url}{path}", **kwargs)
            async with httpx.AsyncClient(timeout=httpx.Timeout(40.0, connect=5.0)) as client:
                return await client.request(method, f"{self.settings.internal_url}{path}", **kwargs)
        except httpx.HTTPError as exc:
            raise BrowserGatewayUnavailable("Browser Gateway 暂时不可用") from exc

    def _response_json(self, response: httpx.Response) -> dict[str, Any]:
        """解析 JSON 对象，同时把非 JSON 上游响应收敛为公开错误。"""
        try:
            payload = response.json()
        except ValueError as exc:
            raise BrowserGatewayUnavailable("Browser Gateway 返回了无法解析的响应") from exc
        if not isinstance(payload, dict):
            raise BrowserGatewayUnavailable("Browser Gateway 返回了无效响应")
        if response.status_code >= 400 and not isinstance(payload.get("error"), dict):
            raise BrowserGatewayUnavailable("Browser Gateway 请求失败")
        return payload

    def _response_data(self, response: httpx.Response) -> dict[str, Any]:
        """解析普通 Gateway ApiResponse，并保留公开错误码。"""
        payload = self._response_json(response)
        error = payload.get("error")
        if isinstance(error, dict):
            raise BrowserGatewayError(
                str(error.get("code") or "BROWSER_GATEWAY_ERROR"),
                str(error.get("message") or "Browser Gateway 调用失败"),
                diagnostic_id=str(error.get("diagnosticId") or error.get("diagnostic_id") or "") or None,
            )
        data = payload.get("data")
        if not isinstance(data, dict):
            raise BrowserGatewayUnavailable("Browser Gateway 返回了无效业务响应")
        return data
