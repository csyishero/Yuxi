"""管理员个人 Skill 目录的 HTTP 权限与部门隔离。"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from server.routers.skill_router import user_skills
from server.utils.auth_middleware import get_db, get_required_user
from yuxi.agents.skills import service
from yuxi.repositories.user_repository import UserRepository
from yuxi.storage.postgres.models_business import User
from yuxi.workspace import paths as workspace_paths

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


@pytest.fixture(scope="session", autouse=True)
def ensure_live_api_schema():
    """本文件使用内存 ASGI 客户端。"""


@pytest.fixture(scope="session", autouse=True)
def cleanup_test_knowledge_resources():
    """本文件不创建知识库资源。"""
    yield


@pytest.fixture(scope="session", autouse=True)
def cleanup_test_sandboxes():
    """本文件不创建沙盒资源。"""
    yield


async def test_personal_skill_catalog_scopes_department_admin_and_reads_selected_owner(monkeypatch, tmp_path: Path):
    """普通用户被拒，部门管理员只能访问本部门，超级管理员可跨部门查询。"""
    monkeypatch.setattr(workspace_paths, "get_user_data_dir", lambda: tmp_path / "user-data")
    content_by_uid = {"alice": "# Alice\n", "bob": "# Bob\n"}
    owners = []
    for index, (uid, content) in enumerate(content_by_uid.items(), start=1):
        source = service.get_personal_skills_root_dir(uid) / "same-skill"
        source.mkdir(parents=True)
        (source / "SKILL.md").write_text(f"---\nname: same-skill\ndescription: {uid}\n---\n{content}", encoding="utf-8")
        owners.append(User(id=index, uid=uid, username=uid, department_id=index, is_deleted=0))

    async def list_page(self, *, offset, limit, department_id=None, role=None, search=None):
        """按真实查询参数模拟有效用户分页。"""
        filtered = [
            owner
            for owner in owners
            if not owner.is_deleted
            and (department_id is None or owner.department_id == department_id)
            and (not search or search in owner.username)
        ]
        return [(owner, f"部门{owner.department_id}") for owner in filtered[offset : offset + limit]], len(filtered)

    async def get_owner(self, db, uid):
        """返回指定有效用户。"""
        return next((owner for owner in owners if owner.uid == uid), None)

    monkeypatch.setattr(UserRepository, "list_page_with_department", list_page)
    monkeypatch.setattr(UserRepository, "get_by_uid_with_db", get_owner)
    current = User(uid="viewer", username="viewer", role="user")
    app = FastAPI()
    app.include_router(user_skills, prefix="/api")

    async def provide_user():
        """用同一客户端切换用户身份。"""
        return current

    async def provide_db():
        """目录测试的用户查询由 repository 测试替身提供。"""
        yield object()

    app.dependency_overrides[get_required_user] = provide_user
    app.dependency_overrides[get_db] = provide_db
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        denied = await client.get("/api/skills/personal-catalog")
        assert denied.status_code == 403
        denied_file = await client.get("/api/skills/personal-catalog/bob/same-skill/file", params={"path": "SKILL.md"})
        assert denied_file.status_code == 403

        current.role = "admin"
        current.department_id = 1
        department_catalog = await client.get("/api/skills/personal-catalog", params={"owner_search": "alice"})
        assert [item["owner_uid"] for item in department_catalog.json()["data"]["items"]] == ["alice"]
        forbidden_filter = await client.get("/api/skills/personal-catalog", params={"department_id": 2})
        assert forbidden_filter.status_code == 404
        forbidden_file = await client.get(
            "/api/skills/personal-catalog/bob/same-skill/file", params={"path": "SKILL.md"}
        )
        assert forbidden_file.status_code == 404
        own_department_file = await client.get(
            "/api/skills/personal-catalog/alice/same-skill/file", params={"path": "SKILL.md"}
        )
        assert own_department_file.status_code == 200

        current.role = "superadmin"
        catalog = await client.get("/api/skills/personal-catalog", params={"department_id": 2, "owner_search": "bob"})
        assert catalog.status_code == 200, catalog.text
        assert [(item["owner_uid"], item["owner_department_name"]) for item in catalog.json()["data"]["items"]] == [
            ("bob", "部门2")
        ]
        assert catalog.json()["data"]["next_offset"] is None

        first_page = await client.get("/api/skills/personal-catalog", params={"limit": 1})
        assert first_page.json()["data"]["next_offset"] == 1
        second_page = await client.get("/api/skills/personal-catalog", params={"limit": 1, "offset": 1})
        assert [item["owner_uid"] for item in second_page.json()["data"]["items"]] == ["bob"]

        file_result = await client.get("/api/skills/personal-catalog/bob/same-skill/file", params={"path": "SKILL.md"})
        assert file_result.status_code == 200, file_result.text
        assert "# Bob" in file_result.json()["data"]["content"]
        assert "# Alice" not in file_result.json()["data"]["content"]

        owners[1].is_deleted = 1
        hidden = await client.get("/api/skills/personal-catalog", params={"owner_search": "bob"})
        assert hidden.json()["data"]["items"] == []
        deleted = await client.get("/api/skills/personal-catalog/bob/same-skill/file", params={"path": "SKILL.md"})
        assert deleted.status_code == 404


async def test_personal_skill_catalog_pages_cards_across_owners_and_filters_before_paging(monkeypatch, tmp_path: Path):
    """同一人的后续技能继续占用下一页，搜索和部门限制先于卡片偏移生效。"""
    monkeypatch.setattr(workspace_paths, "get_user_data_dir", lambda: tmp_path / "user-data")
    owners = [
        User(id=1, uid="alice", username="alice", department_id=1, is_deleted=0),
        User(id=2, uid="bob", username="bob", department_id=2, is_deleted=0),
    ]
    for owner, slugs in [(owners[0], ["alpha", "beta", "charlie"]), (owners[1], ["delta"])]:
        for slug in slugs:
            source = service.get_personal_skills_root_dir(str(owner.uid)) / slug
            source.mkdir(parents=True)
            (source / "SKILL.md").write_text(
                f"---\nname: {slug}\ndescription: {slug} example\n---\n# {slug}\n", encoding="utf-8"
            )

    async def list_page(self, *, offset, limit, department_id=None, role=None, search=None):
        """按固定的有效用户顺序返回一页。"""
        filtered = [
            owner
            for owner in owners
            if (department_id is None or owner.department_id == department_id)
            and (not search or search in owner.username)
        ]
        return [(owner, f"部门{owner.department_id}") for owner in filtered[offset : offset + limit]], len(filtered)

    monkeypatch.setattr(UserRepository, "list_page_with_department", list_page)
    current = User(uid="viewer", username="viewer", role="superadmin")
    app = FastAPI()
    app.include_router(user_skills, prefix="/api")

    async def provide_user():
        """在测试中固定管理员身份。"""
        return current

    async def provide_db():
        """用户目录由本测试的仓储替身提供。"""
        yield object()

    app.dependency_overrides[get_required_user] = provide_user
    app.dependency_overrides[get_db] = provide_db
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        original_parser = service.parse_skill_dir_metadata
        parsed_slugs = []

        def counted_parser(path):
            """记录首页实际解析的个人技能。"""
            parsed_slugs.append(path.name)
            return original_parser(path)

        monkeypatch.setattr(service, "parse_skill_dir_metadata", counted_parser)
        legacy_explicit = await client.get("/api/skills/personal-catalog", params={"limit": 1})
        assert [item["slug"] for item in legacy_explicit.json()["data"]["items"]] == ["alpha", "beta", "charlie"]
        assert legacy_explicit.json()["data"]["next_offset"] == 1
        legacy_next = await client.get("/api/skills/personal-catalog", params={"limit": 1, "offset": 1})
        assert [item["slug"] for item in legacy_next.json()["data"]["items"]] == ["delta"]
        assert legacy_next.json()["data"]["next_offset"] is None
        parsed_slugs.clear()

        first = await client.get("/api/skills/personal-catalog", params={"card_page": True, "limit": 2})
        assert parsed_slugs == ["alpha", "beta", "charlie"]
        second = await client.get("/api/skills/personal-catalog", params={"card_page": True, "limit": 2, "offset": 2})
        assert [(item["owner_uid"], item["slug"]) for item in first.json()["data"]["items"]] == [
            ("alice", "alpha"),
            ("alice", "beta"),
        ]
        assert first.json()["data"]["next_offset"] == 2
        assert [(item["owner_uid"], item["slug"]) for item in second.json()["data"]["items"]] == [
            ("alice", "charlie"),
            ("bob", "delta"),
        ]
        assert second.json()["data"]["next_offset"] is None

        filtered = await client.get(
            "/api/skills/personal-catalog", params={"card_page": True, "limit": 1, "search": "delta"}
        )
        assert [(item["owner_uid"], item["slug"]) for item in filtered.json()["data"]["items"]] == [("bob", "delta")]
        assert filtered.json()["data"]["next_offset"] is None

        owner_match = await client.get(
            "/api/skills/personal-catalog", params={"card_page": True, "limit": 2, "search": "alice"}
        )
        assert [item["slug"] for item in owner_match.json()["data"]["items"]] == ["alpha", "beta"]
        assert owner_match.json()["data"]["next_offset"] == 2

        current.uid = "alice"
        others_only = await client.get(
            "/api/skills/personal-catalog", params={"card_page": True, "limit": 2, "exclude_own": True}
        )
        assert [(item["owner_uid"], item["slug"]) for item in others_only.json()["data"]["items"]] == [("bob", "delta")]
        assert others_only.json()["data"]["next_offset"] is None
        current.uid = "viewer"

        current.role = "admin"
        current.department_id = 1
        own_department = await client.get(
            "/api/skills/personal-catalog", params={"card_page": True, "limit": 2, "offset": 2}
        )
        assert [item["slug"] for item in own_department.json()["data"]["items"]] == ["charlie"]

        legacy = await client.get("/api/skills/personal-catalog")
        assert [item["slug"] for item in legacy.json()["data"]["items"]] == ["alpha", "beta", "charlie"]
