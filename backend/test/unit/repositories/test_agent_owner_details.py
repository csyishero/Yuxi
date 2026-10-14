"""智能体卡片的创建人和所属部门展示。"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from yuxi.agents.buildin import agent_manager
from yuxi.repositories.agent_repository import AgentRepository, DEFAULT_SHARE_CONFIG, SYSTEM_AGENT_SLUGS
from yuxi.storage.postgres.models_business import Agent, Base, Department, User


@pytest.mark.asyncio
async def test_agent_owner_details_include_current_department_and_preserve_system_identity(monkeypatch):
    """真实关联查询显示创建人，系统智能体不借用其历史 created_by。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(
                lambda sync_connection: Base.metadata.create_all(
                    sync_connection, tables=[Department.__table__, User.__table__]
                )
            )

        async with async_sessionmaker(engine, expire_on_commit=False)() as db:
            department = Department(name="产品部")
            owner = User(uid="owner", username="小王", password_hash="x", role="user", department=department)
            named_system = User(
                uid="human-system", username="系统", password_hash="x", role="user", department=department
            )
            db.add_all([owner, named_system])
            await db.commit()

            monkeypatch.setattr(agent_manager, "get_agent", lambda _backend_id: None)
            repo = AgentRepository(db)
            regular = Agent(
                slug="custom-agent",
                backend_id="ChatbotAgent",
                name="个人智能体",
                created_by="owner",
                share_config=DEFAULT_SHARE_CONFIG,
            )
            system_agents = [
                Agent(
                    slug=slug,
                    backend_id="ChatbotAgent",
                    name="内置智能体",
                    created_by="owner",
                    share_config=DEFAULT_SHARE_CONFIG,
                )
                for slug in SYSTEM_AGENT_SLUGS
            ]
            missing = Agent(
                slug="orphan-agent",
                backend_id="ChatbotAgent",
                name="旧智能体",
                created_by="missing-owner",
                share_config=DEFAULT_SHARE_CONFIG,
            )
            human_system = Agent(
                slug="human-system-agent",
                backend_id="ChatbotAgent",
                name="同名创建人智能体",
                created_by="human-system",
                share_config=DEFAULT_SHARE_CONFIG,
            )

            details = await repo.get_owner_details([regular, *system_agents, missing, human_system])
            assert details == {
                "owner": ("小王", department.id, "产品部"),
                "human-system": ("系统", department.id, "产品部"),
            }

            regular_data = await repo.serialize(regular, user=owner, owner_details=details)
            assert regular_data["owner_name"] == "小王"
            assert regular_data["owner_is_system"] is False
            assert regular_data["owner_department_id"] == department.id
            assert regular_data["owner_department_name"] == "产品部"

            for system in system_agents:
                system_data = await repo.serialize(system, user=owner, owner_details=details)
                assert system_data["owner_name"] == "系统"
                assert system_data["owner_is_system"] is True
                assert system_data["owner_department_id"] is None
                assert system_data["owner_department_name"] is None

            missing_data = await repo.serialize(missing, user=owner, owner_details=details)
            assert missing_data["owner_name"] is None
            assert missing_data["owner_is_system"] is False
            assert missing_data["owner_department_id"] is None
            assert missing_data["owner_department_name"] is None

            human_system_data = await repo.serialize(human_system, user=owner, owner_details=details)
            assert human_system_data["owner_name"] == "系统"
            assert human_system_data["owner_is_system"] is False
            assert human_system_data["owner_department_name"] == "产品部"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_agent_share_department_names_keep_each_scope_and_missing_department(monkeypatch):
    """一次批量查询为读取、编辑和管理范围分别保留部门名称及缺失提示。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(
                lambda sync_connection: Base.metadata.create_all(
                    sync_connection, tables=[Department.__table__, User.__table__]
                )
            )

        async with async_sessionmaker(engine, expire_on_commit=False)() as db:
            product = Department(name="产品部")
            engineering = Department(name="研发部")
            viewer = User(uid="viewer", username="查看者", password_hash="x", role="user", department=product)
            db.add_all([viewer, engineering])
            await db.commit()

            monkeypatch.setattr(agent_manager, "get_agent", lambda _backend_id: None)
            repo = AgentRepository(db)
            missing_id = 999999
            shared = Agent(
                slug="shared-agent",
                backend_id="ChatbotAgent",
                name="部门智能体",
                created_by=viewer.uid,
                share_config={
                    "version": 2,
                    "read_scope": {
                        "access_level": "department",
                        "department_ids": [product.id, engineering.id, missing_id],
                    },
                    "edit_scope": {"access_level": "department", "department_ids": [engineering.id]},
                    "manage_scope": {"access_level": "department", "department_ids": [engineering.id]},
                },
            )
            global_agent = Agent(
                slug="global-agent",
                backend_id="ChatbotAgent",
                name="全局智能体",
                created_by=viewer.uid,
                share_config=DEFAULT_SHARE_CONFIG,
            )

            names = await repo.get_share_department_names([shared, global_agent])
            assert names == {product.id: "产品部", engineering.id: "研发部"}

            shared_data = await repo.serialize(
                shared,
                user=viewer,
                owner_details={viewer.uid: (viewer.username, product.id, product.name)},
                share_department_names=names,
            )
            assert shared_data["read_department_names"] == ["产品部", "研发部", f"已删除部门 #{missing_id}"]
            assert shared_data["edit_department_names"] == ["研发部"]
            assert shared_data["manage_department_names"] == ["研发部"]

            global_data = await repo.serialize(global_agent, user=viewer, share_department_names=names)
            assert global_data["read_department_names"] == []
            assert global_data["edit_department_names"] == []
            assert global_data["manage_department_names"] == []
    finally:
        await engine.dispose()
