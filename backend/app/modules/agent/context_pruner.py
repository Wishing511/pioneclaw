"""
Context Pruner — 渐进式 context 压缩（零成本/低成本）

借鉴 Claude Code 的 microCompact.ts 设计：
- MicroCompacter: 清除旧工具结果内容，保留最近 N 个
- Snip: 零成本裁剪空消息、截断超长 reasoning_content
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)

# 可压缩的工具名白名单（与 Claude Code COMPACTABLE_TOOLS 对齐）
COMPACTABLE_TOOLS = frozenset(
    {
        "read_file",
        "write_file",
        "edit_file",
        "grep",
        "glob",
        "file_search",
        "exec",
        "bash",
        "web_search",
        "web_fetch",
        "browser",
    }
)

CLEAR_PLACEHOLDER = "[Old tool result content cleared]"


def _build_tool_placeholder(tool_name: str, original_content: str) -> str:
    """构建保留工具名等元信息的占位符。

    当结构化占位符比原内容还长时，回退到简短占位符，
    确保压缩操作始终释放空间。
    """
    structured = f"[tool_result: {tool_name}, content cleared]"
    if len(structured) >= len(original_content):
        return CLEAR_PLACEHOLDER
    return structured


class MicroCompacter:
    """清除旧工具结果内容以释放 context 空间。

    策略：
    1. 计数触发：保留最近 keep_recent 个工具结果，更早的替换为占位符
    2. 大小触发：单个工具结果超过 max_chars 时截断尾部
    3. 占位符保留工具名等元信息（M1）
    4. 不修改原列表，返回新列表（M3）

    灵感来自 Claude Code 的 microCompact.ts。
    """

    def __init__(
        self,
        keep_recent: int = 8,
        max_chars: int = 4000,
        compactable_tools: frozenset = COMPACTABLE_TOOLS,
    ):
        self.keep_recent = keep_recent
        self.max_chars = max_chars
        self.compactable_tools = compactable_tools

    def prune(self, messages: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
        """裁剪旧工具结果，返回 (新消息列表, 节省的字符数)。

        不修改原列表，返回深拷贝后的新列表。
        """
        if not messages:
            return messages, 0

        # 深拷贝消息列表，避免修改原列表（M3）
        import copy

        new_messages = copy.deepcopy(messages)

        # 收集所有可压缩的工具结果位置
        tool_result_indices: list[tuple[int, str, str]] = []
        for i, msg in enumerate(new_messages):
            if msg.get("role") != "tool":
                continue
            tool_name = msg.get("tool_name", msg.get("name", ""))
            content = msg.get("content", "")
            if tool_name in self.compactable_tools and isinstance(content, str):
                tool_result_indices.append((i, tool_name, content))

        total_results = len(tool_result_indices)
        if total_results == 0:
            return new_messages, 0

        # 计算哪些需要清除
        chars_saved = 0
        keep_count = self.keep_recent

        for idx, (msg_idx, tool_name, content) in enumerate(tool_result_indices):
            # 计数触发：保留最近的 keep_recent 个
            if idx < total_results - keep_count:
                old_len = len(content)
                placeholder = _build_tool_placeholder(tool_name, content)
                # 只有当占位符确实更短时才替换，避免负收益
                if len(placeholder) < old_len:
                    new_messages[msg_idx]["content"] = placeholder
                    chars_saved += old_len - len(placeholder)
            # 大小触发：即使保留的消息也截断超长内容
            elif len(content) > self.max_chars:
                tail = content[-self.max_chars :]
                old_len = len(content)
                new_messages[msg_idx]["content"] = (
                    f"[Result truncated, showing last {self.max_chars} chars]\n{tail}"
                )
                chars_saved += old_len - len(new_messages[msg_idx]["content"])

        if chars_saved > 0:
            cleared = total_results - keep_count
            logger.debug(
                f"MicroCompacter: cleared {max(0, cleared)} old results, "
                f"saved ~{chars_saved} chars"
            )

        return new_messages, chars_saved


class Snip:
    """零成本消息裁剪。

    操作：
    1. 移除空内容 system 消息
    2. 移除空内容 assistant 消息（无 tool_calls 的）
    3. 截断超长 reasoning_content
    4. 合并相邻的 injection system 消息（Agent 间消息）

    不删除任何实际的用户消息或工具结果。
    """

    def __init__(self, max_reasoning_chars: int = 2000):
        self.max_reasoning_chars = max_reasoning_chars

    def prune(self, messages: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
        """裁剪消息，返回 (新消息列表, 节省的字符数)。

        返回新列表，不修改原列表。
        """
        if not messages:
            return messages, 0

        pruned: list[dict[str, Any]] = []
        chars_saved = 0

        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content", "")

            # 移除空 system 消息
            if role == "system" and not str(content).strip():
                chars_saved += len(content)
                continue

            # 移除空 assistant 消息（无 tool_calls 的）
            if role == "assistant":
                if not str(content).strip() and not msg.get("tool_calls"):
                    chars_saved += len(content)
                    continue

            # 截断超长 reasoning_content
            if msg.get("reasoning_content"):
                rc = msg["reasoning_content"]
                if len(rc) > self.max_reasoning_chars:
                    chars_saved += len(rc) - (self.max_reasoning_chars + 18)
                    msg = {
                        **msg,
                        "reasoning_content": rc[: self.max_reasoning_chars]
                        + "...[truncated]",
                    }

            pruned.append(msg)

        if chars_saved > 0:
            logger.debug(
                f"Snip: removed {len(messages) - len(pruned)} empty messages, "
                f"saved ~{chars_saved} chars"
            )

        return pruned, chars_saved


def estimate_tokens(messages: list[dict[str, Any]]) -> int:
    """粗略估算消息列表的 token 数。

    中文字符按 1.5 token/字符，英文按 0.25 token/字符。
    用于 Compactor 阈值检查前的快速预检。
    """
    total = 0
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, list):
            # 多模态内容
            for part in content:
                if isinstance(part, dict):
                    text = part.get("text", part.get("content", ""))
                else:
                    text = str(part)
                total += _rough_char_tokens(str(text))
        elif isinstance(content, str):
            total += _rough_char_tokens(content)
        # tool_calls 额外开销
        if msg.get("tool_calls"):
            for tc in msg["tool_calls"]:
                if isinstance(tc, dict):
                    args = tc.get("function", {}).get("arguments", "")
                    total += _rough_char_tokens(str(args))

    return total


def _rough_char_tokens(text: str) -> int:
    """粗略字符到 token 估算"""
    if not text:
        return 0
    # 检查中文字符比例
    cn_chars = sum(1 for c in text if "一" <= c <= "鿿")
    if cn_chars > len(text) * 0.3:
        # 中文为主
        return int(len(text) * 1.5)
    return int(len(text) * 0.25)


class ContextPruner:
    """统一入口：组合 Snip + MicroCompacter，供 AgentLoop 调用。

    调用顺序：
    1. snip_prune() — 零成本裁剪
    2. micro_compact() — 清除旧工具结果
    """

    def __init__(
        self,
        keep_recent: int = 8,
        max_tool_result_chars: int = 4000,
        max_reasoning_chars: int = 2000,
        compactable_tools: frozenset = COMPACTABLE_TOOLS,
    ):
        self._snip = Snip(max_reasoning_chars=max_reasoning_chars)
        self._micro = MicroCompacter(
            keep_recent=keep_recent,
            max_chars=max_tool_result_chars,
            compactable_tools=compactable_tools,
        )

    def snip_prune(
        self,
        messages: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], int]:
        """零成本裁剪：空消息移除 + reasoning 截断"""
        return self._snip.prune(messages)

    def micro_compact(
        self,
        messages: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], int]:
        """清除旧工具结果内容"""
        return self._micro.prune(messages)
