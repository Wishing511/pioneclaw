"""
GraphRAG API 测试
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.database import get_db
from app.main import app
from app.models import User
from tests.conftest import auth_headers


@pytest_asyncio.fixture
async def graph_client(db_engine):
    """HTTP 测试客户端"""
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    session_maker = async_sessionmaker(
        db_engine, class_=AsyncSession, expire_on_commit=False
    )

    async def override_get_db():
        async with session_maker() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


class TestGraphRAGAPI:
    """测试 GraphRAG API 端点"""

    @pytest.mark.asyncio
    async def test_index_document_success(
        self, graph_client: AsyncClient, test_user: User
    ):
        """测试文档索引成功"""
        with patch("app.api.graph_rag.get_graph_rag_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.index_document = AsyncMock(
                return_value={
                    "success": True,
                    "message": "文档索引成功",
                    "doc_id": "doc-123",
                }
            )
            mock_get_client.return_value = mock_client

            response = await graph_client.post(
                "/api/graph-rag/index",
                json={"content": "test content", "doc_id": "doc-123"},
                headers=auth_headers(test_user.id),
            )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["message"] == "文档索引成功"

    @pytest.mark.asyncio
    async def test_index_document_unauthorized(self, graph_client: AsyncClient):
        """测试未授权索引"""
        response = await graph_client.post(
            "/api/graph-rag/index",
            json={"content": "test content"},
        )
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_index_batch_success(
        self, graph_client: AsyncClient, test_user: User
    ):
        """测试批量索引成功"""
        with patch("app.api.graph_rag.get_graph_rag_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.index_batch = AsyncMock(
                return_value={
                    "success": True,
                    "message": "成功索引 3 个文档",
                    "count": 3,
                }
            )
            mock_get_client.return_value = mock_client

            response = await graph_client.post(
                "/api/graph-rag/index/batch",
                json={"documents": ["doc1", "doc2", "doc3"]},
                headers=auth_headers(test_user.id),
            )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "3" in data["message"]

    @pytest.mark.asyncio
    async def test_query_success(self, graph_client: AsyncClient, test_user: User):
        """测试查询成功"""
        with patch("app.api.graph_rag.get_graph_rag_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.query = AsyncMock(
                return_value={
                    "result": "query result",
                    "mode": "hybrid",
                }
            )
            mock_get_client.return_value = mock_client

            response = await graph_client.post(
                "/api/graph-rag/query",
                json={"query": "test query", "mode": "hybrid"},
                headers=auth_headers(test_user.id),
            )

        assert response.status_code == 200
        data = response.json()
        assert data["result"] == "query result"
        assert data["mode"] == "hybrid"

    @pytest.mark.asyncio
    async def test_query_default_mode(self, graph_client: AsyncClient, test_user: User):
        """测试查询默认模式"""
        with patch("app.api.graph_rag.get_graph_rag_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.query = AsyncMock(
                return_value={
                    "result": "result",
                    "mode": "hybrid",
                }
            )
            mock_get_client.return_value = mock_client

            response = await graph_client.post(
                "/api/graph-rag/query",
                json={"query": "test query"},
                headers=auth_headers(test_user.id),
            )

        assert response.status_code == 200
        # 默认模式应该是 hybrid

    @pytest.mark.asyncio
    async def test_query_unauthorized(self, graph_client: AsyncClient):
        """测试未授权查询"""
        response = await graph_client.post(
            "/api/graph-rag/query",
            json={"query": "test query"},
        )
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_get_stats_success(self, graph_client: AsyncClient, test_user: User):
        """测试获取统计成功"""
        with patch("app.api.graph_rag.get_graph_rag_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.stats = AsyncMock(
                return_value={
                    "working_dir": "/data/graph_rag",
                    "graph_exists": True,
                    "vector_exists": True,
                    "nodes": 10,
                    "edges": 15,
                }
            )
            mock_get_client.return_value = mock_client

            response = await graph_client.get(
                "/api/graph-rag/stats",
                headers=auth_headers(test_user.id),
            )

        assert response.status_code == 200
        data = response.json()
        assert data["working_dir"] == "/data/graph_rag"
        assert data["graph_exists"] is True
        assert data["nodes"] == 10

    @pytest.mark.asyncio
    async def test_get_stats_unauthorized(self, graph_client: AsyncClient):
        """测试未授权获取统计"""
        response = await graph_client.get("/api/graph-rag/stats")
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_clear_success(self, graph_client: AsyncClient, test_user: User):
        """测试清空图谱成功"""
        with patch("app.api.graph_rag.get_graph_rag_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.clear = AsyncMock(
                return_value={
                    "success": True,
                    "message": "知识图谱已清空",
                }
            )
            mock_get_client.return_value = mock_client

            response = await graph_client.delete(
                "/api/graph-rag/clear",
                headers=auth_headers(test_user.id),
            )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "清空" in data["message"]

    @pytest.mark.asyncio
    async def test_clear_unauthorized(self, graph_client: AsyncClient):
        """测试未授权清空"""
        response = await graph_client.delete("/api/graph-rag/clear")
        assert response.status_code == 401


class TestGraphRAGAPIErrors:
    """测试 GraphRAG API 错误处理"""

    @pytest.mark.asyncio
    async def test_index_document_failure(
        self, graph_client: AsyncClient, test_user: User
    ):
        """测试索引失败"""
        with patch("app.api.graph_rag.get_graph_rag_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.index_document = AsyncMock(
                return_value={
                    "success": False,
                    "message": "索引失败",
                    "doc_id": "auto",
                }
            )
            mock_get_client.return_value = mock_client

            response = await graph_client.post(
                "/api/graph-rag/index",
                json={"content": "test content"},
                headers=auth_headers(test_user.id),
            )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is False

    @pytest.mark.asyncio
    async def test_query_failure(self, graph_client: AsyncClient, test_user: User):
        """测试查询失败"""
        with patch("app.api.graph_rag.get_graph_rag_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.query = AsyncMock(
                return_value={
                    "result": "查询失败: error",
                    "mode": "hybrid",
                }
            )
            mock_get_client.return_value = mock_client

            response = await graph_client.post(
                "/api/graph-rag/query",
                json={"query": "test query"},
                headers=auth_headers(test_user.id),
            )

        assert response.status_code == 200
        data = response.json()
        assert "失败" in data["result"]

    @pytest.mark.asyncio
    async def test_clear_failure(self, graph_client: AsyncClient, test_user: User):
        """测试清空失败"""
        with patch("app.api.graph_rag.get_graph_rag_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.clear = AsyncMock(
                return_value={
                    "success": False,
                    "message": "清空失败",
                }
            )
            mock_get_client.return_value = mock_client

            response = await graph_client.delete(
                "/api/graph-rag/clear",
                headers=auth_headers(test_user.id),
            )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is False
