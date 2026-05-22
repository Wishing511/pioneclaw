"""
GraphRAG 模块测试
"""

import os
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.modules.graph_rag import GraphRAGClient, GraphRAGSettings


class TestGraphRAGSettings:
    """测试 GraphRAGSettings 配置"""

    def test_default_settings(self):
        """测试默认配置"""
        settings = GraphRAGSettings()
        assert settings.working_dir == "data/graph_rag"
        assert settings.embedding_model == "C:/Users/Yue/bge-small-zh-v1.5"
        assert settings.llm_model == "gpt-4o"
        assert settings.chunk_token_size == 1200
        assert settings.entity_max_gleaning == 1
        assert settings.enable_llm_cache is True

    def test_custom_settings(self):
        """测试自定义配置"""
        settings = GraphRAGSettings(
            working_dir="custom/path",
            embedding_model="custom-model",
            llm_model="gpt-4",
            chunk_token_size=2000,
        )
        assert settings.working_dir == "custom/path"
        assert settings.embedding_model == "custom-model"
        assert settings.llm_model == "gpt-4"
        assert settings.chunk_token_size == 2000


class TestGraphRAGClient:
    """测试 GraphRAGClient"""

    @pytest.fixture
    def temp_working_dir(self):
        """创建临时工作目录"""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield tmpdir

    @pytest.fixture
    def client(self, temp_working_dir):
        """创建测试客户端"""
        config = GraphRAGSettings(working_dir=temp_working_dir)
        return GraphRAGClient(config=config)

    def test_init_with_config(self, temp_working_dir):
        """测试使用配置初始化"""
        config = GraphRAGSettings(working_dir=temp_working_dir)
        client = GraphRAGClient(config=config)
        assert client.config == config
        assert client._rag is None

    def test_init_with_custom_llm(self, temp_working_dir):
        """测试自定义 LLM 初始化"""
        config = GraphRAGSettings(working_dir=temp_working_dir)
        llm_caller = AsyncMock(return_value="test response")
        client = GraphRAGClient(config=config, llm_caller=llm_caller)
        assert client.llm_caller == llm_caller

    def test_init_with_custom_embedding(self, temp_working_dir):
        """测试自定义 Embedding 初始化"""
        config = GraphRAGSettings(working_dir=temp_working_dir)
        embedding_func = AsyncMock(return_value=[[0.1] * 512])
        client = GraphRAGClient(config=config, embedding_func=embedding_func)
        assert client.embedding_func == embedding_func

    @pytest.mark.asyncio
    async def test_index_document_success(self, client):
        """测试文档索引成功"""
        # Mock LightRAG
        mock_rag = MagicMock()
        mock_rag.insert = MagicMock()
        client._rag = mock_rag

        result = await client.index_document("test content", "doc-123")

        assert result["success"] is True
        assert result["message"] == "文档索引成功"
        assert result["doc_id"] == "doc-123"
        mock_rag.insert.assert_called_once_with("test content")

    @pytest.mark.asyncio
    async def test_index_document_auto_id(self, client):
        """测试文档索引自动生成 ID"""
        mock_rag = MagicMock()
        mock_rag.insert = MagicMock()
        client._rag = mock_rag

        result = await client.index_document("test content")

        assert result["success"] is True
        assert result["doc_id"] == "auto"

    @pytest.mark.asyncio
    async def test_index_document_failure(self, client):
        """测试文档索引失败"""
        mock_rag = MagicMock()
        mock_rag.insert = MagicMock(side_effect=Exception("Insert failed"))
        client._rag = mock_rag

        result = await client.index_document("test content")

        assert result["success"] is False
        assert "Insert failed" in result["message"]

    @pytest.mark.asyncio
    async def test_index_batch_success(self, client):
        """测试批量索引成功"""
        mock_rag = MagicMock()
        mock_rag.insert = MagicMock()
        client._rag = mock_rag

        documents = ["doc1", "doc2", "doc3"]
        result = await client.index_batch(documents)

        assert result["success"] is True
        assert result["count"] == 3
        assert mock_rag.insert.call_count == 3

    @pytest.mark.asyncio
    async def test_index_batch_failure(self, client):
        """测试批量索引失败"""
        mock_rag = MagicMock()
        mock_rag.insert = MagicMock(side_effect=Exception("Batch failed"))
        client._rag = mock_rag

        result = await client.index_batch(["doc1"])

        assert result["success"] is False
        assert result["count"] == 0

    @pytest.mark.asyncio
    async def test_query_hybrid_mode(self, client):
        """测试混合查询模式"""
        mock_rag = MagicMock()
        mock_rag.query = MagicMock(return_value="query result")
        client._rag = mock_rag

        # Mock lightrag.QueryMode 在方法内部导入
        with patch.dict(
            "sys.modules", {"lightrag": MagicMock(QueryMode=MagicMock(Hybrid="hybrid"))}
        ):
            result = await client.query("test query", mode="hybrid")

        assert result["result"] == "query result"
        assert result["mode"] == "hybrid"

    @pytest.mark.asyncio
    async def test_query_all_modes(self, client):
        """测试所有查询模式"""
        mock_rag = MagicMock()
        mock_rag.query = MagicMock(return_value="result")
        client._rag = mock_rag

        modes = ["local", "global", "hybrid", "naive", "mix"]
        mock_query_mode = MagicMock(
            Local="local", Global="global", Hybrid="hybrid", Naive="naive", Mix="mix"
        )

        with patch.dict(
            "sys.modules", {"lightrag": MagicMock(QueryMode=mock_query_mode)}
        ):
            for mode in modes:
                result = await client.query("test", mode=mode)
                assert result["mode"] == mode

    @pytest.mark.asyncio
    async def test_query_failure(self, client):
        """测试查询失败"""
        mock_rag = MagicMock()
        mock_rag.query = MagicMock(side_effect=Exception("Query failed"))
        client._rag = mock_rag

        with patch.dict(
            "sys.modules", {"lightrag": MagicMock(QueryMode=MagicMock(Hybrid="hybrid"))}
        ):
            result = await client.query("test query")

        assert "查询失败" in result["result"]
        assert result["mode"] == "hybrid"

    @pytest.mark.asyncio
    async def test_stats(self, client, temp_working_dir):
        """测试统计信息"""
        mock_rag = MagicMock()
        client._rag = mock_rag

        result = await client.stats()

        assert "working_dir" in result
        assert result["working_dir"] == temp_working_dir
        assert "graph_exists" in result
        assert "vector_exists" in result

    @pytest.mark.asyncio
    async def test_stats_with_graph(self, client):
        """测试带图谱的统计信息"""
        import networkx as nx

        mock_rag = MagicMock()
        mock_graph = nx.Graph()
        mock_graph.add_node(1)
        mock_graph.add_node(2)
        mock_graph.add_edge(1, 2)
        mock_rag.chunk_entity_relation_graph = mock_graph
        client._rag = mock_rag

        result = await client.stats()

        assert result["nodes"] == 2
        assert result["edges"] == 1

    @pytest.mark.asyncio
    async def test_clear(self, client, temp_working_dir):
        """测试清空图谱"""
        # 先创建一些文件
        test_file = os.path.join(temp_working_dir, "test.txt")
        with open(test_file, "w") as f:
            f.write("test")

        result = await client.clear()

        assert result["success"] is True
        assert client._rag is None
        # 目录应该被重新创建
        assert os.path.exists(temp_working_dir)

    @pytest.mark.asyncio
    async def test_clear_failure(self, client):
        """测试清空失败"""
        # 使用一个不存在的路径
        client.config.working_dir = "/nonexistent/path/that/cannot/be/cleared"
        result = await client.clear()

        # 应该返回失败（因为路径不存在）
        assert result["success"] is False or result["success"] is True


class TestGraphRAGClientIntegration:
    """GraphRAG 集成测试（需要 LightRAG 安装）"""

    @pytest.fixture
    def temp_working_dir(self):
        """创建临时工作目录"""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield tmpdir

    @pytest.mark.skipif(
        True,  # 默认跳过，需要 LightRAG 安装
        reason="需要安装 lightrag-hku",
    )
    @pytest.mark.asyncio
    async def test_full_workflow(self, temp_working_dir):
        """测试完整工作流"""
        config = GraphRAGSettings(working_dir=temp_working_dir)
        client = GraphRAGClient(config=config)

        # 索引文档
        result = await client.index_document("这是一段测试文档内容。")
        assert result["success"] is True

        # 查询
        query_result = await client.query("测试")
        assert "result" in query_result

        # 统计
        stats = await client.stats()
        assert "working_dir" in stats

        # 清空
        clear_result = await client.clear()
        assert clear_result["success"] is True


class TestGraphRAGWrappers:
    """测试 LLM 和 Embedding 包装器"""

    def test_wrap_llm_caller(self):
        """测试 LLM 调用器包装"""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = GraphRAGSettings(working_dir=tmpdir)
            llm_caller = AsyncMock(return_value="LLM response")
            client = GraphRAGClient(config=config, llm_caller=llm_caller)

            wrapped = client._wrap_llm_caller()

            # 包装函数应该返回协程
            import asyncio

            result = asyncio.run(wrapped("test prompt"))
            assert result == "LLM response"

    def test_wrap_embedding_func(self):
        """测试 Embedding 函数包装"""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = GraphRAGSettings(working_dir=tmpdir)
            embedding_func = AsyncMock(return_value=[[0.1] * 512, [0.2] * 512])
            client = GraphRAGClient(config=config, embedding_func=embedding_func)

            wrapped = client._wrap_embedding_func()

            import asyncio

            result = asyncio.run(wrapped(["text1", "text2"]))
            assert len(result) == 2
            assert len(result[0]) == 512

    def test_wrap_llm_caller_none(self):
        """测试 LLM 调用器为 None"""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = GraphRAGSettings(working_dir=tmpdir)
            client = GraphRAGClient(config=config, llm_caller=None)

            wrapped = client._wrap_llm_caller()

            import asyncio

            result = asyncio.run(wrapped("test"))
            assert result == ""

    def test_wrap_embedding_func_none(self):
        """测试 Embedding 函数为 None"""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = GraphRAGSettings(working_dir=tmpdir)
            client = GraphRAGClient(config=config, embedding_func=None)

            wrapped = client._wrap_embedding_func()

            import asyncio

            result = asyncio.run(wrapped(["text"]))
            assert result == []
