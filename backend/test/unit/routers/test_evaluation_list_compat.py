"""The evaluation list routes keep their original unbounded response for old callers."""

from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from server.routers import knowledge_eval_router


def _client(monkeypatch, service_type):
    app = FastAPI()
    app.include_router(knowledge_eval_router.evaluation)

    async def allow_view():
        return SimpleNamespace(uid="admin-1", role="admin")

    app.dependency_overrides[knowledge_eval_router.require_knowledge_base_view] = allow_view
    monkeypatch.setattr(knowledge_eval_router, "EvaluationService", service_type)
    return TestClient(app)


@pytest.mark.parametrize("resource", ["datasets", "runs"])
def test_evaluation_list_without_limit_returns_all_rows_in_legacy_shape(monkeypatch, resource):
    class ServiceStub:
        async def list_datasets(self, kb_id):
            assert kb_id == "kb-1"
            return [{"dataset_id": f"dataset-{index}"} for index in range(125)]

        async def list_runs(self, kb_id):
            assert kb_id == "kb-1"
            return [{"run_id": f"run-{index}"} for index in range(125)]

        async def list_datasets_page(self, *args, **kwargs):
            raise AssertionError("legacy request must use the unbounded dataset listing")

        async def list_runs_page(self, *args, **kwargs):
            raise AssertionError("legacy request must use the unbounded run listing")

    response = _client(monkeypatch, ServiceStub).get(f"/evaluation/databases/kb-1/{resource}")

    assert response.status_code == 200
    payload = response.json()
    assert set(payload) == {"message", "data"}
    assert payload["message"] == "success"
    assert len(payload["data"]) == 125


@pytest.mark.parametrize(
    ("resource", "query", "expected_kwargs"),
    [
        (
            "datasets",
            "limit=7&offset=14&status=completed&search=%20report%20",
            {
                "limit": 7,
                "offset": 14,
                "status": "completed",
                "search": "report",
            },
        ),
        (
            "runs",
            "limit=7&offset=14&dataset_id=dataset-1&run_id=run-1&before_id=31",
            {
                "limit": 7,
                "offset": 14,
                "dataset_id": "dataset-1",
                "run_id": "run-1",
                "before_id": 31,
            },
        ),
    ],
)
def test_evaluation_list_with_limit_keeps_filters_and_pagination(monkeypatch, resource, query, expected_kwargs):
    calls = []

    class ServiceStub:
        async def list_datasets_page(self, kb_id, **kwargs):
            calls.append((kb_id, kwargs))
            return {"items": [{"dataset_id": "dataset-1"}], "total": 125, "limit": 7, "offset": 14, "has_more": True}

        async def list_runs_page(self, kb_id, **kwargs):
            calls.append((kb_id, kwargs))
            return {
                "items": [{"run_id": "run-1"}],
                "total": 125,
                "limit": 7,
                "offset": 14,
                "has_more": True,
                "next_cursor": 30,
            }

    response = _client(monkeypatch, ServiceStub).get(f"/evaluation/databases/kb-1/{resource}?{query}")

    assert response.status_code == 200
    payload = response.json()
    assert len(payload["data"]) == 1
    assert payload["pagination"]["total"] == 125
    assert payload["pagination"]["limit"] == 7
    assert calls == [("kb-1", expected_kwargs)]


@pytest.mark.parametrize(
    ("resource", "query", "expected_kwargs"),
    [
        (
            "datasets",
            "offset=3&status=completed&search=%20report%20",
            {
                "offset": 3,
                "status": "completed",
                "search": "report",
            },
        ),
        (
            "runs",
            "offset=3&dataset_id=dataset-1&run_id=run-1&before_id=31",
            {
                "offset": 3,
                "dataset_id": "dataset-1",
                "run_id": "run-1",
                "before_id": 31,
            },
        ),
    ],
)
def test_evaluation_list_without_limit_keeps_filters(monkeypatch, resource, query, expected_kwargs):
    calls = []

    class ServiceStub:
        async def list_datasets(self, kb_id, **kwargs):
            calls.append((kb_id, kwargs))
            return [{"dataset_id": "dataset-1"}]

        async def list_runs(self, kb_id, **kwargs):
            calls.append((kb_id, kwargs))
            return [{"run_id": "run-1"}]

    response = _client(monkeypatch, ServiceStub).get(f"/evaluation/databases/kb-1/{resource}?{query}")

    assert response.status_code == 200
    assert set(response.json()) == {"message", "data"}
    assert len(response.json()["data"]) == 1
    assert calls == [("kb-1", expected_kwargs)]


@pytest.mark.parametrize("resource", ["datasets", "runs"])
@pytest.mark.parametrize("query", ["", "?limit=7"])
def test_evaluation_list_still_requires_knowledge_base_view(resource, query):
    app = FastAPI()
    app.include_router(knowledge_eval_router.evaluation)

    async def deny_view():
        raise HTTPException(status_code=403, detail="No knowledge base view permission")

    app.dependency_overrides[knowledge_eval_router.require_knowledge_base_view] = deny_view

    response = TestClient(app).get(f"/evaluation/databases/kb-1/{resource}{query}")
    assert response.status_code == 403


@pytest.mark.parametrize("resource", ["datasets", "runs"])
@pytest.mark.parametrize("limit", [0, 101])
def test_evaluation_list_limit_validation_is_unchanged(monkeypatch, resource, limit):
    class ServiceStub:
        def __init__(self):
            raise AssertionError("invalid limit must be rejected before constructing the service")

    response = _client(monkeypatch, ServiceStub).get(f"/evaluation/databases/kb-1/{resource}?limit={limit}")
    assert response.status_code == 422
