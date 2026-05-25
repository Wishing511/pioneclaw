"""
工具池过滤 — 预设模式系统

借鉴 claw-code pool.rs: 按模式名自动生成工具策略，不需要手动配置 allow/deny 列表。

复用现有 ToolPolicy + ToolPolicyConfig 作为过滤引擎:
- 每个预设模式生成一个 ToolPolicyConfig
- get_pool_tool_policy() 返回 ToolPolicy 实例
- 与手动 tool_policy 配置完全兼容

预设模式:
- DEFAULT: 全部工具可用（透传）
- PLAN: 只读工具（搜索+读取+计划）
- CODE_REVIEW: 代码审查（只读代码工具）
- BARE: 最小集（所有工具 deny，仅 chat）
"""

import logging
from enum import Enum

from app.core.sandbox_policy import ToolPolicy, ToolPolicyConfig

logger = logging.getLogger(__name__)


class ToolPoolMode(str, Enum):
    """工具池模式枚举"""

    DEFAULT = "default"  # 全部工具可用（无限制）
    PLAN = "plan"  # 只读工具（搜索/读取/计划）
    CODE_REVIEW = "code_review"  # 代码审查（只读代码工具）
    BARE = "bare"  # 最小集（拒绝所有工具）


# ── 预设定义 ──────────────────────────────────────────────────
# 每个预设的 allow 列表
# deny 列表留空（由 ToolPolicy 的 allow 机制处理：有 allow 列表时未列入的工具自动拒绝）

_PLAN_ALLOW = [
    # 文件读取
    "read_file",
    "grep",
    "file_search",
    "list_dir",
    # 信息获取
    "web_search",
    "web_fetch",
    "current_time",
    "calculator",
    # 交互
    "ask_user_question",
    # 计划模式
    "enter_plan_mode",
    "exit_plan_mode",
    # 知识/记忆（只读）
    "knowledge_search",
    "vector_memory_recall",
    # 图片（只读）
    "image",
]

_CODE_REVIEW_ALLOW = [
    "read_file",
    "grep",
    "file_search",
    "list_dir",
    "web_search",
    "current_time",
    "calculator",
]

TOOL_POOL_PRESETS: dict[ToolPoolMode, dict] = {
    ToolPoolMode.DEFAULT: {
        "allow": [],
        "deny": [],
        "description": "全部工具可用",
    },
    ToolPoolMode.PLAN: {
        "allow": _PLAN_ALLOW,
        "deny": [],
        "description": "只读工具（搜索+读取+计划）",
    },
    ToolPoolMode.CODE_REVIEW: {
        "allow": _CODE_REVIEW_ALLOW,
        "deny": [],
        "description": "代码审查（只读代码工具）",
    },
    ToolPoolMode.BARE: {
        "allow": [],
        "deny": ["*"],
        "description": "最小集（拒绝所有工具）",
    },
}


def get_pool_tool_policy(mode: ToolPoolMode | str) -> ToolPolicy:
    """从预设模式生成 ToolPolicy

    复用现有的 ToolPolicy 过滤引擎，不重写逻辑。

    Args:
        mode: 预设模式名（枚举或字符串）

    Returns:
        ToolPolicy 实例，可直接用于 is_allowed() / get_allowed_tools()
    """
    if isinstance(mode, str):
        try:
            mode = ToolPoolMode(mode)
        except ValueError:
            logger.warning(f"Unknown pool mode '{mode}', falling back to DEFAULT")
            mode = ToolPoolMode.DEFAULT

    preset = TOOL_POOL_PRESETS.get(mode)
    if preset is None:
        preset = TOOL_POOL_PRESETS[ToolPoolMode.DEFAULT]

    deny_list = list(preset["deny"])

    # BARE 模式: deny=["*"] 需要展开为所有已注册工具
    if "*" in deny_list:
        try:
            from app.modules.tools.registry import get_tool_registry

            all_tools = get_tool_registry().list_tools()
            deny_list = all_tools  # deny 所有已知工具
        except Exception:
            deny_list = ["*"]  # 回退：保留通配符

    config = ToolPolicyConfig(
        allow=list(preset["allow"]),
        deny=deny_list,
    )

    return ToolPolicy(config)


def get_pool_mode_description(mode: ToolPoolMode | str) -> str:
    """获取预设模式的描述文字"""
    if isinstance(mode, str):
        try:
            mode = ToolPoolMode(mode)
        except ValueError:
            return f"未知模式 ({mode})"
    preset = TOOL_POOL_PRESETS.get(mode, {})
    return preset.get("description", "未知")


def list_pool_modes() -> dict[str, str]:
    """列出所有可用的预设模式及其描述"""
    return {m.value: TOOL_POOL_PRESETS[m]["description"] for m in ToolPoolMode}
