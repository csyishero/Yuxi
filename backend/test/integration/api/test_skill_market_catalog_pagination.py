"""市场目录的真实 HTTP 响应与授权后总数。"""

from datetime import datetime

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from server.routers.skill_market_router import market_router
from server.utils.auth_middleware import get_db, get_required_user
from yuxi.agents.skills import market as market_module
from yuxi.storage.postgres.models_business import (
    Base,
    Department,
    MarketSkill,
    MarketSkillInstall,
    MarketSkillLike,
    MarketSkillRelease,
    MCPServer,
    Skill,
    SkillShareRequest,
    User,
)


@pytest.fixture(scope="session", autouse=True)
def ensure_live_api_schema():
    """本例使用独立数据库，不依赖运行中的 API。"""


@pytest.fixture(scope="session", autouse=True)
def cleanup_test_knowledge_resources():
    """本例不创建知识库资源。"""
    yield


@pytest.fixture(scope="session", autouse=True)
def cleanup_test_sandboxes():
    """本例不创建沙盒。"""
    yield


@pytest.mark.asyncio
@pytest.mark.integration
async def test_market_catalog_total_matches_visible_filtered_rows_across_pages():
    """授权、搜索、分类和已安装过滤必须先于总数计算。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    viewer = User(uid="viewer", username="Viewer", password_hash="x", role="user", department_id=1)
    app = FastAPI()
    app.include_router(market_router, prefix="/api")

    async def provide_user():
        return viewer

    async def provide_db():
        async with sessions() as db:
            yield db

    app.dependency_overrides[get_required_user] = provide_user
    app.dependency_overrides[get_db] = provide_db

    try:
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
                        MCPServer.__table__,
                    ],
                )
            )
        async with sessions() as db:
            db.add_all(
                [
                    Department(id=1, name="Product"),
                    Department(id=2, name="Finance"),
                    viewer,
                    User(uid="author", username="Author", password_hash="x", department_id=1),
                ]
            )
            for slug, scope, department_id, category, month in [
                ("report-alpha", "department", 1, "finance_analysis", 1),
                ("report-beta", "department", 1, "finance_analysis", 2),
                ("report-other", "department", 2, "finance_analysis", 3),
                ("office-tool", "global", None, "office_tools", 4),
            ]:
                db.add(
                    MarketSkill(
                        id=slug,
                        slug=slug,
                        owner_uid="author",
                        personal_slug=slug,
                        scope=scope,
                        department_id=department_id,
                        latest_release_id=f"release-{slug}",
                        updated_at=datetime(2026, month, 1),
                    )
                )
                db.add(
                    MarketSkillRelease(
                        id=f"release-{slug}",
                        market_skill_id=slug,
                        version=1,
                        name=slug,
                        description="A report skill" if slug.startswith("report") else "Office skill 100%_ready",
                        category=category,
                        dir_path=f"market_releases/{slug}",
                        content_hash="hash",
                        request_id=f"request-{slug}",
                    )
                )
            db.add_all(
                [
                    MarketSkillInstall(uid="viewer", market_skill_id="report-alpha", release_id="release-report-alpha"),
                    MarketSkillInstall(uid="viewer", market_skill_id="report-other", release_id="release-report-other"),
                ]
            )
            await db.commit()

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            first = (await client.get("/api/skill-market", params={"category": "finance_analysis", "limit": 1})).json()
            assert first["success"] is True
            assert [item["slug"] for item in first["data"]] == ["report-beta"]
            assert (first["total"], first["limit"], first["offset"], first["has_more"]) == (2, 1, 0, True)

            second = (
                await client.get(
                    "/api/skill-market",
                    params={"query": "report", "category": "finance_analysis", "limit": 1, "offset": 1},
                )
            ).json()
            assert [item["slug"] for item in second["data"]] == ["report-alpha"]
            assert (second["total"], second["has_more"]) == (2, False)

            tail = (
                await client.get("/api/skill-market", params={"category": "finance_analysis", "limit": 1, "offset": 2})
            ).json()
            assert tail["data"] == []
            assert (tail["total"], tail["has_more"]) == (2, False)

            installed = (await client.get("/api/skill-market", params={"installed_only": True, "limit": 1})).json()
            assert [item["slug"] for item in installed["data"]] == ["report-alpha"]
            assert (installed["total"], installed["has_more"]) == (1, False)

            hidden = (await client.get("/api/skill-market", params={"query": "report-other"})).json()
            assert hidden["data"] == []
            assert hidden["total"] == 0

            for literal in ("%", "_", "%_"):
                matched = (await client.get("/api/skill-market", params={"query": literal})).json()
                assert [item["slug"] for item in matched["data"]] == ["office-tool"]
                assert matched["total"] == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_market_file_tree_distinguishes_scripts_from_binary_attachments(monkeypatch, tmp_path):
    """文件树标记可预览类型，文本脚本可读取，二进制和非 UTF-8 内容明确拒绝。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    viewer = User(uid="viewer", username="Viewer", password_hash="x", role="user", department_id=1)
    app = FastAPI()
    app.include_router(market_router, prefix="/api")

    async def provide_user():
        return viewer

    async def provide_db():
        async with sessions() as db:
            yield db

    app.dependency_overrides[get_required_user] = provide_user
    app.dependency_overrides[get_db] = provide_db
    monkeypatch.setattr(market_module, "get_skill_data_dir", lambda: tmp_path)

    release_dir = tmp_path / "market_releases" / "demo"
    release_dir.mkdir(parents=True)
    (release_dir / "SKILL.md").write_text("---\nname: demo\ndescription: Demo\n---\n", encoding="utf-8")
    (release_dir / "run.py").write_text("print('ok')\n", encoding="utf-8")
    (release_dir / "bad.py").write_bytes(b"\xff\xfe")
    (release_dir / "diagram.png").write_bytes(b"\x89PNG\r\n")

    try:
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
                        MCPServer.__table__,
                    ],
                )
            )
        async with sessions() as db:
            db.add_all(
                [
                    Department(id=1, name="Product"),
                    viewer,
                    User(uid="author", username="Author", password_hash="x", department_id=1),
                    MarketSkill(
                        id="listing-demo",
                        slug="demo",
                        owner_uid="author",
                        personal_slug="demo",
                        scope="global",
                        latest_release_id="release-demo",
                    ),
                    MarketSkillRelease(
                        id="release-demo",
                        market_skill_id="listing-demo",
                        version=1,
                        name="Demo",
                        description="Demo skill",
                        category="other",
                        dir_path="market_releases/demo",
                        content_hash="hash",
                        request_id="request-demo",
                    ),
                ]
            )
            await db.commit()

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            tree = await client.get("/api/skill-market/demo/tree")
            assert tree.status_code == 200
            previewable = {item["path"]: item["previewable"] for item in tree.json()["data"]}
            assert previewable == {"SKILL.md": True, "bad.py": True, "diagram.png": False, "run.py": True}

            script = await client.get("/api/skill-market/demo/file", params={"path": "run.py"})
            assert script.status_code == 200
            assert script.json()["data"]["content"] == "print('ok')\n"
            assert (await client.get("/api/skill-market/demo/file", params={"path": "diagram.png"})).status_code == 400
            assert (await client.get("/api/skill-market/demo/file", params={"path": "bad.py"})).status_code == 400
            assert (await client.get("/api/skill-market/demo/file", params={"path": "missing.py"})).status_code == 404
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_market_page_and_detail_report_only_accessible_unshadowed_shared_origin(monkeypatch):
    """跨页市场卡用原件身份查共享副本，并选当前用户可用的最新版本。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    viewer = User(uid="viewer", username="Viewer", password_hash="x", role="user", department_id=1)
    app = FastAPI()
    app.include_router(market_router, prefix="/api")

    async def provide_user():
        return viewer

    async def provide_db():
        async with sessions() as db:
            yield db

    shadowed_slugs: set[str] = set()

    async def personal_skill_if_present(_uid: str, slug: str):
        if slug not in shadowed_slugs:
            raise ValueError("个人 Skill 不存在")
        return object()

    monkeypatch.setattr(market_module, "get_personal_skill", personal_skill_if_present, raising=False)
    app.dependency_overrides[get_required_user] = provide_user
    app.dependency_overrides[get_db] = provide_db

    try:
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
                        MCPServer.__table__,
                    ],
                )
            )
        async with sessions() as db:
            db.add_all(
                [
                    Department(id=1, name="Product"),
                    Department(id=2, name="Finance"),
                    viewer,
                    User(uid="author", username="Author", password_hash="x", department_id=1),
                    User(uid="reviewer", username="Reviewer", password_hash="x", role="superadmin"),
                ]
            )
            for slug, personal_slug, month in [
                ("target", "source", 1),
                ("unrelated", "unrelated", 2),
            ]:
                db.add(
                    MarketSkill(
                        id=slug,
                        slug=slug,
                        owner_uid="author",
                        personal_slug=personal_slug,
                        scope="global",
                        latest_release_id=f"release-{slug}",
                        updated_at=datetime(2026, month, 1),
                    )
                )
                db.add(
                    MarketSkillRelease(
                        id=f"release-{slug}",
                        market_skill_id=slug,
                        version=1,
                        name=slug,
                        description="A skill",
                        category="other",
                        dir_path=f"market_releases/{slug}",
                        content_hash="hash",
                        request_id=f"request-{slug}",
                    )
                )
            for slug, department_id in [
                ("shared-old", 1),
                ("shared-new", 1),
                ("shared-hidden", 2),
            ]:
                skill = Skill(
                    slug=slug,
                    name=slug,
                    description="Shared skill",
                    source_type="personal_share",
                    category="other",
                    dir_path=f"shared/{slug}",
                    content_hash="published-hash",
                    enabled=True,
                    created_by="reviewer",
                    created_at=datetime(2026, 1, 2),
                    share_config={
                        "version": 2,
                        "read_scope": {
                            "access_level": "department",
                            "department_ids": [department_id],
                            "user_uids": [],
                        },
                        "manage_scope": None,
                    },
                )
                db.add(skill)
                await db.flush()
                db.add(
                    SkillShareRequest(
                        id=f"request-{slug}",
                        owner_uid="author",
                        owner_department_id=1,
                        personal_slug="source",
                        name=slug,
                        description="Shared skill",
                        category="other",
                        content_hash="source-hash",
                        channel="shared",
                        status="approved",
                        reviewer_uid="reviewer",
                        published_slug=slug,
                        published_skill_id=skill.id,
                        created_at=datetime(2026, 1, 1),
                        reviewed_at=datetime(2026, 1, 3),
                    )
                )
            await db.commit()

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            first = (await client.get("/api/skill-market", params={"limit": 1})).json()
            assert first["data"][0]["slug"] == "unrelated"
            assert first["data"][0]["shared_available_slug"] is None

            second = (await client.get("/api/skill-market", params={"limit": 1, "offset": 1})).json()
            assert second["data"][0]["slug"] == "target"
            assert second["data"][0]["shared_available_slug"] == "shared-new"
            detail = (await client.get("/api/skill-market/target")).json()["data"]
            assert detail["shared_available_slug"] == "shared-new"

            shadowed_slugs.add("shared-new")
            shadowed = (await client.get("/api/skill-market/target")).json()["data"]
            assert shadowed["shared_available_slug"] == "shared-old"
    finally:
        await engine.dispose()
