from __future__ import annotations

import time
from dataclasses import dataclass, field

import httpx


SANDBOX_TIMING_FIELDS = frozenset(
    {
        "sandbox_discover_ms",
        "sandbox_create_container_ms",
        "sandbox_create_network_ms",
        "sandbox_wait_ready_ms",
        "sandbox_execute_request_ms",
        "sandbox_release_ms",
        "sandbox_delete_container_ms",
        "sandbox_delete_network_ms",
    }
)


def normalize_sandbox_timing(value: object) -> dict[str, float]:
    """只接受协议约定的非负数值 Sandbox 耗时。"""
    if not isinstance(value, dict):
        return {}
    return {
        key: round(float(raw_value), 2)
        for key, raw_value in value.items()
        if key in SANDBOX_TIMING_FIELDS
        and isinstance(raw_value, (int, float))
        and not isinstance(raw_value, bool)
        and raw_value >= 0
    }


def merge_sandbox_timing(*values: object) -> dict[str, float]:
    """按 Run 累计多个 Sandbox 阶段耗时。"""
    merged: dict[str, float] = {}
    for value in values:
        for key, duration in normalize_sandbox_timing(value).items():
            merged[key] = round(merged.get(key, 0.0) + duration, 2)
    return merged


def _elapsed_ms(started_ns: int) -> float:
    return round((time.perf_counter_ns() - started_ns) / 1_000_000, 2)


@dataclass(slots=True)
class SandboxRecord:
    sandbox_id: str
    sandbox_url: str
    status: str | None = None
    generation: str | None = None
    workdir_path: str | None = None
    timing: dict[str, float] = field(default_factory=dict)


class ProvisionerClient:
    def __init__(
        self,
        base_url: str,
        *,
        token: str,
        timeout_seconds: int = 20,
        delete_timeout_seconds: int = 120,
    ):
        self._base_url = base_url.rstrip("/")
        self._timeout = httpx.Timeout(timeout_seconds)
        # create 是同步长操作，镜像拉取和 Sandbox 健康等待由 provisioner
        # 拥有；仅取消响应读取上限，连接、写入和连接池仍快速失败。
        self._create_timeout = httpx.Timeout(timeout_seconds, read=None)
        self._delete_timeout = httpx.Timeout(delete_timeout_seconds)
        self._headers = {"Authorization": f"Bearer {token}"}

    def _request(self, method: str, path: str, *, timeout: httpx.Timeout | None = None, **kwargs) -> httpx.Response:
        return httpx.request(
            method=method,
            url=f"{self._base_url}{path}",
            timeout=timeout or self._timeout,
            headers=self._headers,
            **kwargs,
        )

    def health(self) -> bool:
        response = self._request("GET", "/health")
        return response.status_code == 200

    def create(
        self,
        sandbox_id: str,
        thread_id: str,
        uid: str,
        env: dict[str, str] | None = None,
        *,
        workdir_path: str | None = None,
        inherit_env: bool = True,
    ) -> SandboxRecord:
        response = self._request(
            "POST",
            "/api/sandboxes",
            timeout=self._create_timeout,
            json={
                "sandbox_id": sandbox_id,
                "thread_id": thread_id,
                "workdir_path": workdir_path,
                "uid": uid,
                "env": env or {},
                "inherit_env": inherit_env,
            },
        )
        if response.status_code >= 400:
            raise RuntimeError(f"failed to create sandbox {sandbox_id}: {response.status_code} {response.text}")
        return self._record_from_payload(response.json())

    def discover(self, sandbox_id: str) -> SandboxRecord | None:
        started_ns = time.perf_counter_ns()
        response = self._request("GET", f"/api/sandboxes/{sandbox_id}")
        if response.status_code == 404:
            return None
        if response.status_code >= 400:
            raise RuntimeError(f"failed to discover sandbox {sandbox_id}: {response.status_code} {response.text}")
        record = self._record_from_payload(response.json())
        record.timing = merge_sandbox_timing(
            record.timing,
            {"sandbox_discover_ms": _elapsed_ms(started_ns)},
        )
        return record

    @staticmethod
    def _record_from_payload(payload: dict) -> SandboxRecord:
        """把 provisioner wire payload 转为内部 Sandbox 记录。"""
        return SandboxRecord(
            sandbox_id=payload["sandbox_id"],
            sandbox_url=payload["sandbox_url"],
            status=payload.get("status"),
            generation=payload.get("generation"),
            workdir_path=payload.get("workdir_path"),
            timing=normalize_sandbox_timing(payload.get("timing")),
        )

    def touch(self, sandbox_id: str) -> bool:
        response = self._request("POST", f"/api/sandboxes/{sandbox_id}/touch")
        if response.status_code == 404:
            return False
        if response.status_code >= 400:
            raise RuntimeError(f"failed to touch sandbox {sandbox_id}: {response.status_code} {response.text}")
        return True

    def delete(self, sandbox_id: str, *, expected_generation: str | None = None) -> dict[str, float]:
        params = {"expected_generation": expected_generation} if expected_generation else None
        response = self._request(
            "DELETE",
            f"/api/sandboxes/{sandbox_id}",
            timeout=self._delete_timeout,
            params=params,
        )
        if response.status_code == 404:
            return {}
        if response.status_code == 200:
            response_json = getattr(response, "json", None)
            payload = response_json() if callable(response_json) else {}
            return normalize_sandbox_timing(payload.get("timing") if isinstance(payload, dict) else None)
        raise RuntimeError(f"failed to delete sandbox {sandbox_id}: {response.status_code} {response.text}")
