"""Browser MCP Gateway 环境配置。"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse


@dataclass(frozen=True)
class BrowserGatewaySettings:
    """承载 Browser Gateway 的公开地址和签名配置，不主动建立连接。"""

    internal_url: str
    public_url: str
    yuxi_origin: str
    extension_id: str
    issuer: str
    audience: str
    private_jwk: str
    key_id: str
    assertion_ttl_seconds: int = 50

    @property
    def enabled(self) -> bool:
        """仅在完成全部信任链配置后启用浏览器能力。"""
        return not self.missing_fields

    @property
    def missing_fields(self) -> tuple[str, ...]:
        """返回启用能力仍缺少的环境变量名称。"""
        missing = []
        for value, name in (
            (self.internal_url, "YUXI_BROWSER_GATEWAY_URL"),
            (self.public_url, "YUXI_BROWSER_GATEWAY_PUBLIC_URL"),
            (self.yuxi_origin, "YUXI_BROWSER_ORIGIN"),
            (self.issuer, "YUXI_BROWSER_GATEWAY_ISSUER"),
            (self.audience, "YUXI_BROWSER_GATEWAY_AUDIENCE"),
            (self.private_jwk, "YUXI_BROWSER_GATEWAY_PRIVATE_JWK"),
            (self.key_id, "YUXI_BROWSER_GATEWAY_KEY_ID"),
        ):
            if not value:
                missing.append(name)
        return tuple(missing)

    def validate(self) -> None:
        """验证启用后的 URL 与 ES256 私钥配置。"""
        if self.missing_fields:
            raise BrowserGatewayConfigurationError(f"Browser Gateway 未完整配置：{', '.join(self.missing_fields)}")
        for name, value in (
            ("YUXI_BROWSER_GATEWAY_URL", self.internal_url),
            ("YUXI_BROWSER_GATEWAY_PUBLIC_URL", self.public_url),
            ("YUXI_BROWSER_ORIGIN", self.yuxi_origin),
        ):
            parsed = urlparse(value)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password:
                raise BrowserGatewayConfigurationError(f"{name} 必须是无凭据的 http(s) 地址")
            if name == "YUXI_BROWSER_ORIGIN" and (parsed.path not in {"", "/"} or parsed.query or parsed.fragment):
                raise BrowserGatewayConfigurationError("YUXI_BROWSER_ORIGIN 必须是仅包含协议、主机和端口的来源")
            if (
                name == "YUXI_BROWSER_ORIGIN"
                and parsed.scheme != "https"
                and parsed.hostname not in {"localhost", "127.0.0.1"}
            ):
                raise BrowserGatewayConfigurationError("YUXI_BROWSER_ORIGIN 仅允许 HTTPS 或本机开发地址")
        try:
            jwk = json.loads(self.private_jwk)
        except json.JSONDecodeError as exc:
            raise BrowserGatewayConfigurationError("YUXI_BROWSER_GATEWAY_PRIVATE_JWK 不是合法 JSON") from exc
        if (
            not isinstance(jwk, dict)
            or jwk.get("kty") != "EC"
            or jwk.get("crv") != "P-256"
            or not jwk.get("d")
            or jwk.get("kid") not in {None, self.key_id}
        ):
            raise BrowserGatewayConfigurationError("Browser Gateway 私钥必须是带 d 的 P-256 JWK，kid 必须一致")


class BrowserGatewayConfigurationError(RuntimeError):
    """表示 Browser Gateway 配置不完整或不安全。"""


def _read_private_jwk() -> str:
    """优先读取直接配置，否则从只读密钥文件加载。"""
    value = os.getenv("YUXI_BROWSER_GATEWAY_PRIVATE_JWK", "").strip()
    if value:
        return value
    path_value = os.getenv("YUXI_BROWSER_GATEWAY_PRIVATE_JWK_FILE", "").strip()
    if not path_value:
        return ""
    try:
        return Path(path_value).read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError) as exc:
        raise BrowserGatewayConfigurationError("无法读取 YUXI_BROWSER_GATEWAY_PRIVATE_JWK_FILE") from exc


def _normalized_url(value: str) -> str:
    """移除末尾斜杠，避免拼接接口路径时产生双斜杠。"""
    return value.strip().rstrip("/")


def load_browser_gateway_settings() -> BrowserGatewaySettings:
    """从环境变量读取一次 Browser Gateway 配置快照。"""
    internal_url = _normalized_url(os.getenv("YUXI_BROWSER_GATEWAY_URL", ""))
    public_url = _normalized_url(os.getenv("YUXI_BROWSER_GATEWAY_PUBLIC_URL", "")) or internal_url
    private_jwk = _read_private_jwk()
    configured_key_id = os.getenv("YUXI_BROWSER_GATEWAY_KEY_ID", "").strip()
    if private_jwk and not configured_key_id:
        try:
            configured_key_id = str(json.loads(private_jwk).get("kid") or "").strip()
        except (json.JSONDecodeError, AttributeError):
            configured_key_id = ""
    try:
        ttl = int(os.getenv("YUXI_BROWSER_ASSERTION_TTL_SECONDS", "50"))
    except ValueError as exc:
        raise BrowserGatewayConfigurationError("YUXI_BROWSER_ASSERTION_TTL_SECONDS 必须是整数") from exc
    if not 30 <= ttl <= 60:
        raise BrowserGatewayConfigurationError("YUXI_BROWSER_ASSERTION_TTL_SECONDS 必须在 30 到 60 之间")
    return BrowserGatewaySettings(
        internal_url=internal_url,
        public_url=public_url,
        yuxi_origin=_normalized_url(os.getenv("YUXI_BROWSER_ORIGIN", "")),
        extension_id=os.getenv("YUXI_BROWSER_EXTENSION_ID", "").strip(),
        issuer=os.getenv("YUXI_BROWSER_GATEWAY_ISSUER", "https://yuxi.local").strip(),
        audience=os.getenv("YUXI_BROWSER_GATEWAY_AUDIENCE", "browser-gateway").strip(),
        private_jwk=private_jwk,
        key_id=configured_key_id,
        assertion_ttl_seconds=ttl,
    )
