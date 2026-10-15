"""Yuxi Schema 版本事实在真实 PostgreSQL 上的集成测试。"""

from __future__ import annotations

import asyncio
import os
import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from langchain_core.tools import StructuredTool
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from yuxi.agents.mcp import service as mcp_service
from yuxi.agents.toolkits import service as tool_service
from yuxi.permissions import ResourcePermissionDenied, normalize_permission_config
from yuxi.storage.postgres.manager import BUSINESS_SCHEMA_VERSION, KNOWLEDGE_SCHEMA_VERSION, PostgresManager
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import Department, MCPServer, User
from yuxi.storage.postgres.models_knowledge import KnowledgeBase

from server.routers.mcp_router import mcp
from server.utils.auth_middleware import get_db, get_required_user

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]

LEGACY_TASK_TABLE_SQL = """
CREATE TABLE tasks (
    id VARCHAR(32) PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    type VARCHAR(64) NOT NULL,
    status VARCHAR(32) NOT NULL,
    progress DOUBLE PRECISION NOT NULL DEFAULT 0,
    message TEXT NOT NULL DEFAULT '',
    payload JSONB,
    result JSONB,
    error TEXT,
    cancel_requested INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW(),
    started_at TIMESTAMP WITHOUT TIME ZONE,
    completed_at TIMESTAMP WITHOUT TIME ZONE
)
"""


@pytest.fixture(scope="session", autouse=True)
def ensure_live_api_schema():
    """本文件自行创建隔离 Schema，不依赖运行中的 API。"""


@pytest.fixture(scope="session", autouse=True)
def cleanup_test_knowledge_resources():
    """隔离 Schema 测试没有 HTTP 资源需要清理。"""
    yield


@pytest.fixture(scope="session", autouse=True)
def cleanup_test_sandboxes():
    """隔离 Schema 测试没有 Sandbox 资源需要清理。"""
    yield


def _scoped_manager(engine) -> PostgresManager:
    """创建不触碰进程单例的隔离 manager。"""
    manager = object.__new__(PostgresManager)
    PostgresManager.__init__(manager)
    manager.async_engine = engine
    manager._initialized = True
    return manager


async def _create_isolated_manager(prefix: str):
    """创建位于独立 PostgreSQL Schema 的 manager 与清理句柄。"""
    schema = f"{prefix}_{uuid.uuid4().hex[:16]}"
    admin_engine = create_async_engine(os.environ["POSTGRES_URL"], pool_pre_ping=True)
    async with admin_engine.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    scoped_engine = create_async_engine(
        os.environ["POSTGRES_URL"],
        pool_pre_ping=True,
        connect_args={"server_settings": {"search_path": schema}},
    )
    return schema, admin_engine, scoped_engine, _scoped_manager(scoped_engine)


async def _drop_isolated_schema(schema: str, admin_engine, scoped_engine) -> None:
    """释放隔离 Schema 及其 engine。"""
    await scoped_engine.dispose()
    async with admin_engine.begin() as connection:
        await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
    await admin_engine.dispose()


async def test_schema_migration_lock_serializes_real_postgres_sessions() -> None:
    """两个 migrator 竞争同一 advisory lock 时只允许一个进入临界区。"""
    engine = create_async_engine(os.environ["POSTGRES_URL"], pool_pre_ping=True)
    manager = _scoped_manager(engine)
    first_entered = asyncio.Event()
    release_first = asyncio.Event()
    second_entered = asyncio.Event()

    async def first_migrator() -> None:
        async with manager.schema_migration_lock():
            first_entered.set()
            await release_first.wait()

    async def second_migrator() -> None:
        await first_entered.wait()
        async with manager.schema_migration_lock():
            second_entered.set()

    first_task = asyncio.create_task(first_migrator())
    second_task = asyncio.create_task(second_migrator())
    try:
        await asyncio.wait_for(first_entered.wait(), timeout=2)
        await asyncio.sleep(0.1)
        assert second_entered.is_set() is False
        release_first.set()
        await asyncio.wait_for(asyncio.gather(first_task, second_task), timeout=2)
        assert second_entered.is_set() is True
    finally:
        release_first.set()
        for task in (first_task, second_task):
            if not task.done():
                task.cancel()
        await asyncio.gather(first_task, second_task, return_exceptions=True)
        await engine.dispose()


async def test_v072_business_converges_current_schema_idempotently() -> None:
    """v0.7.2 发布结构一次补齐当前字段与约束，重复执行保持幂等。"""
    schema, admin_engine, scoped_engine, manager = await _create_isolated_manager("pytest_task_schema")

    try:
        await manager.create_business_tables()
        async with scoped_engine.begin() as connection:
            # v0.7.2 tag 没有这些字段，不能用当前 ORM 预建它们来证明迁移。
            for column in ("prepared_at", "first_output_at", "first_model_request_at"):
                await connection.execute(text(f"ALTER TABLE agent_runs DROP COLUMN {column}"))
            await connection.execute(text("ALTER TABLE agent_runs ADD COLUMN last_event_id VARCHAR(64)"))
            await connection.execute(text("DROP TABLE scheduled_agent_runs"))
            await connection.execute(text("DROP TABLE scheduled_agent_jobs"))
            await connection.execute(text("DROP TABLE tasks"))
            await connection.execute(text(LEGACY_TASK_TABLE_SQL))
            await connection.execute(
                text(
                    "INSERT INTO tasks (id, name, type, status) "
                    "VALUES ('legacy-running', 'legacy', 'knowledge_parse', 'running')"
                )
            )

        await manager.ensure_business_schema()
        await manager.ensure_business_schema()

        async with scoped_engine.connect() as connection:
            task_columns = set(
                (
                    await connection.execute(
                        text(
                            "SELECT column_name FROM information_schema.columns "
                            "WHERE table_schema = :schema AND table_name = 'tasks'"
                        ),
                        {"schema": schema},
                    )
                ).scalars()
            )
            run_columns = set(
                (
                    await connection.execute(
                        text(
                            "SELECT column_name FROM information_schema.columns "
                            "WHERE table_schema = :schema AND table_name = 'agent_runs'"
                        ),
                        {"schema": schema},
                    )
                ).scalars()
            )
            row = (
                await connection.execute(
                    text("SELECT status, error, handler_version, attempt_count FROM tasks WHERE id = 'legacy-running'")
                )
            ).one()
            scheduled_tables = set(
                (
                    await connection.execute(
                        text(
                            "SELECT table_name FROM information_schema.tables "
                            "WHERE table_schema = :schema "
                            "AND table_name IN ('scheduled_agent_jobs', 'scheduled_agent_runs')"
                        ),
                        {"schema": schema},
                    )
                ).scalars()
            )
            scheduled_columns = {
                (row.table_name, row.column_name)
                for row in (
                    await connection.execute(
                        text(
                            "SELECT table_name, column_name FROM information_schema.columns "
                            "WHERE table_schema = :schema "
                            "AND table_name IN ('scheduled_agent_jobs', 'scheduled_agent_runs')"
                        ),
                        {"schema": schema},
                    )
                )
            }
            scheduled_constraints = {
                row.conname: row.definition
                for row in (
                    await connection.execute(
                        text(
                            """
                            SELECT con.conname, pg_get_constraintdef(con.oid) AS definition
                            FROM pg_constraint AS con
                            JOIN pg_namespace AS ns ON ns.oid = con.connamespace
                            WHERE ns.nspname = :schema
                              AND con.conname IN (
                                  'fk_scheduled_agent_jobs_project_uid',
                                  'uq_scheduled_agent_jobs_uid_creation_request',
                                  'scheduled_agent_runs_job_id_fkey',
                                  'uq_scheduled_agent_runs_job_occurrence',
                                  'uq_scheduled_agent_runs_request',
                                  'uq_scheduled_agent_runs_thread'
                              )
                            """
                        ),
                        {"schema": schema},
                    )
                )
            }
            scheduled_indexes = set(
                (
                    await connection.execute(
                        text(
                            "SELECT indexname FROM pg_indexes "
                            "WHERE schemaname = :schema "
                            "AND tablename IN ('scheduled_agent_jobs', 'scheduled_agent_runs')"
                        ),
                        {"schema": schema},
                    )
                ).scalars()
            )

        assert {
            "handler_version",
            "dedupe_key",
            "attempt_count",
            "worker_id",
            "heartbeat_at",
            "lease_expires_at",
            "timeout_seconds",
        } <= task_columns
        assert {
            "prepared_at",
            "first_output_at",
            "first_model_request_at",
            "sandbox_timing",
        } <= run_columns
        assert "last_event_id" not in run_columns
        assert tuple(row) == ("running", None, 0, 0)
        assert scheduled_tables == {"scheduled_agent_jobs", "scheduled_agent_runs"}
        assert {
            ("scheduled_agent_jobs", "creation_request_id"),
            ("scheduled_agent_jobs", "creation_intent_hash"),
            ("scheduled_agent_jobs", "model_spec"),
            ("scheduled_agent_runs", "model_spec"),
        }.issubset(scheduled_columns)
        assert "ON DELETE CASCADE" in scheduled_constraints["fk_scheduled_agent_jobs_project_uid"]
        assert "ON DELETE CASCADE" in scheduled_constraints["scheduled_agent_runs_job_id_fkey"]
        assert (
            "UNIQUE (uid, creation_request_id)" in scheduled_constraints["uq_scheduled_agent_jobs_uid_creation_request"]
        )
        assert {
            "uq_scheduled_agent_runs_job_occurrence",
            "uq_scheduled_agent_runs_request",
            "uq_scheduled_agent_runs_thread",
        }.issubset(scheduled_constraints)
        assert {
            "ix_scheduled_agent_jobs_due",
            "ix_scheduled_agent_runs_job_created",
            "ix_scheduled_agent_runs_dispatching",
        }.issubset(scheduled_indexes)
        assert BUSINESS_SCHEMA_VERSION == 11
    finally:
        await _drop_isolated_schema(schema, admin_engine, scoped_engine)


async def test_skill_share_request_table_migrates_from_previous_business_schema() -> None:
    """旧业务库升级后可持久审核快照，且重复迁移不会重复建表。"""
    schema, admin_engine, scoped_engine, manager = await _create_isolated_manager("pytest_skill_share_schema")
    try:
        await manager.create_business_tables()
        async with scoped_engine.begin() as connection:
            await connection.execute(text("DROP TABLE skill_share_requests"))

        await manager.create_schema_version_table()
        await manager.record_schema_version("business", 8)
        await manager.ensure_skill_share_request_schema()
        async with scoped_engine.begin() as connection:
            await connection.execute(text("ALTER TABLE skill_share_requests DROP COLUMN read_scope"))
            await connection.execute(
                text("ALTER TABLE skill_share_requests ALTER COLUMN owner_department_id SET NOT NULL")
            )
        await manager.ensure_skill_share_request_schema()
        async with scoped_engine.connect() as connection:
            columns = set(
                (
                    await connection.execute(
                        text(
                            "SELECT column_name FROM information_schema.columns "
                            "WHERE table_schema = :schema AND table_name = 'skill_share_requests'"
                        ),
                        {"schema": schema},
                    )
                ).scalars()
            )
            indexes = set(
                (
                    await connection.execute(
                        text(
                            "SELECT indexname FROM pg_indexes "
                            "WHERE schemaname = :schema AND tablename = 'skill_share_requests'"
                        ),
                        {"schema": schema},
                    )
                ).scalars()
            )
        assert {"owner_uid", "owner_department_id", "content_hash", "status", "published_slug", "read_scope"}.issubset(
            columns
        )
        async with scoped_engine.connect() as connection:
            nullable = await connection.scalar(
                text(
                    "SELECT is_nullable FROM information_schema.columns "
                    "WHERE table_schema = :schema AND table_name = 'skill_share_requests' "
                    "AND column_name = 'owner_department_id'"
                ),
                {"schema": schema},
            )
        assert nullable == "YES"
        assert "uq_skill_share_requests_pending" in indexes
        assert BUSINESS_SCHEMA_VERSION == 11
        assert (await manager.get_schema_versions())["business"] == 8
    finally:
        await _drop_isolated_schema(schema, admin_engine, scoped_engine)


async def test_skill_market_schema_migrates_v10_idempotently() -> None:
    """已有共享申请和业务行在市场表创建后保持原值。"""
    schema, admin_engine, scoped_engine, manager = await _create_isolated_manager("pytest_skill_market_schema")
    try:
        await manager.create_business_tables()
        async with scoped_engine.begin() as connection:
            await connection.execute(text("DROP TABLE market_skill_likes"))
            await connection.execute(text("DROP TABLE market_skill_installs"))
            await connection.execute(text("DROP TABLE market_skill_releases"))
            await connection.execute(text("DROP TABLE market_skills"))
            await connection.execute(text("ALTER TABLE skill_share_requests DROP COLUMN channel"))
            await connection.execute(
                text(
                    "CREATE UNIQUE INDEX uq_skill_share_requests_pending "
                    "ON skill_share_requests(owner_uid, personal_slug) WHERE status = 'pending'"
                )
            )

        await manager.ensure_skill_market_schema()
        await manager.ensure_skill_market_schema()
        async with scoped_engine.connect() as connection:
            tables = await connection.scalars(
                text("SELECT table_name FROM information_schema.tables WHERE table_schema = :schema"),
                {"schema": schema},
            )
            channel = await connection.scalar(
                text(
                    "SELECT column_default FROM information_schema.columns "
                    "WHERE table_schema = :schema AND table_name = 'skill_share_requests' AND column_name = 'channel'"
                ),
                {"schema": schema},
            )
        assert {"market_skills", "market_skill_releases", "market_skill_installs", "market_skill_likes"}.issubset(
            set(tables)
        )
        assert "shared" in channel
        async with scoped_engine.connect() as connection:
            columns = await connection.scalar(
                text(
                    "SELECT array_agg(att.attname ORDER BY ord.ordinality) FROM pg_class tbl "
                    "JOIN pg_namespace ns ON ns.oid = tbl.relnamespace "
                    "JOIN pg_index idx ON idx.indrelid = tbl.oid "
                    "JOIN pg_class index_tbl ON index_tbl.oid = idx.indexrelid "
                    "JOIN LATERAL unnest(idx.indkey) WITH ORDINALITY AS ord(attnum, ordinality) ON TRUE "
                    "JOIN pg_attribute att ON att.attrelid = tbl.oid AND att.attnum = ord.attnum "
                    "WHERE ns.nspname = :schema AND index_tbl.relname = 'uq_skill_share_requests_pending' "
                    "GROUP BY index_tbl.relname"
                ),
                {"schema": schema},
            )
        assert columns == ["owner_uid", "personal_slug", "channel"]
    finally:
        await _drop_isolated_schema(schema, admin_engine, scoped_engine)


async def test_skill_share_reason_migrates_v9_rows_without_inventing_history() -> None:
    """v9 历史申请保留审核信息，原因仍为空；新列重复迁移安全。"""
    schema, admin_engine, scoped_engine, manager = await _create_isolated_manager("pytest_skill_share_reason")
    try:
        await manager.create_business_tables()
        async with scoped_engine.begin() as connection:
            await connection.execute(text("ALTER TABLE skill_share_requests DROP COLUMN application_reason"))
            await connection.execute(
                text(
                    "INSERT INTO skill_share_requests "
                    "(id, owner_uid, owner_department_id, personal_slug, name, description, "
                    "content_hash, status, review_note, reviewer_uid, department_ids, created_at, reviewed_at) "
                    "VALUES ('legacy-approved', 'owner', 1, 'credit-check', 'Credit Check', "
                    "'legacy description', 'legacy-hash', 'approved', '已通过', 'reviewer', '[]'::jsonb, NOW(), NOW())"
                )
            )
        await manager.create_schema_version_table()
        await manager.record_schema_version("business", 9)
        await manager.ensure_skill_share_request_schema()

        async with scoped_engine.connect() as connection:
            before = await connection.scalar(
                text(
                    "SELECT COUNT(*) FROM information_schema.columns "
                    "WHERE table_schema = :schema AND table_name = 'skill_share_requests' "
                    "AND column_name = 'application_reason'"
                ),
                {"schema": schema},
            )
        assert before == 0

        await manager.upgrade_skill_share_request_reason_schema()
        await manager.upgrade_skill_share_request_reason_schema()
        async with scoped_engine.connect() as connection:
            row = (
                await connection.execute(
                    text(
                        "SELECT application_reason, status, review_note, reviewer_uid "
                        "FROM skill_share_requests WHERE id = 'legacy-approved'"
                    )
                )
            ).one()
        assert tuple(row) == (None, "approved", "已通过", "reviewer")
        assert (await manager.get_schema_versions())["business"] == 9
    finally:
        await _drop_isolated_schema(schema, admin_engine, scoped_engine)


async def test_mcp_sharing_migration_preserves_legacy_rows() -> None:
    """v8 旧 MCP 行升级后仍为 NULL，重复迁移不扩大或改写权限。"""

    schema, admin_engine, scoped_engine, manager = await _create_isolated_manager("pytest_mcp_sharing")
    try:
        await manager.create_business_tables()
        async with scoped_engine.begin() as connection:
            await connection.execute(text("ALTER TABLE mcp_servers DROP COLUMN share_config"))
            await connection.execute(
                text(
                    "INSERT INTO mcp_servers (slug, name, transport, url, enabled, created_by, updated_by) "
                    "VALUES ('legacy-mcp', 'Legacy MCP', 'streamable_http', "
                    "'https://example.com/mcp', 1, 'admin', 'admin')"
                )
            )

        await manager.ensure_business_schema()
        await manager.ensure_business_schema()

        async with scoped_engine.connect() as connection:
            column_type = await connection.scalar(
                text(
                    "SELECT data_type FROM information_schema.columns "
                    "WHERE table_schema = :schema AND table_name = 'mcp_servers' AND column_name = 'share_config'"
                ),
                {"schema": schema},
            )
            legacy_config = await connection.scalar(
                text("SELECT share_config FROM mcp_servers WHERE slug = 'legacy-mcp'")
            )
            legacy_count = await connection.scalar(text("SELECT COUNT(*) FROM mcp_servers WHERE slug = 'legacy-mcp'"))
        assert column_type == "jsonb"
        assert legacy_config is None
        assert legacy_count == 1
    finally:
        await _drop_isolated_schema(schema, admin_engine, scoped_engine)


async def test_mcp_sharing_authorization_over_http_and_postgres(monkeypatch) -> None:
    """真实 HTTP 与 PostgreSQL 同时验证历史可用、新资源私有、委派和脱敏。"""

    schema, admin_engine, scoped_engine, manager = await _create_isolated_manager("pytest_mcp_http")
    try:
        await manager.create_business_tables()
        session_factory = async_sessionmaker(scoped_engine, expire_on_commit=False)
        async with session_factory() as session:
            session.add_all(
                [
                    Department(id=1, name="研发"),
                    Department(id=2, name="市场"),
                    User(uid="delegate", username="Delegate", role="user", password_hash="x", department_id=1),
                    User(uid="viewer", username="Viewer", role="user", password_hash="x", department_id=1),
                    User(uid="outsider", username="Outsider", role="user", password_hash="x", department_id=2),
                    MCPServer(
                        slug="legacy-mcp",
                        name="Legacy MCP",
                        transport="streamable_http",
                        url="https://secret.example/legacy",
                        headers={"Authorization": "Bearer secret"},
                        enabled=1,
                        created_by="admin",
                        updated_by="admin",
                    ),
                    MCPServer(
                        slug="private-mcp",
                        name="Private MCP",
                        transport="streamable_http",
                        url="https://secret.example/private",
                        headers={"Authorization": "Bearer secret"},
                        enabled=1,
                        share_config={"version": 2, "read_scope": None, "edit_scope": None, "manage_scope": None},
                        created_by="admin",
                        updated_by="admin",
                    ),
                ]
            )
            await session.commit()

        actor = {"user": User(uid="viewer", username="Viewer", role="user", password_hash="x")}

        async def fake_user():
            return actor["user"]

        async def fake_db():
            async with session_factory() as session:
                yield session

        app = FastAPI()
        app.include_router(mcp, prefix="/api")
        app.dependency_overrides[get_required_user] = fake_user
        app.dependency_overrides[get_db] = fake_db

        async def fake_tools(_slug):
            return []

        monkeypatch.setattr("server.routers.mcp_router.get_all_mcp_tools", fake_tools)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            listed = await client.get("/api/system/mcp-servers")
            assert listed.status_code == 200
            assert [item["slug"] for item in listed.json()["data"]] == ["legacy-mcp"]
            assert "secret" not in listed.text

            private = await client.get("/api/system/mcp-servers/private-mcp")
            assert private.status_code == 404

            share_config = {
                "version": 2,
                "read_scope": {"access_level": "department", "department_ids": [1]},
                "edit_scope": {"access_level": "user", "user_uids": ["delegate"]},
                "manage_scope": {"access_level": "user", "user_uids": ["delegate"]},
            }
            normalized_share_config = normalize_permission_config(share_config, strict=True)
            denied = await client.put(
                "/api/system/mcp-servers/private-mcp/sharing", json={"share_config": share_config}
            )
            assert denied.status_code == 404
            hidden_options = await client.get("/api/system/mcp-servers/private-mcp/sharing/options")
            assert hidden_options.status_code == 404

            actor["user"] = User(uid="admin", username="admin", role="admin", password_hash="x")
            malformed = await client.put(
                "/api/system/mcp-servers/private-mcp/sharing",
                json={
                    "share_config": {
                        "version": 2,
                        "read_scope": {"access_level": "user", "user_uids": 1},
                        "edit_scope": None,
                        "manage_scope": None,
                    }
                },
            )
            assert malformed.status_code == 400
            granted = await client.put(
                "/api/system/mcp-servers/private-mcp/sharing", json={"share_config": share_config}
            )
            assert granted.status_code == 200
            admin_options = await client.get("/api/system/mcp-servers/private-mcp/sharing/options")
            assert admin_options.status_code == 200
            assert {item["uid"] for item in admin_options.json()["data"]["users"]} == {"delegate", "viewer", "outsider"}

            actor["user"] = User(uid="delegate", username="Delegate", role="user", password_hash="x", department_id=1)
            detail = await client.get("/api/system/mcp-servers/private-mcp")
            assert detail.status_code == 200
            assert detail.json()["data"]["permission"] == "manage"
            assert "share_config" not in detail.json()["data"]
            assert "secret" not in detail.text

            options = await client.get("/api/system/mcp-servers/private-mcp/sharing/options")
            assert options.status_code == 200
            assert options.json()["data"]["departments"] == [{"id": 1, "name": "研发"}]
            assert {item["uid"] for item in options.json()["data"]["users"]} == {"delegate", "viewer"}
            assert options.json()["data"]["share_config"] == normalized_share_config

            cross_department = await client.put(
                "/api/system/mcp-servers/private-mcp/sharing",
                json={
                    "share_config": {
                        "version": 2,
                        "read_scope": {"access_level": "user", "user_uids": ["outsider"]},
                        "edit_scope": None,
                        "manage_scope": None,
                    }
                },
            )
            assert cross_department.status_code == 403

            saved = await client.put("/api/system/mcp-servers/private-mcp/sharing", json={"share_config": share_config})
            assert saved.status_code == 200
            assert "secret" not in saved.text

            actor["user"] = User(uid="viewer", username="Viewer", role="user", password_hash="x", department_id=1)
            read_only = await client.get("/api/system/mcp-servers/private-mcp")
            assert read_only.status_code == 200
            assert read_only.json()["data"]["permission"] == "read"
            assert "share_config" not in read_only.json()["data"]
            tools = await client.get("/api/system/mcp-servers/private-mcp/tools")
            assert tools.status_code == 200
            assert tools.json()["data"] == []

            actor["user"] = User(uid="admin", username="admin", role="admin", password_hash="x")
            mixed_config = {
                "version": 2,
                "read_scope": {"access_level": "user", "user_uids": ["delegate", "outsider"]},
                "edit_scope": {"access_level": "user", "user_uids": ["delegate", "outsider"]},
                "manage_scope": {"access_level": "user", "user_uids": ["delegate"]},
            }
            assert (
                await client.put("/api/system/mcp-servers/private-mcp/sharing", json={"share_config": mixed_config})
            ).status_code == 200
            actor["user"] = User(uid="delegate", username="Delegate", role="user", password_hash="x", department_id=1)
            detail = await client.get("/api/system/mcp-servers/private-mcp")
            assert "share_config" not in detail.json()["data"]
            options = await client.get("/api/system/mcp-servers/private-mcp/sharing/options")
            assert options.status_code == 200
            assert "outsider" not in options.text
            assert options.json()["data"]["share_config"] == {
                "version": 2,
                "read_scope": {"access_level": "user", "department_ids": [], "user_uids": ["delegate"]},
                "edit_scope": {"access_level": "user", "department_ids": [], "user_uids": ["delegate"]},
                "manage_scope": {"access_level": "user", "department_ids": [], "user_uids": ["delegate"]},
            }
            assert options.json()["data"]["protected_scopes"] == {
                "read_scope": True,
                "edit_scope": True,
                "manage_scope": False,
            }
            projected_config = options.json()["data"]["share_config"]
            rejected_scope_change = {
                **projected_config,
                "read_scope": {"access_level": "department", "department_ids": [1]},
            }
            denied = await client.put(
                "/api/system/mcp-servers/private-mcp/sharing", json={"share_config": rejected_scope_change}
            )
            assert denied.status_code == 403
            projected_config["read_scope"]["user_uids"].append("viewer")
            projected_config["edit_scope"]["user_uids"].append("viewer")
            saved = await client.put(
                "/api/system/mcp-servers/private-mcp/sharing", json={"share_config": projected_config}
            )
            assert saved.status_code == 200
            assert "outsider" not in saved.text
            expected_config = normalize_permission_config(
                {
                    **mixed_config,
                    "read_scope": {"access_level": "user", "user_uids": ["delegate", "viewer", "outsider"]},
                    "edit_scope": {"access_level": "user", "user_uids": ["delegate", "viewer", "outsider"]},
                },
                strict=True,
            )

        async with session_factory() as session:
            persisted = await session.scalar(select(MCPServer).where(MCPServer.slug == "private-mcp"))
            assert persisted.share_config == expected_config
    finally:
        await _drop_isolated_schema(schema, admin_engine, scoped_engine)


async def test_assembled_mcp_tool_rechecks_committed_postgres_permissions(monkeypatch) -> None:
    """已装配工具每次调用都读取真实 PostgreSQL 的最新授权与开关。"""

    schema, admin_engine, scoped_engine, manager = await _create_isolated_manager("pytest_mcp_guard")
    try:
        await manager.create_business_tables()
        session_factory = async_sessionmaker(scoped_engine, expire_on_commit=False)

        @asynccontextmanager
        async def session_context():
            async with session_factory() as session:
                yield session

        monkeypatch.setattr(pg_manager, "get_async_session_context", session_context)
        async with session_factory() as session:
            session.add_all(
                [
                    User(uid="guard-user", username="Guard User", role="user", password_hash="x"),
                    MCPServer(
                        slug="guard-mcp",
                        name="Guard MCP",
                        transport="streamable_http",
                        url="https://example.invalid/mcp",
                        enabled=1,
                        created_by="admin",
                        updated_by="admin",
                    ),
                ]
            )
            await session.commit()

        calls = []

        async def remote_call(query: str) -> str:
            calls.append(query)
            return f"remote:{query}"

        remote_tool = StructuredTool.from_function(
            coroutine=remote_call,
            name="remote_tool",
            description="Remote test tool",
        )

        async def load_tools(slug):
            assert slug == "guard-mcp"
            return [remote_tool]

        monkeypatch.setattr(mcp_service, "get_enabled_mcp_tools", load_tools)
        monkeypatch.setattr(tool_service, "get_tool_instances_by_category", lambda _category: [])
        (guarded_tool,) = await tool_service.resolve_configured_runtime_tools(
            SimpleNamespace(uid="guard-user", tools=[], mcps=["guard-mcp"], _skill_runtime_snapshot={})
        )
        assert await guarded_tool.ainvoke({"query": "allowed"}) == "remote:allowed"

        async with session_factory() as session:
            server = await session.scalar(select(MCPServer).where(MCPServer.slug == "guard-mcp"))
            server.share_config = {
                "version": 2,
                "read_scope": {"access_level": "global"},
                "edit_scope": None,
                "manage_scope": None,
            }
            await session.commit()
        with pytest.raises(ResourcePermissionDenied):
            await guarded_tool.ainvoke({"query": "revoked"})

        async with session_factory() as session:
            server = await session.scalar(select(MCPServer).where(MCPServer.slug == "guard-mcp"))
            server.share_config = None
            server.disabled_tools = ["remote_tool"]
            await session.commit()
        with pytest.raises(ResourcePermissionDenied):
            await guarded_tool.ainvoke({"query": "tool-disabled"})

        async with session_factory() as session:
            server = await session.scalar(select(MCPServer).where(MCPServer.slug == "guard-mcp"))
            server.disabled_tools = []
            server.enabled = 0
            await session.commit()
        with pytest.raises(ResourcePermissionDenied):
            await guarded_tool.ainvoke({"query": "server-disabled"})
        assert calls == ["allowed"]
    finally:
        await _drop_isolated_schema(schema, admin_engine, scoped_engine)


async def test_release_upgrade_adds_audit_columns_idempotently() -> None:
    """发布版缺失的 Trace 与 Message 审计列由完整升级补齐，且可安全重放。"""
    schema, admin_engine, scoped_engine, manager = await _create_isolated_manager("pytest_audit_schema")
    try:
        await manager.create_business_tables()
        async with scoped_engine.begin() as connection:
            await connection.execute(text("ALTER TABLE agent_runs DROP COLUMN langfuse_trace_id"))
            await connection.execute(text("ALTER TABLE agent_runs DROP COLUMN sandbox_timing"))
            audit_columns = (
                "operation_id",
                "started_at",
                "finished_at",
                "duration_ms",
                "sequence",
                "execution_status",
                "usage",
            )
            for column in audit_columns:
                await connection.execute(text(f"ALTER TABLE messages DROP COLUMN {column}"))

        await manager.ensure_business_schema()
        async with scoped_engine.begin() as connection:
            await connection.execute(text("DROP INDEX uq_messages_run_role_operation_id"))
            await connection.execute(
                text(
                    "CREATE UNIQUE INDEX uq_messages_run_operation_id "
                    "ON messages(run_id, operation_id) WHERE operation_id IS NOT NULL"
                )
            )
        await manager.ensure_business_schema()

        async with scoped_engine.connect() as connection:
            columns = set(
                (
                    await connection.execute(
                        text(
                            "SELECT table_name, column_name FROM information_schema.columns "
                            "WHERE table_schema = :schema AND table_name IN ('agent_runs', 'messages')"
                        ),
                        {"schema": schema},
                    )
                ).all()
            )
            audit_indexes = {
                name: definition
                for name, definition in (
                    await connection.execute(
                        text(
                            "SELECT indexname, indexdef FROM pg_indexes "
                            "WHERE schemaname = :schema AND tablename = 'messages' "
                            "AND indexname LIKE 'uq_messages_run%operation_id'"
                        ),
                        {"schema": schema},
                    )
                ).all()
            }
        assert ("agent_runs", "langfuse_trace_id") in columns
        assert ("agent_runs", "sandbox_timing") in columns
        assert {("messages", column) for column in audit_columns} <= columns
        assert "uq_messages_run_operation_id" not in audit_indexes
        assert "(run_id, role, operation_id)" in audit_indexes["uq_messages_run_role_operation_id"]
    finally:
        await _drop_isolated_schema(schema, admin_engine, scoped_engine)


async def test_knowledge_v1_to_v2_adds_file_attempt_owner_idempotently() -> None:
    """知识 schema 相邻升级为文件中间态增加 Task attempt fencing。"""
    schema, admin_engine, scoped_engine, manager = await _create_isolated_manager("pytest_knowledge_schema")
    try:
        async with scoped_engine.begin() as connection:
            await connection.execute(
                text(
                    "CREATE TABLE knowledge_files ("
                    "id SERIAL PRIMARY KEY, file_id VARCHAR(64) NOT NULL, status VARCHAR(32), "
                    "error_message TEXT, updated_at TIMESTAMPTZ)"
                )
            )
            await connection.execute(
                text("INSERT INTO knowledge_files (file_id, status) VALUES ('legacy-file', 'parsing')")
            )

        await manager.upgrade_knowledge_schema_v1_to_v2()
        await manager.upgrade_knowledge_schema_v1_to_v2()

        async with scoped_engine.connect() as connection:
            columns = set(
                (
                    await connection.execute(
                        text(
                            "SELECT column_name FROM information_schema.columns "
                            "WHERE table_schema = :schema AND table_name = 'knowledge_files'"
                        ),
                        {"schema": schema},
                    )
                ).scalars()
            )
            legacy = (
                await connection.execute(
                    text("SELECT status, error_message FROM knowledge_files WHERE file_id = 'legacy-file'")
                )
            ).one()
        assert {"processing_task_id", "processing_owner"} <= columns
        assert tuple(legacy) == (
            "error_parsing",
            "service_interrupted: 旧执行实例中断，处理结果未知，请重试",
        )
        assert KNOWLEDGE_SCHEMA_VERSION == 2
    finally:
        await _drop_isolated_schema(schema, admin_engine, scoped_engine)


async def test_knowledge_timestamp_defaults_match_database_clock_in_non_utc_session() -> None:
    """带时区字段不依赖 PostgreSQL 会话时区解释无时区 UTC。"""
    schema, admin_engine, scoped_engine, manager = await _create_isolated_manager("pytest_knowledge_clock")
    try:
        await manager.create_knowledge_tables()
        session_factory = async_sessionmaker(scoped_engine, expire_on_commit=False)
        async with session_factory.begin() as session:
            await session.execute(text("SET TIME ZONE 'Asia/Shanghai'"))
            database = KnowledgeBase(kb_id="clock-kb", name="clock", kb_type="milvus")
            session.add(database)
            await session.flush()
            database_now = await session.scalar(text("SELECT clock_timestamp()"))

        async with session_factory() as verify_session:
            await verify_session.execute(text("SET TIME ZONE 'UTC'"))
            persisted_at = await verify_session.scalar(
                text("SELECT created_at FROM knowledge_bases WHERE kb_id = 'clock-kb'")
            )

        assert persisted_at.tzinfo is not None
        assert abs((persisted_at - database_now).total_seconds()) < 2
    finally:
        await _drop_isolated_schema(schema, admin_engine, scoped_engine)


async def test_unversioned_knowledge_baseline_adds_timestamp_before_owner_convergence() -> None:
    """未版本化的旧表缺少 updated_at 时仍能收敛中间态。"""
    schema, admin_engine, scoped_engine, manager = await _create_isolated_manager("pytest_knowledge_baseline")
    try:
        await manager.create_knowledge_tables()
        async with scoped_engine.begin() as connection:
            await connection.execute(
                text("INSERT INTO knowledge_bases (kb_id, name, kb_type) VALUES ('legacy-kb', 'legacy', 'milvus')")
            )
            await connection.execute(
                text(
                    "INSERT INTO knowledge_files (file_id, kb_id, filename, status) "
                    "VALUES ('legacy-file', 'legacy-kb', 'legacy.txt', 'indexing')"
                )
            )
            await connection.execute(text("ALTER TABLE knowledge_files DROP COLUMN processing_task_id"))
            await connection.execute(text("ALTER TABLE knowledge_files DROP COLUMN processing_owner"))
            await connection.execute(text("ALTER TABLE knowledge_files DROP COLUMN updated_at"))

        await manager.create_knowledge_tables()
        await manager.ensure_knowledge_schema()

        async with scoped_engine.connect() as connection:
            row = (
                await connection.execute(
                    text(
                        "SELECT status, error_message, updated_at, processing_task_id, processing_owner "
                        "FROM knowledge_files WHERE file_id = 'legacy-file'"
                    )
                )
            ).one()
        assert row.status == "error_indexing"
        assert row.error_message == "service_interrupted: 旧执行实例中断，处理结果未知，请重试"
        assert row.updated_at is not None
        assert row.processing_task_id is None
        assert row.processing_owner is None
    finally:
        await _drop_isolated_schema(schema, admin_engine, scoped_engine)


async def test_unversioned_baseline_repairs_existing_legacy_task_table() -> None:
    """未版本化数据库的 create_all + ensure 路径必须补齐旧 tasks 表。"""
    schema, admin_engine, scoped_engine, manager = await _create_isolated_manager("pytest_task_baseline")

    try:
        await manager.create_business_tables()
        async with scoped_engine.begin() as connection:
            await connection.execute(text("DROP TABLE tasks"))
            await connection.execute(text(LEGACY_TASK_TABLE_SQL))
            await connection.execute(
                text(
                    "INSERT INTO tasks (id, name, type, status) "
                    "VALUES ('legacy-pending', 'legacy', 'knowledge_parse', 'pending')"
                )
            )

        await manager.ensure_business_schema()

        async with scoped_engine.connect() as connection:
            row = (
                await connection.execute(
                    text(
                        "SELECT status, error, handler_version, lease_expires_at FROM tasks WHERE id = 'legacy-pending'"
                    )
                )
            ).one()
        assert tuple(row) == ("pending", None, 0, None)
    finally:
        await _drop_isolated_schema(schema, admin_engine, scoped_engine)


async def test_schema_version_is_persisted_and_runtime_validation_fails_closed() -> None:
    """版本表缺失、错误和正确三种状态必须形成精确启动结论。"""
    schema, admin_engine, scoped_engine, manager = await _create_isolated_manager("pytest_schema_version")

    try:
        with pytest.raises(RuntimeError, match="business=missing"):
            await manager.require_current_schema()

        await manager.create_schema_version_table()
        await manager.record_schema_version("business", BUSINESS_SCHEMA_VERSION + 1)
        with pytest.raises(RuntimeError, match=f"business={BUSINESS_SCHEMA_VERSION + 1}"):
            await manager.require_current_schema()

        await manager.record_schema_version("business", BUSINESS_SCHEMA_VERSION)
        with pytest.raises(RuntimeError, match="knowledge=missing"):
            await manager.require_current_schema()

        await manager.record_schema_version("knowledge", KNOWLEDGE_SCHEMA_VERSION)
        await manager.require_current_schema()
        assert await manager.get_schema_versions() == {
            "business": BUSINESS_SCHEMA_VERSION,
            "knowledge": KNOWLEDGE_SCHEMA_VERSION,
        }
    finally:
        await _drop_isolated_schema(schema, admin_engine, scoped_engine)
