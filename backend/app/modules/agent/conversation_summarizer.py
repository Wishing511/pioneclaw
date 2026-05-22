"""
ConversationSummarizer - LLM 对话摘要写入 MEMORY.md (Track 1)

借鉴 AIE 的 ConversationSummarizer 模式：
- 阈值触发（token 数 / 工具调用数）
- LLM 摘要 → 追加到 MEMORY.md
- 冷却期防止频繁触发
- LLM 不可用时降级（截断写入）

替代 SessionMemoryManager（启发式关键词提取，无 LLM）。
"""

import logging
import time
from dataclasses import dataclass
from typing import Any

from app.modules.agent.compactor import CONVERSATION_TO_MEMORY_PROMPT

logger = logging.getLogger(__name__)


@dataclass
class SummarizerConfig:
    """摘要器配置"""

    token_threshold: int = 4000  # token 数阈值
    tool_call_threshold: int = 5  # 工具调用次数阈值
    cooldown_seconds: float = 300.0  # 冷却期（秒）
    max_memory_chars: int = 4000  # 摘要最大字符数
    source_tag: str = "agent-summary"  # MEMORY.md 中的来源标签


class ConversationSummarizer:
    """LLM 对话摘要器 — 写入 MEMORY.md (Track 1)

    用法:
        summarizer = ConversationSummarizer(
            llm_provider=provider,
            memory_store=store,
            config=SummarizerConfig(),
        )
        if summarizer.should_summarize(token_count, tool_call_count):
            await summarizer.summarize_conversation(messages)
    """

    def __init__(
        self,
        llm_provider: Any = None,
        memory_store: Any = None,
        model: str = "gpt-4",
        user_id: int = 1,
        session_id: str = "",
        agent_id: int | None = None,
        config: SummarizerConfig | None = None,
    ):
        self.llm_provider = llm_provider
        self.memory_store = memory_store
        self.model = model
        self.user_id = user_id
        self.session_id = session_id
        self.agent_id = agent_id
        self.config = config or SummarizerConfig()
        self._last_summarized_at: float = 0.0

    def should_summarize(self, token_count: int = 0, tool_call_count: int = 0) -> bool:
        """判断是否需要摘要"""
        # 冷却期检查
        now = time.time()
        if now - self._last_summarized_at < self.config.cooldown_seconds:
            return False
        # 阈值检查（任一达到即可）
        if token_count >= self.config.token_threshold:
            return True
        return tool_call_count >= self.config.tool_call_threshold

    async def summarize_conversation(
        self, messages: list[dict[str, Any]]
    ) -> str | None:
        """摘要对话并写入 MEMORY.md

        Returns:
            写入的摘要文本，或 None（如 LLM 判断无需记录）
        """
        if not messages:
            return None
        if not self.memory_store:
            logger.warning("ConversationSummarizer: no memory_store, skipping")
            return None

        try:
            # 1. 格式化消息
            formatted = self._format_messages(messages)

            # 2. LLM 摘要
            summary = await self._call_llm_for_summary(formatted)

            # 3. 检查是否"无需记录"
            if not summary or "无需记录" in summary:
                logger.info("ConversationSummarizer: LLM decided nothing to record")
                return None

            # 4. 写入 MEMORY.md
            line_number = self.memory_store.append_entry(
                source=self.config.source_tag,
                content=summary[: self.config.max_memory_chars],
            )
            self._last_summarized_at = time.time()
            logger.info(
                f"ConversationSummarizer: wrote summary to MEMORY.md line {line_number}"
            )
            return summary

        except Exception as e:
            logger.error(
                f"ConversationSummarizer: summarize failed: {e}", exc_info=True
            )
            return None

    async def summarize_overflow(
        self, old_messages: list[dict[str, Any]]
    ) -> str | None:
        """Compactor 溢出时调用：摘要被淘汰的消息

        Args:
            old_messages: 被淘汰的旧消息列表

        Returns:
            写入的摘要文本
        """
        if not old_messages or not self.memory_store:
            return None

        try:
            formatted = self._format_messages(old_messages)

            if self.llm_provider:
                summary = await self._call_llm_for_summary(formatted)
            else:
                # 无 LLM 时降级：截断写入
                summary = self._fallback_summary(formatted)

            if not summary or "无需记录" in summary:
                return None

            line_number = self.memory_store.append_entry(
                source="auto-overflow",
                content=summary[: self.config.max_memory_chars],
            )
            logger.info(
                f"ConversationSummarizer: wrote overflow summary to MEMORY.md line {line_number}"
            )
            return summary

        except Exception as e:
            logger.error(f"ConversationSummarizer: overflow summarize failed: {e}")
            return None

    def _format_messages(self, messages: list[dict[str, Any]]) -> str:
        """格式化消息为文本（复用 AIE MessageAnalyzer 的简化版）"""
        lines = []
        total_chars = 0
        max_chars = 8000

        for msg in messages:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            if isinstance(content, list):
                text_parts = []
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        text_parts.append(block.get("text", ""))
                content = " ".join(text_parts)

            if not content or not str(content).strip():
                continue

            line = f"[{role}]: {content}"
            if total_chars + len(line) > max_chars:
                remaining = max_chars - total_chars - 50
                if remaining > 100:
                    lines.append(line[:remaining] + "...")
                lines.append("[剩余对话已截断]")
                break

            lines.append(line)
            total_chars += len(line)

        return "\n".join(lines)

    async def _call_llm_for_summary(self, formatted_messages: str) -> str | None:
        """调用 LLM 生成摘要"""
        if not self.llm_provider:
            return self._fallback_summary(formatted_messages)

        try:
            prompt = CONVERSATION_TO_MEMORY_PROMPT.format(messages=formatted_messages)
            messages = [{"role": "user", "content": prompt}]

            if hasattr(self.llm_provider, "chat_stream"):
                chunks = []
                async for chunk in self.llm_provider.chat_stream(
                    messages=messages,
                    model=self.model,
                ):
                    if isinstance(chunk, dict):
                        content = chunk.get("content", "") or chunk.get("text", "")
                        if content:
                            chunks.append(content)
                return "".join(chunks).strip()
            elif hasattr(self.llm_provider, "chat"):
                response = await self.llm_provider.chat(messages)
                if isinstance(response, dict):
                    return response.get("content", "").strip()
                return str(response).strip()
            else:
                return self._fallback_summary(formatted_messages)

        except Exception as e:
            logger.error(f"ConversationSummarizer: LLM call failed: {e}")
            return self._fallback_summary(formatted_messages)

    def _fallback_summary(self, formatted_messages: str) -> str:
        """LLM 不可用时的降级摘要（截断写入）"""
        # 取最后 500 字符
        text = formatted_messages.strip()
        if len(text) > 500:
            text = "..." + text[-500:]
        return text
