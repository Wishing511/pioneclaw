"""
Wiki 合并测试

测试 Wiki 语义搜索、分块、索引功能
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.database import get_db
from app.main import app
from app.models import User, Wiki
from tests.conftest import auth_headers


@pytest_asyncio.fixture
async def wiki_client(db_engine):
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


@pytest_asyncio.fixture
async def test_wiki(db_session, test_admin: User) -> Wiki:
    """创建测试 Wiki（使用管理员用户）"""
    wiki = Wiki(
        title="测试文档",
        content="# 测试文档\n\n这是测试内容，用于测试语义搜索和分块功能。",
        path="/test/doc1",
        tags=["test", "文档"],
        created_by=test_admin.id,
        doc_type="markdown",
    )
    db_session.add(wiki)
    await db_session.commit()
    await db_session.refresh(wiki)
    return wiki


@pytest_asyncio.fixture
async def admin_wiki_client(db_engine):
    """HTTP 测试客户端（管理员权限）"""
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


class TestWikiSemanticSearch:
    """测试 Wiki 语义搜索"""

    @pytest.mark.asyncio
    async def test_semantic_search_success(
        self, wiki_client: AsyncClient, test_user: User, test_wiki: Wiki
    ):
        """测试语义搜索成功"""
        with patch("app.modules.agent.vector_store.VectorStore") as mock_store_class:
            mock_store = MagicMock()
            mock_store.search = AsyncMock(
                return_value=[
                    {"source_id": test_wiki.id, "content": "测试内容", "score": 0.9}
                ]
            )
            mock_store_class.return_value = mock_store

            response = await wiki_client.post(
                "/api/wiki/search/semantic",
                json={"query": "测试", "top_k": 10},
                headers=auth_headers(test_user.id),
            )

        assert response.status_code == 200
        data = response.json()
        assert "items" in data
        assert "total" in data

    @pytest.mark.asyncio
    async def test_semantic_search_fallback(
        self, wiki_client: AsyncClient, test_user: User, test_wiki: Wiki
    ):
        """测试语义搜索降级到关键词搜索"""
        with patch(
            "app.modules.agent.vector_store.VectorStore",
            side_effect=Exception("Vector store error"),
        ):
            response = await wiki_client.post(
                "/api/wiki/search/semantic",
                json={"query": "测试", "top_k": 10},
                headers=auth_headers(test_user.id),
            )

        assert response.status_code == 200
        data = response.json()
        assert "items" in data

    @pytest.mark.asyncio
    async def test_semantic_search_unauthorized(self, wiki_client: AsyncClient):
        """测试未授权语义搜索"""
        response = await wiki_client.post(
            "/api/wiki/search/semantic",
            json={"query": "测试"},
        )
        assert response.status_code == 401


class TestWikiChunking:
    """测试 Wiki 分块"""

    @pytest.mark.asyncio
    async def test_chunk_wiki_success(
        self, admin_wiki_client: AsyncClient, test_admin: User, test_wiki: Wiki
    ):
        """测试分块成功"""
        response = await admin_wiki_client.post(
            f"/api/wiki/{test_wiki.id}/chunks",
            json={"chunk_size": 100, "chunk_overlap": 20},
            headers=auth_headers(test_admin.id),
        )

        assert response.status_code == 200
        data = response.json()
        assert data["wiki_id"] == test_wiki.id
        assert "chunk_count" in data
        assert "chunks" in data
        assert isinstance(data["chunks"], list)

    @pytest.mark.asyncio
    async def test_chunk_wiki_not_found(
        self, admin_wiki_client: AsyncClient, test_admin: User
    ):
        """测试分块不存在的 Wiki"""
        response = await admin_wiki_client.post(
            "/api/wiki/nonexistent/chunks",
            json={"chunk_size": 500},
            headers=auth_headers(test_admin.id),
        )

        assert response.status_code == 404


class TestWikiIndexing:
    """测试 Wiki 索引"""

    @pytest.mark.asyncio
    async def test_index_to_vector_store(
        self, admin_wiki_client: AsyncClient, test_admin: User, test_wiki: Wiki
    ):
        """测试索引到向量库"""
        # 这个测试需要实际的向量存储，我们跳过 mock
        # 因为 VectorStore 的初始化逻辑复杂
        # 实际测试在集成测试中完成
        pytest.skip("需要实际向量存储支持")

    @pytest.mark.asyncio
    async def test_remove_from_index(
        self, admin_wiki_client: AsyncClient, test_admin: User, test_wiki: Wiki
    ):
        """测试从向量库移除"""
        pytest.skip("需要实际向量存储支持")


class TestWikiGraphIndexing:
    """测试 Wiki 知识图谱索引"""

    @pytest.mark.asyncio
    async def test_index_to_graph(
        self, admin_wiki_client: AsyncClient, test_admin: User, test_wiki: Wiki
    ):
        """测试索引到知识图谱"""
        with patch("app.modules.graph_rag.GraphRAGClient") as mock_client_class:
            mock_client = MagicMock()
            mock_client.index_document = AsyncMock(
                return_value={
                    "success": True,
                    "message": "索引成功",
                    "doc_id": test_wiki.id,
                }
            )
            mock_client_class.return_value = mock_client

            response = await admin_wiki_client.post(
                f"/api/wiki/{test_wiki.id}/graph",
                headers=auth_headers(test_admin.id),
            )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True

    @pytest.mark.asyncio
    async def test_index_to_graph_failure(
        self, admin_wiki_client: AsyncClient, test_admin: User, test_wiki: Wiki
    ):
        """测试图谱索引失败"""
        with patch("app.modules.graph_rag.GraphRAGClient") as mock_client_class:
            mock_client = MagicMock()
            mock_client.index_document = AsyncMock(side_effect=Exception("Graph error"))
            mock_client_class.return_value = mock_client

            response = await admin_wiki_client.post(
                f"/api/wiki/{test_wiki.id}/graph",
                headers=auth_headers(test_admin.id),
            )

        assert response.status_code == 500


class TestKnowledgeAPIDeprecated:
    """测试 Knowledge API 废弃"""

    @pytest.mark.asyncio
    async def test_knowledge_api_returns_410(
        self, admin_wiki_client: AsyncClient, test_admin: User
    ):
        """测试 Knowledge API 返回 410 Gone"""
        response = await admin_wiki_client.get(
            "/api/knowledge-bases/",
            headers=auth_headers(test_admin.id),
        )

        assert response.status_code == 410

    @pytest.mark.asyncio
    async def test_knowledge_api_post_returns_410(
        self, admin_wiki_client: AsyncClient, test_admin: User
    ):
        """测试 POST 请求返回 410"""
        response = await admin_wiki_client.post(
            "/api/knowledge-bases/",
            json={"name": "test", "description": "test"},
            headers=auth_headers(test_admin.id),
        )

        assert response.status_code == 410

    @pytest.mark.asyncio
    async def test_knowledge_api_includes_migration_info(
        self, admin_wiki_client: AsyncClient, test_admin: User
    ):
        """测试返回迁移信息"""
        response = await admin_wiki_client.get(
            "/api/knowledge-bases/",
            headers=auth_headers(test_admin.id),
        )

        assert response.status_code == 410
        data = response.json()
        assert "detail" in data
        assert "migration" in data["detail"]


class TestWikiModelEnhancements:
    """测试 Wiki 模型增强字段"""

    @pytest.mark.asyncio
    async def test_wiki_has_new_fields(self, db_session, test_user: User):
        """测试 Wiki 模型新字段"""
        wiki = Wiki(
            title="测试文档",
            content="测试内容",
            path="/test/new-fields",
            created_by=test_user.id,
            doc_type="markdown",
            source="https://example.com/doc",
            chunk_count=5,
            is_indexed=True,
        )
        db_session.add(wiki)
        await db_session.commit()
        await db_session.refresh(wiki)

        assert wiki.doc_type == "markdown"
        assert wiki.source == "https://example.com/doc"
        assert wiki.chunk_count == 5
        assert wiki.is_indexed is True

    @pytest.mark.asyncio
    async def test_wiki_default_values(self, db_session, test_user: User):
        """测试 Wiki 默认值"""
        wiki = Wiki(
            title="默认值测试",
            content="内容",
            path="/test/defaults",
            created_by=test_user.id,
        )
        db_session.add(wiki)
        await db_session.commit()
        await db_session.refresh(wiki)

        assert wiki.doc_type == "markdown"
        assert wiki.chunk_count == 0
        assert wiki.is_indexed is False
