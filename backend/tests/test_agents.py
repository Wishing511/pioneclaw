"""
Agent API 测试
"""

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.database import get_db
from app.main import app
from app.models import User
from tests.conftest import auth_headers


@pytest_asyncio.fixture
async def agent_client(db_engine):
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


class TestAgentAPI:
    """测试智能体 API"""

    @pytest.mark.asyncio
    async def test_list_agents_success(
        self, agent_client: AsyncClient, test_user: User
    ):
        """测试获取智能体列表"""
        response = await agent_client.get(
            "/api/agents",
            headers=auth_headers(test_user.id),
        )
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_list_agents_unauthorized(self, agent_client: AsyncClient):
        """测试未授权访问"""
        response = await agent_client.get("/api/agents")
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_create_agent_success(
        self, agent_client: AsyncClient, test_admin: User
    ):
        """测试创建智能体成功"""
        response = await agent_client.post(
            "/api/agents",
            json={
                "name": "新智能体",
                "display_name": "新智能体",
                "description": "测试创建",
                "system_prompt": "你是一个助手",
                "model": "gpt-4",
            },
            headers=auth_headers(test_admin.id),
        )
        assert response.status_code in [200, 201]

    @pytest.mark.asyncio
    async def test_get_agent_not_found(
        self, agent_client: AsyncClient, test_user: User
    ):
        """测试智能体不存在"""
        response = await agent_client.get(
            "/api/agents/99999",
            headers=auth_headers(test_user.id),
        )
        assert response.status_code == 404
