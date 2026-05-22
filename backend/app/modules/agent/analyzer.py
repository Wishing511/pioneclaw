"""
Message Analyzer - 对话消息分析工具

借鉴自 AIE 项目的 analyzer.py，提供对话消息的基础分析能力：
- 格式化消息为文本（用于 LLM 总结）
- 判断是否需要总结
- 分割消息（要总结的 vs 要保留的）
- Token 计数估算
"""

import contextlib
import logging
import re
from dataclasses import dataclass
from datetime import datetime

logger = logging.getLogger(__name__)


@dataclass
class MessageStats:
    """消息统计"""

    total_messages: int
    total_chars: int
    estimated_tokens: int
    user_messages: int
    assistant_messages: int
    tool_messages: int
    oldest_message_time: datetime | None
    newest_message_time: datetime | None


class MessageAnalyzer:
    """对话消息分析器"""

    # 短消息无意义词过滤前缀（长度 <= 8 且匹配时跳过）
    SKIP_PREFIXES = (
        "好的",
        "知道了",
        "明白",
        "收到",
        "谢谢",
        "嗯",
        "哦",
        "好的",
        "好",
        "ok",
        "OK",
        "Ok",
        "嗯嗯",
        "哦哦",
        "好好",
        "了解",
        "可以",
        "没问题",
        "是",
        "是的",
        "没错",
        "确实",
        "哈哈",
        "呵呵",
        "嘻嘻",
        "666",
        "👍",
        "🙏",
        "感谢",
        "thanks",
        "thx",
        "yes",
        "no",
        "yep",
        "nope",
        "sure",
        "got it",
        "noted",
        "fine",
        "cool",
        "nice",
        "好的呀",
    )

    # 中文字符平均 token 比例（估算）
    # 中文：1 token ≈ 1.5-2 字符
    # 英文：1 token ≈ 4 字符
    CHARS_PER_TOKEN_CN = 1.5
    CHARS_PER_TOKEN_EN = 4.0

    def format_messages_for_summary(
        self,
        messages: list[dict],
        max_chars: int = 4000,
        include_roles: bool = True,
    ) -> str:
        """
        将消息列表格式化为文本，用于 LLM 总结

        Args:
            messages: 消息列表
            max_chars: 最大字符数
            include_roles: 是否包含角色标识

        Returns:
            str: 格式化的对话文本
        """
        lines = []
        total_chars = 0

        for msg in messages:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")

            # 处理多模态内容
            if isinstance(content, list):
                # 提取文本部分
                text_parts = []
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "text":
                        text_parts.append(part.get("text", ""))
                    elif isinstance(part, str):
                        text_parts.append(part)
                content = " ".join(text_parts)

            content = str(content).strip()

            if not content:
                continue

            # 短消息且以寒暄词开头，跳过
            if len(content) <= 8 and any(
                content.startswith(p) for p in self.SKIP_PREFIXES
            ):
                continue

            # 截断过长内容
            if len(content) > 500:
                content = content[:500] + "..."

            if include_roles:
                role_label = {
                    "user": "用户",
                    "assistant": "助手",
                    "system": "系统",
                    "tool": "工具",
                }.get(role, role.upper())
                line = f"[{role_label}]: {content}"
            else:
                line = content

            if total_chars + len(line) + 1 > max_chars:
                break

            lines.append(line)
            total_chars += len(line) + 1

        return "\n".join(lines)

    def should_summarize(
        self,
        messages: list[dict],
        message_threshold: int = 20,
        char_threshold: int = 10000,
        token_threshold: int = 4000,
    ) -> bool:
        """
        判断是否需要总结对话

        Args:
            messages: 消息列表
            message_threshold: 消息数量阈值
            char_threshold: 总字符数阈值
            token_threshold: Token 数阈值

        Returns:
            bool: 是否需要总结
        """
        if len(messages) > message_threshold:
            return True

        total_chars = sum(
            len(self._extract_text(msg.get("content", ""))) for msg in messages
        )
        if total_chars > char_threshold:
            return True

        # Token 估算
        estimated_tokens = self.estimate_tokens(messages)
        return estimated_tokens > token_threshold

    def split_messages(
        self,
        messages: list[dict],
        keep_recent: int = 10,
    ) -> tuple[list[dict], list[dict]]:
        """
        分割消息：要总结的和要保留的

        Args:
            messages: 所有消息
            keep_recent: 保留最近 N 条消息

        Returns:
            tuple: (要总结的消息, 要保留的消息)
        """
        if len(messages) <= keep_recent:
            return [], messages

        to_summarize = messages[:-keep_recent]
        to_keep = messages[-keep_recent:]

        logger.debug(
            f"Split messages: {len(to_summarize)} to summarize, {len(to_keep)} to keep"
        )

        return to_summarize, to_keep

    def estimate_tokens(self, messages: list[dict]) -> int:
        """
        估算消息的 Token 数量

        Args:
            messages: 消息列表

        Returns:
            int: 估算的 Token 数
        """
        total_tokens = 0

        for msg in messages:
            content = self._extract_text(msg.get("content", ""))

            # 简单估算：中文和英文分开计算
            chinese_chars = len(re.findall(r"[\u4e00-\u9fff]", content))
            english_chars = len(content) - chinese_chars

            tokens = (
                chinese_chars / self.CHARS_PER_TOKEN_CN
                + english_chars / self.CHARS_PER_TOKEN_EN
            )

            # 加上角色和格式的开销
            total_tokens += int(tokens) + 4

        return total_tokens

    def get_message_stats(self, messages: list[dict]) -> MessageStats:
        """
        获取消息统计信息

        Args:
            messages: 消息列表

        Returns:
            MessageStats: 统计信息
        """
        user_messages = 0
        assistant_messages = 0
        tool_messages = 0
        total_chars = 0
        times = []

        for msg in messages:
            role = msg.get("role", "")
            if role == "user":
                user_messages += 1
            elif role == "assistant":
                assistant_messages += 1
            elif role == "tool":
                tool_messages += 1

            content = self._extract_text(msg.get("content", ""))
            total_chars += len(content)

            # 提取时间（如果有）
            if "timestamp" in msg:
                with contextlib.suppress(ValueError, TypeError):
                    times.append(datetime.fromisoformat(msg["timestamp"]))

        return MessageStats(
            total_messages=len(messages),
            total_chars=total_chars,
            estimated_tokens=self.estimate_tokens(messages),
            user_messages=user_messages,
            assistant_messages=assistant_messages,
            tool_messages=tool_messages,
            oldest_message_time=min(times) if times else None,
            newest_message_time=max(times) if times else None,
        )

    def filter_meaningful_messages(self, messages: list[dict]) -> list[dict]:
        """
        过滤掉无意义的消息（如短寒暄）

        Args:
            messages: 消息列表

        Returns:
            List[dict]: 过滤后的消息列表
        """
        filtered = []

        for msg in messages:
            content = self._extract_text(msg.get("content", "")).strip()

            # 跳过空消息
            if not content:
                continue

            # 跳过短寒暄
            if len(content) <= 8 and any(
                content.startswith(p) for p in self.SKIP_PREFIXES
            ):
                continue

            filtered.append(msg)

        return filtered

    def extract_key_points(self, messages: list[dict]) -> list[str]:
        """
        从消息中提取关键点（用于快速了解对话内容）

        Args:
            messages: 消息列表

        Returns:
            List[str]: 关键点列表
        """
        key_points = []

        for msg in messages:
            content = self._extract_text(msg.get("content", ""))
            role = msg.get("role", "")

            # 提取用户的关键问题/请求
            if role == "user":
                # 查找疑问句
                questions = re.findall(r"[^。！？.!?]*[？?]", content)
                key_points.extend(questions[:2])  # 每条消息最多 2 个问题

            # 提取助手的关键结论
            elif role == "assistant":
                # 查找总结性语句
                conclusions = re.findall(
                    r"(因此|所以|总结|结论|建议)[：:]*[^。！？.!?]*", content
                )
                key_points.extend(conclusions[:2])

        # 去重并限制数量
        seen = set()
        unique_points = []
        for point in key_points:
            if point not in seen and len(point) > 3:
                seen.add(point)
                unique_points.append(point)

        return unique_points[:10]  # 最多返回 10 个关键点

    def _extract_text(self, content) -> str:
        """从消息内容中提取文本"""
        if isinstance(content, str):
            return content
        elif isinstance(content, list):
            text_parts = []
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    text_parts.append(part.get("text", ""))
                elif isinstance(part, str):
                    text_parts.append(part)
            return " ".join(text_parts)
        return str(content)
