"""
RetrievalEngine — 层级检索引擎

职责：
- 语义搜索：通过 VectorStore 语义相似度
- 关键词回退：向量搜索无结果时的 LIKE 搜索
- 层级扩展：BFS 沿 parent_uri 向上下扩展
- 分数传播：子节点分数影响父节点
"""

import math
from dataclasses import dataclass

from loguru import logger
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.layered_memory import LayeredMemory


@dataclass
class RetrievalResult:
    """检索结果"""

    uri: str
    name: str
    layer: int
    context_type: str
    text: str
    score: float
    abstract: str | None = None
    overview: str | None = None
    access_count: int = 0
    source: str | None = None
    updated_at: str | None = None
    parent_uri: str | None = None


class RetrievalEngine:
    """层级检索引擎"""

    def __init__(
        self,
        db_session: AsyncSession,
        vector_store=None,
        alpha: float = 0.7,
        max_iterations: int = 10,
        default_limit: int = 5,
    ):
        """
        Args:
            db_session: SQLAlchemy 异步会话
            vector_store: VectorStore 实例（用于语义搜索）
            alpha: 语义分数权重（1-alpha 为热度权重）
            max_iterations: BFS 层级扩展最大迭代次数
            default_limit: 默认返回数量
        """
        self.db = db_session
        self.vector_store = vector_store
        self.alpha = alpha
        self.max_iterations = max_iterations
        self.default_limit = default_limit

    async def retrieve(
        self,
        query: str,
        layers: list[int] | None = None,
        top_k: int = None,
        user_id: int | None = None,
        agent_id: int | None = None,
        session_id: str | None = None,
        context_type: str | None = None,
        uri_prefix: str | None = None,
    ) -> list[RetrievalResult]:
        """
        主检索方法：
        1. 语义搜索（通过 VectorStore）
        2. 关键词回退（如果语义搜索无结果）
        3. 层级扩展（BFS 沿 parent_uri）
        4. 分数传播
        """
        top_k = top_k or self.default_limit
        layers = layers or [2, 1]

        # 1. 语义搜索
        results = await self._semantic_search(
            query=query,
            layers=layers,
            top_k=top_k * 3,
            user_id=user_id,
            agent_id=agent_id,
            session_id=session_id,
            context_type=context_type,
            uri_prefix=uri_prefix,
        )

        # 2. 关键词回退
        if not results:
            results = await self._keyword_fallback(
                query_text=query,
                layers=layers,
                top_k=top_k * 2,
                user_id=user_id,
                agent_id=agent_id,
                session_id=session_id,
                context_type=context_type,
            )

        # 3. 层级扩展
        if results:
            results = await self._hierarchical_expansion(results)

        # 去重 + 排序 + 截取
        seen_uris = set()
        unique_results = []
        for r in sorted(results, key=lambda x: x.score, reverse=True):
            if r.uri not in seen_uris:
                seen_uris.add(r.uri)
                unique_results.append(r)

        return unique_results[:top_k]

    # ==================== 语义搜索 ====================

    async def _semantic_search(
        self,
        query: str,
        layers: list[int],
        top_k: int,
        user_id: int | None = None,
        agent_id: int | None = None,
        session_id: str | None = None,
        context_type: str | None = None,
        uri_prefix: str | None = None,
    ) -> list[RetrievalResult]:
        """通过 VectorStore 语义搜索，然后匹配 DB 记录"""
        if not self.vector_store:
            return await self._keyword_fallback(
                query, layers, top_k, user_id, agent_id, session_id, context_type
            )

        try:
            # 先在 VectorStore 搜索
            source_type = "memory"
            vs_results = self.vector_store.search(
                query=query,
                top_k=top_k,
                source_type=source_type,
                min_score=0.3,
            )

            if not vs_results:
                return []

            # 用 vector_id 匹配 DB 记录
            vector_ids = [r.id for r in vs_results]
            score_map = {r.id: r.score for r in vs_results}

            stmt = select(LayeredMemory).where(
                and_(
                    LayeredMemory.vector_id.in_(vector_ids),
                    LayeredMemory.is_active,
                    LayeredMemory.layer.in_(layers),
                )
            )
            if user_id:
                stmt = stmt.where(LayeredMemory.user_id == user_id)
            if agent_id:
                stmt = stmt.where(LayeredMemory.agent_id == agent_id)
            if session_id:
                stmt = stmt.where(LayeredMemory.session_id == session_id)
            if context_type and context_type != "all":
                stmt = stmt.where(LayeredMemory.context_type == context_type)
            if uri_prefix:
                stmt = stmt.where(LayeredMemory.uri.startswith(uri_prefix))

            db_result = await self.db.execute(stmt)
            memories = db_result.scalars().all()

            results = []
            for mem in memories:
                semantic_score = score_map.get(mem.vector_id, 0.0)
                # 混合语义分数和热度分数
                hotness = self._calc_hotness(mem.access_count)
                combined_score = (
                    self.alpha * semantic_score + (1 - self.alpha) * hotness
                )

                results.append(
                    RetrievalResult(
                        uri=mem.uri,
                        name=mem.name,
                        layer=mem.layer,
                        context_type=mem.context_type,
                        text=mem.get_text_for_embedding(),
                        score=combined_score,
                        abstract=mem.abstract,
                        overview=mem.overview,
                        access_count=mem.access_count,
                        source=mem.source,
                        updated_at=mem.updated_at.isoformat()
                        if mem.updated_at
                        else None,
                        parent_uri=mem.parent_uri,
                    )
                )

            return results

        except Exception as e:
            logger.warning(f"Semantic search failed: {e}")
            return []

    # ==================== 关键词回退 ====================

    async def _keyword_fallback(
        self,
        query_text: str,
        layers: list[int],
        top_k: int,
        user_id: int | None = None,
        agent_id: int | None = None,
        session_id: str | None = None,
        context_type: str | None = None,
    ) -> list[RetrievalResult]:
        """关键词搜索回退"""
        keywords = query_text.strip().split()

        conditions = [
            LayeredMemory.is_active,
            LayeredMemory.layer.in_(layers),
        ]
        if user_id:
            conditions.append(LayeredMemory.user_id == user_id)
        if agent_id:
            conditions.append(LayeredMemory.agent_id == agent_id)
        if session_id:
            conditions.append(LayeredMemory.session_id == session_id)
        if context_type and context_type != "all":
            conditions.append(LayeredMemory.context_type == context_type)

        # 对每个关键词进行 OR 搜索
        keyword_conditions = []
        for kw in keywords:
            keyword_conditions.append(LayeredMemory.content.contains(kw))
            keyword_conditions.append(LayeredMemory.name.contains(kw))
            keyword_conditions.append(LayeredMemory.abstract.contains(kw))

        conditions.append(or_(*keyword_conditions))

        stmt = select(LayeredMemory).where(and_(*conditions)).limit(top_k)
        result = await self.db.execute(stmt)
        memories = result.scalars().all()

        results = []
        for mem in memories:
            # 关键词匹配数作为粗略分数
            match_count = sum(
                1 for kw in keywords if kw in mem.content or kw in mem.name
            )
            score = match_count / max(len(keywords), 1)

            results.append(
                RetrievalResult(
                    uri=mem.uri,
                    name=mem.name,
                    layer=mem.layer,
                    context_type=mem.context_type,
                    text=mem.get_text_for_embedding(),
                    score=score,
                    abstract=mem.abstract,
                    overview=mem.overview,
                    access_count=mem.access_count,
                    source=mem.source,
                    updated_at=mem.updated_at.isoformat() if mem.updated_at else None,
                    parent_uri=mem.parent_uri,
                )
            )

        return results

    # ==================== 层级扩展 ====================

    async def _hierarchical_expansion(
        self, candidates: list[RetrievalResult]
    ) -> list[RetrievalResult]:
        """BFS 沿 parent_uri 扩展父子节点"""
        expanded = list(candidates)
        visited = {r.uri for r in candidates}

        for candidate in candidates:
            # 向上扩展：获取父节点
            if candidate.parent_uri and candidate.parent_uri not in visited:
                parent = await self._get_memory_by_uri(candidate.parent_uri)
                if parent:
                    propagated_score = candidate.score * 0.5
                    expanded.append(
                        RetrievalResult(
                            uri=parent.uri,
                            name=parent.name,
                            layer=parent.layer,
                            context_type=parent.context_type,
                            text=parent.get_text_for_embedding(),
                            score=propagated_score,
                            abstract=parent.abstract,
                            overview=parent.overview,
                            access_count=parent.access_count,
                            source=parent.source,
                            updated_at=parent.updated_at.isoformat()
                            if parent.updated_at
                            else None,
                            parent_uri=parent.parent_uri,
                        )
                    )
                    visited.add(parent.uri)

            # 向下扩展：获取子节点
            children = await self._get_children(candidate.uri)
            for child in children:
                if child.uri not in visited:
                    propagated_score = (
                        self.alpha * child.access_count * 0.01
                        + (1 - self.alpha) * candidate.score * 0.5
                    )
                    expanded.append(
                        RetrievalResult(
                            uri=child.uri,
                            name=child.name,
                            layer=child.layer,
                            context_type=child.context_type,
                            text=child.get_text_for_embedding(),
                            score=propagated_score,
                            abstract=child.abstract,
                            overview=child.overview,
                            access_count=child.access_count,
                            source=child.source,
                            updated_at=child.updated_at.isoformat()
                            if child.updated_at
                            else None,
                            parent_uri=child.parent_uri,
                        )
                    )
                    visited.add(child.uri)

        return expanded

    # ==================== 辅助方法 ====================

    async def _get_memory_by_uri(self, uri: str) -> LayeredMemory | None:
        """按 URI 获取记忆"""
        stmt = select(LayeredMemory).where(
            and_(LayeredMemory.uri == uri, LayeredMemory.is_active)
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def _get_children(self, parent_uri: str) -> list[LayeredMemory]:
        """获取子节点"""
        stmt = select(LayeredMemory).where(
            and_(
                LayeredMemory.parent_uri == parent_uri,
                LayeredMemory.is_active,
            )
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    def _calc_hotness(access_count: int) -> float:
        """计算热度分数：log(1 + count) / 7.0"""
        return min(1.0, math.log(1 + access_count) / 7.0)
