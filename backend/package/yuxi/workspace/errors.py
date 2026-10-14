"""Workspace 文件边界的中立异常。"""


class FileTransferLimitError(ValueError):
    """受信任文件传输超过调用方声明的字节上限。"""


class FileImportConflictError(ValueError):
    """选中文件的目标路径发生冲突，包含可供用户改名的明细。"""

    def __init__(self, conflicts: list[dict[str, str]]):
        self.conflicts = conflicts
        super().__init__("selected files have destination conflicts")


class FileImportLimitError(ValueError):
    """选中文件数量或字节数超过一次导入上限。"""


class FileImportRollbackError(RuntimeError):
    """并发改动阻止安全清理本次导入的文件。"""

    def __init__(self, paths: list[str]):
        self.paths = paths
        super().__init__("imported files changed before rollback")
