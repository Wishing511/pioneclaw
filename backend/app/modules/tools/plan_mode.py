"""
Plan Mode 工具和状态管理

借鉴 Claude Code 的 system prompt overlay 模式：
- EnterPlanMode: 设置 plan_mode_active 标志，Agent 进入只读探索模式
- ExitPlanMode: 验证计划结构，写入 plan 文件，恢复正常执行模式

用于 AgentLoop 的工具过滤和系统提示词覆盖。

工具白名单来源: pool_filter.ToolPoolMode.PLAN 预设
"""

import os
import re
from datetime import datetime

from app.modules.tools.base import BaseTool, ToolParameter

# ── Plan Mode 状态 ──────────────────────────────────────────────
_plan_mode_active: bool = False
_plan_content: str = ""  # 当前累积的计划内容
_plan_session_start: str | None = None  # 进入 plan mode 的时间戳

# Plan 文件存储目录
_PLANS_DIR = os.path.join(os.getcwd(), ".claude", "plans")


def _get_plan_mode_allowed_set() -> set[str]:
    """从 pool_filter PLAN 预设动态获取 plan mode 允许的工具"""
    try:
        from app.modules.tools.pool_filter import ToolPoolMode, get_pool_tool_policy
        from app.modules.tools.registry import get_tool_registry

        policy = get_pool_tool_policy(ToolPoolMode.PLAN)
        all_tools = get_tool_registry().list_tools()
        allowed = set(policy.get_allowed_tools(all_tools))
        if allowed:
            return allowed
    except Exception:
        pass
    # 回退硬编码列表（避免循环导入、工具未注册等情况）
    return {
        "read_file",
        "grep",
        "file_search",
        "list_dir",
        "web_search",
        "web_fetch",
        "current_time",
        "calculator",
        "ask_user_question",
        "exit_plan_mode",
        "image",
        "knowledge_search",
        "vector_memory_recall",
    }


def get_plan_mode_allowed_tools() -> set[str]:
    """获取 plan mode 允许的工具（延迟计算，每次调用时刷新）

    优先从 pool preset 动态计算，回退到硬编码列表。
    """
    return _get_plan_mode_allowed_set()


# Plan mode 下允许的只读工具
# 延迟初始化：首次访问时从 pool preset 加载
# 使用函数而非模块级变量，确保工具注册后调用
PLAN_MODE_ALLOWED_TOOLS: set[str] = (
    set()
)  # 初始为空，由 get_plan_mode_allowed_tools() 动态获取


def is_plan_mode_active() -> bool:
    """检查是否处于计划模式"""
    return _plan_mode_active


def get_plan_content() -> str:
    """获取当前累积的计划内容"""
    return _plan_content


def enter_plan_mode() -> str:
    """进入计划模式，返回给 LLM 的提示消息"""
    global _plan_mode_active, _plan_content, _plan_session_start
    _plan_mode_active = True
    _plan_content = ""
    _plan_session_start = datetime.now().isoformat()

    return (
        "已进入**计划模式**。\n\n"
        "在此模式下，你只能使用只读工具（read_file、grep、file_search、list_dir、"
        "web_search、web_fetch 等）来探索代码库和分析问题。\n\n"
        "**禁止**使用 write_file、edit_file、exec 等修改性工具。\n\n"
        "完成调研后，请调用 exit_plan_mode 工具提交你的计划方案。"
        "计划应包含：Context（背景）、改动方案（具体步骤）、涉及文件、验证方法。"
    )


def exit_plan_mode(plan_text: str) -> str:
    """退出计划模式，验证并保存计划

    Args:
        plan_text: Markdown 格式的计划文本

    Returns:
        结果消息

    Raises:
        ValueError: 计划验证失败
    """
    global _plan_mode_active, _plan_content, _plan_session_start

    # 验证计划非空
    if not plan_text or not plan_text.strip():
        raise ValueError(
            "计划内容不能为空。请写一个包含 Context、改动方案、验证方法的完整计划。"
        )

    # 验证最小长度
    stripped = plan_text.strip()
    if len(stripped) < 50:
        raise ValueError(
            f"计划内容太短（{len(stripped)} 字符）。"
            "请写一个详细的计划，至少包含：背景、改动方案、涉及文件。"
        )

    # 保存计划文件
    os.makedirs(_PLANS_DIR, exist_ok=True)

    # 生成文件名 slug
    slug = _generate_slug(stripped)
    plan_path = os.path.join(_PLANS_DIR, f"{slug}.md")

    # 写入 plan 文件（frontmatter + 内容）
    timestamp = datetime.now().isoformat()
    with open(plan_path, "w", encoding="utf-8") as f:
        f.write("---\n")
        f.write(f"created: {timestamp}\n")
        f.write(f"plan_started: {_plan_session_start or timestamp}\n")
        f.write("---\n\n")
        f.write(stripped)

    # 清除状态
    _plan_mode_active = False
    _plan_content = stripped
    _plan_session_start = None

    return (
        f"计划已保存至 `{plan_path}`。\n\n"
        f"已退出计划模式，恢复正常执行模式。你现在可以使用所有工具执行计划。\n\n"
        f"计划摘要：\n{stripped[:500]}{'...' if len(stripped) > 500 else ''}"
    )


def _generate_slug(text: str, max_length: int = 50) -> str:
    """从计划文本生成文件名 slug"""
    # 取前两行作为标题
    lines = text.strip().split("\n")
    title_line = ""
    for line in lines:
        line = line.strip()
        # 跳过 frontmatter、markdown 标记
        if line and not line.startswith("---") and not line.startswith("#"):
            title_line = line
            break

    if not title_line:
        title_line = lines[0].strip() if lines else "plan"

    # 去除 markdown 标记
    slug = re.sub(r"[#*`\[\]()]", "", title_line)
    # 替换空格和特殊字符为连字符
    slug = re.sub(r"[^a-zA-Z0-9\u4e00-\u9fff]+", "-", slug)
    # 截断
    slug = slug.strip("-").lower()
    if len(slug) > max_length:
        slug = slug[:max_length].rstrip("-")

    if not slug:
        slug = "plan"

    # 添加时间戳避免冲突
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"{timestamp}-{slug}"


# ═══════════════════════════════════════════════════════════════
# 工具类
# ═══════════════════════════════════════════════════════════════


class EnterPlanModeTool(BaseTool):
    """进入计划模式 — Agent 只能使用只读工具探索代码库"""

    name = "enter_plan_mode"
    description = (
        "进入计划模式。在此模式下，你只能使用只读工具（read_file、grep、file_search、"
        "list_dir、web_search 等）来探索代码库和分析问题。禁止使用 write_file、edit_file、"
        "exec 等修改性工具。设计完方案后，调用 exit_plan_mode 提交计划供用户审批。"
    )
    parameters = {}
    required = []

    async def execute(self, **kwargs) -> str:
        return enter_plan_mode()


class ExitPlanModeTool(BaseTool):
    """退出计划模式 — 提交方案供用户审批"""

    name = "exit_plan_mode"
    description = (
        "退出计划模式并提交你的方案。你必须写一个完整的计划，包含："
        "Context（背景和原因）、改动方案（具体步骤）、涉及文件、验证方法。"
        "计划必须是 Markdown 格式，详细且可执行的。"
    )
    parameters = {
        "plan": ToolParameter(
            type="string",
            description="Markdown 格式的完整计划文本。必须包含 Context、改动方案、涉及文件、验证方法。",
        ),
    }
    required = ["plan"]

    async def execute(self, plan: str, **kwargs) -> str:
        try:
            return exit_plan_mode(plan)
        except ValueError as e:
            return f"错误: {e}\n\n请修改计划后重试。计划至少需要 50 个字符，包含 Context、改动方案、涉及文件。"
