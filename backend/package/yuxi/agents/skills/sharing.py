"""个人 Skill 快照的共享申请和审核。"""

from __future__ import annotations

import asyncio
import os
import shutil
import stat
import tempfile
import uuid
import zipfile
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.agents.skills.repository import SkillRepository
from yuxi.agents.skills.service import (
    build_skill_tree,
    compute_skill_dir_hash,
    copy_skill_snapshot,
    generate_available_skill_slug,
    get_manageable_personal_skill_owner,
    get_skills_root_dir,
    is_skill_text_path,
    normalize_skill_share_config,
    parse_skill_dir_metadata,
    personal_skills_root,
    resolve_personal_skill_dir,
    resolve_skill_relative_path,
    skill_dir_contains_symlink,
    validate_skill_dependencies,
    validate_personal_share_read_scope,
)
from yuxi.config import get_skill_data_dir
from yuxi.storage.postgres.models_business import Skill, SkillShareRequest, User
from yuxi.utils.datetime_utils import utc_now_naive

MAX_SHARE_SNAPSHOT_BYTES = 20 * 1024 * 1024
MAX_SHARE_SNAPSHOT_ENTRIES = 1000


def validate_share_tree_limits(source: Path) -> None:
    """限制审核快照的文件数量、字节数和特殊文件。"""
    entry_count = 0
    byte_count = 0
    for directory, dirs, files in os.walk(source, followlinks=False):
        for name in [*dirs, *files]:
            entry_count += 1
            path = Path(directory) / name
            mode = path.lstat().st_mode
            if stat.S_ISREG(mode):
                byte_count += path.stat().st_size
            elif not stat.S_ISDIR(mode):
                raise ValueError("共享申请只允许普通文件和目录")
            if entry_count > MAX_SHARE_SNAPSHOT_ENTRIES or byte_count > MAX_SHARE_SNAPSHOT_BYTES:
                raise ValueError("共享申请快照超过 1000 项或 20 MB 限制")


def _snapshot_root() -> Path:
    """返回不受安装草稿 TTL 清理影响的审核快照目录。"""
    root = get_skill_data_dir() / "share_requests"
    root.mkdir(parents=True, exist_ok=True)
    return root


async def submit_skill_share_request(db: AsyncSession, *, slug: str, operator: User) -> SkillShareRequest:
    """复制本人个人 Skill 为不可变待审快照。"""
    source = resolve_personal_skill_dir(personal_skills_root(str(operator.uid)), slug)
    if not source.is_dir():
        raise ValueError("个人 Skill 不存在")
    existing = await db.scalar(
        select(SkillShareRequest.id).where(
            SkillShareRequest.owner_uid == operator.uid,
            SkillShareRequest.personal_slug == slug,
            SkillShareRequest.status == "pending",
        )
    )
    if existing:
        raise ValueError("该 Skill 已有待审核申请")

    request_id = str(uuid.uuid4())
    snapshot = _snapshot_root() / request_id
    try:
        await asyncio.to_thread(validate_share_tree_limits, source)
        parsed = await asyncio.to_thread(
            copy_skill_snapshot,
            source,
            snapshot,
            expected_slug=slug,
            max_bytes=MAX_SHARE_SNAPSHOT_BYTES,
            max_entries=MAX_SHARE_SNAPSHOT_ENTRIES,
        )
        await asyncio.to_thread(validate_share_tree_limits, snapshot)
        item = SkillShareRequest(
            id=request_id,
            owner_uid=str(operator.uid),
            owner_department_id=operator.department_id,
            personal_slug=slug,
            name=parsed["name"],
            description=parsed["description"],
            content_hash=await asyncio.to_thread(compute_skill_dir_hash, snapshot),
            status="pending",
            created_at=utc_now_naive(),
        )
        db.add(item)
        await db.commit()
        await db.refresh(item)
        return item
    except IntegrityError as exc:
        await db.rollback()
        shutil.rmtree(snapshot, ignore_errors=True)
        raise ValueError("该 Skill 已有待审核申请") from exc
    except BaseException:
        await db.rollback()
        shutil.rmtree(snapshot, ignore_errors=True)
        raise


async def publish_personal_skill_directly(
    db: AsyncSession,
    *,
    owner_uid: str,
    slug: str,
    department_ids: list[int],
    note: str,
    operator: User,
    read_scope: dict | None = None,
) -> SkillShareRequest:
    """管理员对授权成员的当前原件创建快照并发布共享副本。"""
    owner = await get_manageable_personal_skill_owner(db, operator=operator, owner_uid=owner_uid)
    request = await submit_skill_share_request(db, slug=slug, operator=owner)
    request_id = request.id
    review_note = "管理员直接发布" + (f"：{note.strip()}" if note.strip() else "")
    try:
        return await approve_skill_share_request(
            db,
            request_id=request_id,
            department_ids=department_ids,
            note=review_note,
            operator=operator,
            read_scope=read_scope,
        )
    except BaseException:
        await db.rollback()
        pending = await db.scalar(select(SkillShareRequest).where(SkillShareRequest.id == request_id).with_for_update())
        if pending is not None and pending.status == "pending":
            await db.delete(pending)
            await db.commit()
            shutil.rmtree(_snapshot_root() / request_id, ignore_errors=True)
        raise


async def list_skill_share_requests(db: AsyncSession, *, operator: User) -> list[SkillShareRequest]:
    """申请人只看自己的记录，管理员查看待审和历史。"""
    query = select(SkillShareRequest)
    if operator.role == "admin":
        query = query.where(
            SkillShareRequest.owner_department_id == operator.department_id
            if operator.department_id is not None
            else SkillShareRequest.id.is_(None)
        )
    elif operator.role != "superadmin":
        query = query.where(SkillShareRequest.owner_uid == operator.uid)
    return list((await db.scalars(query.order_by(SkillShareRequest.created_at.desc()))).all())


async def _authorized_snapshot_path(db: AsyncSession, *, request_id: str, operator: User) -> Path:
    """授权并校验不可变审核快照。"""
    item = await db.get(SkillShareRequest, request_id)
    allowed = item is not None and (
        item.owner_uid == operator.uid
        or operator.role == "superadmin"
        or (
            operator.role == "admin"
            and operator.department_id is not None
            and item.owner_department_id == operator.department_id
        )
    )
    if not allowed:
        raise ValueError("共享申请不存在或无权访问")
    snapshot = _snapshot_root() / item.id
    if (
        snapshot.is_symlink()
        or not snapshot.is_dir()
        or await asyncio.to_thread(skill_dir_contains_symlink, snapshot)
        or await asyncio.to_thread(validate_share_tree_limits, snapshot)
        or await asyncio.to_thread(compute_skill_dir_hash, snapshot) != item.content_hash
    ):
        raise ValueError("审核快照已损坏")
    return snapshot


async def list_skill_share_snapshot_tree(db: AsyncSession, *, request_id: str, operator: User) -> list[dict]:
    """列出提交时的全部目录和文件，供审核员逐项检查。"""
    snapshot = await _authorized_snapshot_path(db, request_id=request_id, operator=operator)
    return await asyncio.to_thread(build_skill_tree, snapshot, snapshot)


async def read_skill_share_snapshot(
    db: AsyncSession, *, request_id: str, operator: User, relative_path: str = "SKILL.md"
) -> str:
    """读取提交时快照中的指定文本文件。"""
    snapshot = await _authorized_snapshot_path(db, request_id=request_id, operator=operator)
    target, _ = resolve_skill_relative_path(snapshot, relative_path)
    if not target.is_file():
        raise ValueError("文件不存在")
    if not is_skill_text_path(target):
        raise ValueError("仅支持预览文本文件")
    try:
        return await asyncio.to_thread(target.read_text, encoding="utf-8")
    except UnicodeError as exc:
        raise ValueError("仅支持预览 UTF-8 文本文件") from exc


async def export_skill_share_snapshot(db: AsyncSession, *, request_id: str, operator: User) -> tuple[str, str]:
    """打包完整审核快照，供管理员检查非文本附件。"""
    snapshot = await _authorized_snapshot_path(db, request_id=request_id, operator=operator)

    def archive() -> str:
        """在线程中创建临时 ZIP，响应结束后由路由清理。"""
        with tempfile.NamedTemporaryFile(prefix="skill-share-", suffix=".zip", delete=False) as temporary:
            export_path = temporary.name
        try:
            with zipfile.ZipFile(export_path, "w", compression=zipfile.ZIP_DEFLATED) as output:
                for entry in sorted(snapshot.rglob("*")):
                    output.write(entry, (Path(snapshot.name) / entry.relative_to(snapshot)).as_posix())
            return export_path
        except BaseException:
            Path(export_path).unlink(missing_ok=True)
            raise

    path = await asyncio.to_thread(archive)
    return path, f"skill-share-{request_id}.zip"


async def reject_skill_share_request(
    db: AsyncSession, *, request_id: str, note: str, operator: User
) -> SkillShareRequest:
    """记录审核意见并关闭待审申请。"""
    if operator.role not in {"admin", "superadmin"}:
        raise ValueError("无权审核共享申请")
    item = await db.scalar(select(SkillShareRequest).where(SkillShareRequest.id == request_id).with_for_update())
    if item is None or (
        operator.role == "admin"
        and (operator.department_id is None or item.owner_department_id != operator.department_id)
    ):
        raise ValueError("共享申请不存在")
    if item.status != "pending":
        raise ValueError("共享申请已审核")
    item.status = "rejected"
    item.review_note = note.strip() or None
    item.reviewer_uid = str(operator.uid)
    item.reviewed_at = utc_now_naive()
    await db.commit()
    await db.refresh(item)
    return item


async def approve_skill_share_request(
    db: AsyncSession,
    *,
    request_id: str,
    department_ids: list[int],
    note: str,
    operator: User,
    read_scope: dict | None = None,
) -> SkillShareRequest:
    """校验审核快照和部门或人员范围后发布独立共享 Skill。"""
    if operator.role not in {"admin", "superadmin"}:
        raise ValueError("无权审核共享申请")
    item = await db.scalar(select(SkillShareRequest).where(SkillShareRequest.id == request_id).with_for_update())
    if item is None or (
        operator.role == "admin"
        and (operator.department_id is None or item.owner_department_id != operator.department_id)
    ):
        raise ValueError("共享申请不存在")
    if item.status != "pending":
        raise ValueError("共享申请已审核")
    if read_scope is not None and department_ids:
        raise ValueError("发布范围不能同时使用旧部门列表与读取范围")
    requested_scope = read_scope or {
        "access_level": "department",
        "department_ids": department_ids,
        "user_uids": [],
    }
    share_config = normalize_skill_share_config(
        {"version": 2, "read_scope": requested_scope, "manage_scope": None},
        operator_uid=operator.uid,
    )
    await validate_personal_share_read_scope(db, read_scope=share_config["read_scope"], operator=operator)

    snapshot = await _authorized_snapshot_path(db, request_id=request_id, operator=operator)
    parsed = await asyncio.to_thread(parse_skill_dir_metadata, snapshot)
    if parsed["slug"] != item.personal_slug:
        raise ValueError("审核快照的 Skill slug 已变化")

    repo = SkillRepository(db)
    slug = await generate_available_skill_slug(repo, item.personal_slug)
    previous_releases = await db.scalar(
        select(func.count(SkillShareRequest.id)).where(
            SkillShareRequest.owner_uid == item.owner_uid,
            SkillShareRequest.personal_slug == item.personal_slug,
            SkillShareRequest.status == "approved",
        )
    )
    parent = Skill(slug=slug, share_config=share_config, created_by=str(operator.uid), enabled=True)
    available = {skill.slug: skill for skill in await repo.list_enabled()}
    tools, mcps, skills = await validate_skill_dependencies(
        parent=parent,
        tool_dependencies=parsed["tool_dependencies"],
        mcp_dependencies=parsed["mcp_dependencies"],
        skill_dependencies=parsed["skill_dependencies"],
        available_skills=available,
    )

    root = get_skills_root_dir()
    staging = root / f".{slug}.tmp-{uuid.uuid4().hex[:8]}"
    published = root / slug
    moved = False
    try:
        published_metadata = await asyncio.to_thread(
            copy_skill_snapshot, snapshot, staging, expected_slug=item.personal_slug, final_slug=slug
        )
        if published.exists():
            raise ValueError("共享 Skill slug 已被占用，请重试")
        staging.rename(published)
        moved = True
        await repo.create(
            slug=slug,
            name=published_metadata["name"],
            description=published_metadata["description"],
            source_type="personal_share",
            tool_dependencies=tools,
            mcp_dependencies=mcps,
            skill_dependencies=skills,
            dir_path=(Path("shared") / slug).as_posix(),
            share_config=share_config,
            version=f"{int(previous_releases or 0) + 1}.0.0",
            content_hash=await asyncio.to_thread(compute_skill_dir_hash, published),
            created_by=str(operator.uid),
        )
        item.status = "approved"
        item.review_note = note.strip() or None
        item.reviewer_uid = str(operator.uid)
        item.reviewed_at = utc_now_naive()
        item.published_slug = slug
        item.department_ids = share_config["read_scope"]["department_ids"]
        item.read_scope = share_config["read_scope"]
        await db.commit()
        await db.refresh(item)
        return item
    except BaseException:
        await db.rollback()
        if moved:
            shutil.rmtree(published, ignore_errors=True)
        raise
    finally:
        shutil.rmtree(staging, ignore_errors=True)
