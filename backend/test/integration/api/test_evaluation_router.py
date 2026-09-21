"""
Integration tests for evaluation router endpoints.
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


async def _upload_test_dataset(test_client, admin_headers: dict[str, str], kb_id: str) -> tuple[str, str]:
    dataset_name = f"pytest_dataset_{uuid.uuid4().hex[:8]}"
    line = '{"query":"什么是单元测试？","gold_answer":"用于验证代码行为的自动化测试"}\n'

    response = await test_client.post(
        f"/api/evaluation/databases/{kb_id}/datasets/upload",
        data={"name": dataset_name, "description": "pytest dataset for download"},
        files={"file": ("pytest_dataset.jsonl", line.encode("utf-8"), "application/x-ndjson")},
        headers=admin_headers,
    )
    assert response.status_code == 200, response.text

    payload = response.json()
    assert payload.get("message") == "success"
    dataset_id = payload.get("data", {}).get("dataset_id")
    assert dataset_id
    return dataset_id, line


async def test_download_dataset_requires_admin(test_client, standard_user):
    response = await test_client.get(
        "/api/evaluation/datasets/dataset_fake/download",
        headers=standard_user["headers"],
    )
    assert response.status_code == 403


async def test_admin_can_download_dataset(test_client, admin_headers, knowledge_database):
    dataset_id, expected_line = await _upload_test_dataset(test_client, admin_headers, knowledge_database["kb_id"])

    response = await test_client.get(
        f"/api/evaluation/datasets/{dataset_id}/download",
        headers=admin_headers,
    )
    assert response.status_code == 200, response.text
    assert "application/x-ndjson" in response.headers.get("content-type", "")
    assert "attachment" in response.headers.get("content-disposition", "").lower()

    content = response.content.decode("utf-8")
    assert expected_line.strip() in content


async def test_download_dataset_not_found(test_client, admin_headers):
    response = await test_client.get(
        f"/api/evaluation/datasets/dataset_not_found_{uuid.uuid4().hex[:8]}/download",
        headers=admin_headers,
    )
    assert response.status_code == 404, response.text


async def test_admin_can_edit_dataset_question_and_gold_answer(
    test_client,
    admin_headers,
    knowledge_database,
):
    dataset_id, _ = await _upload_test_dataset(test_client, admin_headers, knowledge_database["kb_id"])
    detail_response = await test_client.get(
        f"/api/evaluation/databases/{knowledge_database['kb_id']}/datasets/{dataset_id}",
        headers=admin_headers,
    )
    assert detail_response.status_code == 200, detail_response.text
    item_id = detail_response.json()["data"]["items"][0]["item_id"]

    update_response = await test_client.put(
        f"/api/evaluation/datasets/{dataset_id}/items/{item_id}",
        json={"query": "  修改后的问题  ", "gold_answer": "  修改后的标准答案  "},
        headers=admin_headers,
    )

    assert update_response.status_code == 200, update_response.text
    updated = update_response.json()["data"]
    assert updated["item"]["query"] == "修改后的问题"
    assert updated["item"]["gold_answer"] == "修改后的标准答案"
    assert updated["dataset"]["has_gold_answers"] is True


async def test_standard_user_cannot_edit_dataset_item(test_client, standard_user):
    response = await test_client.put(
        "/api/evaluation/datasets/dataset-fake/items/item-fake",
        json={"query": "无权修改", "gold_answer": ""},
        headers=standard_user["headers"],
    )

    assert response.status_code == 403
