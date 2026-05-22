"""
TierManager — L0/L1/L2 层级管理

职责：
- 存储 L2 全文 + 自动生成 L0 摘要和 L1 概述
- 按层级获取记忆
- L1→L2 提升
- 更新内容并重新生成 L0/L1
- 清理过期 L0

URI 命名规范:
- L2: viking://user/{user_id}/{name}
- L1: {l2_uri}/.level_1
- L0: {l2_uri}/.level_0
"""

import uuid
from datetime import datetime, timezone

from loguru import logger
from sqlalchemy import and_, delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.layered_memory import LayeredMemory, MemoryLayer

# L0/L1 生成提示词
L0_GENERATE_PROMPT = """请用一句话概括以下内容的核心要点，不超过50字：

{content}"""

L1_GENERATE_PROMPT = """请用一段话（100-200字）概述以下内容的关键信息：

{content}"""


class TierManager:
    """L0/L1/L2 层级管理器"""

    def __init__(self, db_session: AsyncSession, llm_caller=None):
        """
        Args:
            db_session: SQLAlchemy 异步会话
            llm_caller: LLM 调用函数，签名 async (prompt: str) -> str
                        如果为 None，L0/L1 将使用截断作为降级方案
        """
        self.db = db_session
        self.llm_caller = llm_caller

    # ==================== 存储 ====================

    async def store(
        self,
        content: str,
        name: str,
        user_id: int,
        context_type: str = "memory",
        uri: str | None = None,
        parent_uri: str | None = None,
        tags: list | None = None,
        source: str | None = None,
        importance: int = 3,
        session_id: str | None = None,
        agent_id: int | None = None,
    ) -> LayeredMemory:
        """存储 L2 全文，并自动生成 L0 摘要和 L1 概述"""

        # 自动生成 URI
        if not uri:
            uri = f"viking://user/{user_id}/{name.lower().replace(' ', '_')}_{uuid.uuid4().hex[:8]}"

        # 创建 L2 记录
        l2 = LayeredMemory(
            uri=uri,
            parent_uri=parent_uri,
            layer=MemoryLayer.LONG_TERM,
            context_type=context_type,
            name=name,
            content=content,
            tags=tags or [],
            source=source,
            importance=importance,
            session_id=session_id,
            user_id=user_id,
            agent_id=agent_id,
        )
        self.db.add(l2)
        await self.db.flush()

        # 自动生成 L0 和 L1
        l0_uri = f"{uri}/.level_0"
        l1_uri = f"{uri}/.level_1"

        abstract = await self._generate_l0(content)
        overview = await self._generate_l1(content)

        # 创建 L0 记录
        l0 = LayeredMemory(
            uri=l0_uri,
            parent_uri=uri,
            layer=MemoryLayer.WORKING,
            context_type=context_type,
            name=name,
            content=content,
            abstract=abstract,
            overview=overview,
            tags=tags or [],
            source=source,
            importance=importance,
            session_id=session_id,
            user_id=user_id,
            agent_id=agent_id,
        )
        self.db.add(l0)

        # 创建 L1 记录
        l1 = LayeredMemory(
            uri=l1_uri,
            parent_uri=uri,
            layer=MemoryLayer.SESSION,
            context_type=context_type,
            name=name,
            content=content,
            abstract=abstract,
            overview=overview,
            tags=tags or [],
            source=source,
            importance=importance,
            session_id=session_id,
            user_id=user_id,
            agent_id=agent_id,
        )
        self.db.add(l1)
        await self.db.flush()

        logger.info(f"Stored L2 memory: {uri} (with L0/L1)")
        return l2

    # ==================== 获取 ====================

    async def get(self, uri: str, layer: int | None = None) -> LayeredMemory | None:
        """按 URI 获取记忆，可指定层级"""
        if layer is not None:
            # 获取特定层级
            # 如果 uri 不包含层级后缀，自动添加
            target_uri = uri
            if layer == 0 and not uri.endswith("/.level_0"):
                target_uri = f"{uri}/.level_0"
            elif layer == 1 and not uri.endswith("/.level_1"):
                target_uri = f"{uri}/.level_1"

            stmt = select(LayeredMemory).where(LayeredMemory.uri == target_uri)
            result = await self.db.execute(stmt)
            memory = result.scalar_one_or_none()
            if memory:
                # 增加访问计数
                memory.access_count += 1
                await self.db.flush()
                return memory

        # 获取 L2（默认）
        stmt = select(LayeredMemory).where(
            and_(LayeredMemory.uri == uri, LayeredMemory.layer == MemoryLayer.LONG_TERM)
        )
        result = await self.db.execute(stmt)
        memory = result.scalar_one_or_none()
        if memory:
            memory.access_count += 1
            await self.db.flush()
        return memory

    async def get_with_context(self, uri: str) -> LayeredMemory | None:
        """获取 L2 记忆，并填充 L0/L1 内容"""
        l2 = await self.get(uri)
        if not l2:
            return None

        # 获取 L0 和 L1
        l0 = await self.get(uri, layer=0)
        l1 = await self.get(uri, layer=1)

        # 填充摘要和概述
        if l0 and l0.abstract:
            l2.abstract = l0.abstract
        if l1 and l1.overview:
            l2.overview = l1.overview

        return l2

    # ==================== 更新 ====================

    async def update_content(self, uri: str, new_content: str) -> LayeredMemory | None:
        """更新 L2 内容并重新生成 L0/L1"""
        stmt = select(LayeredMemory).where(
            and_(LayeredMemory.uri == uri, LayeredMemory.layer == MemoryLayer.LONG_TERM)
        )
        result = await self.db.execute(stmt)
        l2 = result.scalar_one_or_none()
        if not l2:
            return None

        l2.content = new_content
        l2.updated_at = datetime.now(tz=timezone.utc)

        # 重新生成 L0/L1
        abstract = await self._generate_l0(new_content)
        overview = await self._generate_l1(new_content)

        # 更新 L0
        l0_uri = f"{uri}/.level_0"
        stmt_l0 = select(LayeredMemory).where(LayeredMemory.uri == l0_uri)
        result_l0 = await self.db.execute(stmt_l0)
        l0 = result_l0.scalar_one_or_none()
        if l0:
            l0.content = new_content
            l0.abstract = abstract
            l0.overview = overview
            l0.updated_at = datetime.now(tz=timezone.utc)

        # 更新 L1
        l1_uri = f"{uri}/.level_1"
        stmt_l1 = select(LayeredMemory).where(LayeredMemory.uri == l1_uri)
        result_l1 = await self.db.execute(stmt_l1)
        l1 = result_l1.scalar_one_or_none()
        if l1:
            l1.content = new_content
            l1.abstract = abstract
            l1.overview = overview
            l1.updated_at = datetime.now(tz=timezone.utc)

        # 同步更新 L2 的摘要字段
        l2.abstract = abstract
        l2.overview = overview

        await self.db.flush()
        logger.info(f"Updated L2 memory: {uri} (regenerated L0/L1)")
        return l2

    # ==================== 提升 ====================

    async def promote(
        self, uri: str, from_layer: int = 1, to_layer: int = 2
    ) -> LayeredMemory | None:
        """将记忆从低层级提升到高层级（默认 L1→L2）"""
        # 获取源层级记忆
        source_uri = uri
        if from_layer == 0 and not uri.endswith("/.level_0"):
            source_uri = f"{uri}/.level_0"
        elif from_layer == 1 and not uri.endswith("/.level_1"):
            source_uri = f"{uri}/.level_1"

        stmt = select(LayeredMemory).where(LayeredMemory.uri == source_uri)
        result = await self.db.execute(stmt)
        source = result.scalar_one_or_none()
        if not source:
            return None

        # 检查目标层级是否已存在
        if to_layer == 2:
            # L1→L2：将 L1 的概述作为 L2 的内容
            target_uri = source.parent_uri or uri.replace("/.level_1", "").replace(
                "/.level_0", ""
            )
            stmt_target = select(LayeredMemory).where(
                and_(
                    LayeredMemory.uri == target_uri,
                    LayeredMemory.layer == MemoryLayer.LONG_TERM,
                )
            )
            result_target = await self.db.execute(stmt_target)
            target = result_target.scalar_one_or_none()

            if target:
                # 已存在 L2，更新其内容
                target.content = source.overview or source.content
                target.importance = max(target.importance, source.importance)
                target.updated_at = datetime.now(tz=timezone.utc)
                await self.db.flush()
                logger.info(f"Promoted L1→L2 (updated existing): {target_uri}")
                return target
            else:
                # 创建新的 L2
                l2 = LayeredMemory(
                    uri=target_uri,
                    parent_uri=source.parent_uri,
                    layer=MemoryLayer.LONG_TERM,
                    context_type=source.context_type,
                    name=source.name,
                    content=source.overview or source.content,
                    abstract=source.abstract,
                    overview=source.overview,
                    tags=source.tags,
                    source=source.source,
                    importance=source.importance,
                    session_id=source.session_id,
                    user_id=source.user_id,
                    agent_id=source.agent_id,
                )
                self.db.add(l2)
                await self.db.flush()
                logger.info(f"Promoted L1→L2 (new): {target_uri}")
                return l2

        return None

    # ==================== 清理 ====================

    async def evict(self, session_id: str) -> int:
        """清理指定会话的 L0 工作记忆"""
        stmt = delete(LayeredMemory).where(
            and_(
                LayeredMemory.session_id == session_id,
                LayeredMemory.layer == MemoryLayer.WORKING,
            )
        )
        result = await self.db.execute(stmt)
        await self.db.flush()
        count = result.rowcount
        logger.info(f"Evicted {count} L0 memories for session {session_id}")
        return count

    async def delete_memory(self, uri: str) -> bool:
        """删除记忆（含 L0/L1/L2 全部层级）"""
        # 删除 L0 和 L1 子记录
        l0_uri = f"{uri}/.level_0"
        l1_uri = f"{uri}/.level_1"

        for target_uri in [l0_uri, l1_uri, uri]:
            stmt = delete(LayeredMemory).where(LayeredMemory.uri == target_uri)
            await self.db.execute(stmt)

        await self.db.flush()
        logger.info(f"Deleted memory tree: {uri}")
        return True

    # ==================== L0/L1 生成 ====================

    async def _generate_l0(self, content: str) -> str:
        """生成 L0 摘要（一句话概括）"""
        if self.llm_caller:
            try:
                prompt = L0_GENERATE_PROMPT.format(content=content[:2000])
                abstract = await self.llm_caller(prompt)
                return abstract.strip()[:200]
            except Exception as e:
                logger.warning(f"LLM L0 generation failed, using truncation: {e}")

        # 降级：截取前 100 字符
        return content[:100].strip() + ("..." if len(content) > 100 else "")

    async def _generate_l1(self, content: str) -> str:
        """生成 L1 概述（段落总结）"""
        if self.llm_caller:
            try:
                prompt = L1_GENERATE_PROMPT.format(content=content[:4000])
                overview = await self.llm_caller(prompt)
                return overview.strip()[:500]
            except Exception as e:
                logger.warning(f"LLM L1 generation failed, using truncation: {e}")

        # 降级：截取前 300 字符
        return content[:300].strip() + ("..." if len(content) > 300 else "")
