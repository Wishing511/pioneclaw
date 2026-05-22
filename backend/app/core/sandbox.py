"""
文件路径沙箱 — 防止路径穿越和敏感文件访问

所有文件工具（ReadFile/WriteFile/EditFile）必须通过此模块校验路径。

规则:
- 读操作：
  - 路径在工作区内 → 放行
  - 路径在工作区外 → 弹窗确认（用户说 yes 后放行）
  - 敏感文件（.env 等）→ 弹窗确认
- 写操作：
  - 路径在工作区内 → 放行
  - 路径在工作区外 → 硬拦截（永不越界写）
  - 敏感文件 → 弹窗确认

借鉴 OpenClaw/Claude Code 的 sandbox 设计。
"""

from pathlib import Path

# 敏感文件模式：即使在工作区内，也需要用户确认才能访问
SENSITIVE_PATTERNS: list[str] = [
    ".env",
    ".env.*",
    "*.pem",
    "*.key",
    "*.pfx",
    "*.p12",
    "*.jks",
    "*.keystore",
    "*.secret",
    "*.private",
    "id_rsa",
    "id_ed25519",
    "id_ecdsa",
    "authorized_keys",
    "known_hosts",
    ".gitconfig",
    ".npmrc",
    ".pypirc",
    "credentials",
    "secrets",
]


class PathOutsideWorkspaceError(PermissionError):
    """路径超出工作区范围（硬拦截，仅用于写操作）"""

    def __init__(self, path: str, workspace: Path):
        super().__init__(
            f"路径超出工作区范围: {path}。写操作仅限工作区内 ({workspace})。"
        )
        self.path = path
        self.workspace = workspace


class SensitiveFileAccessRequired(Exception):
    """
    文件访问需要用户确认。

    reason 字段标识触发原因:
    - "outside_workspace": 读操作访问工作区外的文件
    - "sensitive": 访问敏感文件（.env 等）
    """

    def __init__(self, path: Path, reason: str, pattern: str = ""):
        self.path = path
        self.reason = reason
        self.pattern = pattern
        self.file_name = path.name

        if reason == "outside_workspace":
            msg = f"Agent 尝试读取工作区外的文件: {path}"
        elif reason == "sensitive":
            msg = f"文件 '{path.name}' 匹配敏感模式 '{pattern}'，需要确认"
        else:
            msg = f"文件访问需确认: {path}"
        super().__init__(msg)


def _is_sensitive(file_path: Path) -> str | None:
    """检查文件是否匹配敏感模式，返回匹配的模式或 None"""
    from fnmatch import fnmatch

    file_name = file_path.name
    for pattern in SENSITIVE_PATTERNS:
        if fnmatch(file_name, pattern):
            return pattern
    return None


def _resolve_path(user_path: str, workspace: Path) -> Path:
    """将用户路径解析为绝对路径（展开 ../ 和符号链接）"""
    raw_path = Path(user_path)
    if not raw_path.is_absolute():
        raw_path = workspace / raw_path
    try:
        return raw_path.resolve()
    except OSError:
        return raw_path.parent.resolve() / raw_path.name


def _check_workspace_boundary(full_path: Path, workspace: Path) -> bool:
    """检查路径是否在工作区内"""
    try:
        full_path.relative_to(workspace)
        return True
    except ValueError:
        return False


def validate_path_for_read(
    path: str,
    workspace_dir: Path,
    allow_sensitive: bool = False,
    allow_outside: bool = False,
) -> Path:
    """
    校验读取路径。

    越界 → SensitiveFileAccessRequired(reason="outside_workspace")
    敏感文件 → SensitiveFileAccessRequired(reason="sensitive")

    调用方（agent loop）捕获 SensitiveFileAccessRequired 后弹窗确认，
    用户批准后用 allow_sensitive=True / allow_outside=True 重新调用。
    """
    workspace = workspace_dir.resolve()

    full_path = _resolve_path(path, workspace)
    in_workspace = _check_workspace_boundary(full_path, workspace)

    # 越界检查
    if not in_workspace and not allow_outside:
        raise SensitiveFileAccessRequired(full_path, reason="outside_workspace")

    # 敏感文件检查
    if not allow_sensitive:
        sensitive_pattern = _is_sensitive(full_path)
        if sensitive_pattern:
            raise SensitiveFileAccessRequired(
                full_path, reason="sensitive", pattern=sensitive_pattern
            )

    return full_path


def validate_path_for_write(
    path: str,
    workspace_dir: Path,
    allow_sensitive: bool = False,
) -> Path:
    """
    校验写入路径。

    越界 → PathOutsideWorkspaceError（硬拦截，从不允许越界写）
    敏感文件 → SensitiveFileAccessRequired(reason="sensitive")
    """
    workspace = workspace_dir.resolve()

    # 确保工作区存在
    workspace.mkdir(parents=True, exist_ok=True)

    full_path = _resolve_path(path, workspace)

    # 越界硬拦截
    if not _check_workspace_boundary(full_path, workspace):
        raise PathOutsideWorkspaceError(path, workspace)

    # 敏感文件检查
    if not allow_sensitive:
        sensitive_pattern = _is_sensitive(full_path)
        if sensitive_pattern:
            raise SensitiveFileAccessRequired(
                full_path, reason="sensitive", pattern=sensitive_pattern
            )

    # 自动创建父目录
    full_path.parent.mkdir(parents=True, exist_ok=True)

    return full_path
