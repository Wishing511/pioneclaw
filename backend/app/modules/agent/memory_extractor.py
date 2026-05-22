"""
MemoryExtractor - 自动记忆提取（VV.1）

借鉴 claude-code-sourcemap extractMemories：
在每个 Agent query loop 完成后，fork 子 Agent 从对话中提取重要记忆
和用户画像特征，写入 LayeredMemory L2。

与 Compactor 的区别：
- Compactor: 压缩对话避免上下文溢出 → L1 会话记忆
- MemoryExtractor: 提取长期价值信息 → L2 长期记忆 + 用户画像
"""

import json
import logging
import re
from datetime import datetime
from typing import Any

from app.core.database import async_session_maker

logger = logging.getLogger(__name__)


# ==================== 提示词模板 ====================

MEMORY_EXTRACTION_PROMPT = """你是一个对话记忆提取器。分析以下对话，提取两类信息：重要事实和用户画像特征。

## 提取规则

### 1. 重要事实 (facts)
只记录有长期价值的事实信息：
- 用户明确表达的偏好和习惯
- 重要决策和结论
- 项目配置和技术细节
- 用户要求记住的内容
- 工作进展和成果

不要记录：
- 寒暄、确认、重复内容
- 一次性查询结果
- 工具执行的中间过程
- 闲聊和测试内容

### 2. 用户画像特征 (traits)
仅在对话中明确体现时才提取，用 JSON 格式：
- preferred_language: 偏好的编程语言
- skill_level: 技术水平（beginner/intermediate/senior/expert）
- primary_role: 主要角色（developer/designer/manager/researcher/student）
- tools: 常用工具列表
- interests: 关注的技术领域
- communication_style: 沟通风格偏好
- 其他从对话中明确观察到的特征

## 对话内容
{messages}

## 输出格式
请严格按照以下格式输出：

---FACTS---
事实1；事实2；事实3
---TRAITS---
{{"preferred_language": "Python", "skill_level": "senior"}}

注意：
- facts 用中文分号（；）分隔，一行写完
- 如果没有任何值得记录的信息，facts 输出：无需记录
- traits 必须是合法的 JSON 对象，如果没有新观察到特征，输出：{{}}
- 不要在格式标记之外输出任何其他内容"""


class MemoryExtractor:
    """从对话中提取重要记忆和用户画像，写入 LayeredMemory L2"""

    def __init__(
        self,
        llm_provider: Any = None,
        model: str = "gpt-4",
        user_id: int = 1,
        session_id: str = "",
        agent_id: int | None = None,
        enabled: bool = True,
        memory_store: Any = None,
    ):
        self.llm_provider = llm_provider
        self.model = model
        self.user_id = user_id
        self.session_id = session_id
        self.agent_id = agent_id
        self.enabled = enabled
        self.memory_store = memory_store

    async def extract_and_store(
        self,
        messages: list[dict[str, Any]],
    ) -> str | None:
        """
        主入口：从对话中提取记忆并写入 L2

        Args:
            messages: 完整对话消息列表

        Returns:
            写入的 fact 数量描述字符串，失败返回 None
        """
        if not self.enabled:
            return None
        if not messages:
            return None
        if not self.llm_provider:
            logger.warning("MemoryExtractor: no LLM provider, skipping extraction")
            return None

        try:
            # 1. 构建 prompt
            prompt = self._build_extraction_prompt(messages)

            # 2. 调用 LLM
            response_text = await self._call_llm(prompt)
            if not response_text:
                logger.warning("MemoryExtractor: empty LLM response")
                return None

            # 3. 解析响应
            facts, traits = self._parse_response(response_text)

            # 4. 存储
            stored_count = 0
            if facts:
                stored_count = await self._store_facts(facts)
            if traits:
                await self._store_traits(traits)

            if stored_count > 0:
                logger.info(
                    f"MemoryExtractor: stored {stored_count} facts and {len(traits)} traits"
                )
            return f"{stored_count} facts, {len(traits)} traits"

        except Exception as e:
            logger.error(f"MemoryExtractor: extraction failed: {e}", exc_info=True)
            return None

    def _build_extraction_prompt(self, messages: list[dict[str, Any]]) -> str:
        """将对话消息格式化为提取 prompt"""
        formatted = []
        total_chars = 0
        max_chars = 8000

        for msg in messages:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            if isinstance(content, list):
                # 多模态内容块
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
                    formatted.append(line[:remaining] + "...")
                formatted.append("[剩余对话已截断]")
                break

            formatted.append(line)
            total_chars += len(line)

        conversation_text = "\n".join(formatted)

        return MEMORY_EXTRACTION_PROMPT.format(messages=conversation_text)

    async def _call_llm(self, prompt: str) -> str | None:
        """调用 LLM 提取记忆"""
        try:
            messages = [{"role": "user", "content": prompt}]

            # 尝试流式调用并收集结果
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
                logger.warning(
                    "MemoryExtractor: LLM provider has no chat_stream or chat method"
                )
                return None
        except Exception as e:
            logger.error(f"MemoryExtractor: LLM call failed: {e}")
            return None

    def _parse_response(self, text: str) -> tuple:
        """
        解析 LLM 响应，提取 facts 和 traits

        格式：
        ---FACTS---
        事实1；事实2
        ---TRAITS---
        {"key": "value"}
        """
        facts: list[str] = []
        traits: dict = {}

        # 解析 facts
        facts_match = re.search(
            r"---FACTS---\s*\n?(.*?)(?=---TRAITS---|---|$)", text, re.DOTALL
        )
        if facts_match:
            facts_text = facts_match.group(1).strip()
            if facts_text and "无需记录" not in facts_text:
                # 按分号分割
                facts = [f.strip() for f in facts_text.split("；") if f.strip()]
                # 也支持换行分隔
                if len(facts) <= 1:
                    facts = [
                        f.strip()
                        for f in facts_text.split("\n")
                        if f.strip() and f.strip() != "无需记录"
                    ]

        # 解析 traits
        traits_match = re.search(r"---TRAITS---\s*\n?(.*?)$", text, re.DOTALL)
        if traits_match:
            traits_text = traits_match.group(1).strip()
            if traits_text and traits_text != "{}":
                try:
                    # 提取 JSON 对象
                    json_match = re.search(r"\{[^}]+\}", traits_text)
                    if json_match:
                        traits = json.loads(json_match.group(0))
                except json.JSONDecodeError:
                    logger.warning(
                        f"MemoryExtractor: failed to parse traits JSON: {traits_text[:100]}"
                    )

        return facts, traits

    async def _store_facts(self, facts: list[str]) -> int:
        """将提取的 facts 写入 L2 + Track 1 (MEMORY.md)"""
        if not facts:
            return 0

        try:
            from app.modules.agent.layered_memory.memory_orchestrator import (
                MemoryOrchestrator,
            )

            stored = 0
            async with async_session_maker() as db:
                orchestrator = MemoryOrchestrator(db)
                for fact in facts:
                    try:
                        await orchestrator.store(
                            content=fact,
                            name=f"提取记忆_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{stored}",
                            user_id=self.user_id,
                            session_id=self.session_id,
                            agent_id=self.agent_id,
                            context_type="memory",
                            source="memory_extractor",
                            importance=3,
                        )
                        stored += 1
                    except Exception as e:
                        logger.warning(
                            f"MemoryExtractor: failed to store fact '{fact[:50]}': {e}"
                        )
                await db.commit()

            # 同时写入 Track 1 (MEMORY.md)
            if self.memory_store and facts:
                try:
                    combined = "；".join(facts)
                    self.memory_store.append_entry(
                        source="memory_extractor",
                        content=combined,
                    )
                    logger.info(
                        f"MemoryExtractor: wrote {len(facts)} facts to MEMORY.md (Track 1)"
                    )
                except Exception as e:
                    logger.warning(f"MemoryExtractor: failed to write to Track 1: {e}")

            return stored
        except Exception as e:
            logger.error(f"MemoryExtractor: _store_facts failed: {e}")
            return 0

    async def _store_traits(self, traits: dict) -> int:
        """将用户画像特征写入 L2 + Track 1 (MEMORY.md)"""
        if not traits:
            return 0

        try:
            from app.modules.agent.layered_memory.memory_orchestrator import (
                MemoryOrchestrator,
            )

            stored = 0
            async with async_session_maker() as db:
                orchestrator = MemoryOrchestrator(db)
                for key, value in traits.items():
                    try:
                        content = f"用户画像 - {key}: {value}"
                        await orchestrator.store(
                            content=content,
                            name=f"用户画像_{key}",
                            user_id=self.user_id,
                            session_id=self.session_id,
                            agent_id=self.agent_id,
                            context_type="memory",
                            source="memory_extractor_traits",
                            importance=4,
                            tags=["user_profile", key],
                        )
                        stored += 1
                    except Exception as e:
                        logger.warning(
                            f"MemoryExtractor: failed to store trait '{key}': {e}"
                        )
                await db.commit()

            # 同时写入 Track 1 (MEMORY.md)
            if self.memory_store and traits:
                try:
                    for key, value in traits.items():
                        self.memory_store.append_entry(
                            source="memory_extractor",
                            content=f"用户画像 - {key}: {value}",
                        )
                    logger.info(
                        f"MemoryExtractor: wrote {len(traits)} traits to MEMORY.md (Track 1)"
                    )
                except Exception as e:
                    logger.warning(
                        f"MemoryExtractor: failed to write traits to Track 1: {e}"
                    )

            return stored
        except Exception as e:
            logger.error(f"MemoryExtractor: _store_traits failed: {e}")
            return 0
