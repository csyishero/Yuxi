from __future__ import annotations

import json
from pathlib import Path

import httpx
import jwt
import pytest
import yaml
from cryptography.hazmat.primitives.asymmetric import ec
from jwt.algorithms import ECAlgorithm

from yuxi.browser_gateway.client import (
    MCP_PROTOCOL_VERSION,
    BrowserGatewayClient,
    BrowserGatewayError,
    BrowserRuntimeIdentity,
)
from yuxi.browser_gateway.config import (
    BrowserGatewayConfigurationError,
    BrowserGatewaySettings,
    load_browser_gateway_settings,
)


def _private_jwk(kid: str = "browser-test-key") -> str:
    key = ec.generate_private_key(ec.SECP256R1())
    value = json.loads(ECAlgorithm.to_jwk(key))
    value["kid"] = kid
    return json.dumps(value)


def _settings(private_jwk: str) -> BrowserGatewaySettings:
    return BrowserGatewaySettings(
        internal_url="http://gateway.test",
        public_url="https://browser.example.com",
        yuxi_origin="https://yuxi.example.com",
        extension_id="extension-id",
        issuer="https://yuxi.example.com",
        audience="browser-gateway",
        private_jwk=private_jwk,
        key_id="browser-test-key",
    )


@pytest.mark.parametrize("compose_name", ["docker-compose.yml", "docker-compose.prod.yml"])
def test_browser_private_key_is_only_exposed_to_api_and_worker(compose_name):
    """Browser JWT 私钥不得通过 env_file 泄露给迁移或解析容器。"""
    project_root = Path(__file__).resolve().parents[4]
    compose = yaml.safe_load((project_root / compose_name).read_text(encoding="utf-8"))
    services = compose["services"]
    for key in ("YUXI_BROWSER_GATEWAY_PRIVATE_JWK", "YUXI_BROWSER_GATEWAY_PRIVATE_JWK_FILE"):
        assert key in services["api"]["environment"]
        assert key in services["worker"]["environment"]
        assert services["storage-migrator"]["environment"][key] == ""
        assert services["mineru-api"]["environment"][key] == ""


def test_load_settings_requires_complete_trust_chain(monkeypatch):
    """缺少私钥或来源时保持禁用，不能只凭 Gateway URL 注册工具。"""
    monkeypatch.setenv("YUXI_BROWSER_GATEWAY_URL", "http://gateway:8080/")
    monkeypatch.delenv("YUXI_BROWSER_GATEWAY_PRIVATE_JWK", raising=False)
    monkeypatch.delenv("YUXI_BROWSER_ORIGIN", raising=False)

    settings = load_browser_gateway_settings()

    assert settings.internal_url == "http://gateway:8080"
    assert settings.enabled is False
    assert "YUXI_BROWSER_GATEWAY_PRIVATE_JWK" in settings.missing_fields
    assert "YUXI_BROWSER_ORIGIN" in settings.missing_fields


def test_non_utf8_private_key_file_degrades_to_configuration_error(monkeypatch, tmp_path):
    """可选 Browser 配置损坏时必须走禁用边界，不能阻断普通工具包导入。"""
    key_file = tmp_path / "browser-private.jwk"
    key_file.write_bytes(b"\xff\xfe\x00")
    monkeypatch.delenv("YUXI_BROWSER_GATEWAY_PRIVATE_JWK", raising=False)
    monkeypatch.setenv("YUXI_BROWSER_GATEWAY_PRIVATE_JWK_FILE", str(key_file))

    with pytest.raises(BrowserGatewayConfigurationError, match="无法读取"):
        load_browser_gateway_settings()


def test_settings_reject_yuxi_origin_with_path():
    """Yuxi 来源必须能与浏览器 sender.origin 做严格等值比较。"""
    settings = _settings(_private_jwk())
    settings = BrowserGatewaySettings(**{**settings.__dict__, "yuxi_origin": "https://yuxi.example.com/app"})

    with pytest.raises(BrowserGatewayConfigurationError, match="仅包含协议"):
        settings.validate()


def test_settings_reject_insecure_remote_yuxi_origin():
    """动态配对信任只允许 HTTPS 生产来源，本机开发地址除外。"""
    settings = _settings(_private_jwk())
    settings = BrowserGatewaySettings(**{**settings.__dict__, "yuxi_origin": "http://intranet.example.com"})

    with pytest.raises(BrowserGatewayConfigurationError, match="仅允许 HTTPS"):
        settings.validate()


def test_invalid_ttl_is_optional_feature_configuration_error(monkeypatch):
    """可选 Browser 配置错误应被注册层收敛，不能以裸 ValueError 阻断工具包导入。"""
    monkeypatch.setenv("YUXI_BROWSER_ASSERTION_TTL_SECONDS", "not-an-integer")

    with pytest.raises(BrowserGatewayConfigurationError, match="必须是整数"):
        load_browser_gateway_settings()


def test_issue_token_contains_runtime_identity_and_strict_headers():
    """JWT 身份来自后端 Run，并使用 ES256、kid 与短有效期。"""
    client = BrowserGatewayClient(_settings(_private_jwk()))
    identity = BrowserRuntimeIdentity(
        uid="user-1",
        run_id="run-1",
        thread_id="thread-1",
        request_id="request-1",
    )

    token = client.issue_token(identity)
    header = jwt.get_unverified_header(token)
    claims = jwt.decode(token, options={"verify_signature": False})

    assert header == {"alg": "ES256", "kid": "browser-test-key", "typ": "JWT"}
    assert claims["sub"] == "user-1"
    assert claims["run_id"] == "run-1"
    assert claims["thread_id"] == "thread-1"
    assert claims["request_id"] == "request-1"
    assert claims["scope"] == ["browser:mcp"]
    assert 0 < claims["exp"] - claims["iat"] <= 300


@pytest.mark.asyncio
async def test_pairing_and_mcp_use_expected_gateway_contract():
    """配对与工具调用发送 Gateway 规定的 Origin、JWT 和 MCP 信封。"""
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/api/v1/pairings":
            return httpx.Response(
                200,
                json={"ok": True, "data": {"pairingToken": "p" * 43, "expiresAt": "soon"}},
            )
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": "request-1",
                "result": {"structuredContent": {"data": {"tabs": [{"tab_id": 1}]}}},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http_client:
        client = BrowserGatewayClient(_settings(_private_jwk()), http_client=http_client)
        pairing = await client.create_pairing(uid="user-1")
        result = await client.call_tool(
            identity=BrowserRuntimeIdentity("user-1", "run-1", "thread-1", "request-1"),
            name="browser_list_tabs",
            arguments={"active_only": False},
            assertion="assertion-value",
        )

    assert pairing["pairingToken"] == "p" * 43
    assert result == {"tabs": [{"tab_id": 1}]}
    pairing_request, mcp_request = requests
    assert pairing_request.headers["origin"] == "https://yuxi.example.com"
    assert pairing_request.headers["authorization"].startswith("Bearer ")
    assert mcp_request.headers["mcp-protocol-version"] == MCP_PROTOCOL_VERSION
    assert mcp_request.headers["mcp-method"] == "tools/call"
    assert mcp_request.headers["mcp-name"] == "browser_list_tabs"
    assert mcp_request.headers["x-yuxi-current-device-assertion"] == "assertion-value"
    body = json.loads(mcp_request.content)
    assert body["params"]["arguments"] == {"active_only": False}
    assert body["params"]["_meta"]["io.modelcontextprotocol/protocolVersion"] == MCP_PROTOCOL_VERSION


@pytest.mark.asyncio
async def test_gateway_error_exposes_only_public_code():
    """Gateway JSON-RPC 错误保留公开码，不把完整响应拼入异常。"""
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(
            400,
            json={
                "error": {
                    "code": -32602,
                    "message": "Current browser is offline",
                    "data": {"code": "BROWSER_OFFLINE", "diagnostic_id": "diag-1"},
                }
            },
        )
    )
    async with httpx.AsyncClient(transport=transport) as http_client:
        client = BrowserGatewayClient(_settings(_private_jwk()), http_client=http_client)
        with pytest.raises(BrowserGatewayError) as caught:
            await client.call_tool(
                identity=BrowserRuntimeIdentity("user-1", "run-1", "thread-1", "request-1"),
                name="browser_current_device",
                arguments={},
            )

    assert caught.value.code == "BROWSER_OFFLINE"
    assert caught.value.diagnostic_id == "diag-1"


@pytest.mark.asyncio
async def test_download_screenshot_uses_run_jwt_and_validates_png():
    """截图 artifact 必须由 Yuxi 代表同一 Run 下载，模型不接触 Gateway JWT。"""
    png = b"\x89PNG\r\n\x1a\nvalid-png"
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, headers={"Content-Type": "image/png"}, content=png)

    artifact = {
        "artifactId": "artifact-1",
        "contentType": "image/png",
        "size": len(png),
        "downloadPath": "/api/v1/artifacts/artifact-1",
    }
    identity = BrowserRuntimeIdentity("user-1", "run-1", "thread-1", "request-1")
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http_client:
        client = BrowserGatewayClient(_settings(_private_jwk()), http_client=http_client)
        downloaded = await client.download_artifact(identity=identity, artifact=artifact)

    assert downloaded == png
    assert requests[0].headers["authorization"].startswith("Bearer ")
    assert requests[0].url.path == "/api/v1/artifacts/artifact-1"
