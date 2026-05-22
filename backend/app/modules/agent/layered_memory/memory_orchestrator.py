"""
MemoryOrchestrator — 分层记忆门面

组合 TierManager, RetrievalEngine, RerankModule, IntentAnalyzer
暴露统一的 store/recall/get/update/delete/list/stats 接口
"""

from datetime import datetime, timezone

from loguru import logger
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.layered_memory import LayeredMemory, MemoryLayer
from app.modules.agent.layered_memory.intent_analyzer import (
    IntentAnalyzer,
)
from app.modules.agent.layered_memory.rerank_module import RerankConfig, RerankModule
from app.modules.agent.layered_memory.retrieval_engine import (
    RetrievalEngine,
)
from app.modules.agent.layered_memory.tier_manager import TierManager


class MemoryOrchestrator:
    """分层记忆编排器（门面类）"""

    def __init__(
        self,
        db_session: AsyncSession,
        vector_store=None,
        llm_caller=None,
    ):
        """
        Args:
            db_session: SQLAlchemy 异步会话
            vector_store: VectorStore 实例
            llm_caller: LLM 调用函数 async (prompt: str) -> str
        """
        self.db = db_session
        self.vector_store = vector_store
        self.llm_caller = llm_caller

        # 初始化子模块
        self.tier_manager = TierManager(db_session, llm_caller)
        self.retrieval_engine = RetrievalEngine(db_session, vector_store)
        self.rerank_module = RerankModule(RerankConfig())
        self.intent_analyzer = IntentAnalyzer(llm_caller)

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
        generate_vector: bool = True,
    ) -> LayeredMemory:
        """存储记忆（含 L0/L1 自动生成 + 可选向量索引）"""
        # 存储到 DB（TierManager 自动生成 L0/L1）
        l2 = await self.tier_manager.store(
            content=content,
            name=name,
            user_id=user_id,
            context_type=context_type,
            uri=uri,
            parent_uri=parent_uri,
            tags=tags,
            source=source,
            importance=importance,
            session_id=session_id,
            agent_id=agent_id,
        )

        # 生成向量索引
        if generate_vector and self.vector_store:
            try:
                # L2 向量
                vector_id = self.vector_store.add(
                    content=l2.content,
                    metadata={
                        "uri": l2.uri,
                        "name": name,
                        "layer": 2,
                        "user_id": user_id,
                    },
                    source_type="memory",
                    source_id=str(l2.id),
                )
                l2.vector_id = vector_id

                # L0 向量（摘要更短，语义更集中）
                l0_uri = f"{l2.uri}/.level_0"
                stmt = select(LayeredMemory).where(LayeredMemory.uri == l0_uri)
                result = await self.db.execute(stmt)
                l0 = result.scalar_one_or_none()
                if l0 and l0.abstract:
                    l0_vector_id = self.vector_store.add(
                        content=l0.abstract,
                        metadata={
                            "uri": l0_uri,
                            "name": name,
                            "layer": 0,
                            "user_id": user_id,
                        },
                        source_type="memory",
                        source_id=str(l0.id),
                    )
                    l0.vector_id = l0_vector_id

                # L1 向量
                l1_uri = f"{l2.uri}/.level_1"
                stmt = select(LayeredMemory).where(LayeredMemory.uri == l1_uri)
                result = await self.db.execute(stmt)
                l1 = result.scalar_one_or_none()
                if l1 and l1.overview:
                    l1_vector_id = self.vector_store.add(
                        content=l1.overview,
                        metadata={
                            "uri": l1_uri,
                            "name": name,
                            "layer": 1,
                            "user_id": user_id,
                        },
                        source_type="memory",
                        source_id=str(l1.id),
                    )
                    l1.vector_id = l1_vector_id

                await self.db.flush()
            except Exception as e:
                logger.warning(f"Vector generation failed (non-fatal): {e}")

        return l2

    # ==================== 检索 ====================

    async def recall(
        self,
        query: str,
        context_type: str = "all",
        layers: list[int] | None = None,
        top_k: int = 10,
        user_id: int | None = None,
        agent_id: int | None = None,
        session_id: str | None = None,
        context: str | None = None,
    ) -> dict:
        """
        语义检索（完整流水线：意图分析 → 检索 → 重排序）

        Returns:
            {"results": [...], "intent": IntentResult, "total": int}
        """
        # 1. 意图分析
        intent = await self.intent_analyzer.analyze(query, context)

        # 用优化后的查询
        search_query = intent.optimized_query or query
        search_type = (
            intent.context_type if intent.context_type != "all" else context_type
        )

        # 2. 检索
        results = await self.retrieval_engine.retrieve(
            query=search_query,
            layers=layers or [2, 1],
            top_k=top_k,
            user_id=user_id,
            agent_id=agent_id,
            session_id=session_id,
            context_type=search_type if search_type != "all" else None,
        )

        # 3. 重排序
        results = self.rerank_module.rerank(
            results,
            query_context_type=search_type if search_type != "all" else None,
        )

        # 增加访问计数
        for r in results[:top_k]:
            stmt = select(LayeredMemory).where(LayeredMemory.uri == r.uri)
            db_result = await self.db.execute(stmt)
            mem = db_result.scalar_one_or_none()
            if mem:
                mem.access_count += 1
        await self.db.flush()

        return {
            "results": results[:top_k],
            "intent": intent,
            "total": len(results),
        }

    # ==================== 获取/更新/删除 ====================

    async def get(self, uri: str, layer: int | None = None) -> LayeredMemory | None:
        """获取指定记忆"""
        return await self.tier_manager.get(uri, layer)

    async def get_with_context(self, uri: str) -> LayeredMemory | None:
        """获取 L2 并填充 L0/L1"""
        return await self.tier_manager.get_with_context(uri)

    async def update(
        self,
        uri: str,
        content: str | None = None,
        name: str | None = None,
        tags: list | None = None,
        importance: int | None = None,
        is_active: bool | None = None,
        regenerate_tiers: bool = True,
    ) -> LayeredMemory | None:
        """更新记忆"""
        # 获取 L2
        stmt = select(LayeredMemory).where(
            and_(LayeredMemory.uri == uri, LayeredMemory.layer == MemoryLayer.LONG_TERM)
        )
        result = await self.db.execute(stmt)
        l2 = result.scalar_one_or_none()
        if not l2:
            return None

        # 更新字段
        if name is not None:
            l2.name = name
        if tags is not None:
            l2.tags = tags
        if importance is not None:
            l2.importance = importance
        if is_active is not None:
            l2.is_active = is_active

        # 内容更新需要重新生成 L0/L1
        if content is not None:
            l2 = await self.tier_manager.update_content(uri, content)
        else:
            l2.updated_at = datetime.now(tz=timezone.utc)
            await self.db.flush()

        return l2

    async def delete(self, uri: str) -> bool:
        """删除记忆（含 L0/L1/L2 + 向量）"""
        # 删除关联向量
        if self.vector_store:
            try:
                stmt = select(LayeredMemory).where(
                    or_(LayeredMemory.uri == uri, LayeredMemory.parent_uri == uri)
                )
                result = await self.db.execute(stmt)
                for mem in result.scalars().all():
                    if mem.vector_id:
                        self.vector_store.delete(mem.vector_id)
            except Exception as e:
                logger.warning(f"Vector deletion failed (non-fatal): {e}")

        return await self.tier_manager.delete_memory(uri)

    # ==================== 提升/清理 ====================

    async def promote(self, uri: str) -> LayeredMemory | None:
        """L1→L2 提升"""
        return await self.tier_manager.promote(uri, from_layer=1, to_layer=2)

    async def evict(self, session_id: str) -> int:
        """清理指定会话的 L0"""
        return await self.tier_manager.evict(session_id)

    # ==================== 列表/统计 ====================

    async def list_memories(
        self,
        user_id: int | None = None,
        layer: int | None = None,
        context_type: str | None = None,
        session_id: str | None = None,
        keyword: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> dict:
        """列表查询（分页）"""
        conditions = [LayeredMemory.is_active]
        # 只查 L2，避免重复
        conditions.append(LayeredMemory.layer == MemoryLayer.LONG_TERM)

        if user_id:
            conditions.append(LayeredMemory.user_id == user_id)
        if layer is not None:
            # 查特定层级（取消 L2 限制）
            conditions[-1] = LayeredMemory.layer == layer
        if context_type and context_type != "all":
            conditions.append(LayeredMemory.context_type == context_type)
        if session_id:
            conditions.append(LayeredMemory.session_id == session_id)
        if keyword:
            conditions.append(
                LayeredMemory.name.contains(keyword)
                | LayeredMemory.content.contains(keyword)
            )

        # 总数
        count_stmt = (
            select(func.count()).select_from(LayeredMemory).where(and_(*conditions))
        )
        total_result = await self.db.execute(count_stmt)
        total = total_result.scalar()

        # 分页
        stmt = (
            select(LayeredMemory)
            .where(and_(*conditions))
            .order_by(LayeredMemory.updated_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        result = await self.db.execute(stmt)
        items = list(result.scalars().all())

        return {"items": items, "total": total}

    async def stats(self, user_id: int | None = None) -> dict:
        """统计信息"""
        base_conditions = [LayeredMemory.is_active]
        if user_id:
            base_conditions.append(LayeredMemory.user_id == user_id)

        # 总数
        total_stmt = (
            select(func.count())
            .select_from(LayeredMemory)
            .where(and_(*base_conditions))
        )
        total_result = await self.db.execute(total_stmt)
        total = total_result.scalar()

        # 按层级统计
        l0_count = await self._count_by_layer(0, base_conditions)
        l1_count = await self._count_by_layer(1, base_conditions)
        l2_count = await self._count_by_layer(2, base_conditions)

        # 按类型统计
        by_type = {}
        for ct in ["memory", "resource", "skill"]:
            conditions = base_conditions + [LayeredMemory.context_type == ct]
            stmt = (
                select(func.count()).select_from(LayeredMemory).where(and_(*conditions))
            )
            result = await self.db.execute(stmt)
            by_type[ct] = result.scalar()

        # 按来源统计
        by_source = {}
        source_stmt = (
            select(LayeredMemory.source, func.count())
            .where(and_(*base_conditions))
            .group_by(LayeredMemory.source)
        )
        source_result = await self.db.execute(source_stmt)
        for source, count in source_result.all():
            by_source[source or "unknown"] = count

        # 向量数
        vector_count = 0
        if self.vector_store:
            try:
                vs_stats = self.vector_store.stats()
                vector_count = vs_stats.get("total", 0)
            except Exception:
                pass

        return {
            "total": total,
            "l0_count": l0_count,
            "l1_count": l1_count,
            "l2_count": l2_count,
            "by_type": by_type,
            "by_source": by_source,
            "vector_count": vector_count,
        }

    async def _count_by_layer(self, layer: int, base_conditions: list) -> int:
        """按层级统计"""
        conditions = base_conditions + [LayeredMemory.layer == layer]
        stmt = select(func.count()).select_from(LayeredMemory).where(and_(*conditions))
        result = await self.db.execute(stmt)
        return result.scalar()
