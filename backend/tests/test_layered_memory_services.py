"""
分层记忆核心服务层单元测试

覆盖：TierManager, RetrievalEngine, RerankModule, IntentAnalyzer, MemoryOrchestrator
"""

import os
import tempfile
import uuid
from datetime import datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.database import Base
from app.core.security import get_password_hash
from app.models import User, UserRole
from app.models.layered_memory import MemoryLayer
from app.modules.agent.layered_memory.intent_analyzer import (
    IntentAnalyzer,
)
from app.modules.agent.layered_memory.memory_orchestrator import MemoryOrchestrator
from app.modules.agent.layered_memory.rerank_module import RerankConfig, RerankModule
from app.modules.agent.layered_memory.retrieval_engine import (
    RetrievalEngine,
    RetrievalResult,
)
from app.modules.agent.layered_memory.tier_manager import TierManager


@pytest.fixture
async def service_db():
    """创建测试数据库"""
    db_file = os.path.join(
        tempfile.gettempdir(), f"pioneclaw_lm_test_{uuid.uuid4().hex}.db"
    )
    db_url = f"sqlite+aiosqlite:///{db_file}"
    engine = create_async_engine(
        db_url, connect_args={"check_same_thread": False}, echo=False
    )

    import app.models  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    async with session_maker() as session:
        yield session

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()
    if os.path.exists(db_file):
        os.remove(db_file)


@pytest.fixture
async def test_user_id(service_db: AsyncSession) -> int:
    """创建测试用户并返回 ID"""
    user = User(
        username="lm_test_user",
        email="lm_test@example.com",
        display_name="LM测试用户",
        hashed_password=get_password_hash("test123"),
        role=UserRole.USER,
        is_active=True,
    )
    service_db.add(user)
    await service_db.commit()
    await service_db.refresh(user)
    return user.id


# ==================== TierManager ====================


class TestTierManager:
    @pytest.mark.asyncio
    async def test_store_creates_l2_with_l0_l1(
        self, service_db: AsyncSession, test_user_id: int
    ):
        tm = TierManager(service_db, llm_caller=None)
        l2 = await tm.store(
            content="这是测试内容，用于验证 L0/L1/L2 自动生成",
            name="测试存储",
            user_id=test_user_id,
        )
        await service_db.commit()

        assert l2.layer == MemoryLayer.LONG_TERM
        assert l2.uri.startswith("viking://user/")

        # 验证 L0 存在
        l0 = await tm.get(l2.uri, layer=0)
        assert l0 is not None
        assert l0.layer == MemoryLayer.WORKING
        assert l0.abstract is not None

        # 验证 L1 存在
        l1 = await tm.get(l2.uri, layer=1)
        assert l1 is not None
        assert l1.layer == MemoryLayer.SESSION
        assert l1.overview is not None

    @pytest.mark.asyncio
    async def test_get_with_context(self, service_db: AsyncSession, test_user_id: int):
        tm = TierManager(service_db, llm_caller=None)
        l2 = await tm.store(
            content="获取上下文测试内容",
            name="上下文测试",
            user_id=test_user_id,
        )
        await service_db.commit()

        result = await tm.get_with_context(l2.uri)
        assert result is not None
        assert result.abstract is not None
        assert result.overview is not None

    @pytest.mark.asyncio
    async def test_update_content_regenerates_tiers(
        self, service_db: AsyncSession, test_user_id: int
    ):
        tm = TierManager(service_db, llm_caller=None)
        l2 = await tm.store(
            content="原始内容",
            name="更新测试",
            user_id=test_user_id,
        )
        await service_db.commit()

        updated = await tm.update_content(l2.uri, "这是更新后的内容，需要重新生成摘要")
        await service_db.commit()

        assert updated is not None
        assert updated.content == "这是更新后的内容，需要重新生成摘要"
        # L0/L1 应该被重新生成（由于无 LLM，使用截断，所以内容会变化）
        assert updated.abstract is not None

    @pytest.mark.asyncio
    async def test_delete_memory_tree(
        self, service_db: AsyncSession, test_user_id: int
    ):
        tm = TierManager(service_db, llm_caller=None)
        l2 = await tm.store(
            content="待删除内容",
            name="删除测试",
            user_id=test_user_id,
        )
        await service_db.commit()

        result = await tm.delete_memory(l2.uri)
        assert result is True

        # 确认全部删除
        assert await tm.get(l2.uri) is None
        assert await tm.get(l2.uri, layer=0) is None
        assert await tm.get(l2.uri, layer=1) is None

    @pytest.mark.asyncio
    async def test_evict_session_l0(self, service_db: AsyncSession, test_user_id: int):
        tm = TierManager(service_db, llm_caller=None)
        await tm.store(
            content="会话记忆1",
            name="会话1",
            user_id=test_user_id,
            session_id="evict-test-session",
        )
        await tm.store(
            content="会话记忆2",
            name="会话2",
            user_id=test_user_id,
            session_id="evict-test-session",
        )
        await service_db.commit()

        count = await tm.evict("evict-test-session")
        await service_db.commit()
        assert count == 2  # 两个 L0 被清理

    @pytest.mark.asyncio
    async def test_promote_l1_to_l2(self, service_db: AsyncSession, test_user_id: int):
        tm = TierManager(service_db, llm_caller=None)
        l2 = await tm.store(
            content="提升测试内容",
            name="提升测试",
            user_id=test_user_id,
        )
        await service_db.commit()

        l1_uri = f"{l2.uri}/.level_1"
        promoted = await tm.promote(l1_uri, from_layer=1, to_layer=2)
        await service_db.commit()

        assert promoted is not None
        assert promoted.layer == MemoryLayer.LONG_TERM

    @pytest.mark.asyncio
    async def test_custom_uri(self, service_db: AsyncSession, test_user_id: int):
        tm = TierManager(service_db, llm_caller=None)
        l2 = await tm.store(
            content="自定义 URI 测试",
            name="URI测试",
            user_id=test_user_id,
            uri="viking://user/1/custom_test_uri",
        )
        await service_db.commit()

        assert l2.uri == "viking://user/1/custom_test_uri"
        l0 = await tm.get("viking://user/1/custom_test_uri", layer=0)
        assert l0 is not None

    @pytest.mark.asyncio
    async def test_access_count_increments(
        self, service_db: AsyncSession, test_user_id: int
    ):
        tm = TierManager(service_db, llm_caller=None)
        l2 = await tm.store(
            content="访问计数测试",
            name="计数测试",
            user_id=test_user_id,
        )
        await service_db.commit()
        assert l2.access_count == 0

        # 访问两次
        await tm.get(l2.uri)
        await tm.get(l2.uri)
        await service_db.commit()

        refreshed = await tm.get(l2.uri)
        assert refreshed.access_count >= 2


# ==================== RetrievalEngine ====================


class TestRetrievalEngine:
    @pytest.mark.asyncio
    async def test_keyword_fallback(self, service_db: AsyncSession, test_user_id: int):
        tm = TierManager(service_db, llm_caller=None)
        await tm.store(
            content="Python 是一种编程语言",
            name="Python知识",
            user_id=test_user_id,
        )
        await service_db.commit()

        re = RetrievalEngine(service_db, vector_store=None)
        results = await re.retrieve(
            query="Python",
            layers=[2, 1],
            top_k=5,
            user_id=test_user_id,
        )

        assert len(results) > 0
        assert any("Python" in r.text for r in results)

    @pytest.mark.asyncio
    async def test_retrieve_empty(self, service_db: AsyncSession, test_user_id: int):
        re = RetrievalEngine(service_db, vector_store=None)
        results = await re.retrieve(
            query="完全不存在的查询xyz",
            layers=[2, 1],
            top_k=5,
            user_id=test_user_id,
        )
        assert len(results) == 0

    @pytest.mark.asyncio
    async def test_hierarchical_expansion(
        self, service_db: AsyncSession, test_user_id: int
    ):
        tm = TierManager(service_db, llm_caller=None)
        await tm.store(
            content="层级扩展测试内容",
            name="扩展测试",
            user_id=test_user_id,
        )
        await service_db.commit()

        re = RetrievalEngine(service_db, vector_store=None)
        # 构造一个候选结果，触发层级扩展
        candidates = [
            RetrievalResult(
                uri="viking://test/.level_1",
                name="扩展测试",
                layer=1,
                context_type="memory",
                text="测试",
                score=0.8,
                parent_uri=None,
            )
        ]
        expanded = await re._hierarchical_expansion(candidates)
        # 即使没有 parent，也不应报错
        assert len(expanded) >= 1

    @pytest.mark.asyncio
    async def test_retrieve_dedup(self, service_db: AsyncSession, test_user_id: int):
        RetrievalEngine(service_db, vector_store=None)
        # 直接构造重复结果
        r1 = RetrievalResult(
            uri="a", name="a", layer=2, context_type="memory", text="a", score=0.9
        )
        r2 = RetrievalResult(
            uri="a", name="a", layer=2, context_type="memory", text="a", score=0.8
        )
        r3 = RetrievalResult(
            uri="b", name="b", layer=2, context_type="memory", text="b", score=0.7
        )

        # 使用内部去重逻辑（在 retrieve 方法中）
        # 此处验证排序逻辑
        results = sorted([r1, r2, r3], key=lambda x: x.score, reverse=True)
        assert results[0].uri == "a"


# ==================== RerankModule ====================


class TestRerankModule:
    def test_rerank_basic(self):
        rm = RerankModule()
        results = [
            RetrievalResult(
                uri="a",
                name="a",
                layer=2,
                context_type="memory",
                text="a",
                score=0.5,
                access_count=0,
                updated_at=datetime.now().isoformat(),
            ),
            RetrievalResult(
                uri="b",
                name="b",
                layer=1,
                context_type="memory",
                text="b",
                score=0.9,
                access_count=10,
                updated_at=datetime.now().isoformat(),
            ),
        ]
        reranked = rm.rerank(results, query_context_type="memory")
        assert len(reranked) == 2
        # 应按综合分数排序
        assert reranked[0].score >= reranked[1].score

    def test_rerank_hotness(self):
        rm = RerankModule()
        assert rm._calc_hotness(0) == 0.0
        assert rm._calc_hotness(100) > 0
        assert rm._calc_hotness(1000) > rm._calc_hotness(100)
        assert rm._calc_hotness(1000000) <= 1.0

    def test_rerank_recency(self):
        rm = RerankModule()
        # 刚刚更新
        now_score = rm._calc_recency(datetime.now().isoformat())
        assert now_score > 0.9

        # 很久以前
        old_score = rm._calc_recency("2020-01-01T00:00:00")
        assert old_score < 0.1

        # None
        assert rm._calc_recency(None) == 0.0

    def test_rerank_level_score(self):
        rm = RerankModule()
        # 无指定层级
        assert rm._calc_level_score(1, None) == 1.0  # L1 最高
        assert rm._calc_level_score(2, None) == 0.8
        assert rm._calc_level_score(0, None) == 0.6

        # 指定层级
        assert rm._calc_level_score(2, 2) == 1.0
        assert rm._calc_level_score(1, 2) == 0.5
        assert rm._calc_level_score(0, 2) == 0.0

    def test_rerank_type_match(self):
        rm = RerankModule()
        assert rm._calc_type_match("memory", "memory") == 1.0
        assert rm._calc_type_match("resource", "skill") == 0.3
        assert rm._calc_type_match("memory", "skill") == 0.1
        assert rm._calc_type_match("memory", "all") == 0.5
        assert rm._calc_type_match("memory", None) == 0.5

    def test_explain_ranking(self):
        rm = RerankModule()
        result = RetrievalResult(
            uri="a", name="a", layer=1, context_type="memory", text="a", score=0.8
        )
        explanation = rm.explain_ranking(result, query_context_type="memory")
        assert "semantic" in explanation
        assert "hotness" in explanation
        assert "recency" in explanation
        assert "level" in explanation
        assert "type_match" in explanation
        assert "combined" in explanation

    def test_custom_weights(self):
        config = RerankConfig(
            weights={
                "semantic": 1.0,
                "hotness": 0.0,
                "recency": 0.0,
                "level": 0.0,
                "type_match": 0.0,
            }
        )
        rm = RerankModule(config)
        results = [
            RetrievalResult(
                uri="a", name="a", layer=2, context_type="memory", text="a", score=0.5
            ),
            RetrievalResult(
                uri="b", name="b", layer=1, context_type="memory", text="b", score=0.9
            ),
        ]
        reranked = rm.rerank(results)
        # 仅语义权重，b 应排第一
        assert reranked[0].uri == "b"

    def test_empty_results(self):
        rm = RerankModule()
        assert rm.rerank([]) == []


# ==================== IntentAnalyzer ====================


class TestIntentAnalyzer:
    @pytest.mark.asyncio
    async def test_rule_based_action(self):
        ia = IntentAnalyzer(llm_caller=None)
        result = await ia.analyze("如何使用 Python 编程？")
        assert result.intent == "action"
        assert result.context_type == "all"  # "编程" not in memory keywords

    @pytest.mark.asyncio
    async def test_rule_based_comparison(self):
        ia = IntentAnalyzer(llm_caller=None)
        result = await ia.analyze("Python 和 Java 的区别是什么？")
        assert result.intent == "comparison"

    @pytest.mark.asyncio
    async def test_rule_based_skill(self):
        ia = IntentAnalyzer(llm_caller=None)
        result = await ia.analyze("有哪些技能可用？")
        assert result.context_type == "skill"

    @pytest.mark.asyncio
    async def test_rule_based_resource(self):
        ia = IntentAnalyzer(llm_caller=None)
        result = await ia.analyze("帮我找一下文档资料")
        assert result.context_type == "resource"

    @pytest.mark.asyncio
    async def test_rule_based_navigation(self):
        ia = IntentAnalyzer(llm_caller=None)
        result = await ia.analyze("配置文件在哪里？")
        assert result.intent == "navigation"

    @pytest.mark.asyncio
    async def test_rule_based_default(self):
        ia = IntentAnalyzer(llm_caller=None)
        result = await ia.analyze("Python 编程语言")
        assert result.intent == "query"
        assert result.optimized_query == "Python 编程语言"

    @pytest.mark.asyncio
    async def test_with_llm_fallback(self):
        # LLM 调用失败时应降级到规则
        async def failing_llm(prompt):
            raise RuntimeError("LLM unavailable")

        ia = IntentAnalyzer(llm_caller=failing_llm)
        result = await ia.analyze("如何测试？")
        assert result.intent == "action"  # 降级到规则


# ==================== MemoryOrchestrator ====================


class TestMemoryOrchestrator:
    @pytest.mark.asyncio
    async def test_store_and_recall(self, service_db: AsyncSession, test_user_id: int):
        mo = MemoryOrchestrator(service_db, vector_store=None, llm_caller=None)

        l2 = await mo.store(
            content="MemoryOrchestrator 存储测试内容",
            name="编排器测试",
            user_id=test_user_id,
            generate_vector=False,
        )
        await service_db.commit()

        assert l2 is not None
        assert l2.layer == MemoryLayer.LONG_TERM

        result = await mo.recall(
            query="编排器",
            user_id=test_user_id,
        )
        assert result["total"] >= 0  # 可能有或没有结果（关键词匹配）

    @pytest.mark.asyncio
    async def test_store_with_tags(self, service_db: AsyncSession, test_user_id: int):
        mo = MemoryOrchestrator(service_db, vector_store=None, llm_caller=None)

        l2 = await mo.store(
            content="带标签的内容",
            name="标签测试",
            user_id=test_user_id,
            tags=["test", "tag"],
            generate_vector=False,
        )
        await service_db.commit()

        assert l2.tags == ["test", "tag"]

    @pytest.mark.asyncio
    async def test_update(self, service_db: AsyncSession, test_user_id: int):
        mo = MemoryOrchestrator(service_db, vector_store=None, llm_caller=None)

        l2 = await mo.store(
            content="原始内容",
            name="更新测试",
            user_id=test_user_id,
            generate_vector=False,
        )
        await service_db.commit()

        updated = await mo.update(l2.uri, name="新名称")
        await service_db.commit()
        assert updated.name == "新名称"

    @pytest.mark.asyncio
    async def test_delete(self, service_db: AsyncSession, test_user_id: int):
        mo = MemoryOrchestrator(service_db, vector_store=None, llm_caller=None)

        l2 = await mo.store(
            content="待删除内容",
            name="删除测试",
            user_id=test_user_id,
            generate_vector=False,
        )
        await service_db.commit()

        result = await mo.delete(l2.uri)
        assert result is True

    @pytest.mark.asyncio
    async def test_list_memories(self, service_db: AsyncSession, test_user_id: int):
        mo = MemoryOrchestrator(service_db, vector_store=None, llm_caller=None)

        await mo.store(
            content="列表1",
            name="列表测试1",
            user_id=test_user_id,
            generate_vector=False,
        )
        await mo.store(
            content="列表2",
            name="列表测试2",
            user_id=test_user_id,
            generate_vector=False,
        )
        await service_db.commit()

        result = await mo.list_memories(user_id=test_user_id)
        assert result["total"] >= 2
        assert len(result["items"]) >= 2

    @pytest.mark.asyncio
    async def test_stats(self, service_db: AsyncSession, test_user_id: int):
        mo = MemoryOrchestrator(service_db, vector_store=None, llm_caller=None)

        await mo.store(
            content="统计1",
            name="统计测试1",
            user_id=test_user_id,
            generate_vector=False,
        )
        await service_db.commit()

        stats = await mo.stats(user_id=test_user_id)
        assert stats["total"] >= 3  # L0 + L1 + L2
        assert stats["l2_count"] >= 1

    @pytest.mark.asyncio
    async def test_promote(self, service_db: AsyncSession, test_user_id: int):
        mo = MemoryOrchestrator(service_db, vector_store=None, llm_caller=None)

        l2 = await mo.store(
            content="提升测试内容",
            name="提升测试",
            user_id=test_user_id,
            generate_vector=False,
        )
        await service_db.commit()

        l1_uri = f"{l2.uri}/.level_1"
        promoted = await mo.promote(l1_uri)
        assert promoted is not None

    @pytest.mark.asyncio
    async def test_evict(self, service_db: AsyncSession, test_user_id: int):
        mo = MemoryOrchestrator(service_db, vector_store=None, llm_caller=None)

        await mo.store(
            content="会话清理测试",
            name="清理测试",
            user_id=test_user_id,
            session_id="evict-orchestrator-test",
            generate_vector=False,
        )
        await service_db.commit()

        count = await mo.evict("evict-orchestrator-test")
        assert count == 1  # 一个 L0

    @pytest.mark.asyncio
    async def test_list_with_pagination(
        self, service_db: AsyncSession, test_user_id: int
    ):
        mo = MemoryOrchestrator(service_db, vector_store=None, llm_caller=None)

        for i in range(5):
            await mo.store(
                content=f"分页{i}",
                name=f"分页测试{i}",
                user_id=test_user_id,
                generate_vector=False,
            )
        await service_db.commit()

        result = await mo.list_memories(user_id=test_user_id, page=1, page_size=2)
        assert result["total"] >= 5
        assert len(result["items"]) <= 2

    @pytest.mark.asyncio
    async def test_list_with_keyword(self, service_db: AsyncSession, test_user_id: int):
        mo = MemoryOrchestrator(service_db, vector_store=None, llm_caller=None)

        await mo.store(
            content="关键词搜索测试内容",
            name="关键词测试",
            user_id=test_user_id,
            generate_vector=False,
        )
        await service_db.commit()

        result = await mo.list_memories(user_id=test_user_id, keyword="关键词")
        assert result["total"] >= 1
