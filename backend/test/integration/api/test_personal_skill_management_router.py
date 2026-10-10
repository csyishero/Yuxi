"""个人 Skill 文件管理的真实 HTTP 与工作区边界。"""

from __future__ import annotations

import io
import shutil
import zipfile
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from server.routers.skill_router import user_skills
from server.utils.auth_middleware import get_db, get_required_user
from yuxi.agents.skills import service
from yuxi.storage.postgres.models_business import User
from yuxi.workspace import paths as workspace_paths
from yuxi.workspace.filesystem import Workspace

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


@pytest.fixture(scope="session", autouse=True)
def ensure_live_api_schema():
    """本文件不访问 PostgreSQL。"""


@pytest.fixture(scope="session", autouse=True)
def cleanup_test_knowledge_resources():
    """本文件没有知识库资源。"""
    yield


@pytest.fixture(scope="session", autouse=True)
def cleanup_test_sandboxes():
    """本文件没有沙盒资源。"""
    yield


async def test_personal_skill_file_management_stays_in_own_workspace(monkeypatch, tmp_path: Path):
    """编辑、新建、导出、删除仅操作本人目录，并拒绝非法路径与链接。"""
    monkeypatch.setattr(workspace_paths, "get_user_data_dir", lambda: tmp_path / "user-data")
    source = service.get_personal_skills_root_dir("alice") / "credit-check"
    source.mkdir(parents=True)
    original = "---\nname: credit-check\ndescription: old\n---\n# Credit\n"
    (source / "SKILL.md").write_text(original, encoding="utf-8")
    (source / "scripts").mkdir()
    script = source / "scripts" / "check.py"
    script.write_text("print('old')\n", encoding="utf-8")
    script.chmod(0o755)
    current = User(uid="alice", username="alice", password_hash="x", role="user")
    app = FastAPI()
    app.include_router(user_skills, prefix="/api")

    async def provide_user():
        """在同一客户端切换认证用户。"""
        return current

    app.dependency_overrides[get_required_user] = provide_user
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        tree = await client.get("/api/skills/personal/credit-check/tree")
        assert tree.status_code == 200, tree.text
        assert {entry["name"] for entry in tree.json()["data"]} == {"SKILL.md", "scripts"}

        wrong_slug = await client.put(
            "/api/skills/personal/credit-check/file",
            json={"path": "SKILL.md", "content": "---\nslug: other\nname: Renamed Skill\ndescription: invalid\n---\n"},
        )
        assert wrong_slug.status_code == 400, wrong_slug.text
        assert (source / "SKILL.md").read_text(encoding="utf-8") == original

        updated = "---\nname: Credit Check\ndescription: revised\n---\n# Revised\n"
        saved_content = "---\nslug: credit-check\nname: Credit Check\ndescription: revised\n---\n# Revised\n"
        saved = await client.put(
            "/api/skills/personal/credit-check/file", json={"path": "SKILL.md", "content": updated}
        )
        assert saved.status_code == 200, saved.text
        personal_skill = (await service.list_personal_skills("alice"))[0]
        assert personal_skill.name == "Credit Check"
        assert personal_skill.slug == "credit-check"
        assert personal_skill.description == "revised"
        assert (source / "SKILL.md").read_text(encoding="utf-8") == saved_content

        created_dir = await client.post(
            "/api/skills/personal/credit-check/file", json={"path": "notes", "is_dir": True}
        )
        assert created_dir.status_code == 200, created_dir.text
        created_file = await client.post(
            "/api/skills/personal/credit-check/file",
            json={"path": "notes/review.md", "content": "# Review\n"},
        )
        assert created_file.status_code == 200, created_file.text
        duplicate = await client.post(
            "/api/skills/personal/credit-check/file",
            json={"path": "notes/review.md", "content": "overwrite"},
        )
        assert duplicate.status_code == 400, duplicate.text
        assert (source / "notes" / "review.md").read_text(encoding="utf-8") == "# Review\n"

        script_save = await client.put(
            "/api/skills/personal/credit-check/file",
            json={"path": "scripts/check.py", "content": "print('new')\n"},
        )
        assert script_save.status_code == 200, script_save.text
        assert script.stat().st_mode & 0o111
        read = await client.get("/api/skills/personal/credit-check/file", params={"path": "scripts/check.py"})
        assert read.status_code == 200, read.text
        assert read.json()["data"]["content"] == "print('new')\n"

        traversal = await client.put(
            "/api/skills/personal/credit-check/file", json={"path": "../outside.txt", "content": "bad"}
        )
        assert traversal.status_code == 400, traversal.text
        root_delete = await client.delete("/api/skills/personal/credit-check/file", params={"path": "SKILL.md"})
        assert root_delete.status_code == 400, root_delete.text

        archive = await client.get("/api/skills/personal/credit-check/export")
        assert archive.status_code == 200, archive.text
        with zipfile.ZipFile(io.BytesIO(archive.content)) as zipped:
            assert zipped.read("credit-check/notes/review.md") == b"# Review\n"
            assert zipped.read("credit-check/SKILL.md") == saved_content.encode()

        outside = tmp_path / "private.txt"
        outside.write_text("secret", encoding="utf-8")
        (source / "notes" / "link.md").symlink_to(outside)
        linked_read = await client.get("/api/skills/personal/credit-check/file", params={"path": "notes/link.md"})
        assert linked_read.status_code == 400, linked_read.text
        linked_export = await client.get("/api/skills/personal/credit-check/export")
        assert linked_export.status_code == 400, linked_export.text
        (source / "notes" / "link.md").unlink()

        current = User(uid="bob", username="bob", password_hash="x", role="user")
        for path in ("tree", "export"):
            denied = await client.get(f"/api/skills/personal/credit-check/{path}")
            assert denied.status_code == 404, denied.text
        denied_write = await client.put(
            "/api/skills/personal/credit-check/file",
            json={"path": "SKILL.md", "content": updated},
        )
        assert denied_write.status_code == 404, denied_write.text
        assert (source / "SKILL.md").read_text(encoding="utf-8") == saved_content

        current = User(uid="alice", username="alice", password_hash="x", role="user")
        removed = await client.delete("/api/skills/personal/credit-check/file", params={"path": "notes"})
        assert removed.status_code == 200, removed.text
        assert not (source / "notes").exists()
        removed_skill = await client.delete("/api/skills/personal/credit-check")
        assert removed_skill.status_code == 200, removed_skill.text
        assert not source.exists()


async def test_personal_skill_dependencies_are_saved_for_owner_and_rejected_for_other_user(monkeypatch, tmp_path: Path):
    """个人依赖通过 HTTP 写回文件，拒绝无效依赖和跨用户写入。"""
    monkeypatch.setattr(workspace_paths, "get_user_data_dir", lambda: tmp_path / "user-data")
    source = service.get_personal_skills_root_dir("alice") / "credit-check"
    source.mkdir(parents=True)
    skill_file = source / "SKILL.md"
    skill_file.write_text("---\nname: credit-check\ndescription: test\n---\n# Credit\n", encoding="utf-8")
    current = User(uid="alice", username="alice", password_hash="x", role="user", department_id=1)

    department_skill = service.Skill(
        slug="department-guide",
        name="Department guide",
        description="test",
        source_type="upload",
        enabled=True,
        share_config={"version": 2, "read_scope": {"access_level": "department", "department_ids": [1]}},
    )

    async def shared_skills(_db, _user):
        return [department_skill]

    async def enabled_mcps(*, db):
        return []

    monkeypatch.setattr(service, "_list_accessible_shared_skills", shared_skills)
    monkeypatch.setattr(service, "get_enabled_mcp_server_slugs", enabled_mcps)
    monkeypatch.setattr(service, "_get_all_tool_names", lambda: ["calculator"])

    app = FastAPI()
    app.include_router(user_skills, prefix="/api")

    async def provide_user():
        return current

    async def provide_db():
        yield None

    app.dependency_overrides[get_required_user] = provide_user
    app.dependency_overrides[get_db] = provide_db
    url = "/api/skills/personal/credit-check/dependencies"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        saved = await client.put(
            url,
            json={"tool_dependencies": ["calculator"], "mcp_dependencies": [], "skill_dependencies": []},
        )
        assert saved.status_code == 200, saved.text
        assert saved.json()["data"]["tool_dependencies"] == ["calculator"]
        assert (await service.list_personal_skills("alice"))[0].tool_dependencies == ["calculator"]
        assert "tool_dependencies:\n- calculator" in skill_file.read_text(encoding="utf-8")

        department_dependency = await client.put(
            url,
            json={
                "tool_dependencies": [],
                "mcp_dependencies": [],
                "skill_dependencies": ["department-guide"],
            },
        )
        assert department_dependency.status_code == 200, department_dependency.text
        assert department_dependency.json()["data"]["skill_dependencies"] == ["department-guide"]
        assert "- department-guide" in skill_file.read_text(encoding="utf-8")

        before = skill_file.read_text(encoding="utf-8")
        invalid = await client.put(url, json={"tool_dependencies": ["unknown-tool"]})
        assert invalid.status_code == 400, invalid.text
        assert skill_file.read_text(encoding="utf-8") == before

        current = User(uid="bob", username="bob", password_hash="x", role="user")
        denied = await client.put(url, json={"tool_dependencies": []})
        assert denied.status_code == 404, denied.text
        assert skill_file.read_text(encoding="utf-8") == before


@pytest.mark.parametrize("is_dir", [False, True])
async def test_create_does_not_recreate_a_removed_personal_skill(monkeypatch, tmp_path: Path, is_dir: bool):
    """并发删除后，新建操作不能重新造出缺少 SKILL.md 的目录。"""
    monkeypatch.setattr(workspace_paths, "get_user_data_dir", lambda: tmp_path / "user-data")
    source = service.get_personal_skills_root_dir("alice") / "credit-check"
    source.mkdir(parents=True)
    (source / "SKILL.md").write_text("---\nname: credit-check\ndescription: test\n---\n", encoding="utf-8")
    original_stat = Workspace.stat_authorized_path

    def remove_after_root_check(self, path, *, root):
        metadata = original_stat(self, path, root=root)
        if path == "/agents/skills/credit-check":
            shutil.rmtree(source)
        return metadata

    monkeypatch.setattr(Workspace, "stat_authorized_path", remove_after_root_check)
    with pytest.raises(ValueError, match="个人 Skill 不存在"):
        await service.create_personal_skill_node(
            "alice", "credit-check", "notes" if is_dir else "notes.md", is_dir=is_dir, content="text"
        )
    assert not source.exists()


async def test_whole_skill_delete_rejects_replaced_parent_symlink(monkeypatch, tmp_path: Path):
    """校验后替换 skills 父目录不能把删除引向外部目录。"""
    monkeypatch.setattr(workspace_paths, "get_user_data_dir", lambda: tmp_path / "user-data")
    skills = service.get_personal_skills_root_dir("alice")
    source = skills / "credit-check"
    source.mkdir(parents=True)
    (source / "SKILL.md").write_text("source", encoding="utf-8")
    external = tmp_path / "external"
    target = external / "credit-check"
    target.mkdir(parents=True)
    (target / "SKILL.md").write_text("keep", encoding="utf-8")
    original_delete = Workspace.delete_authorized_path

    def replace_parent_before_delete(self, path, *, root):
        skills.rename(skills.with_name("skills-original"))
        skills.symlink_to(external, target_is_directory=True)
        return original_delete(self, path, root=root)

    monkeypatch.setattr(Workspace, "delete_authorized_path", replace_parent_before_delete)
    with pytest.raises(PermissionError, match="symlink"):
        await service.delete_personal_skill("alice", "credit-check")
    assert (target / "SKILL.md").read_text(encoding="utf-8") == "keep"


async def test_personal_skill_tree_rejects_oversized_directory(monkeypatch, tmp_path: Path):
    """目录宽度超限时直接拒绝，不加载全部条目。"""
    monkeypatch.setattr(workspace_paths, "get_user_data_dir", lambda: tmp_path / "user-data")
    source = service.get_personal_skills_root_dir("alice") / "credit-check"
    source.mkdir(parents=True)
    for index in range(1001):
        (source / f"note-{index:04}.md").touch()

    with pytest.raises(ValueError, match="directory entry limit exceeded"):
        await service.get_personal_skill_tree("alice", "credit-check")
