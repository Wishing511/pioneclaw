"""
Tasks API 测试
"""

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.database import get_db
from app.main import app
from app.models import User
from tests.conftest import auth_headers


@pytest_asyncio.fixture
async def task_client(db_engine):
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


class TestTaskAPI:
    """测试任务 API"""

    @pytest.mark.asyncio
    async def test_list_tasks_success(self, task_client: AsyncClient, test_user: User):
        """测试获取任务列表"""
        response = await task_client.get(
            "/api/tasks",
            headers=auth_headers(test_user.id),
        )
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_list_tasks_unauthorized(self, task_client: AsyncClient):
        """测试未授权访问"""
        response = await task_client.get("/api/tasks")
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_create_task_success(self, task_client: AsyncClient, test_user: User):
        """测试创建任务成功"""
        response = await task_client.post(
            "/api/tasks",
            json={
                "title": "新任务",
                "description": "测试创建",
            },
            headers=auth_headers(test_user.id),
        )
        assert response.status_code in [200, 201]

    @pytest.mark.asyncio
    async def test_get_task_not_found(self, task_client: AsyncClient, test_user: User):
        """测试任务不存在"""
        response = await task_client.get(
            "/api/tasks/99999",
            headers=auth_headers(test_user.id),
        )
        assert response.status_code == 404
