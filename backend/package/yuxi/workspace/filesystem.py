"""当前用户持久化 UserWorkspace 的 no-follow 文件访问边界。"""

from __future__ import annotations

import errno
import itertools
import os
import stat
import uuid
from pathlib import Path, PurePosixPath

from yuxi.utils.paths import open_directory_fd, open_regular_file_fd

from .errors import FileImportLimitError, FileTransferLimitError
from .paths import user_workspace_dir


class Workspace:
    """以 uid 为边界访问持久化 UserWorkspace。"""

    def __init__(self, uid: str):
        uid = str(uid)
        self._workspace_root = user_workspace_dir(uid)

    def same_workspace(self, other: Workspace) -> bool:
        """确认两个 Workdir 属于同一用户文件根。"""
        return self._workspace_root == other._workspace_root

    def list_authorized_directory(self, path: str, *, root: str, max_entries: int | None = None) -> list[dict]:
        """列出 Workdir 内的普通文件与真实目录。"""
        if max_entries is not None:
            entries, examined = self._list_authorized_directory_limited(path, root=root, max_entries=max_entries + 1)
            if examined > max_entries:
                raise ValueError("directory entry limit exceeded")
            return entries
        self._require_within(path, root)
        base, parts = self._resolve_path(path)
        directory_fd = self._open_directory(base, parts)
        try:
            entries = []
            for name in sorted(os.listdir(directory_fd), key=str.lower):
                item_stat = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
                if not (stat.S_ISDIR(item_stat.st_mode) or stat.S_ISREG(item_stat.st_mode)):
                    continue
                entries.append({"name": name, **self._metadata_from_stat(item_stat)})
            return entries
        finally:
            os.close(directory_fd)

    def search_authorized_tree(
        self,
        root: str,
        query: str,
        *,
        include_directories: bool = True,
        exclude_directories: frozenset[str] = frozenset(),
        exclude_hidden: bool = False,
        max_results: int = 500,
        max_directories: int = 600,
        max_depth: int = 15,
        max_entries_per_directory: int = 500,
        max_scanned_entries: int = 10_000,
    ) -> list[dict]:
        """在授权根内执行有界实时扫描并返回文件名或路径匹配项。"""
        normalized_query = str(query or "").strip().lower()
        pending: list[tuple[str, int]] = [(root, 0)]
        results: list[dict] = []
        visited_directories = 0
        scanned_entries = 0

        while pending and visited_directories < max_directories and scanned_entries < max_scanned_entries:
            directory, depth = pending.pop(0)
            visited_directories += 1
            remaining_entries = max_scanned_entries - scanned_entries
            entries, examined_entries = self._list_authorized_directory_limited(
                directory,
                root=root,
                max_entries=min(max_entries_per_directory, remaining_entries),
            )
            scanned_entries += examined_entries
            for item in entries:
                name = str(item["name"])
                path = f"{directory.rstrip('/')}/{name}"
                is_dir = bool(item["is_dir"])
                excluded = is_dir and (name in exclude_directories or (exclude_hidden and name.startswith(".")))
                if is_dir and not excluded and depth < max_depth:
                    pending.append((path, depth + 1))

                matched = not normalized_query or normalized_query in name.lower() or normalized_query in path.lower()
                if matched and not excluded and (include_directories or not is_dir):
                    results.append({"path": path, **item})
                    if len(results) >= max_results:
                        return results
        return results

    def _list_authorized_directory_limited(
        self,
        path: str,
        *,
        root: str,
        max_entries: int,
    ) -> tuple[list[dict], int]:
        """最多检查指定数量的目录项，避免宽目录触发全量 I/O。"""
        if max_entries < 0:
            raise ValueError("directory entry limit must be non-negative")
        self._require_within(path, root)
        base, parts = self._resolve_path(path)
        directory_fd = self._open_directory(base, parts)
        entries: list[dict] = []
        examined_entries = 0
        try:
            with os.scandir(directory_fd) as iterator:
                for entry in itertools.islice(iterator, max_entries):
                    examined_entries += 1
                    item_stat = entry.stat(follow_symlinks=False)
                    if not (stat.S_ISDIR(item_stat.st_mode) or stat.S_ISREG(item_stat.st_mode)):
                        continue
                    entries.append({"name": entry.name, **self._metadata_from_stat(item_stat)})
        finally:
            os.close(directory_fd)
        entries.sort(key=lambda item: str(item["name"]).lower())
        return entries, examined_entries

    def download_authorized_file_to_path(self, path: str, target_path: str, max_bytes: int) -> int:
        """把授权普通文件有界复制到服务临时文件。"""
        if max_bytes < 0:
            raise ValueError("file download limit must be non-negative")
        target_fd = None
        with self._open_regular_file(path, writable=False) as (source_fd, _source_stat):
            try:
                target_fd = os.open(target_path, os.O_WRONLY | os.O_TRUNC | os.O_NOFOLLOW)
                total = 0
                while chunk := os.read(source_fd, 1024 * 1024):
                    total += len(chunk)
                    if total > max_bytes:
                        raise FileTransferLimitError("file exceeds transfer limit")
                    self._write_all(target_fd, chunk)
                return total
            finally:
                if target_fd is not None:
                    os.close(target_fd)

    def read_authorized_file(self, path: str, max_bytes: int) -> bytes:
        """在 no-follow 边界内有界读取普通文件。"""
        if max_bytes < 0:
            raise ValueError("file read limit must be non-negative")
        with self._open_regular_file(path, writable=False) as (source_fd, source_stat):
            if source_stat.st_size > max_bytes:
                raise FileTransferLimitError("file exceeds transfer limit")
            chunks: list[bytes] = []
            total = 0
            while chunk := os.read(source_fd, 1024 * 1024):
                total += len(chunk)
                if total > max_bytes:
                    raise FileTransferLimitError("file exceeds transfer limit")
                chunks.append(chunk)
            return b"".join(chunks)

    def read_authorized_file_prefix(self, path: str, max_bytes: int) -> tuple[bytes, bool]:
        """有界读取普通文件前缀，并报告内容是否被截断。"""
        if max_bytes < 0:
            raise ValueError("file read limit must be non-negative")
        with self._open_regular_file(path, writable=False) as (source_fd, _source_stat):
            chunks: list[bytes] = []
            remaining = max_bytes + 1
            while remaining > 0:
                chunk = os.read(source_fd, min(1024 * 1024, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            content = b"".join(chunks)
        return content[:max_bytes], len(content) > max_bytes

    def write_authorized_file(self, path: str, content: bytes) -> dict:
        """通过已打开的普通文件描述符覆盖内容，拒绝 symlink 与目录。"""
        with self._open_regular_file(path, writable=True) as (target_fd, _target_stat):
            os.ftruncate(target_fd, 0)
            self._write_all(target_fd, content)
            final_stat = os.fstat(target_fd)
            return self._metadata_from_stat(final_stat)

    def replace_authorized_file(self, path: str, content: bytes) -> dict:
        """在同一目录内完整写入并原子替换普通文件。"""
        base, parts = self._resolve_path(path)
        if not parts:
            raise IsADirectoryError(path)

        parent_fd = self._open_directory(base, parts[:-1])
        target_fd = None
        temp_name = f".yuxi-replace-{uuid.uuid4().hex}"
        try:
            try:
                target_stat = os.stat(parts[-1], dir_fd=parent_fd, follow_symlinks=False)
            except FileNotFoundError:
                # 目标不存在时 rename 直接创建，无需校验既有类型
                target_stat = None
            if target_stat is not None:
                if stat.S_ISLNK(target_stat.st_mode):
                    raise PermissionError("symlink paths are not allowed")
                if not stat.S_ISREG(target_stat.st_mode):
                    raise PermissionError("only regular files can be replaced")

            target_fd = os.open(
                temp_name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=parent_fd,
            )
            self._write_all(target_fd, content)
            os.fsync(target_fd)
            final_stat = os.fstat(target_fd)
            os.close(target_fd)
            target_fd = None
            os.rename(temp_name, parts[-1], src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
            os.fsync(parent_fd)
            return self._metadata_from_stat(final_stat)
        finally:
            if target_fd is not None:
                os.close(target_fd)
            try:
                os.unlink(temp_name, dir_fd=parent_fd)
            except FileNotFoundError:
                pass
            os.close(parent_fd)

    def stat_authorized_path(self, path: str, *, root: str) -> dict:
        """在 no-follow 边界内读取普通文件或真实目录元数据。"""
        self._require_within(path, root)
        base, parts = self._resolve_path(path)
        if not parts:
            item_stat = os.stat(base, follow_symlinks=False)
            is_dir = stat.S_ISDIR(item_stat.st_mode)
        else:
            parent_fd = self._open_directory(base, parts[:-1])
            try:
                item_stat = os.stat(parts[-1], dir_fd=parent_fd, follow_symlinks=False)
            finally:
                os.close(parent_fd)
            if stat.S_ISLNK(item_stat.st_mode):
                raise PermissionError("symlink paths are not allowed")
            is_dir = stat.S_ISDIR(item_stat.st_mode)
        if not (is_dir or stat.S_ISREG(item_stat.st_mode)):
            raise PermissionError("only regular files and directories are allowed")
        return self._metadata_from_stat(item_stat)

    def upload_authorized_file_from_path(
        self,
        path: str,
        source_path: str,
        *,
        overwrite: bool = True,
        create_parents: bool = True,
    ) -> dict:
        """从受信任服务临时文件原子写入 UserWorkspace。"""
        base, parts = self._resolve_path(path)
        if not parts:
            raise IsADirectoryError(path)
        parent_fd = self._open_directory(base, parts[:-1], create=create_parents)
        source_fd = target_fd = None
        temp_name = f".yuxi-write-{uuid.uuid4().hex}"
        try:
            source_fd = os.open(source_path, os.O_RDONLY | os.O_NOFOLLOW)
            if not stat.S_ISREG(os.fstat(source_fd).st_mode):
                raise ValueError("upload source is not a regular file")
            target_fd = os.open(
                temp_name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=parent_fd,
            )
            while chunk := os.read(source_fd, 1024 * 1024):
                self._write_all(target_fd, chunk)
            final_stat = os.fstat(target_fd)
            os.close(target_fd)
            target_fd = None
            if overwrite:
                os.rename(temp_name, parts[-1], src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
            else:
                os.link(
                    temp_name,
                    parts[-1],
                    src_dir_fd=parent_fd,
                    dst_dir_fd=parent_fd,
                    follow_symlinks=False,
                )
                os.unlink(temp_name, dir_fd=parent_fd)
            return self._metadata_from_stat(final_stat)
        finally:
            if target_fd is not None:
                os.close(target_fd)
            if source_fd is not None:
                os.close(source_fd)
            try:
                os.unlink(temp_name, dir_fd=parent_fd)
            except FileNotFoundError:
                pass
            os.close(parent_fd)

    def copy_authorized_file_from_workspace(
        self,
        source_workspace: Workspace,
        source_path: str,
        target_path: str,
        *,
        max_bytes: int,
    ) -> dict:
        """从同用户的普通文件流式复制，原子创建目标且绝不覆盖。"""
        if not self.same_workspace(source_workspace):
            raise PermissionError("cross-user Workdir import is not allowed")
        if max_bytes < 0:
            raise ValueError("file import limit must be non-negative")
        base, parts = self._resolve_path(target_path)
        if not parts:
            raise IsADirectoryError(target_path)

        with source_workspace._open_regular_file(source_path, writable=False) as (source_fd, source_stat):
            if source_stat.st_size > max_bytes:
                raise FileImportLimitError("selected file exceeds import limit")
            parent_fd = self._open_directory(base, parts[:-1], create=True)
            target_fd = None
            temp_name = f".yuxi-import-{uuid.uuid4().hex}"
            linked = False
            try:
                target_fd = os.open(
                    temp_name,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                    0o600,
                    dir_fd=parent_fd,
                )
                total = 0
                while chunk := os.read(source_fd, 1024 * 1024):
                    total += len(chunk)
                    if total > max_bytes:
                        raise FileImportLimitError("selected file exceeds import limit")
                    self._write_all(target_fd, chunk)
                final_source_stat = os.fstat(source_fd)
                if (
                    final_source_stat.st_size != source_stat.st_size
                    or final_source_stat.st_mtime_ns != source_stat.st_mtime_ns
                    or final_source_stat.st_ctime_ns != source_stat.st_ctime_ns
                ):
                    raise RuntimeError("source file changed during import")
                os.fsync(target_fd)
                os.link(
                    temp_name,
                    parts[-1],
                    src_dir_fd=parent_fd,
                    dst_dir_fd=parent_fd,
                    follow_symlinks=False,
                )
                linked = True
                os.unlink(temp_name, dir_fd=parent_fd)
                os.fsync(parent_fd)
                final_stat = os.fstat(target_fd)
                return {
                    "size": total,
                    "device": final_stat.st_dev,
                    "inode": final_stat.st_ino,
                    "modified_at_ns": final_stat.st_mtime_ns,
                    "changed_at_ns": final_stat.st_ctime_ns,
                }
            except BaseException:
                if linked:
                    try:
                        current_stat = os.stat(parts[-1], dir_fd=parent_fd, follow_symlinks=False)
                        if target_fd is not None and self._same_imported_file(current_stat, os.fstat(target_fd)):
                            os.unlink(parts[-1], dir_fd=parent_fd)
                    except FileNotFoundError:
                        pass
                raise
            finally:
                if target_fd is not None:
                    os.close(target_fd)
                try:
                    os.unlink(temp_name, dir_fd=parent_fd)
                except FileNotFoundError:
                    pass
                os.close(parent_fd)

    def remove_authorized_imported_file(self, path: str, *, root: str, identity: dict) -> None:
        """仅当目标仍是本次创建且未修改的普通文件时回滚。"""
        self._require_within(path, root, allow_root=False)
        base, parts = self._resolve_path(path)
        try:
            parent_fd = self._open_directory(base, parts[:-1])
        except FileNotFoundError:
            return
        try:
            try:
                current_stat = os.stat(parts[-1], dir_fd=parent_fd, follow_symlinks=False)
            except FileNotFoundError:
                return
            if not self._same_imported_file(current_stat, identity):
                raise PermissionError("imported file changed before rollback")
            # stat 与 unlink 之间可能被并发写入者换成另一个文件。先原子移到随机隔离名，
            # 再检查实际被移走的 inode；若不是本次文件，就把它恢复到原位置。
            quarantine_name = f".yuxi-rollback-{uuid.uuid4().hex}"
            try:
                os.rename(parts[-1], quarantine_name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
            except FileNotFoundError:
                return
            moved_stat = os.stat(quarantine_name, dir_fd=parent_fd, follow_symlinks=False)
            # rename 自身会更新 ctime，隔离后的复核只比较稳定身份与内容尺寸/mtime。
            same_moved_file = (
                stat.S_ISREG(moved_stat.st_mode)
                and moved_stat.st_dev == identity["device"]
                and moved_stat.st_ino == identity["inode"]
                and moved_stat.st_size == identity["size"]
                and moved_stat.st_mtime_ns == identity["modified_at_ns"]
            )
            if not same_moved_file:
                try:
                    os.link(
                        quarantine_name,
                        parts[-1],
                        src_dir_fd=parent_fd,
                        dst_dir_fd=parent_fd,
                        follow_symlinks=False,
                    )
                except FileExistsError as exc:
                    raise PermissionError("replacement file could not be restored after rollback race") from exc
                os.unlink(quarantine_name, dir_fd=parent_fd)
                raise PermissionError("imported file changed before rollback")
            os.unlink(quarantine_name, dir_fd=parent_fd)
        finally:
            os.close(parent_fd)

    def create_authorized_directory(
        self, parent_path: str, name: str, *, root: str, create_parents: bool = True
    ) -> dict:
        """在 Workdir 内创建一个单层目录。"""
        if not name or name in {".", ".."} or "/" in name or "\\" in name:
            raise ValueError("directory name must be one path component")
        self._require_within(parent_path, root)
        base, parts = self._resolve_path(parent_path)
        parent_fd = self._open_directory(base, parts, create=create_parents)
        try:
            os.mkdir(name, 0o700, dir_fd=parent_fd)
            item_stat = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        finally:
            os.close(parent_fd)
        return self._metadata_from_stat(item_stat)

    def delete_authorized_path(self, path: str, *, root: str) -> None:
        """递归删除 Workdir 内的真实文件或目录，不允许删除根。"""
        self._require_within(path, root, allow_root=False)
        base, parts = self._resolve_path(path)
        parent_fd = self._open_directory(base, parts[:-1])
        try:
            self._remove_entry(parent_fd, parts[-1])
        finally:
            os.close(parent_fd)

    def delete_project_workdir(self, path: str) -> None:
        """幂等清理 managed 项目目录；只忽略目标目录本身缺失。"""
        self._require_within(path, "/", allow_root=False)
        base, parts = self._resolve_path(path)
        try:
            parent_fd = self._open_directory(base, parts[:-1])
        except FileNotFoundError:
            return
        try:
            try:
                target = os.stat(parts[-1], dir_fd=parent_fd, follow_symlinks=False)
            except FileNotFoundError:
                return
            if not stat.S_ISDIR(target.st_mode):
                raise PermissionError("project workdir must be a real directory")
            self._remove_entry(parent_fd, parts[-1], allow_special_entries=True)
        finally:
            os.close(parent_fd)

    def _open_regular_file(self, path: str, *, writable: bool):
        """固定父目录与最终普通文件，统一拒绝 symlink、目录和特殊文件。"""
        base, parts = self._resolve_path(path)
        return open_regular_file_fd(base, parts, writable=writable)

    def _resolve_path(self, path: str) -> tuple[Path, tuple[str, ...]]:
        """把 UserWorkspace scope 路径解析为持久化根内组件。"""
        raw = str(path or "").strip()
        pure = PurePosixPath(raw)
        if not raw or not pure.is_absolute() or ".." in pure.parts or "\\" in raw:
            raise ValueError("invalid Workspace path")
        return self._workspace_root, tuple(pure.parts[1:])

    @staticmethod
    def _require_within(path: str, root: str, *, allow_root: bool = True) -> None:
        normalized_path = PurePosixPath(str(path)).as_posix()
        normalized_root = PurePosixPath(str(root)).as_posix()
        if normalized_path == normalized_root:
            if allow_root:
                return
            raise ValueError("operation cannot target the Workdir root")
        if normalized_root == "/":
            if normalized_path.startswith("/"):
                return
            raise ValueError("path is outside the Workdir")
        normalized_root = normalized_root.rstrip("/")
        if not normalized_path.startswith(f"{normalized_root}/"):
            raise ValueError("path is outside the Workdir")

    @staticmethod
    def _metadata_from_stat(item_stat: os.stat_result) -> dict:
        is_dir = stat.S_ISDIR(item_stat.st_mode)
        return {
            "is_dir": is_dir,
            "size": 0 if is_dir else item_stat.st_size,
            "modified_at": item_stat.st_mtime,
        }

    @staticmethod
    def _same_imported_file(item_stat: os.stat_result, identity: dict | os.stat_result) -> bool:
        """比较导入文件身份与写入后状态，避免删除后续修改。"""
        if isinstance(identity, dict):
            expected = (
                identity["device"],
                identity["inode"],
                identity["size"],
                identity["modified_at_ns"],
                identity["changed_at_ns"],
            )
        else:
            expected = (
                identity.st_dev,
                identity.st_ino,
                identity.st_size,
                identity.st_mtime_ns,
                identity.st_ctime_ns,
            )
        return (
            stat.S_ISREG(item_stat.st_mode)
            and (
                item_stat.st_dev,
                item_stat.st_ino,
                item_stat.st_size,
                item_stat.st_mtime_ns,
                item_stat.st_ctime_ns,
            )
            == expected
        )

    @staticmethod
    def _open_directory(base: Path, parts: tuple[str, ...], *, create: bool = False) -> int:
        try:
            return open_directory_fd(base, parts, create=create)
        except OSError as exc:
            if exc.errno == errno.ELOOP:
                raise PermissionError("symlink paths are not allowed") from exc
            raise

    @classmethod
    def _remove_entry(cls, parent_fd: int, name: str, *, allow_special_entries: bool = False) -> None:
        item_stat = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if not stat.S_ISDIR(item_stat.st_mode):
            if not allow_special_entries and stat.S_ISLNK(item_stat.st_mode):
                raise PermissionError("symlink paths are not allowed")
            if not allow_special_entries and not stat.S_ISREG(item_stat.st_mode):
                raise PermissionError("only regular files and directories can be deleted")
            os.unlink(name, dir_fd=parent_fd)
            return
        child_fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
        try:
            for child_name in os.listdir(child_fd):
                cls._remove_entry(child_fd, child_name, allow_special_entries=allow_special_entries)
        finally:
            os.close(child_fd)
        os.rmdir(name, dir_fd=parent_fd)

    @staticmethod
    def _write_all(file_fd: int, content: bytes) -> None:
        offset = 0
        while offset < len(content):
            offset += os.write(file_fd, content[offset:])
