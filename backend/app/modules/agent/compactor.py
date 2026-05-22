"""
Compactor - 对话压缩和上下文管理

借鉴自 CountBot 的 prompts.py 和 AIE 的 analyzer.py，实现：
1. 对话历史压缩（递归总结）
2. Token 计数和阈值判断
3. 消息分割和保留策略
4. 记忆条目生成
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime

logger = logging.getLogger(__name__)


# ==================== 提示词模板 ====================

# 对话总结 → 记忆条目提示词
CONVERSATION_TO_MEMORY_PROMPT = """你是一个对话总结器。将下面的对话总结为简洁的记忆条目。

要求:
1. 输出格式: 一行文本，多个事项用中文分号（；）分隔
2. 只记录有长期价值的事实信息:
   - 用户明确表达的偏好和习惯
   - 重要决策和结论
   - 项目配置和技术细节
   - 用户要求记住的内容
3. 不要记录:
   - 寒暄、确认、重复内容
   - 一次性查询结果（天气、新闻、搜索结果）
   - 工具执行的中间过程
   - 闲聊和测试内容
4. 每个事项必须包含具体信息（名称、数字、时间、地点等）
5. 如果对话没有值得长期记录的信息，输出: 无需记录

对话内容:
{messages}

输出（一行，事项用；分隔）:"""


# 递归总结提示词（用于更新已有总结）
RECURSIVE_SUMMARY_PROMPT = """你是一个对话总结器。将新的对话内容合并到已有总结中。

已有总结:
{previous_summary}

新对话:
{past_messages}

要求:
1. 合并新信息到已有总结
2. 去除过时或重复的内容
3. 保持简洁，不超过 {char_limit} 字符
4. 输出纯文本，不要 markdown 格式

更新后的总结:"""


# 短上下文压缩提示词
SHORT_CONTEXT_SUMMARY_PROMPT = """你是一个对话压缩器。将以下对话历史压缩成一个结构化的简洁摘要。

按以下 9 个部分组织输出：

### 1. 当前任务
用户的核心目标和当前正在进行的工作

### 2. 已完成
已经完成的关键步骤和成果

### 3. 待办
尚未完成的任务和下一步计划

### 4. 关键决策
已做出的重要决策及其原因

### 5. 技术细节
关键文件路径、代码片段、配置参数

### 6. 错误与修复
遇到的错误及其解决方案

### 7. 用户偏好
用户表达的习惯、偏好、风格要求

### 8. 开放问题
尚未解决或需要进一步确认的问题

### 9. 上下文
其他对后续对话重要的背景信息

要求：
- 保留用户原始消息的完整内容（不压缩用户消息）
- 不编造未出现的信息
- 某部分若无内容则写"无"
- 不超过 {char_limit} 字符

对话内容:
{messages}

简洁摘要:"""


# 递归短上下文总结提示词
RECURSIVE_SHORT_CONTEXT_SUMMARY_PROMPT = """你是一个对话压缩器。将新对话合并到已有的短摘要中，生成新的短摘要。

旧摘要:
{previous_summary}

新对话:
{past_messages}

要求:
1. 保留当前主题、关键决策、未解决问题
2. 删除过时、重复、无关信息
3. 输出纯文本，不要 markdown 格式
4. 不超过 {char_limit} 字符

更新后的短摘要:"""


# 溢出总结提示词
OVERFLOW_SUMMARY_PROMPT = """你是一个对话总结器。在即将截断旧的对话历史前，将其中有长期价值的信息总结为记忆条目。

要求:
1. 输出格式: 一行文本，多个事项用中文分号（；）分隔
2. 只记录有长期价值的事实信息:
   - 用户明确表达的偏好和习惯
   - 重要决策和结论
   - 项目配置和技术细节
   - 或者涉及的重要关键信息（如时间查询）
3. 不要记录:
   - 寒暄、确认、重复内容
   - 一次性查询结果
   - 工具执行的中间过程
4. 每个事项必须包含具体信息
5. 如果没有值得记录的信息，输出: 无需记录

对话内容:
{messages}

输出（一行，事项用；分隔）:"""


# ==================== 数据类 ====================


@dataclass
class CompactionResult:
    """压缩结果"""

    summary: str  # 压缩后的摘要
    removed_messages: int  # 移除的消息数
    kept_messages: int  # 保留的消息数
    saved_tokens: int  # 节省的 Token 数
    memory_entries: list[str] = field(default_factory=list)  # 生成的记忆条目


@dataclass
class CompactionConfig:
    """压缩配置

    阈值策略（对标 Claude Code）：
    - 日常 context 控制由 Snip + MicroCompact 承担
    - Compactor 是最后防线，只在接近模型上下文上限时才触发
    - token_threshold = context_window - buffer_tokens
    """

    # 模型上下文窗口大小，从 AI 配置读取
    context_window: int = 200_000

    # 安全缓冲 token：预留空间给工具结果 + 模型输出
    # Claude Code 用 33K (20K output reserve + 13K buffer)
    buffer_tokens: int = 33_000

    # 消息数阈值（兜底保护：token 估算可能不准）
    message_threshold: int = 200

    # 保留最近消息数
    keep_recent_messages: int = 8

    # 摘要最大字符数
    max_summary_chars: int = 3000

    # 是否生成记忆条目
    generate_memory: bool = True

    # 是否使用递归总结
    use_recursive_summary: bool = True

    @property
    def token_threshold(self) -> int:
        """触发压缩的 token 阈值 = context_window - effective_buffer

        小上下文模型（context_window <= buffer_tokens）自动缩小 buffer
        到窗口的 10%，避免阈值变成负数。
        """
        effective_buffer = self.buffer_tokens
        if self.context_window <= self.buffer_tokens:
            effective_buffer = max(1, self.context_window // 10)
        return self.context_window - effective_buffer


# ==================== Compactor 类 ====================


class Compactor:
    """
    对话压缩器

    功能：
    1. 检测是否需要压缩
    2. 分割消息（总结 vs 保留）
    3. 生成摘要
    4. 提取记忆条目
    """

    def __init__(
        self,
        config: CompactionConfig | None = None,
        llm_client=None,  # LLM 客户端（用于生成摘要）
        memory_orchestrator=None,  # LayeredMemory MemoryOrchestrator（写入 L1）
        user_id: int = 1,  # 用户 ID（写入记忆时使用）
        session_id: str | None = None,  # 会话 ID
        agent_id: int | None = None,  # Agent ID
    ):
        self.config = config or CompactionConfig()
        self.llm_client = llm_client
        self.memory_orchestrator = memory_orchestrator
        self.user_id = user_id
        self.session_id = session_id
        self.agent_id = agent_id

        # 当前摘要（递归总结用）
        self._current_summary: str | None = None

        # 熔断器：连续失败次数
        self._consecutive_failures: int = 0
        self.MAX_CONSECUTIVE_FAILURES: int = 3

    def should_compact(
        self,
        messages: list[dict],
        token_count: int | None = None,
    ) -> bool:
        """
        判断是否需要压缩

        触发条件（任一满足即触发）：
        1. 消息数超过 message_threshold（兜底保护，token 估算可能不准）
        2. Token 数超过 token_threshold = context_window - buffer_tokens

        Args:
            messages: 消息列表
            token_count: Token 数（可选，不传则估算）

        Returns:
            bool: 是否需要压缩
        """
        if len(messages) > self.config.message_threshold:
            logger.info(
                f"Compaction triggered: message count {len(messages)} > {self.config.message_threshold}"
            )
            return True

        if token_count is None:
            token_count = self._estimate_tokens(messages)

        threshold = self.config.token_threshold
        if token_count > threshold:
            logger.info(
                f"Compaction triggered: tokens {token_count} > threshold {threshold} "
                f"(window={self.config.context_window}, buffer={self.config.buffer_tokens})"
            )
            return True

        return False

    async def compact(
        self,
        messages: list[dict],
        existing_summary: str | None = None,
        instruction: str | None = None,
        force: bool = False,
    ) -> CompactionResult:
        """
        压缩对话历史

        Args:
            messages: 消息列表
            existing_summary: 已有的摘要（用于递归总结）
            instruction: 用户自定义压缩指令（如"重点保留 API 设计"）
            force: 是否强制压缩（忽略 should_compact 阈值，用于手动压缩）

        Returns:
            CompactionResult: 压缩结果
        """
        if not force and not self.should_compact(messages):
            return CompactionResult(
                summary="",
                removed_messages=0,
                kept_messages=len(messages),
                saved_tokens=0,
            )

        # 分割消息
        to_summarize, to_keep = self._split_messages(messages)

        if not to_summarize:
            return CompactionResult(
                summary="",
                removed_messages=0,
                kept_messages=len(messages),
                saved_tokens=0,
            )

        # 估算节省的 Token
        saved_tokens = self._estimate_tokens(to_summarize)

        # 熔断器检查
        if self._consecutive_failures >= self.MAX_CONSECUTIVE_FAILURES:
            logger.warning(
                f"Compactor circuit breaker open ({self._consecutive_failures} "
                f"consecutive failures), skipping compaction"
            )
            return CompactionResult(
                summary="",
                removed_messages=0,
                kept_messages=len(messages),
                saved_tokens=0,
            )

        try:
            # 生成摘要
            summary = await self._generate_summary(
                to_summarize,
                existing_summary or self._current_summary,
                instruction=instruction,
            )

            # 空摘要保护：摘要为空 = 压缩失败，不丢弃历史
            if not summary or not summary.strip():
                logger.warning(
                    "Compaction failed: empty summary, preserving original messages"
                )
                self._consecutive_failures += 1
                return CompactionResult(
                    summary="",
                    removed_messages=0,
                    kept_messages=len(messages),
                    saved_tokens=0,
                )

            # 成功，重置失败计数
            self._consecutive_failures = 0

            # 更新当前摘要
            self._current_summary = summary

            # 生成记忆条目
            memory_entries = []
            if self.config.generate_memory:
                memory_entries = await self._generate_memory_entries(to_summarize)

                # 将记忆条目写入 L1（如果 MemoryOrchestrator 可用）
                if self.memory_orchestrator and memory_entries:
                    await self._write_memories_to_l1(memory_entries)

            return CompactionResult(
                summary=summary,
                removed_messages=len(to_summarize),
                kept_messages=len(to_keep),
                saved_tokens=saved_tokens,
                memory_entries=memory_entries,
            )
        except Exception as e:
            logger.error(f"Compaction failed: {e}, preserving original messages")
            self._consecutive_failures += 1
            return CompactionResult(
                summary="",
                removed_messages=0,
                kept_messages=len(messages),
                saved_tokens=0,
            )

    def _split_messages(
        self,
        messages: list[dict],
    ) -> tuple[list[dict], list[dict]]:
        """分割消息：要总结的和要保留的"""
        keep_recent = self.config.keep_recent_messages

        if len(messages) <= keep_recent:
            return [], messages

        if keep_recent == 0:
            to_summarize = messages
            to_keep = []
        else:
            to_summarize = messages[:-keep_recent]
            to_keep = messages[-keep_recent:]

        return to_summarize, to_keep

    async def _generate_summary(
        self,
        messages: list[dict],
        existing_summary: str | None = None,
        instruction: str | None = None,
    ) -> str:
        """生成摘要"""
        if not self.llm_client:
            # 无 LLM 客户端，返回简单统计
            return self._generate_simple_summary(messages)

        # 格式化消息
        formatted_messages = self._format_messages(messages)

        try:
            if existing_summary and self.config.use_recursive_summary:
                # 递归总结
                prompt = RECURSIVE_SHORT_CONTEXT_SUMMARY_PROMPT.format(
                    previous_summary=existing_summary,
                    past_messages=formatted_messages,
                    char_limit=self.config.max_summary_chars,
                )
            else:
                # 首次总结
                prompt = SHORT_CONTEXT_SUMMARY_PROMPT.format(
                    messages=formatted_messages,
                    char_limit=self.config.max_summary_chars,
                )

            # 注入用户自定义压缩指令
            if instruction:
                prompt += f"\n\n## 用户自定义要求\n{instruction}\n"

            # 调用 LLM
            response = await self._call_llm(prompt)
            return response.strip()

        except Exception as e:
            logger.error(f"Failed to generate summary: {e}")
            return self._generate_simple_summary(messages)

    async def _generate_memory_entries(
        self,
        messages: list[dict],
    ) -> list[str]:
        """生成记忆条目（使用 CONVERSATION_TO_MEMORY_PROMPT）"""
        if not self.llm_client:
            return []

        formatted_messages = self._format_messages(messages)

        try:
            # 使用更完整的对话→记忆提示词
            prompt = CONVERSATION_TO_MEMORY_PROMPT.format(messages=formatted_messages)
            response = await self._call_llm(prompt)

            if "无需记录" in response:
                return []

            # 按分号分割
            entries = [e.strip() for e in response.split("；") if e.strip()]
            return entries

        except Exception as e:
            logger.error(f"Failed to generate memory entries: {e}")
            return []

    async def _write_memories_to_l1(self, entries: list[str]) -> None:
        """将记忆条目写入 L1 会话记忆（通过 MemoryOrchestrator）"""
        try:
            for entry in entries:
                await self.memory_orchestrator.store(
                    content=entry,
                    name=f"压缩记忆_{datetime.now().strftime('%H%M%S')}",
                    user_id=self.user_id,
                    session_id=self.session_id,
                    agent_id=self.agent_id,
                    context_type="memory",
                    source="compactor",
                    importance=2,
                )
            logger.info(
                f"Wrote {len(entries)} memory entries to L1 via MemoryOrchestrator"
            )
        except Exception as e:
            logger.warning(f"Failed to write memories to L1: {e}")

    async def _call_llm(self, prompt: str) -> str:
        """调用 LLM

        支持多种 LLM 客户端接口：
        - chat(messages) -> dict: 标准非流式接口
        - complete(prompt) -> str: 简化接口
        - chat_stream(messages) -> AsyncIterator[dict]: 流式接口（自动聚合）
        """
        if not self.llm_client:
            return ""

        try:
            # 1. 标准非流式 chat 接口
            if hasattr(self.llm_client, "chat"):
                response = await self.llm_client.chat(
                    [{"role": "user", "content": prompt}]
                )
                if isinstance(response, dict):
                    return response.get("content", "")
                return str(response)

            # 2. 简化 complete 接口
            elif hasattr(self.llm_client, "complete"):
                response = await self.llm_client.complete(prompt)
                return str(response)

            # 3. 流式 chat_stream 接口（如 SimpleLLMProvider）— 聚合所有 chunk
            elif hasattr(self.llm_client, "chat_stream"):
                content_parts = []
                async for chunk in self.llm_client.chat_stream(
                    [{"role": "user", "content": prompt}]
                ):
                    if isinstance(chunk, dict):
                        if chunk.get("error"):
                            logger.error(f"LLM stream error: {chunk['error']}")
                            return ""
                        if chunk.get("content"):
                            content_parts.append(chunk["content"])
                    else:
                        content_parts.append(str(chunk))
                return "".join(content_parts)

            else:
                logger.warning(
                    f"LLM client has no supported interface: {type(self.llm_client).__name__}"
                )
                return ""
        except Exception as e:
            logger.error(f"LLM call failed: {e}")
            return ""

    def _format_messages(self, messages: list[dict]) -> str:
        """格式化消息为文本（压缩前调用）

        处理多模态内容：将图片/文档替换为占位符，避免压缩请求本身过大。
        """
        lines = []
        for msg in messages:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")

            if isinstance(content, list):
                # 多模态内容：图片/文档替换为占位符
                parts = []
                for part in content:
                    if isinstance(part, dict):
                        part_type = part.get("type", "")
                        if part_type == "image":
                            parts.append("[image]")
                        elif part_type == "document":
                            parts.append("[document]")
                        else:
                            parts.append(part.get("text", ""))
                    else:
                        parts.append(str(part))
                content = " ".join(parts)

            content = str(content).strip()
            if len(content) > 500:
                content = content[:500] + "..."

            role_label = {
                "user": "用户",
                "assistant": "助手",
                "system": "系统",
                "tool": "工具",
            }.get(role, role)

            lines.append(f"[{role_label}]: {content}")

        return "\n".join(lines)

    def _generate_simple_summary(self, messages: list[dict]) -> str:
        """生成简单统计摘要（无 LLM 时使用）"""
        user_msgs = sum(1 for m in messages if m.get("role") == "user")
        assistant_msgs = sum(1 for m in messages if m.get("role") == "assistant")

        return (
            f"[历史对话摘要] 共 {len(messages)} 条消息 "
            f"(用户: {user_msgs}, 助手: {assistant_msgs})。"
            f"已压缩 {len(messages)} 条历史消息以节省上下文空间。"
        )

    def _estimate_tokens(self, messages: list[dict]) -> int:
        """估算 Token 数"""
        total = 0
        for msg in messages:
            content = msg.get("content", "")
            if isinstance(content, list):
                content = " ".join(
                    part.get("text", "") if isinstance(part, dict) else str(part)
                    for part in content
                )
            # 简单估算：中文 1.5 字符/token，英文 4 字符/token
            import re

            chinese = len(re.findall(r"[\u4e00-\u9fff]", str(content)))
            english = len(str(content)) - chinese
            total += int(chinese / 1.5 + english / 4) + 4
        return total

    @property
    def current_summary(self) -> str | None:
        """获取当前摘要"""
        return self._current_summary

    def reset_summary(self) -> None:
        """重置摘要"""
        self._current_summary = None


# ==================== 便捷函数 ====================


def create_compactor(
    context_window: int = 200_000,
    buffer_tokens: int = 33_000,
    message_threshold: int = 200,
    keep_recent: int = 10,
    llm_client=None,
    user_id: int = 1,
    session_id: str | None = None,
    agent_id: int | None = None,
) -> Compactor:
    """创建 Compactor 实例"""
    config = CompactionConfig(
        context_window=context_window,
        buffer_tokens=buffer_tokens,
        message_threshold=message_threshold,
        keep_recent_messages=keep_recent,
    )
    return Compactor(
        config=config,
        llm_client=llm_client,
        user_id=user_id,
        session_id=session_id,
        agent_id=agent_id,
    )
