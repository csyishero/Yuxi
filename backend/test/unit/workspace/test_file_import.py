"""选中文件跨 Project Workdir 导入的文件边界。"""

from __future__ import annotations

from pathlib import Path

import pytest

from yuxi.workspace import filesystem as filesystem_module
from yuxi.workspace.errors import FileImportConflictError, FileImportLimitError, FileImportRollbackError
from yuxi.workspace.filesystem import Workspace
from yuxi.workspace.workdir import Workdir


def _workdirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Workdir, Workdir, Path, Path]:
    """创建同一用户的两个真实 Workdir。"""
    workspace_root = tmp_path / "workspace"
    source_root = workspace_root / "projects" / "source"
    target_root = workspace_root / "projects" / "target"
    source_root.mkdir(parents=True)
    target_root.mkdir()
    monkeypatch.setattr(filesystem_module, "user_workspace_dir", lambda _uid: workspace_root)
    return (
        Workdir("projects/source", Workspace("user-1")),
        Workdir("projects/target", Workspace("user-1")),
        source_root,
        target_root,
    )


def test_import_copies_only_selected_regular_files_and_preserves_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, target, source_root, target_root = _workdirs(tmp_path, monkeypatch)
    (source_root / "notes").mkdir()
    (source_root / "notes" / "selected.txt").write_text("selected", encoding="utf-8")
    (source_root / "notes" / "unselected.txt").write_text("unselected", encoding="utf-8")

    result = target.copy_selected_files_from(source, [{"source_path": "/notes/selected.txt"}])

    assert result["files"] == [
        {"source_path": "/notes/selected.txt", "destination_path": "/notes/selected.txt", "size": 8}
    ]
    assert result["count"] == 1
    assert result["total_bytes"] == 8
    assert [item["path"] for item in result["created_paths"]] == ["/notes/selected.txt"]
    assert (target_root / "notes" / "selected.txt").read_text(encoding="utf-8") == "selected"
    assert not (target_root / "notes" / "unselected.txt").exists()
    assert (source_root / "notes" / "selected.txt").read_text(encoding="utf-8") == "selected"

    target.remove_imported_files(result["created_paths"])
    assert not (target_root / "notes" / "selected.txt").exists()
    assert (source_root / "notes" / "selected.txt").exists()


def test_import_accepts_chosen_destination_without_overwriting_existing_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, target, source_root, target_root = _workdirs(tmp_path, monkeypatch)
    (source_root / "report.txt").write_text("new", encoding="utf-8")
    (target_root / "report.txt").write_text("old", encoding="utf-8")

    with pytest.raises(FileImportConflictError) as exc:
        target.copy_selected_files_from(source, [{"source_path": "/report.txt"}])
    assert exc.value.conflicts == [
        {"source_path": "/report.txt", "destination_path": "/report.txt", "reason": "already_exists"}
    ]
    assert (target_root / "report.txt").read_text(encoding="utf-8") == "old"

    result = target.copy_selected_files_from(
        source, [{"source_path": "/report.txt", "destination_path": "/archive/report-new.txt"}]
    )
    assert result["files"][0]["destination_path"] == "/archive/report-new.txt"
    assert (target_root / "archive" / "report-new.txt").read_text(encoding="utf-8") == "new"
    assert (target_root / "report.txt").read_text(encoding="utf-8") == "old"


def test_import_preflights_every_conflict_before_copying(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source, target, source_root, target_root = _workdirs(tmp_path, monkeypatch)
    (source_root / "one.txt").write_text("one", encoding="utf-8")
    (source_root / "two.txt").write_text("two", encoding="utf-8")
    (target_root / "two.txt").write_text("existing", encoding="utf-8")

    with pytest.raises(FileImportConflictError) as exc:
        target.copy_selected_files_from(source, [{"source_path": "/one.txt"}, {"source_path": "/two.txt"}])

    assert exc.value.conflicts == [
        {"source_path": "/two.txt", "destination_path": "/two.txt", "reason": "already_exists"}
    ]
    assert not (target_root / "one.txt").exists()
    assert (target_root / "two.txt").read_text(encoding="utf-8") == "existing"


def test_import_rejects_duplicate_and_nested_destinations(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source, target, source_root, target_root = _workdirs(tmp_path, monkeypatch)
    (source_root / "one.txt").write_text("one", encoding="utf-8")
    (source_root / "two.txt").write_text("two", encoding="utf-8")

    with pytest.raises(FileImportConflictError) as duplicate:
        target.copy_selected_files_from(
            source,
            [
                {"source_path": "/one.txt", "destination_path": "/copy.txt"},
                {"source_path": "/two.txt", "destination_path": "/copy.txt"},
            ],
        )
    assert {item["reason"] for item in duplicate.value.conflicts} == {"duplicate_destination"}

    with pytest.raises(FileImportConflictError) as nested:
        target.copy_selected_files_from(
            source,
            [
                {"source_path": "/one.txt", "destination_path": "/copy.txt"},
                {"source_path": "/two.txt", "destination_path": "/copy.txt/child.txt"},
            ],
        )
    assert {item["reason"] for item in nested.value.conflicts} == {"selected_parent_is_file"}
    assert list(target_root.iterdir()) == []


def test_import_rejects_same_source_and_target_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source, _target, source_root, _target_root = _workdirs(tmp_path, monkeypatch)
    (source_root / "one.txt").write_text("one", encoding="utf-8")

    with pytest.raises(FileImportConflictError) as exc:
        source.copy_selected_files_from(source, [{"source_path": "/one.txt"}])

    assert "same_file" in {item["reason"] for item in exc.value.conflicts}
    assert (source_root / "one.txt").read_text(encoding="utf-8") == "one"


def test_import_rejects_symlink_source_and_target_without_following(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, target, source_root, target_root = _workdirs(tmp_path, monkeypatch)
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    (source_root / "linked.txt").symlink_to(outside)

    with pytest.raises(PermissionError):
        target.copy_selected_files_from(source, [{"source_path": "/linked.txt"}])
    assert list(target_root.iterdir()) == []

    (source_root / "regular.txt").write_text("regular", encoding="utf-8")
    (target_root / "regular.txt").symlink_to(outside)
    with pytest.raises(FileImportConflictError) as exc:
        target.copy_selected_files_from(source, [{"source_path": "/regular.txt"}])
    assert exc.value.conflicts[0]["reason"] == "destination_unavailable"
    assert outside.read_text(encoding="utf-8") == "outside"


def test_import_rejects_symlink_destination_parent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source, target, source_root, target_root = _workdirs(tmp_path, monkeypatch)
    outside = tmp_path / "outside"
    outside.mkdir()
    (source_root / "regular.txt").write_text("regular", encoding="utf-8")
    (target_root / "linked").symlink_to(outside, target_is_directory=True)

    with pytest.raises(FileImportConflictError) as exc:
        target.copy_selected_files_from(
            source, [{"source_path": "/regular.txt", "destination_path": "/linked/copied.txt"}]
        )

    assert exc.value.conflicts[0]["reason"] == "destination_unavailable"
    assert not (outside / "copied.txt").exists()


def test_import_rolls_back_earlier_copies_when_destination_appears(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, target, source_root, target_root = _workdirs(tmp_path, monkeypatch)
    (source_root / "one.txt").write_text("one", encoding="utf-8")
    (source_root / "two.txt").write_text("two", encoding="utf-8")
    real_copy = target.workspace.copy_authorized_file_from_workspace
    calls = 0

    def copy_with_race(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            (target_root / "two.txt").write_text("concurrent", encoding="utf-8")
        return real_copy(*args, **kwargs)

    monkeypatch.setattr(target.workspace, "copy_authorized_file_from_workspace", copy_with_race)

    with pytest.raises(FileImportConflictError) as exc:
        target.copy_selected_files_from(source, [{"source_path": "/one.txt"}, {"source_path": "/two.txt"}])

    assert exc.value.conflicts == [
        {"source_path": "/two.txt", "destination_path": "/two.txt", "reason": "already_exists"}
    ]
    assert not (target_root / "one.txt").exists()
    assert (target_root / "two.txt").read_text(encoding="utf-8") == "concurrent"


def test_import_rollback_keeps_file_changed_after_copy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source, target, source_root, target_root = _workdirs(tmp_path, monkeypatch)
    (source_root / "one.txt").write_text("one", encoding="utf-8")
    result = target.copy_selected_files_from(source, [{"source_path": "/one.txt"}])
    (target_root / "one.txt").write_text("changed", encoding="utf-8")

    with pytest.raises(FileImportRollbackError) as exc:
        target.remove_imported_files(result["created_paths"])

    assert exc.value.paths == ["/one.txt"]
    assert (target_root / "one.txt").read_text(encoding="utf-8") == "changed"


def test_import_rollback_preserves_file_replaced_between_check_and_remove(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, target, source_root, target_root = _workdirs(tmp_path, monkeypatch)
    (source_root / "one.txt").write_text("one", encoding="utf-8")
    result = target.copy_selected_files_from(source, [{"source_path": "/one.txt"}])
    real_rename = filesystem_module.os.rename
    replaced = False

    def replace_just_before_rename(source_name, destination_name, *, src_dir_fd=None, dst_dir_fd=None):
        nonlocal replaced
        if source_name == "one.txt" and not replaced:
            replaced = True
            (target_root / "one.txt").unlink()
            (target_root / "one.txt").write_text("new owner file", encoding="utf-8")
        return real_rename(source_name, destination_name, src_dir_fd=src_dir_fd, dst_dir_fd=dst_dir_fd)

    monkeypatch.setattr(filesystem_module.os, "rename", replace_just_before_rename)
    with pytest.raises(FileImportRollbackError):
        target.remove_imported_files(result["created_paths"])

    assert replaced
    assert (target_root / "one.txt").read_text(encoding="utf-8") == "new owner file"
    assert not list(target_root.glob(".yuxi-rollback-*"))


def test_import_enforces_file_count_and_byte_limits_before_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, target, source_root, target_root = _workdirs(tmp_path, monkeypatch)
    (source_root / "huge.txt").write_bytes(b"")
    with (source_root / "huge.txt").open("r+b") as file:
        file.truncate(50 * 1024 * 1024 + 1)

    with pytest.raises(FileImportLimitError):
        target.copy_selected_files_from(source, [{"source_path": "/huge.txt"}])
    with pytest.raises(FileImportLimitError):
        target.copy_selected_files_from(source, [{"source_path": "/huge.txt"}] * 101)
    assert list(target_root.iterdir()) == []


def test_import_enforces_total_byte_limit_before_copy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source, target, source_root, target_root = _workdirs(tmp_path, monkeypatch)
    selections = []
    for index in range(3):
        name = f"file-{index}.bin"
        with (source_root / name).open("wb") as file:
            file.truncate(40 * 1024 * 1024)
        selections.append({"source_path": f"/{name}"})

    with pytest.raises(FileImportLimitError, match="total"):
        target.copy_selected_files_from(source, selections)
    assert list(target_root.iterdir()) == []


@pytest.mark.parametrize("invalid_path", ["relative.txt", "/../outside.txt", "\\outside.txt", "/"])
def test_import_rejects_paths_outside_file_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, invalid_path: str
) -> None:
    source, target, source_root, target_root = _workdirs(tmp_path, monkeypatch)
    (source_root / "one.txt").write_text("one", encoding="utf-8")

    with pytest.raises((ValueError, IsADirectoryError)):
        target.copy_selected_files_from(source, [{"source_path": "/one.txt", "destination_path": invalid_path}])
    assert list(target_root.iterdir()) == []


def test_import_rejects_other_users_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(filesystem_module, "user_workspace_dir", lambda uid: tmp_path / uid)
    source_root = tmp_path / "user-1" / "projects" / "source"
    target_root = tmp_path / "user-2" / "projects" / "target"
    source_root.mkdir(parents=True)
    target_root.mkdir(parents=True)
    (source_root / "one.txt").write_text("one", encoding="utf-8")
    source = Workdir("projects/source", Workspace("user-1"))
    target = Workdir("projects/target", Workspace("user-2"))

    with pytest.raises(PermissionError, match="cross-user"):
        target.copy_selected_files_from(source, [{"source_path": "/one.txt"}])
    assert list(target_root.iterdir()) == []
