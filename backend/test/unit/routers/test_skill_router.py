from __future__ import annotations

import io
import zipfile

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from yuxi.agents.skills import service as skill_service
from yuxi.storage.postgres.models_business import Skill, User

from server.routers.skill_router import skills, user_skills
from server.utils.auth_middleware import get_admin_user, get_db, get_required_user


def _build_app(*, role: str = "admin") -> FastAPI:
    app = FastAPI()
    app.include_router(skills, prefix="/api")
    app.include_router(user_skills, prefix="/api")

    async def fake_db():
        return None

    async def fake_required_user():
        return User(
            username=role,
            uid=role,
            password_hash="x",
            role=role,
            department_id=1,
        )

    async def fake_admin_user():
        if role not in {"admin", "superadmin"}:
            raise HTTPException(status_code=403, detail="需要管理员权限")
        return await fake_required_user()

    app.dependency_overrides[get_db] = fake_db
    app.dependency_overrides[get_required_user] = fake_required_user
    app.dependency_overrides[get_admin_user] = fake_admin_user
    return app


def _skill(
    slug: str = "demo",
    *,
    source_type: str = "upload",
    created_by: str = "admin",
    enabled: bool = True,
    user_uids: list[str] | None = None,
) -> Skill:
    return Skill(
        slug=slug,
        name=slug,
        description="demo skill",
        source_type=source_type,
        dir_path=f"shared/{slug}",
        share_config={
            "version": 2,
            "read_scope": {"access_level": "user", "user_uids": user_uids or [created_by]},
            "manage_scope": {"access_level": "user", "user_uids": user_uids or [created_by]},
        },
        enabled=enabled,
        created_by=created_by,
        updated_by=created_by,
    )


def test_list_visible_skills_route_returns_allowed_levels_and_can_manage(monkeypatch):
    async def fake_list_visible_skills_for_management(_db, user):
        assert user.uid == "admin"
        return [_skill()]

    monkeypatch.setattr(
        "server.routers.skill_router.list_visible_skills_for_management",
        fake_list_visible_skills_for_management,
    )

    client = TestClient(_build_app())
    resp = client.get("/api/system/skills")

    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload["success"] is True
    assert payload["data"][0]["slug"] == "demo"
    assert payload["data"][0]["can_manage"] is True
    assert payload["allowed_access_levels"] == ["global", "department", "user"]


def test_list_visible_skills_route_allows_normal_user_readonly_items(monkeypatch):
    async def fake_list_visible_skills_for_management(_db, user):
        assert user.uid == "user"
        return [
            _skill(slug="owned-disabled", created_by="user", enabled=False),
            _skill(slug="shared", created_by="other", user_uids=["user"]),
        ]

    monkeypatch.setattr(
        "server.routers.skill_router.list_visible_skills_for_management",
        fake_list_visible_skills_for_management,
    )

    client = TestClient(_build_app(role="user"))
    resp = client.get("/api/system/skills")

    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload["success"] is True
    assert [(item["slug"], item["can_manage"]) for item in payload["data"]] == [
        ("owned-disabled", True),
        ("shared", True),
    ]
    assert payload["allowed_access_levels"] == ["user"]


def test_list_accessible_skills_route(monkeypatch):
    async def fake_list_accessible_skills(_db, user):
        assert user.uid == "user"
        return [_skill(created_by="user")]

    monkeypatch.setattr("server.routers.skill_router.list_accessible_skills", fake_list_accessible_skills)

    client = TestClient(_build_app(role="user"))
    resp = client.get("/api/skills/accessible")

    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload["success"] is True
    assert payload["data"][0]["slug"] == "demo"
    assert payload["data"][0]["can_manage"] is True


def test_list_skill_cards_route_scans_personal_source(monkeypatch):
    captured = {}

    async def fake_list_skill_cards(_db, user, *, audience_search=None, audience_department_id=None):
        captured["uid"] = user.uid
        assert audience_search is None
        assert audience_department_id is None
        item = _skill(source_type="personal", created_by="user")
        return [item]

    monkeypatch.setattr("server.routers.skill_router.list_skill_cards_for_user", fake_list_skill_cards)

    client = TestClient(_build_app(role="user"))
    resp = client.get("/api/skills")

    assert resp.status_code == 200, resp.text
    assert "personal_cache" not in resp.json()
    assert captured == {"uid": "user"}


def test_personal_skill_confirm_and_delete_routes(monkeypatch):
    async def fake_confirm(*, draft_id, slugs, operator):
        assert draft_id == "draft-1"
        assert slugs == ["demo-v2"]
        assert operator.uid == "user"
        return [{"slug": "demo", "requested_slug": "demo-v2", "success": True}]

    async def fake_delete(uid, slug):
        assert (uid, slug) == ("user", "demo")

    monkeypatch.setattr("server.routers.skill_router.confirm_personal_skill_install_draft", fake_confirm)
    monkeypatch.setattr("server.routers.skill_router.delete_personal_skill", fake_delete)

    client = TestClient(_build_app(role="user"))
    confirm_resp = client.post(
        "/api/skills/personal/install-drafts/draft-1/confirm",
        json={"slugs": ["demo-v2"]},
    )
    delete_resp = client.delete("/api/skills/personal/demo")

    assert confirm_resp.status_code == 200, confirm_resp.text
    assert confirm_resp.json()["data"][0]["slug"] == "demo"
    assert delete_resp.status_code == 200, delete_resp.text


def test_prepare_skill_upload_route(monkeypatch):
    captured: dict[str, object] = {}

    async def fake_prepare_skill_upload(_db, *, filename, file_bytes, operator):
        captured["filename"] = filename
        captured["file_bytes"] = file_bytes.decode("utf-8")
        captured["operator_uid"] = operator.uid
        return {"draft_id": "draft-1", "items": [{"slug": "demo", "success": True}]}

    monkeypatch.setattr("server.routers.skill_router.prepare_skill_upload", fake_prepare_skill_upload)

    client = TestClient(_build_app(role="user"))
    resp = client.post(
        "/api/skills/import/prepare",
        files={"file": ("SKILL.md", b"---\nname: demo\ndescription: demo skill\n---\n", "text/markdown")},
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["draft_id"] == "draft-1"
    assert captured == {
        "filename": "SKILL.md",
        "file_bytes": "---\nname: demo\ndescription: demo skill\n---\n",
        "operator_uid": "user",
    }


def test_remote_skill_prepare_and_admin_confirm_routes(monkeypatch):
    captured: dict[str, object] = {}

    async def fake_prepare_remote_skill_install(_db, *, source, skills, operator):
        captured["prepare"] = {"source": source, "skills": skills, "operator_uid": operator.uid}
        return {"draft_id": "draft-remote", "items": [{"slug": "frontend-design", "success": True}]}

    async def fake_confirm_skill_install_draft(_db, *, draft_id, share_config, slugs, categories, operator):
        captured["confirm"] = {
            "draft_id": draft_id,
            "share_config": share_config,
            "slugs": slugs,
            "categories": categories,
            "operator_uid": operator.uid,
        }
        return [
            {"slug": "frontend-design", "success": True},
            {"slug": "broken", "success": False, "error": "解析失败"},
        ]

    monkeypatch.setattr("server.routers.skill_router.prepare_remote_skill_install", fake_prepare_remote_skill_install)
    monkeypatch.setattr("server.routers.skill_router.confirm_skill_install_draft", fake_confirm_skill_install_draft)

    client = TestClient(_build_app(role="admin"))
    prepare_resp = client.post(
        "/api/skills/remote/prepare",
        json={"source": "anthropics/skills", "skills": ["frontend-design"]},
    )
    confirm_resp = client.post(
        "/api/skills/install-drafts/draft-remote/confirm",
        json={
            "share_config": {
                "version": 2,
                "read_scope": {"access_level": "user", "user_uids": ["admin"]},
                "manage_scope": None,
            },
            "slugs": ["frontend-design"],
            "categories": {"frontend-design": "development_tools"},
        },
    )

    assert prepare_resp.status_code == 200, prepare_resp.text
    assert confirm_resp.status_code == 200, confirm_resp.text
    assert confirm_resp.json()["summary"] == {"total": 2, "success": 1, "failed": 1}
    assert captured["prepare"] == {
        "source": "anthropics/skills",
        "skills": ["frontend-design"],
        "operator_uid": "admin",
    }
    assert captured["confirm"]["draft_id"] == "draft-remote"
    assert captured["confirm"]["slugs"] == ["frontend-design"]
    assert captured["confirm"]["categories"] == {"frontend-design": "development_tools"}
    assert captured["confirm"]["operator_uid"] == "admin"


def test_normal_user_cannot_confirm_shared_skill_install(monkeypatch):
    async def unexpected_confirm(*_args, **_kwargs):
        raise AssertionError("普通用户不应进入共享 Skill 安装服务")

    monkeypatch.setattr("server.routers.skill_router.confirm_skill_install_draft", unexpected_confirm)

    client = TestClient(_build_app(role="user"))
    response = client.post(
        "/api/skills/install-drafts/draft-remote/confirm",
        json={"share_config": None, "slugs": ["frontend-design"]},
    )

    assert response.status_code == 403


def test_dependency_options_route_checks_manage_permission(monkeypatch):
    captured: dict[str, object] = {}

    async def fake_get_manageable_skill_or_raise(_db, user, slug):
        captured["manageable"] = {"slug": slug, "operator_uid": user.uid}
        return _skill(slug=slug)

    async def fake_get_skill_dependency_options(_db, user, slug=None):
        captured["options"] = {"slug": slug, "operator_uid": user.uid}
        return {"tools": [{"slug": "calculator", "name": "Calculator"}], "mcps": ["mcp-a"], "skills": ["other"]}

    monkeypatch.setattr("server.routers.skill_router.get_manageable_skill_or_raise", fake_get_manageable_skill_or_raise)
    monkeypatch.setattr("server.routers.skill_router.get_skill_dependency_options", fake_get_skill_dependency_options)

    client = TestClient(_build_app())
    resp = client.get("/api/system/skills/dependency-options?slug=demo")

    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["skills"] == ["other"]
    assert captured["manageable"] == {"slug": "demo", "operator_uid": "admin"}
    assert captured["options"] == {"slug": "demo", "operator_uid": "admin"}


def test_skill_tree_and_file_routes_check_management_read_permission(monkeypatch):
    captured: dict[str, object] = {}

    async def fake_get_skill_tree(_db, *, slug, operator):
        captured["tree_slug"] = slug
        captured["tree_operator"] = operator.uid
        return [{"name": "SKILL.md", "path": "SKILL.md", "is_dir": False}]

    async def fake_read_skill_file(_db, *, slug, relative_path, operator):
        captured["file"] = {"slug": slug, "path": relative_path, "operator_uid": operator.uid}
        return {"path": relative_path, "content": "---\nname: demo\n---\n"}

    monkeypatch.setattr("server.routers.skill_router.get_skill_tree", fake_get_skill_tree)
    monkeypatch.setattr("server.routers.skill_router.read_skill_file", fake_read_skill_file)

    client = TestClient(_build_app(role="user"))
    tree_resp = client.get("/api/system/skills/demo/tree")
    file_resp = client.get("/api/system/skills/demo/file?path=SKILL.md")

    assert tree_resp.status_code == 200, tree_resp.text
    assert file_resp.status_code == 200, file_resp.text
    assert captured["tree_slug"] == "demo"
    assert captured["tree_operator"] == "user"
    assert captured["file"] == {"slug": "demo", "path": "SKILL.md", "operator_uid": "user"}


def test_shared_skill_file_projects_private_mcp_frontmatter_and_preserves_it_on_save(monkeypatch, tmp_path):
    """真实文件服务在 HTTP 读写时隐藏私有引用，并保留原文件中的旧依赖。"""

    skill_dir = tmp_path / "demo"
    skill_dir.mkdir()
    source = skill_dir / "SKILL.md"
    original = (
        "---\nslug: demo\nname: demo\ndescription: Demo\n"
        "mcp_dependencies:\n- visible-mcp\n- read-only-mcp\n- private-mcp\n"
        "---\n# Guide\n"
    )
    source.write_text(original, encoding="utf-8")
    (skill_dir / "alias.md").symlink_to(source)
    item = _skill(slug="demo", created_by="user", user_uids=["user"])
    item.dir_path = str(skill_dir)

    class FakeDB:
        async def commit(self):
            pass

    class FakeRepo:
        def __init__(self, _db):
            pass

        async def get_by_slug(self, slug):
            return item if slug == "demo" else None

        async def update_metadata(self, _item, *, name, description, updated_by):
            item.name = name
            item.description = description
            item.updated_by = updated_by
            return item

    async def visible_mcps(_db, _user):
        return {"visible-mcp", "read-only-mcp"}

    async def usable_mcps(_db, _user):
        return {"visible-mcp"}

    monkeypatch.setattr(skill_service, "SkillRepository", FakeRepo)
    monkeypatch.setattr(skill_service, "get_visible_mcp_reference_slugs", visible_mcps)
    monkeypatch.setattr(skill_service, "get_usable_mcp_server_slugs", usable_mcps)

    app = _build_app(role="user")
    app.dependency_overrides[get_db] = lambda: FakeDB()
    client = TestClient(app)
    url = "/api/system/skills/demo/file"
    read = client.get(url, params={"path": "SKILL.md"})
    assert read.status_code == 200, read.text
    projected = read.json()["data"]["content"]
    assert "- visible-mcp" in projected
    assert "- read-only-mcp" in projected
    assert "private-mcp" not in projected
    assert projected.endswith("# Guide\n")
    assert source.read_text(encoding="utf-8") == original
    alias_read = client.get(url, params={"path": "./SKILL.md"})
    assert alias_read.json()["data"]["content"] == projected
    symlink_read = client.get(url, params={"path": "alias.md"})
    assert symlink_read.json()["data"]["content"] == projected
    for path in ("./SKILL.md", "alias.md"):
        denied_delete = client.delete(url, params={"path": path})
        assert denied_delete.status_code == 400, denied_delete.text
    assert source.read_text(encoding="utf-8") == original

    admin = TestClient(_build_app(role="superadmin"))
    admin_read = admin.get(url, params={"path": "SKILL.md"})
    assert admin_read.status_code == 200, admin_read.text
    assert admin_read.json()["data"]["content"] == original

    user_archive = client.get("/api/system/skills/demo/export")
    assert user_archive.status_code == 200, user_archive.text
    with zipfile.ZipFile(io.BytesIO(user_archive.content)) as zipped:
        assert zipped.read("demo/SKILL.md").decode("utf-8") == projected
        assert zipped.read("demo/alias.md").decode("utf-8") == projected
    admin_archive = admin.get("/api/system/skills/demo/export")
    assert admin_archive.status_code == 200, admin_archive.text
    with zipfile.ZipFile(io.BytesIO(admin_archive.content)) as zipped:
        assert zipped.read("demo/SKILL.md").decode("utf-8") == original
    assert source.read_text(encoding="utf-8") == original

    edited = projected.replace("- read-only-mcp\n", "").replace("# Guide\n", "# Revised\n")
    saved = client.put(url, json={"path": "./SKILL.md", "content": edited})
    assert saved.status_code == 200, saved.text
    on_disk = source.read_text(encoding="utf-8")
    assert "- read-only-mcp" not in on_disk
    assert "- private-mcp" in on_disk
    assert on_disk.endswith("# Revised\n")
    assert skill_service.parse_skill_dir_metadata(skill_dir)["mcp_dependencies"] == ["visible-mcp", "private-mcp"]

    forbidden = client.put(
        url,
        json={"path": "SKILL.md", "content": edited.replace("- visible-mcp\n", "- visible-mcp\n- new-mcp\n")},
    )
    assert forbidden.status_code == 400, forbidden.text
    assert source.read_text(encoding="utf-8") == on_disk

    for malformed in (
        "---\nslug: demo\nname: demo\ndescription: Demo\nmcp_dependencies: [private-mcp\n---\n# Guide\n",
        "---\nslug: demo\nname: demo\ndescription: Demo\nmcp_dependencies: private-mcp\n---\n# Guide\n",
    ):
        source.write_text(malformed, encoding="utf-8")
        failed_read = client.get(url, params={"path": "SKILL.md"})
        assert failed_read.status_code == 400, failed_read.text
        assert "private-mcp" not in failed_read.text
        failed_archive = client.get("/api/system/skills/demo/export")
        assert failed_archive.status_code == 400, failed_archive.text
        assert "private-mcp" not in failed_archive.text
        failed_write = client.put(url, json={"path": "SKILL.md", "content": edited})
        assert failed_write.status_code == 400, failed_write.text
        assert source.read_text(encoding="utf-8") == malformed
        assert admin.get(url, params={"path": "SKILL.md"}).json()["data"]["content"] == malformed


def test_skill_export_route_still_checks_manage_permission(monkeypatch, tmp_path):
    captured: dict[str, object] = {}
    export_path = tmp_path / "demo.zip"
    export_path.write_bytes(b"zip")

    async def fake_export_skill_zip(_db, *, slug, operator):
        captured["export_slug"] = slug
        captured["operator_uid"] = operator.uid
        return str(export_path), "demo.zip"

    monkeypatch.setattr("server.routers.skill_router.export_skill_zip", fake_export_skill_zip)

    client = TestClient(_build_app())
    resp = client.get("/api/system/skills/demo/export")

    assert resp.status_code == 200, resp.text
    assert captured["export_slug"] == "demo"
    assert captured["operator_uid"] == "admin"


def test_update_skill_dependencies_route_passes_operator(monkeypatch):
    captured: dict[str, object] = {}

    async def fake_update_skill_dependencies(
        _db,
        *,
        slug,
        tool_dependencies,
        mcp_dependencies,
        skill_dependencies,
        operator,
    ):
        captured["slug"] = slug
        captured["tool_dependencies"] = tool_dependencies
        captured["mcp_dependencies"] = mcp_dependencies
        captured["skill_dependencies"] = skill_dependencies
        captured["operator_uid"] = operator.uid
        return _skill(slug=slug)

    monkeypatch.setattr("server.routers.skill_router.update_skill_dependencies", fake_update_skill_dependencies)

    client = TestClient(_build_app())
    resp = client.put(
        "/api/system/skills/demo/dependencies",
        json={
            "tool_dependencies": ["calculator"],
            "mcp_dependencies": ["mcp-a"],
            "skill_dependencies": ["other-skill"],
        },
    )

    assert resp.status_code == 200, resp.text
    assert captured == {
        "slug": "demo",
        "tool_dependencies": ["calculator"],
        "mcp_dependencies": ["mcp-a"],
        "skill_dependencies": ["other-skill"],
        "operator_uid": "admin",
    }


def test_builtin_routes_require_admin():
    client = TestClient(_build_app(role="user"))

    resp = client.get("/api/system/skills/builtin")

    assert resp.status_code == 403


def test_sync_builtin_skills_route(monkeypatch):
    captured: dict[str, str] = {}

    async def fake_init_builtin_skills(_db, *, created_by):
        captured["created_by"] = created_by
        return [_skill(slug="builtin-demo", source_type="builtin")]

    monkeypatch.setattr("server.routers.skill_router.init_builtin_skills", fake_init_builtin_skills)

    client = TestClient(_build_app())
    resp = client.post("/api/system/skills/builtin/sync")

    assert resp.status_code == 200, resp.text
    assert resp.json()["data"][0]["slug"] == "builtin-demo"
    assert captured == {"created_by": "admin"}
