"""以一个持久化 Project Workdir 为根的文件视图。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath

from .errors import FileImportConflictError, FileImportLimitError, FileImportRollbackError
from .filesystem import Workspace
from .paths import normalize_workdir_path

MAX_SELECTED_IMPORT_FILES = 100
MAX_SELECTED_IMPORT_FILE_BYTES = 50 * 1024 * 1024
MAX_SELECTED_IMPORT_TOTAL_BYTES = 100 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class Workdir:
    """把浏览 scope `/...` 固定到一个持久化 Workspace 目录。"""

    relative_path: str
    workspace: Workspace

    def __post_init__(self) -> None:
        object.__setattr__(self, "relative_path", normalize_workdir_path(self.relative_path))

    @classmethod
    def open_existing(cls, uid: str, workdir_path: str) -> Workdir:
        """打开已存在的持久化 Workdir，并返回受限文件 capability。"""
        workdir = cls(workdir_path, Workspace(str(uid)))
        if not workdir.stat("/")["is_dir"]:
            raise ValueError("workdir_path does not reference an existing directory")
        return workdir

    @property
    def root_path(self) -> str:
        return f"/{self.relative_path}"

    def resolve_path(self, path: str | None) -> str:
        """把 Workdir scope 路径解析为 UserWorkspace scope 路径。"""
        raw = str(path or "/").strip() or "/"
        pure = PurePosixPath(raw)
        if not pure.is_absolute() or ".." in pure.parts or "\\" in raw or "://" in raw:
            raise ValueError("invalid Workdir scope path")
        normalized = pure.as_posix()
        if normalized == "/":
            return self.root_path
        return f"{self.root_path}{normalized}"

    def scope_path(self, workspace_path: str) -> str:
        """把当前 Workdir 内的 UserWorkspace 路径转换为浏览 scope。"""
        normalized = PurePosixPath(str(workspace_path)).as_posix()
        root = self.root_path.rstrip("/")
        if normalized == root:
            return "/"
        if not normalized.startswith(f"{root}/"):
            raise ValueError("path is outside the Workdir")
        return f"/{normalized[len(root) + 1 :]}"

    def list_directory(self, path: str = "/") -> list[dict]:
        return self.workspace.list_authorized_directory(self.resolve_path(path), root=self.root_path)

    def search(self, query: str, **limits) -> list[dict]:
        matches = self.workspace.search_authorized_tree(self.root_path, query, **limits)
        return [{**item, "path": self.scope_path(str(item["path"]))} for item in matches]

    def read_file(self, path: str, max_bytes: int) -> bytes:
        return self.workspace.read_authorized_file(self.resolve_path(path), max_bytes)

    def read_file_prefix(self, path: str, max_bytes: int) -> tuple[bytes, bool]:
        return self.workspace.read_authorized_file_prefix(self.resolve_path(path), max_bytes)

    def write_file(self, path: str, content: bytes) -> dict:
        return self.workspace.write_authorized_file(self.resolve_path(path), content)

    def stat(self, path: str) -> dict:
        return self.workspace.stat_authorized_path(self.resolve_path(path), root=self.root_path)

    def copy_file_to_path(self, path: str, target_path: str, max_bytes: int) -> int:
        return self.workspace.download_authorized_file_to_path(self.resolve_path(path), target_path, max_bytes)

    def copy_file_from_path(self, path: str, source_path: str, *, overwrite: bool = True) -> dict:
        return self.workspace.upload_authorized_file_from_path(
            self.resolve_path(path),
            source_path,
            overwrite=overwrite,
        )

    def copy_selected_files_from(self, source: Workdir, files: list[dict[str, str]]) -> dict:
        """将明确选中的源文件复制到本 Workdir，保留源文件并拒绝覆盖。"""
        if not self.workspace.same_workspace(source.workspace):
            raise PermissionError("cross-user Workdir import is not allowed")
        if len(files) > MAX_SELECTED_IMPORT_FILES:
            raise FileImportLimitError("too many selected files")

        plans: list[dict] = []
        conflicts: list[dict[str, str]] = []
        seen_destinations: set[str] = set()
        planned_bytes = 0
        for item in files:
            source_path = source._canonical_file_scope(item["source_path"])
            destination_path = self._canonical_file_scope(item.get("destination_path", source_path))
            conflict_reason = None
            if destination_path in seen_destinations:
                conflict_reason = "duplicate_destination"
            seen_destinations.add(destination_path)
            if conflict_reason is None and source.resolve_path(source_path) == self.resolve_path(destination_path):
                conflict_reason = "same_file"

            source_info = source.stat(source_path)
            if source_info["is_dir"]:
                raise IsADirectoryError(source_path)
            size = int(source_info["size"])
            if size > MAX_SELECTED_IMPORT_FILE_BYTES:
                raise FileImportLimitError("selected file exceeds import limit")
            planned_bytes += size
            if planned_bytes > MAX_SELECTED_IMPORT_TOTAL_BYTES:
                raise FileImportLimitError("selected files exceed total import limit")

            if conflict_reason is None:
                try:
                    self.stat(destination_path)
                except FileNotFoundError:
                    pass
                except (NotADirectoryError, PermissionError):
                    conflict_reason = "destination_unavailable"
                else:
                    conflict_reason = "already_exists"
            if conflict_reason is not None:
                conflicts.append(
                    {"source_path": source_path, "destination_path": destination_path, "reason": conflict_reason}
                )
            plans.append({"source_path": source_path, "destination_path": destination_path})

        destination_paths = {plan["destination_path"] for plan in plans}
        for plan in plans:
            parent = PurePosixPath(plan["destination_path"]).parent
            while parent != PurePosixPath("/"):
                if parent.as_posix() in destination_paths:
                    if not any(
                        conflict["source_path"] == plan["source_path"]
                        and conflict["destination_path"] == plan["destination_path"]
                        for conflict in conflicts
                    ):
                        conflicts.append({**plan, "reason": "selected_parent_is_file"})
                    break
                parent = parent.parent
        if conflicts:
            raise FileImportConflictError(conflicts)

        copied_files: list[dict] = []
        created_paths: list[dict] = []
        total_bytes = 0
        try:
            for plan in plans:
                source_path = plan["source_path"]
                destination_path = plan["destination_path"]
                try:
                    identity = self.workspace.copy_authorized_file_from_workspace(
                        source.workspace,
                        source.resolve_path(source_path),
                        self.resolve_path(destination_path),
                        max_bytes=min(MAX_SELECTED_IMPORT_FILE_BYTES, MAX_SELECTED_IMPORT_TOTAL_BYTES - total_bytes),
                    )
                except FileExistsError as exc:
                    raise FileImportConflictError([{**plan, "reason": "already_exists"}]) from exc
                created_paths.append({"path": destination_path, **identity})
                copied_files.append({**plan, "size": identity["size"]})
                total_bytes += identity["size"]
        except BaseException:
            self.remove_imported_files(created_paths)
            raise
        return {
            "files": copied_files,
            "count": len(copied_files),
            "total_bytes": total_bytes,
            "created_paths": created_paths,
        }

    def remove_imported_files(self, created_paths: list[dict]) -> None:
        """回滚本次导入中仍保持原样的文件，不触碰并发改动。"""
        failed: list[str] = []
        for record in reversed(created_paths):
            path = record["path"]
            try:
                self.workspace.remove_authorized_imported_file(
                    self.resolve_path(path),
                    root=self.root_path,
                    identity=record,
                )
            except Exception:
                failed.append(path)
        if failed:
            raise FileImportRollbackError(failed)

    def _canonical_file_scope(self, path: str) -> str:
        """把用户选择规范为本 Workdir 内非根文件路径。"""
        scope = self.scope_path(self.resolve_path(path))
        if scope == "/":
            raise IsADirectoryError(path)
        return scope

    def create_directory(self, parent_path: str, name: str) -> dict:
        return self.workspace.create_authorized_directory(
            self.resolve_path(parent_path),
            name,
            root=self.root_path,
        )

    def delete(self, path: str) -> None:
        self.workspace.delete_authorized_path(self.resolve_path(path), root=self.root_path)
