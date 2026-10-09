from types import SimpleNamespace

import pytest

from yuxi.services import workspace_service as svc

pytestmark = [pytest.mark.asyncio, pytest.mark.unit]


class _ProjectRepository:
    def __init__(self, _db):
        pass

    async def list_workspace_project_paths_for_user(self, uid: str) -> list[str]:
        assert uid == "user-1"
        return ["projects/client", "projects/group/nested", "outside"]


async def test_project_tree_hides_anonymous_directories_and_keeps_selected_ancestors(monkeypatch):
    monkeypatch.setattr(svc, "ProjectRepository", _ProjectRepository)
    entries = [
        {"path": "/projects/", "is_dir": True},
        {"path": "/projects/anonymous/", "is_dir": True},
        {"path": "/projects/client/", "is_dir": True},
        {"path": "/projects/client/report.txt", "is_dir": False},
        {"path": "/projects/group/", "is_dir": True},
        {"path": "/projects/group/nested/", "is_dir": True},
        {"path": "/projects/orphan.txt", "is_dir": False},
        {"path": "/notes/", "is_dir": True},
    ]

    filtered = await svc._filter_project_tree_entries(
        entries,
        uid="user-1",
        db=SimpleNamespace(),
    )

    assert [entry["path"] for entry in filtered] == [
        "/projects/",
        "/projects/client/",
        "/projects/client/report.txt",
        "/projects/group/",
        "/projects/group/nested/",
        "/notes/",
    ]


async def test_selected_projects_root_exposes_its_complete_subtree(monkeypatch):
    class _RootProjectRepository:
        def __init__(self, _db):
            pass

        async def list_workspace_project_paths_for_user(self, _uid: str) -> list[str]:
            return ["projects"]

    monkeypatch.setattr(svc, "ProjectRepository", _RootProjectRepository)
    entries = [
        {"path": "/projects/anonymous/", "is_dir": True},
        {"path": "/projects/anonymous/report.txt", "is_dir": False},
    ]

    assert await svc._filter_project_tree_entries(
        entries,
        uid="user-1",
        db=SimpleNamespace(),
    ) == entries


async def test_tree_without_projects_descendants_does_not_query_projects(monkeypatch):
    class _UnexpectedRepository:
        def __init__(self, _db):
            raise AssertionError("普通 Workspace 目录不应读取 Project")

    monkeypatch.setattr(svc, "ProjectRepository", _UnexpectedRepository)
    entries = [{"path": "/notes/readme.md", "is_dir": False}]

    assert await svc._filter_project_tree_entries(
        entries,
        uid="user-1",
        db=SimpleNamespace(),
    ) == entries


async def test_project_picker_tree_keeps_unbound_project_directories(monkeypatch):
    """Project 选目录用途绕过展示投影，但仍复用 Workspace 文件边界。"""

    entries = [{"path": "/projects/unbound/", "name": "unbound", "is_dir": True}]
    monkeypatch.setattr(svc, "_workspace_backend", lambda _user: SimpleNamespace())
    monkeypatch.setattr(svc, "_list_workspace_directory", lambda *_args, **_kwargs: entries)

    result = await svc.list_workspace_tree(
        path="/projects",
        include_unbound_project_dirs=True,
        current_user=SimpleNamespace(uid="user-1"),
        db=SimpleNamespace(),
    )

    assert result == {"entries": entries}


async def test_workspace_tree_keeps_deleted_project_files_without_exposing_anonymous_dirs():
    """真实目录归属查询保留软删除项目，旧根绑定不能展开匿名目录。"""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from yuxi.storage.postgres.models_business import Base, Project, User

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with async_sessionmaker(engine)() as db:
            db.add_all(
                [
                    User(uid="user-1", username="user-1", password_hash="test"),
                    User(uid="user-2", username="user-2", password_hash="test"),
                ]
            )
            await db.flush()
            cases = [
                ("kept", "user-1", "selectable", "deleted", "managed", "projects/kept"),
                ("linked", "user-1", "selectable", "deleted", "linked", "projects/linked"),
                ("root", "user-1", "selectable", "deleted", "linked", "projects"),
                ("implicit", "user-1", "implicit", "active", "managed", "projects/implicit"),
                ("other-user", "user-2", "selectable", "deleted", "managed", "projects/other-user"),
            ]
            for key, uid, selection, status, mode, path in cases:
                db.add(
                    Project(
                        id=key,
                        uid=uid,
                        selection_status=selection,
                        status=status,
                        directory_mode=mode,
                        workdir_path=path,
                    )
                )
            await db.flush()
            paths = [
                "/projects/",
                "/projects/kept/",
                "/projects/kept/file.txt",
                "/projects/linked/",
                "/projects/implicit/",
                "/projects/other-user/",
                "/projects/anonymous/",
            ]
            result = await svc._filter_project_tree_entries([{"path": path} for path in paths], uid="user-1", db=db)
            assert [entry["path"] for entry in result] == paths[:4]
    finally:
        await engine.dispose()
