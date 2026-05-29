"""Background extraction agent — analyzes conversations and auto-extracts memories."""

from __future__ import annotations

import logging
from typing import Any, Callable, List, Optional

from .errors import ExtractionTimeoutError
from .memory_index import MemoryIndex
from .memory_store import MemoryStore
from .models import (
    ConversationContext,
    ExtractionResult,
    MemoryEntry,
    Message,
)

logger = logging.getLogger(__name__)

MEMORY_BLOCK_SEPARATOR = "<<<MEMORY_BLOCK>>>"

DEFAULT_TURNS_BETWEEN_EXTRACTION = 5

# Overlap ratio threshold for duplicate detection (0.0 ~ 1.0).
# Lower values are more aggressive at detecting near-duplicates.
DUPLICATE_SIMILARITY_THRESHOLD = 0.55

# Minimum description length for similarity-based duplicate detection.
# Very short descriptions (e.g., "记忆1" vs "记忆2") produce too many
# false positives at the character level.
MIN_DESC_LENGTH_FOR_DUPLICATE_CHECK = 15


def _cjk_ngrams(text: str, n: int = 2) -> set:
    """Generate n-gram character tokens for CJK text similarity."""
    return {text[i:i + n] for i in range(len(text) - n + 1)}


def _text_overlap_ratio(text_a: str, text_b: str) -> float:
    """Compute token overlap ratio, auto-detecting CJK vs word-based text."""
    if len(text_a) < MIN_DESC_LENGTH_FOR_DUPLICATE_CHECK or len(text_b) < MIN_DESC_LENGTH_FOR_DUPLICATE_CHECK:
        return 0.0

    a_has_cjk = any("一" <= c <= "鿿" for c in text_a)
    b_has_cjk = any("一" <= c <= "鿿" for c in text_b)

    if a_has_cjk or b_has_cjk:
        # 2-gram for CJK text — avoids false positives where
        # single-character sets share most chars despite different meaning
        # (e.g. "用户喜欢用中文交流" vs "用户喜欢用英文交流").
        tokens_a = _cjk_ngrams(text_a, 2)
        tokens_b = _cjk_ngrams(text_b, 2)
    else:
        tokens_a = set(text_a.lower().split())
        tokens_b = set(text_b.lower().split())

    if not tokens_a or not tokens_b:
        return 0.0

    common = tokens_a & tokens_b
    return len(common) / min(len(tokens_a), len(tokens_b))


class MemoryExtractor:
    """Analyzes dialog and auto-extracts valuable memories.

    Runs synchronously but with strict budget constraints (30s timeout, 5 turns).
    Implements mutual exclusion, throttling, and cursor-based incremental extraction.
    """

    def __init__(
        self,
        memory_store: MemoryStore,
        memory_index: MemoryIndex,
        extract_agent_fn: Callable[[str, str], str],
        turns_between: int = DEFAULT_TURNS_BETWEEN_EXTRACTION,
        save_callback: Optional[Callable[[str], Any]] = None,
    ):
        self.store = memory_store
        self.index = memory_index
        self._run_agent = extract_agent_fn
        self.turns_since_last = 0
        self.turns_between = turns_between
        self._pending_context: List[Message] = []
        self._last_cursor: Optional[str] = None
        self.save_callback = save_callback

    def extract(self, context: ConversationContext) -> ExtractionResult:
        """Main extraction entry point. Performs checks, then runs the agent."""
        if context.main_agent_wrote_memory:
            return ExtractionResult(
                extracted=0,
                skipped=True,
                reason="主 Agent 已写入记忆，跳过自动提取 (互斥)",
            )

        if self.turns_since_last < self.turns_between:
            self.turns_since_last += 1
            if context.messages:
                self._pending_context.extend(context.messages)
            return ExtractionResult(
                extracted=0,
                skipped=True,
                reason=f"距上次提取不足 {self.turns_between} 轮，暂存上下文 (当前: {self.turns_since_last})",
            )

        new_messages = self._get_new_messages(context)
        if not new_messages and not self._pending_context:
            return ExtractionResult(
                extracted=0,
                skipped=True,
                reason="无新消息需要处理",
            )

        messages_to_process = self._pending_context + new_messages
        self._pending_context.clear()

        if not messages_to_process:
            return ExtractionResult(extracted=0, skipped=True, reason="无消息内容")

        try:
            result = self._run_extraction(messages_to_process)
            if context.messages:
                self._last_cursor = context.messages[-1].uuid
            self.turns_since_last = 0
            return result
        except Exception as e:
            logger.error("记忆提取失败: %s", e)
            return ExtractionResult(
                extracted=0,
                skipped=True,
                error=str(e),
            )

    def should_extract(self, context: ConversationContext) -> bool:
        """Check whether extraction should run for this context."""
        if context.main_agent_wrote_memory:
            return False
        if self.turns_since_last < self.turns_between:
            return False
        return bool(self._get_new_messages(context) or self._pending_context)

    def stash_context(self, context: ConversationContext) -> None:
        """Stash conversation context for later batch processing."""
        if context.messages:
            self._pending_context.extend(context.messages)

    def _get_new_messages(self, context: ConversationContext) -> List[Message]:
        """Return only messages after the cursor (incremental)."""
        if not context.messages:
            return []
        if self._last_cursor is None and context.last_memory_message_uuid is not None:
            self._last_cursor = context.last_memory_message_uuid
        if self._last_cursor is None:
            return list(context.messages)

        new_msgs: List[Message] = []
        found_cursor = False
        for msg in context.messages:
            if found_cursor:
                new_msgs.append(msg)
            elif msg.uuid == self._last_cursor:
                found_cursor = True
        return new_msgs

    def _run_extraction(self, messages: List[Message]) -> ExtractionResult:
        """Build the extraction prompt and run the sandboxed agent."""
        prompt = self._build_extraction_prompt(messages)
        system_prompt = self._build_system_prompt()

        try:
            response = self._run_agent(system_prompt, prompt)
        except TimeoutError:
            raise ExtractionTimeoutError()
        except Exception:
            logger.warning("LLM extraction call failed, returning empty result", exc_info=True)
            return ExtractionResult(extracted=0, skipped=True, reason="LLM 调用失败，降级跳过")

        contents = self._parse_extraction_result(response)
        saved: List[MemoryEntry] = []
        for content in contents:
            if self.save_callback:
                result = self.save_callback(content)
                if hasattr(result, 'success') and result.success:
                    saved.append(result.data)
            else:
                logger.warning("save_callback not configured, skipping save for extracted content")

        return ExtractionResult(
            extracted=len(saved),
            new_entries=saved,
        )

    def _build_system_prompt(self) -> str:
        lines = [
            "你是一个记忆提取助手。你的任务是从对话中识别出有价值的记忆内容。",
            "",
            "## 何时提取",
            '- 用户明确表达偏好或习惯(例如: "我喜欢...", "我总是...")',
            '- 用户给出明确反馈(例如: "你做错了...", "下次记得...")',
            "- 讨论中出现了可复用的项目知识(架构决策, API约定, 技术栈选择)",
            "- 用户分享了对后续对话有用的参考信息",
            "",
            "## 何时不提取",
            "- 代码片段和实现细节(属于 git 历史)",
            "- 调试过程和临时修复方案",
            "- 已存在于 CLAUDE.md 中的内容",
            "- 单次任务的具体细节",
            "- 闲聊和不含信息的交互",
            "",
            "## 输出格式",
            "只输出记忆的原始内容，不需要分类、不需要摘要、不需要 frontmatter。",
            f"使用 {MEMORY_BLOCK_SEPARATOR} 分隔每条提取出的记忆。",
            "如果没有需要提取的记忆，回复 NONE。",
        ]
        return "\n".join(lines)

    def _build_extraction_prompt(
        self,
        messages: List[Message],
    ) -> str:
        lines = ["## 对话内容\n\n"]
        for msg in messages:
            role = "用户" if msg.role == "user" else "助手"
            content = msg.content[:500]
            lines.append(f"**{role}**: {content}\n\n")

        existing_manifest = self.store.scan_files()
        lines.append("## 已有记忆清单 (避免重复)\n\n")
        if existing_manifest:
            for m in existing_manifest:
                lines.append(f"- {m.filename}: {m.description}\n")
        else:
            lines.append("(尚无已有记忆)\n")

        lines.append(
            "\n请分析对话，提取有价值的记忆。对每条记忆，只输出原始内容。\n\n"
            f"使用 {MEMORY_BLOCK_SEPARATOR} 分隔每条记忆。\n\n"
            "如果没有需要提取的记忆，回复 NONE。\n"
        )
        return "".join(lines)

    def _parse_extraction_result(
        self,
        response: str,
    ) -> List[str]:
        """Parse the agent's response into raw content strings.

        Blocks are separated by MEMORY_BLOCK_SEPARATOR.
        """
        if not response or response.strip().upper() == "NONE":
            return []

        contents: List[str] = []
        blocks = response.split(MEMORY_BLOCK_SEPARATOR)

        for block in blocks:
            block = block.strip()
            if not block:
                continue
            contents.append(block)

        return contents
