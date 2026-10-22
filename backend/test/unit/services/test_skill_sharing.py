"""个人 Skill 共享快照和审核的持久化边界。"""

import zipfile
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from yuxi.agents.skills import service, sharing
from yuxi.permissions import ResourcePermission, resolve_skill_permission
from yuxi.storage.postgres.models_business import Base, Department, Skill, SkillShareRequest, User


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["shared", "market"])
async def test_duplicate_pending_share_commit_removes_losing_snapshot(monkeypatch, tmp_path: Path, channel: str):
    """唯一约束在提交时拒绝并发申请后，只保留胜出申请的快照。"""
    personal_root = tmp_path / "personal"
    source = personal_root / "demo"
    source.mkdir(parents=True)
    markdown = "---\nname: demo\ndescription: test\n---\n# Demo\n"
    (source / "SKILL.md").write_text(markdown, encoding="utf-8")
    monkeypatch.setattr(sharing, "personal_skills_root", lambda _uid: personal_root)
    monkeypatch.setattr(sharing, "get_skill_data_dir", lambda: tmp_path)

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(lambda conn: Base.metadata.create_all(conn, tables=[SkillShareRequest.__table__]))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    user = User(uid="owner", username="owner", password_hash="x", role="user")
    request_options = {"market_generation": 1} if channel == "market" else {}
    try:
        async with sessions() as db:
            winner = await sharing.submit_skill_share_request(
                db, slug="demo", application_reason="first", operator=user, channel=channel, **request_options
            )
            winner_id = winner.id

            async def stale_pending_check(_statement):
                """模拟另一事务在申请预检之后提交了待审记录。"""
                return None

            monkeypatch.setattr(db, "scalar", stale_pending_check)
            with pytest.raises(ValueError, match="已有待审核申请"):
                await sharing.submit_skill_share_request(
                    db, slug="demo", application_reason="second", operator=user, channel=channel, **request_options
                )

            assert (await db.scalars(select(SkillShareRequest.id))).all() == [winner_id]
            snapshot_root = tmp_path / "share_requests"
            assert sorted(path.name for path in snapshot_root.iterdir()) == [winner_id]
            assert (snapshot_root / winner_id / "SKILL.md").read_text(encoding="utf-8") == markdown
            assert (source / "SKILL.md").read_text(encoding="utf-8") == markdown
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_committed_share_request_keeps_snapshot_if_refresh_fails(monkeypatch, tmp_path: Path):
    """数据库已提交而响应装配失败时保留已持久化记录的快照。"""
    personal_root = tmp_path / "personal"
    source = personal_root / "demo"
    source.mkdir(parents=True)
    (source / "SKILL.md").write_text("---\nname: demo\ndescription: test\n---\n# Demo\n", encoding="utf-8")
    monkeypatch.setattr(sharing, "personal_skills_root", lambda _uid: personal_root)
    monkeypatch.setattr(sharing, "get_skill_data_dir", lambda: tmp_path)

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(lambda conn: Base.metadata.create_all(conn, tables=[SkillShareRequest.__table__]))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    user = User(uid="owner", username="owner", password_hash="x", role="user")
    try:
        async with sessions() as db:

            async def fail_refresh(_item):
                """模拟提交成功后的响应装配失败。"""
                raise RuntimeError("refresh failed")

            monkeypatch.setattr(db, "refresh", fail_refresh)
            with pytest.raises(RuntimeError, match="refresh failed"):
                await sharing.submit_skill_share_request(db, slug="demo", application_reason="first", operator=user)

            stored = (await db.scalars(select(SkillShareRequest))).one()
            assert (tmp_path / "share_requests" / stored.id / "SKILL.md").exists()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_direct_publish_only_uses_administrators_own_personal_skill(monkeypatch, tmp_path: Path):
    """跨用户直接发布不创建申请或副本，管理员仍可发布自己的原件。"""
    personal_root = tmp_path / "personal"
    for uid, slug in (("member", "member-skill"), ("admin", "admin-skill")):
        source = personal_root / uid / slug
        source.mkdir(parents=True)
        (source / "SKILL.md").write_text(f"---\nname: {slug}\ndescription: test\n---\n# {slug}\n", encoding="utf-8")
    shared_root = tmp_path / "shared"
    shared_root.mkdir()
    monkeypatch.setattr(sharing, "personal_skills_root", lambda uid: personal_root / uid)
    monkeypatch.setattr(sharing, "get_skill_data_dir", lambda: tmp_path)
    monkeypatch.setattr(sharing, "get_skills_root_dir", lambda: shared_root)

    async def no_dependencies(**_kwargs):
        """本例只验证直接发布的所有权边界。"""
        return [], [], []

    monkeypatch.setattr(sharing, "validate_skill_dependencies", no_dependencies)
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda conn: Base.metadata.create_all(
                conn, tables=[Department.__table__, User.__table__, Skill.__table__, SkillShareRequest.__table__]
            )
        )
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    admin = User(uid="admin", username="admin", password_hash="x", role="admin", department_id=1)
    superadmin = User(uid="root", username="root", password_hash="x", role="superadmin")
    try:
        async with sessions() as db:
            db.add_all(
                [
                    Department(id=1, name="Credit"),
                    User(uid="member", username="member", password_hash="x", role="user", department_id=1),
                    User(uid="admin", username="admin", password_hash="x", role="admin", department_id=1),
                ]
            )
            await db.commit()

            for operator in (admin, superadmin):
                with pytest.raises(ValueError, match="只能直接发布自己的个人 Skill"):
                    await sharing.publish_personal_skill_directly(
                        db, owner_uid="member", slug="member-skill", department_ids=[1], note="", operator=operator
                    )
            assert (await db.scalars(select(SkillShareRequest))).all() == []
            assert list(shared_root.iterdir()) == []

            published = await sharing.publish_personal_skill_directly(
                db, owner_uid="admin", slug="admin-skill", department_ids=[1], note="", operator=admin
            )
            assert published.status == "approved"
            assert published.owner_uid == "admin"
            assert (shared_root / "admin-skill" / "SKILL.md").exists()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_share_request_keeps_snapshot_and_personal_source(monkeypatch, tmp_path: Path):
    """审核使用提交时快照，拒绝无效范围并保留个人原件。"""
    personal_root = tmp_path / "personal"
    source = personal_root / "credit-check"
    source.mkdir(parents=True)
    original = "---\nname: credit-check\ndescription: original\n---\n# Original\n"
    (source / "SKILL.md").write_text(original, encoding="utf-8")
    (source / "scripts").mkdir()
    (source / "scripts" / "check.py").write_text("print('original')\n", encoding="utf-8")
    (source / "sample.bin").write_bytes(b"\x00\x01")
    shared_root = tmp_path / "shared"
    shared_root.mkdir()
    monkeypatch.setattr(sharing, "personal_skills_root", lambda _uid: personal_root)
    monkeypatch.setattr(sharing, "get_skill_data_dir", lambda: tmp_path)
    monkeypatch.setattr(sharing, "get_skills_root_dir", lambda: shared_root)

    async def no_dependencies(**_kwargs):
        """让本例只验证审核与快照边界。"""
        return [], [], []

    monkeypatch.setattr(sharing, "validate_skill_dependencies", no_dependencies)
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda conn: Base.metadata.create_all(
                conn, tables=[Department.__table__, Skill.__table__, SkillShareRequest.__table__]
            )
        )
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    user = User(uid="owner", username="owner", password_hash="x", role="user", department_id=1)
    admin = User(uid="admin", username="admin", password_hash="x", role="admin", department_id=1)

    try:
        async with sessions() as db:
            db.add(Department(id=1, name="Credit"))
            await db.commit()
            with pytest.raises(ValueError, match="请填写"):
                await sharing.submit_skill_share_request(
                    db, slug="credit-check", application_reason="   ", operator=user
                )
            with pytest.raises(ValueError, match="1000 字"):
                await sharing.submit_skill_share_request(
                    db, slug="credit-check", application_reason="a" * 1001, operator=user
                )
            request = await sharing.submit_skill_share_request(
                db, slug="credit-check", application_reason="  供信贷团队复用  ", operator=user
            )
            assert request.to_dict()["application_reason"] == "供信贷团队复用"
            with pytest.raises(ValueError, match="待审核"):
                await sharing.submit_skill_share_request(
                    db, slug="credit-check", application_reason="重复申请", operator=user
                )

            (source / "SKILL.md").write_text(
                "---\nname: credit-check\ndescription: revised\n---\n# Revised\n", encoding="utf-8"
            )
            (source / "scripts" / "check.py").write_text("print('revised')\n", encoding="utf-8")
            assert await sharing.read_skill_share_snapshot(db, request_id=request.id, operator=user) == original
            tree = await sharing.list_skill_share_snapshot_tree(db, request_id=request.id, operator=admin)
            assert {entry["name"] for entry in tree} == {"SKILL.md", "scripts", "sample.bin"}
            assert (
                await sharing.read_skill_share_snapshot(
                    db, request_id=request.id, operator=admin, relative_path="scripts/check.py"
                )
            ) == "print('original')\n"
            with pytest.raises(ValueError, match="非法路径"):
                await sharing.read_skill_share_snapshot(
                    db, request_id=request.id, operator=admin, relative_path="../SKILL.md"
                )
            with pytest.raises(ValueError, match="文本文件"):
                await sharing.read_skill_share_snapshot(
                    db, request_id=request.id, operator=admin, relative_path="sample.bin"
                )
            archive_path, _ = await sharing.export_skill_share_snapshot(db, request_id=request.id, operator=admin)
            try:
                with zipfile.ZipFile(archive_path) as archive:
                    assert archive.read(f"{request.id}/sample.bin") == b"\x00\x01"
            finally:
                Path(archive_path).unlink()
            stranger = User(uid="stranger", username="stranger", password_hash="x", role="user")
            with pytest.raises(ValueError, match="无权"):
                await sharing.read_skill_share_snapshot(db, request_id=request.id, operator=stranger)
            with pytest.raises(ValueError, match="所属部门"):
                await sharing.approve_skill_share_request(
                    db, request_id=request.id, department_ids=[2], note="", operator=admin
                )

            approved = await sharing.approve_skill_share_request(
                db, request_id=request.id, department_ids=[1], note="通过", operator=admin
            )
            assert approved.status == "approved"
            assert approved.published_slug == "credit-check"
            published_markdown = (shared_root / "credit-check" / "SKILL.md").read_text(encoding="utf-8")
            assert "category: other\n" in published_markdown
            assert published_markdown.endswith("# Original\n")
            assert (shared_root / "credit-check" / "scripts" / "check.py").read_text(
                encoding="utf-8"
            ) == "print('original')\n"
            assert (source / "SKILL.md").read_text(encoding="utf-8").endswith("# Revised\n")
            stored = await db.get(Skill, 1)
            assert stored.share_config["read_scope"]["department_ids"] == [1]
            assert stored.source_type == "personal_share"
            assert stored.created_by == admin.uid
            assert resolve_skill_permission(user, stored) == ResourcePermission.READ
            assert resolve_skill_permission(admin, stored) == ResourcePermission.MANAGE
            with pytest.raises(ValueError, match="不允许直接修改文件"):
                await service.update_skill_file(
                    db,
                    slug="credit-check",
                    relative_path="SKILL.md",
                    content="modified",
                    updated_by=admin.uid,
                    operator=admin,
                )
            with pytest.raises(ValueError, match="不可原地修改"):
                await service.update_skill_dependencies(
                    db,
                    slug="credit-check",
                    tool_dependencies=[],
                    mcp_dependencies=[],
                    skill_dependencies=[],
                    operator=admin,
                )
            with pytest.raises(ValueError, match="仅支持指定部门或人员"):
                await service.update_skill_share_config(
                    db,
                    slug="credit-check",
                    share_config={
                        "version": 2,
                        "read_scope": {"access_level": "global", "department_ids": [], "user_uids": []},
                        "manage_scope": None,
                    },
                    operator=admin,
                )

            next_request = await sharing.submit_skill_share_request(
                db, slug="credit-check", application_reason="更新版本", operator=user
            )
            next_approved = await sharing.approve_skill_share_request(
                db, request_id=next_request.id, department_ids=[1], note="", operator=admin
            )
            assert next_approved.published_slug == "credit-check-v2"
            second_release = await db.get(Skill, 2)
            assert second_release.version == "2.0.0"
            assert second_release.name == "credit-check"
            second_markdown = (shared_root / "credit-check-v2" / "SKILL.md").read_text(encoding="utf-8")
            assert "name: credit-check\n" in second_markdown
            assert "slug: credit-check-v2\n" in second_markdown
            assert second_markdown.endswith("# Revised\n")

            with (source / "too-large.bin").open("wb") as oversized:
                oversized.truncate(sharing.MAX_SHARE_SNAPSHOT_BYTES + 1)
            with pytest.raises(ValueError, match="20 MB"):
                await sharing.submit_skill_share_request(
                    db, slug="credit-check", application_reason="再次更新", operator=user
                )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_unassigned_owner_request_is_only_reviewable_by_superadmin(monkeypatch, tmp_path: Path):
    """无部门用户可提交；无部门管理员不能借 NULL 范围查看或审核。"""
    personal_root = tmp_path / "personal"
    skill_dir = personal_root / "credit-check"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\nname: credit-check\ndescription: test\n---\n# Credit\n", encoding="utf-8")
    monkeypatch.setattr(sharing, "personal_skills_root", lambda _uid: personal_root)
    monkeypatch.setattr(sharing, "get_skill_data_dir", lambda: tmp_path)
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda conn: Base.metadata.create_all(
                conn, tables=[Department.__table__, User.__table__, SkillShareRequest.__table__]
            )
        )
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    owner = User(uid="owner", username="owner", password_hash="x", role="user", department_id=None)
    admin = User(uid="admin", username="admin", password_hash="x", role="admin", department_id=None)
    superadmin = User(uid="root", username="root", password_hash="x", role="superadmin")
    try:
        async with sessions() as db:
            db.add(User(uid="owner", username="Owner Name", password_hash="x", role="user"))
            await db.commit()
            request = await sharing.submit_skill_share_request(
                db, slug="credit-check", application_reason="申请跨团队共享", operator=owner
            )
            assert request.owner_department_id is None
            assert (await sharing.list_skill_share_requests(db, operator=admin))["items"] == []
            queue = (await sharing.list_skill_share_requests(db, operator=superadmin))["items"]
            assert queue[0]["owner_name"] == "Owner Name"
            assert queue[0]["application_reason"] == "申请跨团队共享"
            assert [item["id"] for item in queue] == [request.id]
            with pytest.raises(ValueError, match="无权访问"):
                await sharing.read_skill_share_snapshot(db, request_id=request.id, operator=admin)
            with pytest.raises(ValueError, match="共享申请不存在"):
                await sharing.reject_skill_share_request(db, request_id=request.id, note="", operator=admin)
            reviewed = await sharing.reject_skill_share_request(
                db, request_id=request.id, note="缺少说明", operator=superadmin
            )
            assert reviewed.status == "rejected"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_generated_shared_slug_stays_within_database_limit(monkeypatch, tmp_path: Path):
    """同名发布和超长个人 slug 均生成合法的 128 字符共享标识。"""

    class ExistingSlugRepository:
        """仅将原始 slug 标记为已占用。"""

        async def exists_slug(self, slug: str) -> bool:
            """模拟已有共享记录。"""
            return slug == "a" * 128

    monkeypatch.setattr(service, "get_skills_root_dir", lambda: tmp_path)
    collision = await service.generate_available_skill_slug(ExistingSlugRepository(), "a" * 128)
    oversized = await service.generate_available_skill_slug(ExistingSlugRepository(), "b" * 200)
    assert collision.endswith("-v2")
    assert len(collision) == 128
    assert len(oversized) <= 128
    assert service.is_valid_skill_slug(collision)
    assert service.is_valid_skill_slug(oversized)


def test_snapshot_copy_rejects_file_replaced_by_symlink(monkeypatch, tmp_path: Path):
    """检查后若文件被替换为符号链接，快照不能复制目标内容。"""
    source = tmp_path / "source"
    source.mkdir()
    file = source / "SKILL.md"
    file.write_text("public", encoding="utf-8")
    secret = tmp_path / "secret.txt"
    secret.write_text("private", encoding="utf-8")
    destination = tmp_path / "snapshot"
    open_original = service.open_regular_file_fd

    def replace_before_open(directory_fd: int, parts: tuple[str, ...]):
        """在类型检查后、fd 打开前模拟用户并发替换。"""
        file.unlink()
        file.symlink_to(secret)
        return open_original(directory_fd, parts)

    monkeypatch.setattr(service, "open_regular_file_fd", replace_before_open)
    with pytest.raises(PermissionError, match="symlink"):
        service.copy_skill_tree_no_symlinks(source, destination)
    assert not destination.exists()
    assert secret.read_text(encoding="utf-8") == "private"


def test_snapshot_copy_rejects_replaced_source_directory(tmp_path: Path):
    """预先检查过的个人 Skill 根目录被替换后也不能跟随链接。"""
    source = tmp_path / "source"
    source.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "SKILL.md").write_text("private", encoding="utf-8")
    source.rmdir()
    source.symlink_to(outside, target_is_directory=True)
    destination = tmp_path / "snapshot"

    with pytest.raises(OSError):
        service.copy_skill_tree_no_symlinks(source, destination)
    assert not destination.exists()


def test_snapshot_copy_enforces_budget_during_file_read(monkeypatch, tmp_path: Path):
    """预检后文件增长时，复制过程仍受字节上限约束。"""
    source = tmp_path / "source"
    source.mkdir()
    file = source / "SKILL.md"
    file.write_text("small", encoding="utf-8")
    destination = tmp_path / "snapshot"
    open_original = service.open_regular_file_fd

    def grow_before_open(directory_fd: int, parts: tuple[str, ...]):
        """模拟预检与实际读取之间用户写入更多内容。"""
        file.write_text("larger than allowed", encoding="utf-8")
        return open_original(directory_fd, parts)

    monkeypatch.setattr(service, "open_regular_file_fd", grow_before_open)
    with pytest.raises(ValueError, match="20 MB"):
        service.copy_skill_tree_no_symlinks(source, destination, max_bytes=5, max_entries=2)
    assert not destination.exists()
