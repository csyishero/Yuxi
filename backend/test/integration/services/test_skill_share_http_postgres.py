"""个人 Skill 共享审核的 HTTP 与 PostgreSQL 集成边界。"""

from __future__ import annotations

import io
import os
import uuid
import zipfile
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from server.routers.skill_router import skills, user_skills
from server.utils.auth_middleware import get_db, get_required_user
from yuxi.agents.skills import sharing
from yuxi.permissions import ResourcePermission, resolve_skill_permission
from yuxi.storage.postgres.models_business import Base, Department, Skill, SkillShareRequest, User

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


@pytest.fixture(scope="session", autouse=True)
def ensure_live_api_schema():
    """本文件使用独立 PostgreSQL schema，无需运行中的 API。"""


@pytest.fixture(scope="session", autouse=True)
def cleanup_test_knowledge_resources():
    """本文件没有知识库资源。"""
    yield


@pytest.fixture(scope="session", autouse=True)
def cleanup_test_sandboxes():
    """本文件没有沙盒资源。"""
    yield


async def test_skill_share_http_uses_snapshot_and_department_review(monkeypatch, tmp_path: Path):
    """真实 HTTP 请求按角色隔离，并在 PostgreSQL 持久化审核和发布。"""
    schema = f"pytest_skill_share_{uuid.uuid4().hex[:12]}"
    admin_engine = create_async_engine(os.environ["POSTGRES_URL"])
    async with admin_engine.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_async_engine(os.environ["POSTGRES_URL"], connect_args={"server_settings": {"search_path": schema}})
    personal_root = tmp_path / "personal"
    skill_dir = personal_root / "credit-check"
    skill_dir.mkdir(parents=True)
    original = "---\nname: credit-check\ndescription: first copy\n---\n# First copy\n"
    (skill_dir / "SKILL.md").write_text(original, encoding="utf-8")
    (skill_dir / "scripts").mkdir()
    (skill_dir / "scripts" / "check.py").write_text("print('submitted')\n", encoding="utf-8")
    shared_root = tmp_path / "shared"
    shared_root.mkdir()
    monkeypatch.setattr(sharing, "personal_skills_root", lambda _uid: personal_root)
    monkeypatch.setattr(sharing, "get_skill_data_dir", lambda: tmp_path)
    monkeypatch.setattr(sharing, "get_skills_root_dir", lambda: shared_root)

    async def no_dependencies(**_kwargs):
        """本例仅验证申请和发布，不加载独立 MCP 服务。"""
        return [], [], []

    monkeypatch.setattr(sharing, "validate_skill_dependencies", no_dependencies)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    current = User(uid="owner", username="owner", password_hash="x", role="user", department_id=1)
    app = FastAPI()
    app.include_router(user_skills, prefix="/api")
    app.include_router(skills, prefix="/api")

    async def provide_user():
        """对同一 HTTP 客户端切换认证角色。"""
        return current

    async def provide_db():
        """每个请求使用独立真实 PostgreSQL 会话。"""
        async with sessions() as db:
            yield db

    app.dependency_overrides[get_required_user] = provide_user
    app.dependency_overrides[get_db] = provide_db

    try:
        async with engine.begin() as connection:
            await connection.run_sync(
                lambda conn: Base.metadata.create_all(
                    conn, tables=[Department.__table__, User.__table__, Skill.__table__, SkillShareRequest.__table__]
                )
            )
            await connection.execute(text("INSERT INTO departments (id, name) VALUES (1, 'Credit'), (2, 'Risk')"))
        async with sessions() as db:
            db.add_all(
                [
                    User(uid="target-credit", username="target-credit", password_hash="x", department_id=1),
                    User(uid="target-risk", username="target-risk", password_hash="x", department_id=2),
                    User(uid="deleted-risk", username="deleted-risk", password_hash="x", department_id=2, is_deleted=1),
                ]
            )
            await db.commit()

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            submitted = await client.post("/api/skills/personal/credit-check/share-request")
            assert submitted.status_code == 200, submitted.text
            request = submitted.json()["data"]
            assert request["status"] == "pending"
            assert request["owner_department_id"] == 1

            duplicate = await client.post("/api/skills/personal/credit-check/share-request")
            assert duplicate.status_code == 400, duplicate.text
            unauthorized = await client.post(
                f"/api/skills/share-requests/{request['id']}/approve", json={"department_ids": [1]}
            )
            assert unauthorized.status_code == 403, unauthorized.text

            (skill_dir / "SKILL.md").write_text(
                "---\nname: credit-check\ndescription: later edit\n---\n# Later edit\n", encoding="utf-8"
            )
            current = User(uid="other-admin", username="other-admin", password_hash="x", role="admin", department_id=2)
            other_queue = await client.get("/api/skills/share-requests")
            assert other_queue.status_code == 200, other_queue.text
            assert other_queue.json()["data"] == []
            other_snapshot = await client.get(f"/api/skills/share-requests/{request['id']}/snapshot")
            assert other_snapshot.status_code == 404, other_snapshot.text
            other_tree = await client.get(f"/api/skills/share-requests/{request['id']}/tree")
            assert other_tree.status_code == 404, other_tree.text
            other_export = await client.get(f"/api/skills/share-requests/{request['id']}/export")
            assert other_export.status_code == 404, other_export.text

            current = User(
                uid="credit-admin", username="credit-admin", password_hash="x", role="admin", department_id=1
            )
            snapshot = await client.get(f"/api/skills/share-requests/{request['id']}/snapshot")
            assert snapshot.status_code == 200, snapshot.text
            assert snapshot.json()["data"]["content"] == original
            tree = await client.get(f"/api/skills/share-requests/{request['id']}/tree")
            assert tree.status_code == 200, tree.text
            assert "scripts/check.py" in [
                child["path"] for entry in tree.json()["data"] for child in entry.get("children", [])
            ]
            script = await client.get(
                f"/api/skills/share-requests/{request['id']}/snapshot", params={"path": "scripts/check.py"}
            )
            assert script.status_code == 200, script.text
            assert script.json()["data"]["content"] == "print('submitted')\n"
            escape = await client.get(
                f"/api/skills/share-requests/{request['id']}/snapshot", params={"path": "../SKILL.md"}
            )
            assert escape.status_code == 400, escape.text
            exported = await client.get(f"/api/skills/share-requests/{request['id']}/export")
            assert exported.status_code == 200, exported.text
            with zipfile.ZipFile(io.BytesIO(exported.content)) as archive:
                assert archive.read(f"{request['id']}/scripts/check.py") == b"print('submitted')\n"
            wrong_scope = await client.post(
                f"/api/skills/share-requests/{request['id']}/approve", json={"department_ids": [2]}
            )
            assert wrong_scope.status_code == 400, wrong_scope.text
            approved = await client.post(
                f"/api/skills/share-requests/{request['id']}/approve",
                json={"department_ids": [1], "note": "可共享"},
            )
            assert approved.status_code == 200, approved.text
            assert approved.json()["data"]["status"] == "approved"
            assert approved.json()["data"]["published_slug"] == "credit-check"
            assert (shared_root / "credit-check" / "SKILL.md").read_text(encoding="utf-8") == original
            assert (shared_root / "credit-check" / "scripts" / "check.py").read_text(
                encoding="utf-8"
            ) == "print('submitted')\n"
            assert (skill_dir / "SKILL.md").read_text(encoding="utf-8").endswith("# Later edit\n")
            immutable_file = await client.put(
                "/api/system/skills/credit-check/file",
                json={"path": "SKILL.md", "content": "modified"},
            )
            assert immutable_file.status_code == 400, immutable_file.text
            immutable_dependencies = await client.put(
                "/api/system/skills/credit-check/dependencies",
                json={"tool_dependencies": [], "mcp_dependencies": [], "skill_dependencies": []},
            )
            assert immutable_dependencies.status_code == 400, immutable_dependencies.text

            current = User(uid="super-admin", username="super-admin", password_hash="x", role="superadmin")
            widened_scope = await client.put(
                "/api/system/skills/credit-check/share-config",
                json={
                    "share_config": {
                        "version": 2,
                        "read_scope": {"access_level": "global", "department_ids": [], "user_uids": []},
                        "manage_scope": None,
                    }
                },
            )
            assert widened_scope.status_code == 400, widened_scope.text
            assert (shared_root / "credit-check" / "SKILL.md").read_text(encoding="utf-8") == original

            user_scope = {"access_level": "user", "department_ids": [], "user_uids": ["target-risk"]}
            updated_scope = await client.put(
                "/api/system/skills/credit-check/share-config",
                json={"share_config": {"version": 2, "read_scope": user_scope, "manage_scope": None}},
            )
            assert updated_scope.status_code == 200, updated_scope.text
            assert updated_scope.json()["data"]["share_config"]["read_scope"] == user_scope
            deleted_target = await client.put(
                "/api/system/skills/credit-check/share-config",
                json={
                    "share_config": {
                        "version": 2,
                        "read_scope": {**user_scope, "user_uids": ["deleted-risk"]},
                        "manage_scope": None,
                    }
                },
            )
            assert deleted_target.status_code == 404, deleted_target.text
            delegated_manager = await client.put(
                "/api/system/skills/credit-check/share-config",
                json={"share_config": {"version": 2, "read_scope": user_scope, "manage_scope": user_scope}},
            )
            assert delegated_manager.status_code == 400, delegated_manager.text

            current = User(uid="owner", username="owner", password_hash="x", role="user", department_id=1)
            second_request = await client.post("/api/skills/personal/credit-check/share-request")
            assert second_request.status_code == 200, second_request.text
            current = User(
                uid="credit-admin", username="credit-admin", password_hash="x", role="admin", department_id=1
            )
            cross_department_person = await client.post(
                f"/api/skills/share-requests/{second_request.json()['data']['id']}/approve",
                json={"read_scope": user_scope},
            )
            assert cross_department_person.status_code == 400, cross_department_person.text
            current = User(uid="super-admin", username="super-admin", password_hash="x", role="superadmin")
            approved_person = await client.post(
                f"/api/skills/share-requests/{second_request.json()['data']['id']}/approve",
                json={"read_scope": user_scope},
            )
            assert approved_person.status_code == 200, approved_person.text
            assert approved_person.json()["data"]["read_scope"] == user_scope
            assert approved_person.json()["data"]["department_ids"] == []

        async with sessions() as db:
            saved_request = await db.get(SkillShareRequest, request["id"])
            published = await db.get(Skill, 1)
            assert saved_request.reviewer_uid == "credit-admin"
            assert saved_request.published_slug == published.slug
            assert saved_request.read_scope["department_ids"] == [1]
            assert published.share_config["read_scope"] == user_scope
            assert published.created_by == "credit-admin"
            assert published.version == "1.0.0"
            requester = User(uid="owner", username="owner", password_hash="x", role="user", department_id=1)
            assert resolve_skill_permission(requester, published) == ResourcePermission.NONE
            target = User(uid="target-risk", username="target-risk", password_hash="x", role="user", department_id=2)
            assert resolve_skill_permission(target, published) == ResourcePermission.READ
            person_release = await db.get(Skill, 2)
            assert person_release.share_config["read_scope"] == user_scope
    finally:
        await engine.dispose()
        async with admin_engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin_engine.dispose()


async def test_department_admin_direct_publish_keeps_owner_file(monkeypatch, tmp_path: Path):
    """直接发布走真实 HTTP、PostgreSQL 和快照，并拒绝越权与无效范围。"""
    schema = f"pytest_skill_direct_{uuid.uuid4().hex[:12]}"
    admin_engine = create_async_engine(os.environ["POSTGRES_URL"])
    async with admin_engine.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_async_engine(os.environ["POSTGRES_URL"], connect_args={"server_settings": {"search_path": schema}})
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    personal_root = tmp_path / "personal"
    source = personal_root / "member" / "member-skill"
    source.mkdir(parents=True)
    original = "---\nname: member-skill\ndescription: member skill\n---\n# Owner copy\n"
    (source / "SKILL.md").write_text(original, encoding="utf-8")
    unassigned_source = personal_root / "unassigned" / "unassigned-skill"
    unassigned_source.mkdir(parents=True)
    (unassigned_source / "SKILL.md").write_text(
        "---\nname: unassigned-skill\ndescription: unassigned\n---\n# Owner copy\n", encoding="utf-8"
    )
    shared_root = tmp_path / "shared"
    shared_root.mkdir()
    monkeypatch.setattr(sharing, "personal_skills_root", lambda uid: personal_root / uid)
    monkeypatch.setattr(sharing, "get_skill_data_dir", lambda: tmp_path)
    monkeypatch.setattr(sharing, "get_skills_root_dir", lambda: shared_root)

    async def no_dependencies(**_kwargs):
        """隔离发布权限与持久化，不访问其他服务。"""
        return [], [], []

    monkeypatch.setattr(sharing, "validate_skill_dependencies", no_dependencies)
    current = User(uid="member", username="member", password_hash="x", role="user", department_id=1)
    app = FastAPI()
    app.include_router(user_skills, prefix="/api")

    async def provide_user():
        """对同一客户端切换身份。"""
        return current

    async def provide_db():
        """每个 HTTP 请求使用独立 PostgreSQL 会话。"""
        async with sessions() as db:
            yield db

    app.dependency_overrides[get_required_user] = provide_user
    app.dependency_overrides[get_db] = provide_db
    try:
        async with engine.begin() as connection:
            await connection.run_sync(
                lambda conn: Base.metadata.create_all(
                    conn, tables=[Department.__table__, User.__table__, Skill.__table__, SkillShareRequest.__table__]
                )
            )
            await connection.execute(text("INSERT INTO departments (id, name) VALUES (1, 'Credit'), (2, 'Risk')"))
        async with sessions() as db:
            db.add_all(
                [
                    User(uid="member", username="member", password_hash="x", role="user", department_id=1),
                    User(
                        uid="credit-reader", username="credit-reader", password_hash="x", role="user", department_id=1
                    ),
                    User(uid="risk-reader", username="risk-reader", password_hash="x", role="user", department_id=2),
                    User(uid="unassigned", username="unassigned", password_hash="x", role="user"),
                ]
            )
            await db.commit()

        url = "/api/skills/personal-catalog/member/member-skill/publish"
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            denied = await client.post(url, json={"department_ids": [1]})
            assert denied.status_code == 403

            current = User(uid="risk-admin", username="risk-admin", password_hash="x", role="admin", department_id=2)
            wrong_department = await client.post(url, json={"department_ids": [2]})
            assert wrong_department.status_code == 404

            current = User(
                uid="credit-admin", username="credit-admin", password_hash="x", role="admin", department_id=1
            )
            wrong_target = await client.post(url, json={"department_ids": [2]})
            assert wrong_target.status_code == 400
            assert not list((tmp_path / "share_requests").iterdir())

            published = await client.post(url, json={"department_ids": [1], "note": "已核对"})
            assert published.status_code == 200, published.text
            record = published.json()["data"]
            assert record["status"] == "approved"
            assert record["owner_uid"] == "member"
            assert record["reviewer_uid"] == "credit-admin"
            assert record["review_note"] == "管理员直接发布：已核对"
            assert record["published_slug"] == "member-skill"
            assert (shared_root / "member-skill" / "SKILL.md").read_text(encoding="utf-8") == original
            assert (source / "SKILL.md").read_text(encoding="utf-8") == original

            user_scope = {"access_level": "user", "department_ids": [], "user_uids": ["credit-reader"]}
            conflicting_target = await client.post(
                url,
                json={"department_ids": [1], "read_scope": user_scope},
            )
            assert conflicting_target.status_code == 400, conflicting_target.text
            assert "同时" in conflicting_target.json()["detail"]
            cross_department_reader = await client.post(
                url,
                json={"read_scope": {**user_scope, "user_uids": ["risk-reader"]}},
            )
            assert cross_department_reader.status_code == 400, cross_department_reader.text
            published_to_person = await client.post(url, json={"read_scope": user_scope})
            assert published_to_person.status_code == 200, published_to_person.text
            assert published_to_person.json()["data"]["read_scope"] == user_scope
            assert published_to_person.json()["data"]["department_ids"] == []
            assert (shared_root / "member-skill-v2" / "SKILL.md").read_text(encoding="utf-8").endswith("# Owner copy\n")

            current = User(uid="member", username="member", password_hash="x", role="user", department_id=1)
            pending = await client.post("/api/skills/personal/member-skill/share-request")
            assert pending.status_code == 200, pending.text
            current = User(
                uid="credit-admin", username="credit-admin", password_hash="x", role="admin", department_id=1
            )
            existing_request = await client.post(url, json={"department_ids": [1]})
            assert existing_request.status_code == 400
            assert "待审核申请" in existing_request.json()["detail"]

            current = User(uid="super-admin", username="super-admin", password_hash="x", role="superadmin")
            unassigned_publish = await client.post(
                "/api/skills/personal-catalog/unassigned/unassigned-skill/publish",
                json={"department_ids": [1]},
            )
            assert unassigned_publish.status_code == 200, unassigned_publish.text
            assert unassigned_publish.json()["data"]["owner_department_id"] is None
            assert (shared_root / "unassigned-skill" / "SKILL.md").exists()

        async with sessions() as db:
            saved = await db.get(SkillShareRequest, record["id"])
            pending_record = await db.get(SkillShareRequest, pending.json()["data"]["id"])
            skill = await db.get(Skill, 1)
            assert saved.content_hash
            assert pending_record.status == "pending"
            assert skill.source_type == "personal_share"
            assert skill.share_config["read_scope"]["department_ids"] == [1]
            person_skill = await db.get(Skill, 2)
            assert person_skill.share_config["read_scope"] == user_scope
            person_request = await db.get(SkillShareRequest, published_to_person.json()["data"]["id"])
            assert person_request.read_scope == user_scope
    finally:
        await engine.dispose()
        async with admin_engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin_engine.dispose()
