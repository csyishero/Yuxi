from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import server.routers.system_router as system_router
from server.routers.system_router import system

pytestmark = pytest.mark.unit


def test_discovery_endpoint_is_public(monkeypatch):
    monkeypatch.setattr("server.routers.system_router.get_version", lambda: "0.7.1.dev0")

    app = FastAPI()
    app.include_router(system, prefix="/api")
    response = TestClient(app).get("/api/system/discovery")

    assert response.status_code == 200
    payload = response.json()
    assert payload["name"] == "联合智擎"
    assert payload["version"] == "0.7.1.dev0"
    assert payload["api_prefix"] == "/api"
    assert payload["capabilities"]["features"]["knowledge"] is True
    assert payload["capabilities"]["cli"]["browser_login"] is True
    assert payload["capabilities"]["cli"]["api_key_auth"] is True
    assert payload["capabilities"]["cli"]["agent_list"] is True
    assert payload["capabilities"]["cli"]["agent_show"] is True
    assert payload["capabilities"]["cli"]["kb_upload"] is True
    assert payload["endpoints"]["cli_auth_sessions"] == "/api/auth/cli/sessions"
    assert payload["endpoints"]["readiness"] == "/api/system/ready"


def test_readiness_endpoint_returns_structured_503(monkeypatch):
    async def fake_readiness(*, startup_complete: bool, startup_components):
        assert startup_complete is False
        assert startup_components is None
        return {
            "status": "not_ready",
            "checks": {
                "startup": {"status": "error", "code": "not_complete"},
                "postgres": {"status": "ok"},
                "redis": {"status": "ok"},
            },
        }

    monkeypatch.setattr(system_router, "get_readiness", fake_readiness)
    monkeypatch.setattr(system_router, "get_version", lambda: "0.7.2.dev0")
    app = FastAPI()
    app.include_router(system, prefix="/api")

    response = TestClient(app).get("/api/system/ready")

    assert response.status_code == 503
    assert response.json()["status"] == "not_ready"
    assert response.json()["version"] == "0.7.2.dev0"


def test_serialize_system_config_includes_field_metadata(monkeypatch):
    monkeypatch.setenv("YUXI_ENV", "development")
    result = system_router._serialize_system_config({"default_model": "test-provider:latest"})

    assert result["default_model"] == "test-provider:latest"
    assert result["_config_items"]["default_model"]["type"] == "model"
    assert result["_service_links"] == {
        "neo4j": {"port": "7474", "path": "/"},
        "api_docs": {"port": "5050", "path": "/docs"},
        "minio": {"port": "9001", "path": "/"},
        "milvus": {"port": "9091", "path": "/webui/"},
    }


def test_serialize_system_config_uses_production_and_custom_service_ports(monkeypatch):
    monkeypatch.setenv("YUXI_ENV", "prod")
    monkeypatch.setenv("YUXI_NEO4J_HTTP_PORT", "18474")
    monkeypatch.setenv("YUXI_API_PORT", "16050")
    monkeypatch.setenv("YUXI_MINIO_CONSOLE_PORT", "19001")
    monkeypatch.setenv("YUXI_MILVUS_HEALTH_PORT", "19091")

    result = system_router._serialize_system_config({})

    assert result["_service_links"] == {
        "neo4j": {"port": "18474", "path": "/"},
        "api_docs": {"port": "16050", "path": "/docs"},
        "minio": {"port": "19001", "path": "/"},
        "milvus": {"port": "19091", "path": "/webui/"},
    }
