import asyncio
import threading
from datetime import datetime
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi import HTTPException

from yuxi.services import project_service as svc
from yuxi.workspace.paths import ensure_bound_user_workdir, user_workspace_dir

pytestmark = [pytest.mark.asyncio, pytest.mark.unit]


class _Db:
    def __init__(self):
        self.items = []
        self.commits = 0

    def add(self, item):
        self.items.append(item)

    async def flush(self):
        return None

    async def commit(self):
        self.commits += 1

    async def refresh(self, _item):
        return None

    async def execute(self, _statement, _params=None):
        return None

    async def scalar(self, _statement):
        return None

    async def scalars(self, _statement):
        return SimpleNamespace(all=lambda: [])


async def test_manual_managed_project_creates_named_directory(monkeypatch, tmp_path: Path):
    monkeypatch.setattr("yuxi.workspace.paths.get_user_data_dir", lambda: tmp_path)
    monkeypatch.setattr(svc.uuid, "uuid4", lambda: UUID("a1b2c3d4-e5f6-4789-8123-456789abcdef"))
    db = _Db()
    result = await svc.create_project_view(
        uid="user-1",
        request_id="request-managed",
        name="  项目/示例  ",
        directory_mode="managed",
        workdir_path=None,
        db=db,
    )
    assert result["directory_mode"] == "managed"
    assert result["workdir_path"] == "projects/项目-示例_a1b2c3d4"
    assert (user_workspace_dir("user-1") / result["workdir_path"]).is_dir()
    assert db.commits == 1


async def test_cancelled_directory_cleanup_waits_for_filesystem_thread(monkeypatch, tmp_path: Path):
    monkeypatch.setattr("yuxi.workspace.paths.get_user_data_dir", lambda: tmp_path)
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    def delayed_delete(_workspace, _path):
        started.set()
        release.wait(timeout=2)
        finished.set()

    monkeypatch.setattr(svc.Workspace, "delete_project_workdir", delayed_delete)
    task = asyncio.create_task(svc._delete_project_workdir_and_wait("user-1", "projects/example_12345678"))
    try:
        assert await asyncio.to_thread(started.wait, 1)
        task.cancel()
        await asyncio.sleep(0.02)
        assert not task.done()
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert finished.is_set()


async def test_implicit_project_uses_timestamped_managed_workdir(monkeypatch, tmp_path: Path):
    monkeypatch.setattr("yuxi.workspace.paths.get_user_data_dir", lambda: tmp_path)
    monkeypatch.setattr(
        "yuxi.workspace.paths.shanghai_now",
        lambda: datetime.fromisoformat("2026-09-02T14:35:08+08:00"),
    )
    monkeypatch.setattr(svc.uuid, "uuid4", lambda: UUID("a1b2c3d4-e5f6-4789-8123-456789abcdef"))

    project = await svc.create_implicit_project(uid="user-1", db=_Db())

    assert project.id == "a1b2c3d4-e5f6-4789-8123-456789abcdef"
    assert project.workdir_path == "projects/2026-09-02_14-35-08_a1b2c3d4"
    assert not (user_workspace_dir("user-1") / project.workdir_path).exists()


@pytest.mark.parametrize(
    ("directory_mode", "workdir_path"),
    [
        ("managed", "clients/acme"),
        ("linked", None),
        ("linked", "clients/acme"),
    ],
)
async def test_manual_project_rejects_existing_directory(directory_mode: str, workdir_path: str | None):
    with pytest.raises(HTTPException) as exc:
        await svc.create_project_view(
            uid="user-1",
            request_id="request-manual-without-directory",
            name="Invalid",
            directory_mode=directory_mode,
            workdir_path=workdir_path,
            db=_Db(),
        )

    assert exc.value.status_code == 422
    assert exc.value.detail == "新建项目只支持自动创建专属目录"


async def test_internal_project_creation_also_rejects_new_linked_binding():
    with pytest.raises(HTTPException) as exc:
        await svc.create_project_record(
            uid="user-1",
            name="Legacy",
            directory_mode="linked",
            selection_status="selectable",
            workdir_path="clients/acme",
            db=_Db(),
        )
    assert exc.value.status_code == 422


async def test_new_managed_project_rejects_existing_parent_binding(monkeypatch, tmp_path: Path):
    monkeypatch.setattr("yuxi.workspace.paths.get_user_data_dir", lambda: tmp_path)

    class _DbWithProjectsParent(_Db):
        async def scalars(self, _statement):
            return SimpleNamespace(all=lambda: ["projects"])

    db = _DbWithProjectsParent()
    with pytest.raises(HTTPException) as exc:
        await svc.create_project_view(
            uid="user-1",
            request_id="new-project",
            name="新项目",
            directory_mode="managed",
            workdir_path=None,
            db=db,
        )
    assert exc.value.status_code == 409
    assert db.commits == 0
    assert not (user_workspace_dir("user-1") / "projects").exists()


@pytest.mark.parametrize(
    ("directory_mode", "path", "overlap", "active", "expected"),
    [
        ("linked", "clients/acme", False, False, 422),
        ("managed", "projects/other_aaaaaaaa", False, False, 409),
        ("managed", "projects/demo_aaaaaaaa", False, False, 409),
        ("managed", "projects/demo_a1b2c3d4", True, False, 409),
        ("managed", "projects/demo_a1b2c3d4", False, True, 409),
    ],
)
async def test_directory_deletion_rejects_unowned_or_busy_paths(
    directory_mode: str,
    path: str,
    overlap: bool,
    active: bool,
    expected: int,
):
    project = SimpleNamespace(
        id="a1b2c3d4-e5f6-4789-8123-456789abcdef",
        directory_mode=directory_mode,
        workdir_path=path,
    )

    class _Repository:
        async def has_other_workdir_overlap(self, _project):
            return overlap

        async def has_active_project_work(self, _project):
            return active

    with pytest.raises(HTTPException) as exc:
        await svc._validate_managed_directory_deletion(project=project, repository=_Repository())
    assert exc.value.status_code == expected


async def test_project_workdir_delete_preserves_outside_files_and_is_retryable(monkeypatch, tmp_path: Path):
    monkeypatch.setattr("yuxi.workspace.paths.get_user_data_dir", lambda: tmp_path)
    uid = "user-1"
    project_id = "a1b2c3d4-e5f6-4789-8123-456789abcdef"
    path = "projects/demo_a1b2c3d4"
    ensure_bound_user_workdir(uid, path)
    workdir = user_workspace_dir(uid) / path
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    (workdir / "safe.txt").write_text("safe", encoding="utf-8")
    (workdir / "link").symlink_to(outside)
    project = SimpleNamespace(
        id=project_id,
        uid=uid,
        status="deleted",
        directory_mode="managed",
        workdir_path=path,
    )

    class _Repository:
        def __init__(self, _db):
            pass

        async def get_for_user(self, _project_id, _uid):
            return project

        async def has_other_workdir_overlap(self, _project):
            return False

        async def has_active_project_work(self, _project):
            return False

    @asynccontextmanager
    async def fake_session():
        yield _Db()

    async def fake_lock(**_kwargs):
        return None

    async def not_cancelled():
        return None

    monkeypatch.setattr(svc, "ProjectRepository", _Repository)
    monkeypatch.setattr(svc, "_lock_project_workdir_changes", fake_lock)
    monkeypatch.setattr(svc.pg_manager, "get_async_session_context", fake_session)
    context = SimpleNamespace(payload={"uid": uid, "project_id": project_id}, raise_if_cancelled=not_cancelled)

    real_delete = svc.Workspace.delete_project_workdir

    def missing_child(_workspace, _path):
        raise FileNotFoundError("a child disappeared during recursive deletion")

    monkeypatch.setattr(svc.Workspace, "delete_project_workdir", missing_child)
    with pytest.raises(FileNotFoundError, match="child disappeared"):
        await svc.run_project_workdir_delete(context)
    monkeypatch.setattr(svc.Workspace, "delete_project_workdir", real_delete)
    assert workdir.exists()
    assert outside.read_text(encoding="utf-8") == "outside"

    assert await svc.run_project_workdir_delete(context) == {"deleted_workdir": path}
    assert not workdir.exists()
    assert outside.read_text(encoding="utf-8") == "outside"
    assert await svc.run_project_workdir_delete(context) == {"deleted_workdir": path}


async def test_rename_project_updates_only_active_selectable_project(monkeypatch):
    project = SimpleNamespace(
        name="Old",
        updated_at=None,
        to_dict=lambda: {"id": "project-1", "name": project.name},
    )

    class _ProjectRepository:
        def __init__(self, _db):
            pass

        async def lock_active_selectable_for_user(self, project_id, uid):
            assert (project_id, uid) == ("project-1", "user-1")
            return project

    monkeypatch.setattr(svc, "ProjectRepository", _ProjectRepository)
    db = _Db()

    result = await svc.rename_project_view(
        uid="user-1",
        project_id="project-1",
        name="  New name  ",
        db=db,
    )

    assert result == {"id": "project-1", "name": "New name"}
    assert db.commits == 1


async def test_rename_project_rejects_missing_or_deleted_project(monkeypatch):
    class _ProjectRepository:
        def __init__(self, _db):
            pass

        async def lock_active_selectable_for_user(self, *_args, **_kwargs):
            return None

    monkeypatch.setattr(svc, "ProjectRepository", _ProjectRepository)

    with pytest.raises(HTTPException) as exc:
        await svc.rename_project_view(uid="user-1", project_id="missing", name="New", db=_Db())

    assert exc.value.status_code == 404


async def test_rename_project_rejects_blank_name_before_write():
    with pytest.raises(HTTPException) as exc:
        await svc.rename_project_view(uid="user-1", project_id="project-1", name="   ", db=_Db())

    assert exc.value.status_code == 422


async def test_delete_project_soft_deletes_all_conversations_in_one_commit(monkeypatch):
    project = SimpleNamespace(id="project-1")
    calls = []

    class _ProjectRepository:
        def __init__(self, _db):
            pass

        async def lock_active_selectable_for_user(self, project_id, uid):
            assert (project_id, uid) == ("project-1", "user-1")
            return project

        async def soft_delete_with_conversations(self, actual_project, *, deleted_at):
            calls.append((actual_project, deleted_at))
            return 3

    monkeypatch.setattr(svc, "ProjectRepository", _ProjectRepository)
    db = _Db()

    result = await svc.delete_project_view(uid="user-1", project_id="project-1", db=db)

    assert result == {"message": "删除成功", "deleted_conversations": 3, "workdir_delete_task_id": None}
    assert calls[0][0] is project
    assert db.commits == 1
