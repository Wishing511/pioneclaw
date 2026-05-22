"""
Dashboard API 测试
"""

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.database import get_db
from app.main import app
from app.models import User
from tests.conftest import auth_headers


@pytest_asyncio.fixture
async def dashboard_client(db_engine):
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


class TestDashboardAPI:
    """测试仪表盘 API"""

    @pytest.mark.asyncio
    async def test_get_stats_success(
        self, dashboard_client: AsyncClient, test_user: User
    ):
        """测试获取统计信息"""
        response = await dashboard_client.get(
            "/api/dashboard/stats",
            headers=auth_headers(test_user.id),
        )
        assert response.status_code == 200
        data = response.json()
        assert "total_calls" in data
        assert "total_tokens" in data

    @pytest.mark.asyncio
    async def test_get_stats_unauthorized(self, dashboard_client: AsyncClient):
        """测试未授权访问"""
        response = await dashboard_client.get("/api/dashboard/stats")
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_get_counts_success(
        self, dashboard_client: AsyncClient, test_user: User
    ):
        """测试获取计数"""
        response = await dashboard_client.get(
            "/api/dashboard/counts",
            headers=auth_headers(test_user.id),
        )
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_get_counts_unauthorized(self, dashboard_client: AsyncClient):
        """测试未授权访问"""
        response = await dashboard_client.get("/api/dashboard/counts")
        assert response.status_code == 401
