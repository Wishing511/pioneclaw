"""
Chat, Workflow, Memories, Logs, Providers API 测试
"""

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.database import get_db
from app.main import app
from app.models import User
from tests.conftest import auth_headers


@pytest_asyncio.fixture
async def api_client(db_engine):
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


class TestChatAPI:
    """测试聊天 API"""

    @pytest.mark.asyncio
    async def test_chat_unauthorized(self, api_client: AsyncClient):
        """测试未授权访问"""
        response = await api_client.post(
            "/api/chat/completions",
            json={"message": "Hello"},
        )
        assert response.status_code == 401


class TestWorkflowAPI:
    """测试工作流 API"""

    @pytest.mark.asyncio
    async def test_list_workflows_unauthorized(self, api_client: AsyncClient):
        """测试未授权访问"""
        response = await api_client.post("/api/workflow/pipeline")
        assert response.status_code == 401


class TestLogsAPI:
    """测试日志 API"""

    @pytest.mark.asyncio
    async def test_list_logs_success(self, api_client: AsyncClient, test_user: User):
        """测试获取日志列表"""
        response = await api_client.get(
            "/api/logs",
            headers=auth_headers(test_user.id),
        )
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_list_logs_unauthorized(self, api_client: AsyncClient):
        """测试未授权访问"""
        response = await api_client.get("/api/logs")
        assert response.status_code == 401


class TestProvidersAPI:
    """测试 Provider API"""

    @pytest.mark.asyncio
    async def test_list_providers_success(
        self, api_client: AsyncClient, test_user: User
    ):
        """测试获取 Provider 列表"""
        response = await api_client.get(
            "/api/providers",
            headers=auth_headers(test_user.id),
        )
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_list_providers_unauthorized(self, api_client: AsyncClient):
        """测试未授权访问"""
        response = await api_client.get("/api/providers")
        assert response.status_code == 401
