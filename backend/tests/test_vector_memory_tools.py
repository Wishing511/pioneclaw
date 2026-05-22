"""
Vector Memory Tools 测试: VectorMemoryRecallTool, VectorMemoryStoreTool,
VectorMemoryGetTool, VectorMemoryStatsTool
Track 2 — 分层向量记忆库
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.modules.tools.builtin import (
    VectorMemoryGetTool,
    VectorMemoryRecallTool,
    VectorMemoryStatsTool,
    VectorMemoryStoreTool,
)

# ============================================================
# VectorMemoryRecallTool 测试 (sync vector store, async execute wrapper)
# ============================================================


class TestVectorMemoryRecallTool:
    @pytest.mark.asyncio
    async def test_recall_with_results(self):
        mock_store = MagicMock()
        mock_store.search.return_value = [
            MagicMock(
                id="mem-1",
                content="Python async programming guide",
                source_type="memory",
                score=0.95,
            ),
            MagicMock(
                id="mem-2",
                content="Database optimization tips",
                source_type="knowledge",
                score=0.72,
            ),
        ]
        with patch(
            "app.modules.agent.vector_store.get_vector_store", lambda: mock_store
        ):
            tool = VectorMemoryRecallTool()
            result = json.loads(await tool.execute(query="Python programming", top_k=5))
        assert len(result["results"]) == 2
        assert result["results"][0]["id"] == "mem-1"
        assert result["results"][0]["score"] == 0.95

    @pytest.mark.asyncio
    async def test_recall_empty_results(self):
        mock_store = MagicMock()
        mock_store.search.return_value = []
        with patch(
            "app.modules.agent.vector_store.get_vector_store", lambda: mock_store
        ):
            tool = VectorMemoryRecallTool()
            result = json.loads(await tool.execute(query="zzzznotfound"))
        assert result["results"] == []
        assert result["total"] == 0

    @pytest.mark.asyncio
    async def test_recall_content_truncation(self):
        mock_store = MagicMock()
        long_content = "A" * 1000
        mock_store.search.return_value = [
            MagicMock(
                id="mem-long", content=long_content, source_type="memory", score=0.8
            ),
        ]
        with patch(
            "app.modules.agent.vector_store.get_vector_store", lambda: mock_store
        ):
            tool = VectorMemoryRecallTool()
            result = json.loads(await tool.execute(query="test"))
        assert len(result["results"][0]["content"]) <= 500

    @pytest.mark.asyncio
    async def test_recall_source_type_filter(self):
        mock_store = MagicMock()
        mock_store.search.return_value = []
        with patch(
            "app.modules.agent.vector_store.get_vector_store", lambda: mock_store
        ):
            tool = VectorMemoryRecallTool()
            await tool.execute(query="test", source_type="memory", top_k=10)
        mock_store.search.assert_called_with(
            "test", top_k=10, source_type="memory", min_score=0.3
        )

    @pytest.mark.asyncio
    async def test_recall_all_source_type(self):
        mock_store = MagicMock()
        mock_store.search.return_value = []
        with patch(
            "app.modules.agent.vector_store.get_vector_store", lambda: mock_store
        ):
            tool = VectorMemoryRecallTool()
            await tool.execute(query="test", source_type="all")
        mock_store.search.assert_called_with(
            "test", top_k=5, source_type=None, min_score=0.3
        )

    @pytest.mark.asyncio
    async def test_recall_exception(self):
        mock_store = MagicMock()
        mock_store.search.side_effect = RuntimeError("Vector store unavailable")
        with patch(
            "app.modules.agent.vector_store.get_vector_store", lambda: mock_store
        ):
            tool = VectorMemoryRecallTool()
            result = json.loads(await tool.execute(query="test"))
        assert "error" in result


# ============================================================
# VectorMemoryStoreTool 测试
# ============================================================


def _make_session_mock():
    s = AsyncMock()
    s.commit = AsyncMock()
    s.__aenter__ = AsyncMock(return_value=s)
    s.__aexit__ = AsyncMock(return_value=None)
    return s


class TestVectorMemoryStoreTool:
    def _mock_memory(self, **attrs):
        """Create a mock memory with proper attributes (not MagicMock kwargs)."""
        m = MagicMock()
        for k, v in attrs.items():
            setattr(m, k, v)
        return m

    @pytest.mark.asyncio
    async def test_store_success(self):
        mock_session = _make_session_mock()
        mock_orchestrator = MagicMock()
        mock_memory = self._mock_memory(
            uri="viking://user/1/test_memory",
            name="Test Memory",
            context_type="memory",
            layer=2,
        )
        mock_orchestrator.store = AsyncMock(return_value=mock_memory)

        with (
            patch(
                "app.core.database.async_session_maker",
                MagicMock(return_value=mock_session),
            ),
            patch(
                "app.modules.agent.layered_memory.MemoryOrchestrator",
                lambda **kw: mock_orchestrator,
            ),
            patch(
                "app.modules.agent.vector_store.get_vector_store", lambda: MagicMock()
            ),
        ):
            tool = VectorMemoryStoreTool()
            result = json.loads(
                await tool.execute(
                    content="Memory content for testing",
                    name="Test Memory",
                    context_type="memory",
                    tags="python,test",
                    importance=4,
                )
            )
        assert result["success"] is True
        assert result["uri"] == "viking://user/1/test_memory"
        assert result["name"] == "Test Memory"

    @pytest.mark.asyncio
    async def test_store_with_defaults(self):
        mock_session = _make_session_mock()
        mock_orchestrator = MagicMock()
        mock_memory = self._mock_memory(
            uri="uri://1",
            name="Default Memory",
            context_type="memory",
            layer=2,
        )
        mock_orchestrator.store = AsyncMock(return_value=mock_memory)

        with (
            patch(
                "app.core.database.async_session_maker",
                MagicMock(return_value=mock_session),
            ),
            patch(
                "app.modules.agent.layered_memory.MemoryOrchestrator",
                lambda **kw: mock_orchestrator,
            ),
            patch(
                "app.modules.agent.vector_store.get_vector_store", lambda: MagicMock()
            ),
        ):
            tool = VectorMemoryStoreTool()
            result = json.loads(
                await tool.execute(
                    content="Basic content",
                    name="Default Memory",
                )
            )
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_store_exception(self):
        mock_session = _make_session_mock()
        mock_orchestrator = MagicMock()
        mock_orchestrator.store = AsyncMock(side_effect=RuntimeError("DB error"))

        with (
            patch(
                "app.core.database.async_session_maker",
                MagicMock(return_value=mock_session),
            ),
            patch(
                "app.modules.agent.layered_memory.MemoryOrchestrator",
                lambda **kw: mock_orchestrator,
            ),
            patch(
                "app.modules.agent.vector_store.get_vector_store", lambda: MagicMock()
            ),
        ):
            tool = VectorMemoryStoreTool()
            result = json.loads(
                await tool.execute(
                    content="Should fail",
                    name="Error Test",
                )
            )
        assert result["success"] is False
        assert "error" in result


# ============================================================
# VectorMemoryGetTool 测试
# ============================================================


class TestVectorMemoryGetTool:
    def _mock_memory(self, **attrs):
        m = MagicMock()
        for k, v in attrs.items():
            setattr(m, k, v)
        return m

    @pytest.mark.asyncio
    async def test_get_success(self):
        mock_session = _make_session_mock()
        mock_orchestrator = MagicMock()
        mock_orchestrator.get_with_context = AsyncMock(
            return_value={
                "l0": self._mock_memory(
                    uri="uri://1/.level_0", content="L0 summary", name="Test"
                ),
                "l1": self._mock_memory(
                    uri="uri://1/.level_1", content="L1 overview", name="Test"
                ),
                "l2": self._mock_memory(
                    uri="uri://1/.level_2", content="L2 full content", name="Test"
                ),
            }
        )

        with (
            patch(
                "app.core.database.async_session_maker",
                MagicMock(return_value=mock_session),
            ),
            patch(
                "app.modules.agent.layered_memory.MemoryOrchestrator",
                lambda **kw: mock_orchestrator,
            ),
        ):
            tool = VectorMemoryGetTool()
            result = json.loads(await tool.execute(uri="uri://1"))
        assert result["success"] is True
        assert "l0" in result["memory"]

    @pytest.mark.asyncio
    async def test_get_not_found(self):
        mock_session = _make_session_mock()
        mock_orchestrator = MagicMock()
        mock_orchestrator.get_with_context = AsyncMock(return_value=None)

        with (
            patch(
                "app.core.database.async_session_maker",
                MagicMock(return_value=mock_session),
            ),
            patch(
                "app.modules.agent.layered_memory.MemoryOrchestrator",
                lambda **kw: mock_orchestrator,
            ),
        ):
            tool = VectorMemoryGetTool()
            result = json.loads(await tool.execute(uri="uri://notfound"))
        assert result["success"] is False
        assert "not found" in result["error"].lower()


# ============================================================
# VectorMemoryStatsTool 测试
# ============================================================


class TestVectorMemoryStatsTool:
    @pytest.mark.asyncio
    async def test_stats_success(self):
        mock_session = _make_session_mock()
        mock_orchestrator = MagicMock()
        mock_orchestrator.stats = AsyncMock(
            return_value={
                "total": 10,
                "l0_count": 3,
                "l1_count": 3,
                "l2_count": 4,
                "by_type": {"memory": 6},
                "by_source": {"manual": 8},
                "vector_count": 30,
            }
        )

        with (
            patch(
                "app.core.database.async_session_maker",
                MagicMock(return_value=mock_session),
            ),
            patch(
                "app.modules.agent.layered_memory.MemoryOrchestrator",
                lambda **kw: mock_orchestrator,
            ),
        ):
            tool = VectorMemoryStatsTool()
            result = json.loads(await tool.execute())
        assert result["success"] is True
        assert result["stats"]["total"] == 10
        assert result["stats"]["vector_count"] == 30

    @pytest.mark.asyncio
    async def test_stats_exception(self):
        mock_session = _make_session_mock()
        mock_orchestrator = MagicMock()
        mock_orchestrator.stats = AsyncMock(side_effect=RuntimeError("DB unavailable"))

        with (
            patch(
                "app.core.database.async_session_maker",
                MagicMock(return_value=mock_session),
            ),
            patch(
                "app.modules.agent.layered_memory.MemoryOrchestrator",
                lambda **kw: mock_orchestrator,
            ),
        ):
            tool = VectorMemoryStatsTool()
            result = json.loads(await tool.execute())
        assert result["success"] is False
        assert "error" in result
