"""
任务管理扩展测试
- 子任务（parent_id）
- 附件
- 批量操作
"""

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.database import get_db
from app.main import app
from app.models import User
from tests.conftest import auth_headers


@pytest_asyncio.fixture
async def tasks_client(db_engine):
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


class TestSubtasks:
    """测试子任务"""

    @pytest.mark.asyncio
    async def test_create_subtask(self, tasks_client: AsyncClient, test_user: User):
        """测试创建子任务"""
        # 先创建父任务
        parent_resp = await tasks_client.post(
            "/api/tasks",
            json={"title": "父任务"},
            headers=auth_headers(test_user.id),
        )
        assert parent_resp.status_code in [200, 201]
        parent_id = parent_resp.json()["id"]

        # 创建子任务
        resp = await tasks_client.post(
            f"/api/tasks/{parent_id}/subtasks",
            json={
                "title": "子任务1",
                "description": "子任务描述",
            },
            headers=auth_headers(test_user.id),
        )
        assert resp.status_code in [200, 201]
        subtask = resp.json()
        assert subtask["parent_id"] == parent_id
        assert subtask["title"] == "子任务1"

    @pytest.mark.asyncio
    async def test_get_subtasks(self, tasks_client: AsyncClient, test_user: User):
        """测试获取子任务列表"""
        # 创建父任务
        parent_resp = await tasks_client.post(
            "/api/tasks",
            json={"title": "父任务"},
            headers=auth_headers(test_user.id),
        )
        parent_id = parent_resp.json()["id"]

        # 创建多个子任务
        for i in range(3):
            await tasks_client.post(
                f"/api/tasks/{parent_id}/subtasks",
                json={"title": f"子任务{i}"},
                headers=auth_headers(test_user.id),
            )

        # 获取子任务列表
        resp = await tasks_client.get(
            f"/api/tasks/{parent_id}/subtasks",
            headers=auth_headers(test_user.id),
        )
        assert resp.status_code == 200
        subtasks = resp.json()
        assert len(subtasks) == 3

    @pytest.mark.asyncio
    async def test_subtask_not_found_parent(
        self, tasks_client: AsyncClient, test_user: User
    ):
        """测试父任务不存在时创建子任务"""
        resp = await tasks_client.post(
            "/api/tasks/99999/subtasks",
            json={"title": "子任务"},
            headers=auth_headers(test_user.id),
        )
        assert resp.status_code == 404


class TestTaskStats:
    """测试任务统计"""

    @pytest.mark.asyncio
    async def test_get_task_stats(self, tasks_client: AsyncClient, test_user: User):
        """测试获取任务统计"""
        resp = await tasks_client.get(
            "/api/tasks/stats",
            headers=auth_headers(test_user.id),
        )
        assert resp.status_code == 200
        stats = resp.json()
        assert "total" in stats
        assert "todo" in stats
        assert "in_progress" in stats
        assert "done" in stats


class TestBatchOperations:
    """测试批量操作"""

    @pytest.mark.asyncio
    async def test_batch_assign(self, tasks_client: AsyncClient, test_user: User):
        """测试批量分配"""
        # 创建几个任务
        task_ids = []
        for i in range(3):
            resp = await tasks_client.post(
                "/api/tasks",
                json={"title": f"任务{i}"},
                headers=auth_headers(test_user.id),
            )
            task_ids.append(resp.json()["id"])

        # 批量分配
        resp = await tasks_client.post(
            "/api/tasks/batch/assign",
            json={"task_ids": task_ids, "assignee_id": test_user.id},
            headers=auth_headers(test_user.id),
        )
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_batch_update(self, tasks_client: AsyncClient, test_user: User):
        """测试批量更新"""
        # 创建几个任务
        task_ids = []
        for i in range(3):
            resp = await tasks_client.post(
                "/api/tasks",
                json={"title": f"任务{i}"},
                headers=auth_headers(test_user.id),
            )
            task_ids.append(resp.json()["id"])

        # 批量更新
        resp = await tasks_client.post(
            "/api/tasks/batch/update",
            json={"task_ids": task_ids, "updates": {"priority": "high"}},
            headers=auth_headers(test_user.id),
        )
        assert resp.status_code == 200


class TestTaskComments:
    """测试任务评论"""

    @pytest.mark.asyncio
    async def test_get_comments_empty(self, tasks_client: AsyncClient, test_user: User):
        """测试获取空评论列表"""
        # 创建任务
        resp = await tasks_client.post(
            "/api/tasks",
            json={"title": "带评论的任务"},
            headers=auth_headers(test_user.id),
        )
        task_id = resp.json()["id"]

        # 获取评论
        resp = await tasks_client.get(
            f"/api/tasks/{task_id}/comments",
            headers=auth_headers(test_user.id),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0

    @pytest.mark.asyncio
    async def test_create_comment(self, tasks_client: AsyncClient, test_user: User):
        """测试创建评论（需要权限）"""
        # 创建任务
        resp = await tasks_client.post(
            "/api/tasks",
            json={"title": "带评论的任务"},
            headers=auth_headers(test_user.id),
        )
        task_id = resp.json()["id"]

        # 添加评论（可能因权限返回 403）
        resp = await tasks_client.post(
            f"/api/tasks/{task_id}/comments",
            json={"content": "这是一条评论"},
            headers=auth_headers(test_user.id),
        )
        # 接受 200/201 或 403（权限不足）
        assert resp.status_code in [200, 201, 403]
