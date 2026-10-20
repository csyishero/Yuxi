"""市场 Skill 响应按当前用户 MCP 可见范围投影。"""

from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from yuxi.agents.skills import market
from yuxi.services import mcp_reference_service as references
from yuxi.workspace import paths as workspace_paths
from yuxi.storage.postgres.models_business import (
    Base,
    Department,
    MarketSkill,
    MarketSkillInstall,
    MarketSkillLike,
    MarketSkillRelease,
    Skill,
    SkillShareRequest,
    User,
)


@pytest_asyncio.fixture
async def market_case(monkeypatch, tmp_path):
    """建立两个全员可见但含私有 MCP 引用的市场版本。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda conn: Base.metadata.create_all(
                conn,
                tables=[
                    Department.__table__,
                    User.__table__,
                    Skill.__table__,
                    SkillShareRequest.__table__,
                    MarketSkill.__table__,
                    MarketSkillRelease.__table__,
                    MarketSkillInstall.__table__,
                    MarketSkillLike.__table__,
                ],
            )
        )
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(market, "get_skill_data_dir", lambda: tmp_path)
    monkeypatch.setattr(workspace_paths, "get_user_data_dir", lambda: tmp_path)

    originals = {}
    for slug in ("one", "two"):
        source = tmp_path / "market_releases" / slug
        source.mkdir(parents=True)
        original = (
            f"---\nname: {slug}\ndescription: test\n"
            "mcp_dependencies: [visible-mcp, private-mcp]\n"
            "---\n# Guide mentions private-mcp in the body\n"
        )
        (source / "SKILL.md").write_text(original, encoding="utf-8")
        (source / "notes.md").write_text("private-mcp in another file\n", encoding="utf-8")
        originals[slug] = original

    calls = {"count": 0}

    async def mcp_servers(_db):
        """提供一个可见和一个仅他人可见的 MCP。"""
        calls["count"] += 1
        return [
            SimpleNamespace(
                slug="visible-mcp",
                share_config={
                    "version": 2,
                    "read_scope": {"access_level": "global", "department_ids": [], "user_uids": []},
                    "edit_scope": None,
                    "manage_scope": None,
                },
            ),
            SimpleNamespace(
                slug="private-mcp",
                share_config={"version": 2, "read_scope": None, "edit_scope": None, "manage_scope": None},
            ),
        ]

    monkeypatch.setattr(references, "get_all_mcp_servers", mcp_servers)
    try:
        async with sessions() as db:
            db.add(Department(id=1, name="Demo"))
            viewer = User(uid="viewer", username="Viewer", password_hash="x", role="user", department_id=1)
            admin = User(uid="admin", username="Admin", password_hash="x", role="admin", department_id=1)
            author = User(uid="author", username="Author", password_hash="x", role="user", department_id=1)
            db.add_all([viewer, admin, author])
            for slug in ("one", "two"):
                db.add(
                    MarketSkill(
                        id=f"listing-{slug}",
                        slug=slug,
                        owner_uid="author",
                        personal_slug=slug,
                        scope="global",
                        latest_release_id=f"release-{slug}",
                    )
                )
                db.add(
                    MarketSkillRelease(
                        id=f"release-{slug}",
                        market_skill_id=f"listing-{slug}",
                        version=1,
                        name=slug,
                        description="test",
                        category="other",
                        dir_path=f"market_releases/{slug}",
                        content_hash="hash",
                        tool_dependencies=[],
                        mcp_dependencies=["visible-mcp", "private-mcp"],
                        skill_dependencies=[],
                        request_id=f"request-{slug}",
                    )
                )
            db.add_all(
                [
                    MarketSkillLike(uid="viewer", market_skill_id="listing-one"),
                    MarketSkillLike(uid="author", market_skill_id="listing-one"),
                    MarketSkillLike(uid="viewer", market_skill_id="listing-two"),
                ]
            )
            await db.commit()
            yield db, viewer, admin, calls, originals, tmp_path
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_market_cards_hide_private_mcp_across_list_detail_and_rankings(market_case):
    """市场条目可见不意味着其私有 MCP 引用也可见。"""
    db, viewer, admin, calls, _originals, _root = market_case

    cards = await market.list_market_skills(db, user=viewer)
    assert {card["slug"]: card["mcp_dependencies"] for card in cards} == {
        "one": ["visible-mcp"],
        "two": ["visible-mcp"],
    }
    assert calls["count"] == 1

    rankings = await market.list_market_rankings(db, user=viewer, metric="likes", scope="company")
    assert {card["slug"]: card["mcp_dependencies"] for card in rankings} == {
        "one": ["visible-mcp"],
        "two": ["visible-mcp"],
    }
    assert calls["count"] == 2

    detail = await market.get_market_skill(db, slug="one", user=viewer)
    assert detail["mcp_dependencies"] == ["visible-mcp"]
    assert calls["count"] == 3

    admin_cards = await market.list_market_skills(db, user=admin)
    assert all(card["mcp_dependencies"] == ["visible-mcp", "private-mcp"] for card in admin_cards)
    assert calls["count"] == 3
    release = await db.get(MarketSkillRelease, "release-one")
    assert release.mcp_dependencies == ["visible-mcp", "private-mcp"]


@pytest.mark.asyncio
async def test_market_list_and_detail_report_install_conflict_without_exposing_reserved_skill(
    market_case, monkeypatch, tmp_path
):
    """安装前区分本人的同名技能和数据库保留标识，不暴露隐藏共享技能内容。"""
    db, viewer, _admin, _calls, _originals, _root = market_case
    personal_dir = workspace_paths.user_workspace_dir("viewer") / "agents" / "skills" / "one"
    personal_dir.mkdir(parents=True)
    (personal_dir / "SKILL.md").write_text(
        "---\nname: one\ndescription: viewer's private skill\n---\n# One\n", encoding="utf-8"
    )
    db.add_all(
        [
            Skill(
                slug=slug,
                name="Hidden shared secret",
                description="CLASSIFIED RESERVED SKILL",
                source_type="personal_share",
                dir_path=f"shared/{slug}",
                share_config={"version": 2, "read_scope": None, "edit_scope": None, "manage_scope": None},
                enabled=False,
            )
            for slug in ("one", "two")
        ]
    )
    db.add(
        MarketSkill(
            id="listing-clear",
            slug="clear",
            owner_uid="author",
            personal_slug="clear",
            scope="global",
            latest_release_id="release-clear",
        )
    )
    db.add(
        MarketSkillRelease(
            id="release-clear",
            market_skill_id="listing-clear",
            version=1,
            name="clear",
            description="clear listing",
            category="other",
            dir_path="market_releases/clear",
            content_hash="hash",
            tool_dependencies=[],
            mcp_dependencies=[],
            skill_dependencies=[],
            request_id="request-clear",
        )
    )
    await db.commit()

    cards = {card["slug"]: card for card in await market.list_market_skills(db, user=viewer)}
    assert {slug: card["install_conflict"] for slug, card in cards.items()} == {
        "one": "personal",
        "two": "reserved",
        "clear": None,
    }
    assert "CLASSIFIED RESERVED SKILL" not in str(cards)
    assert (await market.get_market_skill(db, slug="one", user=viewer))["install_conflict"] == "personal"
    assert (await market.get_market_skill(db, slug="two", user=viewer))["install_conflict"] == "reserved"
    assert (await market.get_market_skill(db, slug="clear", user=viewer))["install_conflict"] is None


@pytest.mark.asyncio
async def test_market_install_conflicts_return_distinct_actionable_errors(market_case, monkeypatch, tmp_path):
    """点击安装时与卡片冲突提示保持一致，个人标识优先。"""
    db, viewer, _admin, _calls, _originals, _root = market_case
    personal_dir = workspace_paths.user_workspace_dir("viewer") / "agents" / "skills" / "one"
    personal_dir.mkdir(parents=True)
    (personal_dir / "SKILL.md").write_text("---\nname: one\ndescription: private\n---\n# One\n", encoding="utf-8")
    db.add_all(
        [
            Skill(
                slug=slug,
                name=slug,
                description="hidden",
                source_type="personal_share",
                dir_path=f"shared/{slug}",
                share_config={},
                enabled=False,
            )
            for slug in ("one", "two")
        ]
    )
    for release_id in ("release-one", "release-two"):
        release = await db.get(MarketSkillRelease, release_id)
        release.mcp_dependencies = []
    await db.commit()

    with pytest.raises(ValueError, match="^已有同名个人技能，无法安装市场版$"):
        await market.install_market_skill(db, slug="one", user=viewer)
    with pytest.raises(ValueError, match="^技能标识已被占用，无法安装市场版$"):
        await market.install_market_skill(db, slug="two", user=viewer)


@pytest.mark.asyncio
async def test_market_skill_file_projects_only_skill_frontmatter_without_changing_source(market_case):
    """普通用户预览根级声明时隐藏私有引用，其他文件和源版本保持原样。"""
    db, viewer, admin, _calls, originals, root = market_case

    projected = await market.market_skill_file(db, slug="one", user=viewer, path="./SKILL.md")
    frontmatter, body = projected.split("---", 2)[1:]
    assert "visible-mcp" in frontmatter
    assert "private-mcp" not in frontmatter
    assert "private-mcp" in body
    assert await market.market_skill_file(db, slug="one", user=viewer, path="notes.md") == (
        "private-mcp in another file\n"
    )
    assert await market.market_skill_file(db, slug="one", user=admin) == originals["one"]
    assert (root / "market_releases/one/SKILL.md").read_text(encoding="utf-8") == originals["one"]

    (root / "market_releases/one/SKILL.md").write_text(
        "---\nname: one\ndescription: test\nmcp_dependencies: [private-mcp\n---\n# Guide\n", encoding="utf-8"
    )
    with pytest.raises(ValueError, match="SKILL.md frontmatter 无效") as error:
        await market.market_skill_file(db, slug="one", user=viewer)
    assert "private-mcp" not in str(error.value)


@pytest.mark.asyncio
async def test_market_skill_file_reports_non_utf8_script_as_unsupported_preview(market_case):
    db, viewer, _admin, _calls, _originals, root = market_case
    (root / "market_releases/one/run.py").write_bytes(b"\xff\xfe")

    with pytest.raises(ValueError, match="^仅支持预览 UTF-8 文本文件$"):
        await market.market_skill_file(db, slug="one", user=viewer, path="run.py")
