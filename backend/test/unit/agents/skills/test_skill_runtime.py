from types import SimpleNamespace

import pytest

from yuxi.agents.skills.market import market_skill_visible
import yuxi.agents.skills.runtime as skill_runtime
from yuxi.agents.skills.runtime import (
    build_dependency_bundle,
    build_runtime_skills,
    expand_skill_closure,
    resolve_runtime_skills_for_context,
)
from yuxi.agents.skills.service import ResolvedSkill


def test_withdrawn_market_skill_is_invisible_to_superadmin():
    """下架状态也约束最高权限用户的普通市场入口。"""
    listing = SimpleNamespace(is_published=False, scope="global", department_id=None)
    root = SimpleNamespace(role="superadmin", department_id=1)

    assert market_skill_visible(listing, root) is False


@pytest.fixture(autouse=True)
def _admin_mcp_visibility_by_default(monkeypatch):
    async def visible(_db, _user):
        return None

    monkeypatch.setattr(skill_runtime, "get_visible_mcp_reference_slugs", visible)


def _skill(tmp_path, slug: str, *, dependencies: list[str] | None = None, content: str | None = None):
    source_dir = tmp_path / slug
    source_dir.mkdir()
    (source_dir / "SKILL.md").write_text(content or f"# {slug}", encoding="utf-8")
    return SimpleNamespace(
        slug=slug,
        name=slug.title(),
        description=f"{slug} desc",
        source_scope="shared",
        version="v1",
        content_hash="hash-v1",
        source_dir=source_dir,
        tool_dependencies=[],
        mcp_dependencies=[],
        skill_dependencies=dependencies or [],
    )


def test_market_runtime_uses_pinned_release_directory(tmp_path):
    """市场稳定 slug 的运行路径指向审核版本目录。"""
    item = _skill(tmp_path, "credit-review")
    item.source_scope = "market"
    item.projection_slug = "market-release-123456"
    runtime = build_runtime_skills([item])
    assert runtime["credit-review"]["path"] == "/home/gem/skills/market-release-123456/SKILL.md"


@pytest.mark.asyncio
async def test_resolve_runtime_skills_derives_authorized_scope(monkeypatch):
    """运行时 scope 只保留授权选择，并按依赖闭包区分共享与个人来源。"""

    async def fake_list_accessible_skills(db, user):
        assert db is not None
        assert user is not None
        return [
            SimpleNamespace(
                slug="alpha",
                name="Alpha",
                description="alpha desc",
                source_scope="shared",
                version="v1",
                content_hash="hash-v1",
                source_dir="/tmp/shared/alpha",
                tool_dependencies=[],
                mcp_dependencies=[],
                skill_dependencies=["beta"],
            ),
            SimpleNamespace(
                slug="beta",
                name="Beta",
                description="beta desc",
                source_scope="personal",
                version=None,
                content_hash=None,
                source_dir="/tmp/personal/beta",
                tool_dependencies=[],
                mcp_dependencies=[],
                skill_dependencies=[],
            ),
        ]

    monkeypatch.setattr(skill_runtime, "list_accessible_skills", fake_list_accessible_skills)

    scope = await resolve_runtime_skills_for_context(
        SimpleNamespace(skills=["alpha", "missing"]),
        db=object(),
        user=object(),
    )

    assert scope["context_skills"] == ["alpha"]
    assert scope["effective_skills"] == ["alpha", "beta"]
    assert set(scope["runtime_skills"]) == {"alpha", "beta"}
    assert scope["runtime_skills"]["alpha"]["path"] == "/home/gem/skills/alpha/SKILL.md"
    assert scope["runtime_skills"]["beta"]["path"] == "/home/gem/user-data/agents/skills/beta/SKILL.md"
    assert scope["runtime_skills"]["alpha"]["skills"] == ["beta"]


@pytest.mark.asyncio
async def test_personal_skill_runtime_excludes_unavailable_file_dependencies(monkeypatch, tmp_path):
    """直接改写个人文件也不能装配未知工具、禁用 MCP 或不可访问 Skill。"""
    personal = ResolvedSkill(
        id="personal:demo",
        slug="demo",
        name="Demo",
        description="demo",
        source_type="personal",
        source_scope="personal",
        source_dir=tmp_path / "demo",
        enabled=True,
        created_by="alice",
        share_config=None,
        tool_dependencies=["calculator", "unknown-tool"],
        mcp_dependencies=["reports", "disabled-mcp"],
        skill_dependencies=["base", "private-skill"],
    )
    base = _skill(tmp_path, "base")

    async def accessible(_db, _user):
        return [personal, base]

    async def usable_mcps(db, user):
        assert db is not None
        assert user is not None
        return {"reports"}

    monkeypatch.setattr(skill_runtime, "list_accessible_skills", accessible)
    monkeypatch.setattr(skill_runtime, "get_usable_mcp_server_slugs", usable_mcps)
    monkeypatch.setattr(
        skill_runtime,
        "get_all_tool_instances",
        lambda: [SimpleNamespace(name="calculator")],
    )

    scope = await resolve_runtime_skills_for_context(SimpleNamespace(skills=["demo"]), db=object(), user=object())
    assert scope["runtime_skills"]["demo"]["tools"] == ["calculator"]
    assert scope["runtime_skills"]["demo"]["mcps"] == ["reports"]
    assert scope["runtime_skills"]["demo"]["skills"] == ["base"]


@pytest.mark.asyncio
async def test_shared_skill_runtime_excludes_mcp_without_user_use_permission(monkeypatch, tmp_path):
    """共享 Skill 的 MCP 依赖也受当前运行用户的服务权限约束。"""
    shared = ResolvedSkill(
        id=1,
        slug="shared",
        name="Shared",
        description="Shared skill",
        source_type="upload",
        source_scope="shared",
        source_dir=tmp_path / "shared",
        enabled=True,
        created_by="admin",
        share_config=None,
        tool_dependencies=[],
        mcp_dependencies=["allowed-mcp", "forbidden-mcp"],
        skill_dependencies=[],
    )

    async def accessible(_db, _user):
        return [shared]

    async def usable_mcps(_db, _user):
        return {"allowed-mcp"}

    monkeypatch.setattr(skill_runtime, "list_accessible_skills", accessible)
    monkeypatch.setattr(skill_runtime, "get_usable_mcp_server_slugs", usable_mcps)

    scope = await resolve_runtime_skills_for_context(SimpleNamespace(skills=["shared"]), db=object(), user=object())

    assert scope["runtime_skills"]["shared"]["mcps"] == ["allowed-mcp"]
    assert build_dependency_bundle(["shared"], scope["runtime_skills"])["mcps"] == ["allowed-mcp"]


def test_expand_skill_closure_handles_cycles_missing_and_duplicates():
    """循环、缺失目标和重复依赖保持 fail-safe 且稳定去重。"""
    runtime_skills = {
        "alpha": {"tools": [], "mcps": [], "skills": ["beta", "missing", "beta"]},
        "beta": {"tools": [], "mcps": [], "skills": ["alpha"]},
    }

    assert expand_skill_closure(["alpha", "alpha"], runtime_skills) == ["alpha", "beta"]


def test_dependency_bundle_returns_only_consumed_dependencies():
    """依赖包只暴露 Middleware 消费的工具和 MCP 字段。"""
    runtime_skills = {
        "alpha": {"tools": ["tool-a", "tool-a"], "mcps": ["mcp-a"], "skills": ["beta"]},
        "beta": {"tools": ["tool-b"], "mcps": ["mcp-a", "mcp-b"], "skills": []},
    }

    bundle = build_dependency_bundle(["alpha", "beta"], runtime_skills)

    assert bundle == {"tools": ["tool-a", "tool-b"], "mcps": ["mcp-a", "mcp-b"]}
    assert "skills" not in bundle


@pytest.mark.asyncio
async def test_preload_reads_authorized_dependency_closure(tmp_path, monkeypatch):
    skills = [
        _skill(tmp_path, "alpha", dependencies=["beta"], content="# Alpha\nUSE_ALPHA"),
        _skill(tmp_path, "beta", content="# Beta\nUSE_BETA"),
    ]

    async def fake_list_accessible_skills(_db, _user):
        return skills

    monkeypatch.setattr(skill_runtime, "list_accessible_skills", fake_list_accessible_skills)
    scope = await resolve_runtime_skills_for_context(
        SimpleNamespace(skills=["alpha"], preload_skills=["alpha", "beta", "missing"]),
        db=object(),
        user=object(),
    )

    assert scope["context_preload_skills"] == ["alpha"]
    assert scope["preloaded_skills"] == ["alpha", "beta"]
    assert scope["preloaded_skill_contents"] == {
        "alpha": "# Alpha\nUSE_ALPHA",
        "beta": "# Beta\nUSE_BETA",
    }


@pytest.mark.asyncio
async def test_preload_projects_shared_skill_frontmatter_by_read_without_granting_use(tmp_path, monkeypatch):
    content = (
        "---\nname: shared\ndescription: Shared skill\n"
        "mcp_dependencies: [read-only-mcp, private-mcp]\n---\n"
        "The author can mention private-mcp in this body.\n"
    )
    source_dir = tmp_path / "shared"
    source_dir.mkdir()
    (source_dir / "SKILL.md").write_text(content, encoding="utf-8")
    skill = ResolvedSkill(
        id=1,
        slug="shared",
        name="Shared",
        description="Shared skill",
        source_type="upload",
        source_scope="shared",
        source_dir=source_dir,
        enabled=True,
        created_by="admin",
        share_config=None,
        tool_dependencies=[],
        mcp_dependencies=["read-only-mcp", "private-mcp"],
        skill_dependencies=[],
    )

    async def accessible(_db, _user):
        return [skill]

    async def usable(_db, _user):
        return set()

    async def visible(_db, _user):
        return {"read-only-mcp"}

    monkeypatch.setattr(skill_runtime, "list_accessible_skills", accessible)
    monkeypatch.setattr(skill_runtime, "get_usable_mcp_server_slugs", usable)
    monkeypatch.setattr(skill_runtime, "get_visible_mcp_reference_slugs", visible)
    scope = await resolve_runtime_skills_for_context(
        SimpleNamespace(skills=["shared"], preload_skills=["shared"]), db=object(), user=object()
    )

    preloaded = scope["preloaded_skill_contents"]["shared"]
    frontmatter = preloaded.split("---", 2)[1]
    assert "read-only-mcp" in frontmatter
    assert "private-mcp" not in frontmatter
    assert "private-mcp" in preloaded.split("---", 2)[2]
    assert (skill.source_dir / "SKILL.md").read_text(encoding="utf-8") == content
    assert scope["runtime_skills"]["shared"]["mcps"] == []


@pytest.mark.asyncio
async def test_preload_rejects_symlinked_source_ancestor(tmp_path, monkeypatch):
    real_parent = tmp_path / "real"
    real_parent.mkdir()
    item = _skill(real_parent, "alpha")
    linked_parent = tmp_path / "linked"
    linked_parent.symlink_to(real_parent, target_is_directory=True)
    item.source_dir = linked_parent / "alpha"

    async def fake_list_accessible_skills(_db, _user):
        return [item]

    monkeypatch.setattr(skill_runtime, "list_accessible_skills", fake_list_accessible_skills)

    with pytest.raises(RuntimeError, match="根级 SKILL.md 不可读"):
        await resolve_runtime_skills_for_context(
            SimpleNamespace(skills=["alpha"], preload_skills=["alpha"]),
            db=object(),
            user=object(),
        )


@pytest.mark.asyncio
async def test_manifest_retains_metadata_from_authorized_resolution(tmp_path, monkeypatch):
    """源记录更新后，manifest 仍使用首次解析的版本与内容摘要。"""
    from yuxi.services.agent_run_manifest_service import build_skill_manifest_entries

    item = _skill(tmp_path, "alpha", content="original body")

    async def accessible(db, user):
        return [item]

    monkeypatch.setattr(skill_runtime, "list_accessible_skills", accessible)
    scope = await resolve_runtime_skills_for_context(
        SimpleNamespace(skills=["alpha"], preload_skills=["alpha"]),
        db=object(),
        user=object(),
    )
    item.version, item.content_hash = "v2", "hash-v2"
    (item.source_dir / "SKILL.md").write_text("changed body", encoding="utf-8")
    entries = build_skill_manifest_entries({"skills": ["alpha"]}, scope)
    assert entries[0]["version"] == "v1"
    assert entries[0]["content_hash"] == "hash-v1"
    assert scope["preloaded_skill_contents"]["alpha"] == "original body"
