"""
Permission Mode 权限模式层级

借鉴 claw-code permissions.rs + permission_enforcer.rs:
  PermissionMode 枚举 (5 级) → PermissionChecker → 工具/Bash/文件 门控

与现有基础设施集成:
- ToolPolicy (sandbox_policy.py): 工具级 allow/deny 过滤
- InterruptManager (interrupt.py): 权限拒绝升级为 InterruptPoint
- bash_safety: PermissionMode 影响危险命令确认策略
- UserRole (models.py): 角色决定权限上限

核心规则: UserRole 设上限，Agent.config 只能降级不能越权。
"""

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


# ==================== PermissionMode Enum ====================


class PermissionMode(str, Enum):
    """
    权限模式 —— 5 级强度排序

    ReadOnly < WorkspaceWrite < DangerFullAccess < Prompt < Allow
    """

    READ_ONLY = "read_only"
    WORKSPACE_WRITE = "workspace_write"
    DANGER_FULL_ACCESS = "danger_full_access"
    PROMPT = "prompt"
    ALLOW = "allow"

    # 强度值，用于比较
    @property
    def _strength(self) -> int:
        _order = {
            PermissionMode.READ_ONLY: 0,
            PermissionMode.WORKSPACE_WRITE: 1,
            PermissionMode.DANGER_FULL_ACCESS: 2,
            PermissionMode.PROMPT: 3,
            PermissionMode.ALLOW: 4,
        }
        return _order[self]

    def __lt__(self, other: "PermissionMode") -> bool:
        return self._strength < other._strength

    def __le__(self, other: "PermissionMode") -> bool:
        return self._strength <= other._strength

    def at_least(self, required: "PermissionMode") -> bool:
        """当前模式是否 >= 所需模式"""
        return self._strength >= required._strength


# ==================== PermissionCheckResult ====================


@dataclass
class PermissionCheckResult:
    """权限检查结果"""

    allowed: bool
    reason: str = ""
    require_confirmation: bool = False
    deny_tool: bool = False  # 是否在 deny 列表中（硬拒绝）

    @classmethod
    def ok(cls) -> "PermissionCheckResult":
        return cls(allowed=True)

    @classmethod
    def denied(cls, reason: str) -> "PermissionCheckResult":
        return cls(allowed=False, reason=reason)

    @classmethod
    def confirmation_required(cls, reason: str) -> "PermissionCheckResult":
        return cls(allowed=True, reason=reason, require_confirmation=True)


# ==================== PermissionChecker ====================


@dataclass
class PermissionChecker:
    """
    权限检查器 —— 组合 PermissionMode + ToolPolicy + 安全设置

    检查链路:
      1. ToolPolicy.is_allowed() — deny 列表硬拒绝
      2. PermissionMode 门控 — ReadOnly 限制写/Bash
      3. 安全设置 — file_approval/command_approval 等
    """

    mode: PermissionMode = PermissionMode.WORKSPACE_WRITE
    tool_policy: Any | None = None  # ToolPolicy | None
    settings: dict[str, str] = field(default_factory=dict)
    workspace_root: str | None = None

    # ========== 设置读取 helpers ==========

    def _setting_bool(self, key: str, default: bool = False) -> bool:
        val = self.settings.get(key, str(default).lower())
        return val.lower() in ("true", "1", "yes")

    # ========== 工具检查 ==========

    def check_tool(self, tool_name: str) -> PermissionCheckResult:
        """
        检查工具是否可以执行。

        返回 PermissionCheckResult:
          - allowed=False → 硬拒绝
          - require_confirmation=True → 允许但需确认
          - allowed=True → 直接放行
        """
        # 1. ToolPolicy deny 检查（最高优先级，ALLOW 模式也受约束）
        if self.tool_policy is not None:
            allowed, reason = self.tool_policy.is_allowed(tool_name)
            if not allowed:
                return PermissionCheckResult.denied(reason)

        # 2. ALLOW 模式 —— 跳过后续模式级检查
        if self.mode == PermissionMode.ALLOW:
            return PermissionCheckResult.ok()

        # 3. ReadOnly 模式 —— 禁止写类工具
        if self.mode == PermissionMode.READ_ONLY:
            write_tools = {
                "exec",
                "shell",
                "filesystem",
                "write_file",
                "replace_in_file",
                "delete_file",
                "git",
                "create_directory",
                "move_file",
                "copy_file",
                "task",
                "skill",
                "sub_agent",
            }
            if tool_name in write_tools:
                return PermissionCheckResult.denied(
                    f"ReadOnly 模式禁止使用工具 '{tool_name}'"
                )

        # 4. 需要确认的工具（高安全设置下）
        if tool_name == "exec" and self._setting_bool("command_approval", True):
            return PermissionCheckResult.confirmation_required("命令执行需要审批")
        if tool_name in ("filesystem", "write_file", "delete_file", "replace_in_file"):
            if self._setting_bool("file_approval", False):
                return PermissionCheckResult.confirmation_required("文件操作需要审批")

        return PermissionCheckResult.ok()

    # ========== Bash 检查 ==========

    def check_bash(
        self,
        command: str,
        danger_level: Any | None = None,
    ) -> PermissionCheckResult:
        """
        检查 Bash 命令是否可执行。

        Args:
            command: 命令字符串
            danger_level: DangerLevel（可选，如果已分析过则复用）
        """
        # ALLOW 和 PROMPT 模式 —— 全放行
        if self.mode.at_least(PermissionMode.PROMPT):
            return PermissionCheckResult.ok()

        # ReadOnly —— 拒绝所有 Bash
        if self.mode == PermissionMode.READ_ONLY:
            return PermissionCheckResult.denied("ReadOnly 模式禁止执行 Bash 命令")

        # 如果传入了 danger_level，按级别处理
        if danger_level is not None:
            from app.core.bash_safety import DangerLevel

            if danger_level == DangerLevel.BLOCKED:
                return PermissionCheckResult.denied("命令被安全策略拦截")
            if danger_level == DangerLevel.DANGEROUS:
                if self.mode.at_least(PermissionMode.DANGER_FULL_ACCESS):
                    # DangerFullAccess: 危险命令需确认
                    if self._setting_bool("command_approval", True):
                        return PermissionCheckResult.confirmation_required(
                            "危险命令需要输入确认短语"
                        )
                    return PermissionCheckResult.ok()
                else:
                    # WorkspaceWrite 及以下：危险命令需确认
                    return PermissionCheckResult.confirmation_required(
                        "危险命令需要审批"
                    )
            if danger_level == DangerLevel.CAUTION:
                if self._setting_bool("command_approval", True):
                    return PermissionCheckResult.confirmation_required(
                        "CAUTION 级别命令需要确认"
                    )
                return PermissionCheckResult.ok()

        return PermissionCheckResult.ok()

    # ========== 文件写检查 ==========

    def check_file_write(self, path: str) -> PermissionCheckResult:
        """检查是否可以写文件"""
        if self.mode.at_least(PermissionMode.DANGER_FULL_ACCESS):
            return PermissionCheckResult.ok()

        if self.mode == PermissionMode.READ_ONLY:
            return PermissionCheckResult.denied("ReadOnly 模式禁止写文件")

        # WorkspaceWrite: workspace 内直接放行，workspace 外需确认
        if self.workspace_root:
            import os

            abs_path = os.path.abspath(path)
            abs_ws = os.path.abspath(self.workspace_root)
            if not abs_path.startswith(abs_ws):
                return PermissionCheckResult.confirmation_required(
                    f"目标路径在 workspace 外，需要确认: {path}"
                )

        if self._setting_bool("file_approval", False):
            return PermissionCheckResult.confirmation_required(
                f"文件写入需要审批: {path}"
            )

        return PermissionCheckResult.ok()

    # ========== 网络检查 ==========

    def check_network(self, url: str) -> PermissionCheckResult:
        """检查是否可以进行网络请求"""
        if self.mode == PermissionMode.READ_ONLY:
            return PermissionCheckResult.denied("ReadOnly 模式仅允许只读网络请求")

        if self._setting_bool("network_approval", False):
            return PermissionCheckResult.confirmation_required(
                f"网络请求需要审批: {url}"
            )

        return PermissionCheckResult.ok()


# ==================== 模式解析 ====================

# 角色 → 上限映射
_ROLE_MAX_MODE: dict[str, str] = {
    "super_admin": "allow",
    "org_admin": "danger_full_access",
    "user": "workspace_write",
}

# 角色 → 默认映射
_ROLE_DEFAULT_MODE: dict[str, str] = {
    "super_admin": "allow",
    "org_admin": "danger_full_access",
    "user": "workspace_write",
}


def get_max_mode_for_role(role) -> PermissionMode:
    """
    角色 → 权限上限

    Args:
        role: UserRole 枚举 或 字符串
    """
    if isinstance(role, str):
        role_key = role.lower()
    else:
        role_key = role.value if hasattr(role, "value") else str(role).lower()

    mode_str = _ROLE_MAX_MODE.get(role_key, "workspace_write")
    return PermissionMode(mode_str)


def get_default_mode_for_role(role) -> PermissionMode:
    """
    角色 → 默认权限模式
    """
    if isinstance(role, str):
        role_key = role.lower()
    else:
        role_key = role.value if hasattr(role, "value") else str(role).lower()

    mode_str = _ROLE_DEFAULT_MODE.get(role_key, "workspace_write")
    return PermissionMode(mode_str)


def resolve_permission_mode(
    user_role: Any | None = None,
    agent_config: dict[str, Any] | None = None,
    db_settings: dict[str, str] | None = None,
) -> PermissionMode:
    """
    解析最终权限模式。

    优先级:
      1. agent_config.permission_mode（Agent 级配置）
      2. db_settings.permission_mode（全局系统设置）
      3. 角色默认值
    最终用角色上限裁剪。

    Args:
        user_role: UserRole 枚举/字符串
        agent_config: Agent.config JSON dict
        db_settings: SystemSetting 字典 {key: value}

    Returns:
        PermissionMode: 最终模式
    """
    # 1. 确定角色上限
    if user_role is not None:
        max_mode = get_max_mode_for_role(user_role)
        default_mode = get_default_mode_for_role(user_role)
    else:
        max_mode = PermissionMode.WORKSPACE_WRITE
        default_mode = PermissionMode.WORKSPACE_WRITE

    # 2. 从 agent_config 读取
    if agent_config and "permission_mode" in agent_config:
        try:
            chosen = PermissionMode(agent_config["permission_mode"])
        except ValueError:
            logger.warning(
                f"Invalid permission_mode in agent_config: "
                f"{agent_config['permission_mode']}"
            )
            chosen = default_mode
    elif db_settings and "permission_mode" in db_settings:
        try:
            chosen = PermissionMode(db_settings["permission_mode"])
        except ValueError:
            logger.warning(
                f"Invalid permission_mode in db_settings: "
                f"{db_settings['permission_mode']}"
            )
            chosen = default_mode
    else:
        chosen = default_mode

    # 3. 用角色上限裁剪
    if chosen._strength > max_mode._strength:
        logger.info(
            f"Permission mode {chosen.value} exceeds role ceiling "
            f"{max_mode.value}, clamping to {max_mode.value}"
        )
        return max_mode

    return chosen
