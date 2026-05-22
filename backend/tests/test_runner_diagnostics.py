"""
Runner Diagnostics & Logging API 测试

TDD: 测试先于实现。首次运行应全部 FAIL（端点尚未实现）。
"""

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ConnectionEvent, Runner, RunnerStatus, User
from tests.conftest import auth_headers

# ============================
# 辅助函数
# ============================


async def create_runner(db_session: AsyncSession, name: str, **kwargs) -> Runner:
    """在测试数据库中创建一个 Runner"""
    runner = Runner(
        name=name,
        display_name=kwargs.pop("display_name", name),
        status=kwargs.pop("status", RunnerStatus.ONLINE),
        **kwargs,
    )
    db_session.add(runner)
    await db_session.commit()
    await db_session.refresh(runner)
    return runner


# ============================
# 1. GET /{runner_id}/diagnostics
# ============================


class TestGetDiagnostics:
    """测试获取 Runner 诊断信息"""

    @pytest.mark.asyncio
    async def test_get_diagnostics_returns_data(
        self, client: AsyncClient, db_session: AsyncSession, test_admin: User
    ):
        """GET /runners/{id}/diagnostics 返回 CPU/memory/disk/processes"""
        runner = await create_runner(
            db_session,
            "diag-with-data",
            diagnostics={
                "cpu_percent": 45.2,
                "memory_percent": 72.8,
                "disk_percent": 60.0,
                "processes": [{"name": "python", "cpu": 12.5, "memory": 256}],
            },
        )

        response = await client.get(
            f"/api/runners/{runner.id}/diagnostics",
            headers=auth_headers(test_admin.id),
        )

        assert response.status_code == 200
        data = response.json()
        assert data["cpu_percent"] == 45.2
        assert data["memory_percent"] == 72.8
        assert data["disk_percent"] == 60.0
        assert len(data["processes"]) == 1
        assert data["processes"][0]["name"] == "python"
        assert "updated_at" in data

    @pytest.mark.asyncio
    async def test_get_diagnostics_no_data(
        self, client: AsyncClient, db_session: AsyncSession, test_admin: User
    ):
        """Runner diagnostics 为 null 时返回默认值（零值）"""
        runner = await create_runner(db_session, "diag-no-data", diagnostics=None)

        response = await client.get(
            f"/api/runners/{runner.id}/diagnostics",
            headers=auth_headers(test_admin.id),
        )

        assert response.status_code == 200
        data = response.json()
        assert data["cpu_percent"] == 0
        assert data["memory_percent"] == 0
        assert data["disk_percent"] == 0
        assert data["processes"] == []

    @pytest.mark.asyncio
    async def test_get_diagnostics_runner_not_found(
        self, client: AsyncClient, test_admin: User
    ):
        """获取不存在的 Runner 诊断信息应返回 404"""
        response = await client.get(
            "/api/runners/99999/diagnostics",
            headers=auth_headers(test_admin.id),
        )

        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_get_diagnostics_unauthorized(self, client: AsyncClient):
        """未认证获取诊断信息应返回 401"""
        response = await client.get("/api/runners/1/diagnostics")
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_get_diagnostics_not_admin(
        self, client: AsyncClient, db_session: AsyncSession, test_user: User
    ):
        """普通用户获取诊断信息应返回 403"""
        runner = await create_runner(db_session, "diag-not-admin")

        response = await client.get(
            f"/api/runners/{runner.id}/diagnostics",
            headers=auth_headers(test_user.id),
        )

        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_get_diagnostics_org_admin_allowed(
        self, client: AsyncClient, db_session: AsyncSession, test_org_admin: User
    ):
        """组织管理员获取诊断信息应返回 200"""
        runner = await create_runner(
            db_session,
            "diag-org-admin",
            diagnostics={
                "cpu_percent": 30.0,
                "memory_percent": 50.0,
                "disk_percent": 40.0,
                "processes": [],
            },
        )

        response = await client.get(
            f"/api/runners/{runner.id}/diagnostics",
            headers=auth_headers(test_org_admin.id),
        )

        assert response.status_code == 200


# ============================
# 2. GET /{runner_id}/local-logs
# ============================


class TestGetLocalLogs:
    """测试获取 Runner 本地日志"""

    @pytest.mark.asyncio
    async def test_get_local_logs_with_filters(
        self, client: AsyncClient, db_session: AsyncSession, test_admin: User
    ):
        """GET /runners/{id}/local-logs?category=agent&limit=50 返回过滤后的日志"""
        runner = await create_runner(db_session, "logs-with-filter")

        response = await client.get(
            f"/api/runners/{runner.id}/local-logs",
            params={"category": "agent", "limit": 50},
            headers=auth_headers(test_admin.id),
        )

        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        # 所有返回的日志 category 都应为 agent
        for entry in data:
            assert entry["category"] == "agent"

    @pytest.mark.asyncio
    async def test_get_local_logs_empty(
        self, client: AsyncClient, db_session: AsyncSession, test_admin: User
    ):
        """没有日志时返回空列表"""
        runner = await create_runner(db_session, "logs-empty")

        response = await client.get(
            f"/api/runners/{runner.id}/local-logs",
            params={"category": "nonexistent_category"},
            headers=auth_headers(test_admin.id),
        )

        assert response.status_code == 200
        data = response.json()
        assert data == []

    @pytest.mark.asyncio
    async def test_get_local_logs_runner_not_found(
        self, client: AsyncClient, test_admin: User
    ):
        """获取不存在 Runner 的日志应返回 404"""
        response = await client.get(
            "/api/runners/99999/local-logs",
            headers=auth_headers(test_admin.id),
        )

        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_get_local_logs_unauthorized(self, client: AsyncClient):
        """未认证获取日志应返回 401"""
        response = await client.get("/api/runners/1/local-logs")
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_get_local_logs_not_admin(
        self, client: AsyncClient, db_session: AsyncSession, test_user: User
    ):
        """普通用户获取日志应返回 403"""
        runner = await create_runner(db_session, "logs-not-admin")

        response = await client.get(
            f"/api/runners/{runner.id}/local-logs",
            headers=auth_headers(test_user.id),
        )

        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_get_local_logs_default_limit(
        self, client: AsyncClient, db_session: AsyncSession, test_admin: User
    ):
        """不带 limit 参数时返回默认数量（最多 100 条模拟数据）"""
        runner = await create_runner(db_session, "logs-default-limit")

        response = await client.get(
            f"/api/runners/{runner.id}/local-logs",
            headers=auth_headers(test_admin.id),
        )

        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        # 模拟数据总共只有 5 条，所以应返回 5 条
        assert len(data) <= 100


# ============================
# 3. GET /{runner_id}/local-logs/categories
# ============================


class TestGetLogCategories:
    """测试获取日志分类列表"""

    @pytest.mark.asyncio
    async def test_get_log_categories(
        self, client: AsyncClient, db_session: AsyncSession, test_admin: User
    ):
        """GET /runners/{id}/local-logs/categories 返回分类列表"""
        runner = await create_runner(db_session, "log-cats")

        response = await client.get(
            f"/api/runners/{runner.id}/local-logs/categories",
            headers=auth_headers(test_admin.id),
        )

        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert "agent" in data
        assert "task" in data
        assert "system" in data
        assert "error" in data

    @pytest.mark.asyncio
    async def test_get_log_categories_runner_not_found(
        self, client: AsyncClient, test_admin: User
    ):
        """获取不存在 Runner 的日志分类应返回 404"""
        response = await client.get(
            "/api/runners/99999/local-logs/categories",
            headers=auth_headers(test_admin.id),
        )

        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_get_log_categories_unauthorized(self, client: AsyncClient):
        """未认证获取分类应返回 401"""
        response = await client.get("/api/runners/1/local-logs/categories")
        assert response.status_code == 401


# ============================
# 4. GET /{runner_id}/connection-events
# ============================


class TestGetConnectionEvents:
    """测试获取 Runner 连接事件历史"""

    @pytest.mark.asyncio
    async def test_get_connection_events(
        self, client: AsyncClient, db_session: AsyncSession, test_admin: User
    ):
        """GET /runners/{id}/connection-events 返回事件列表"""
        runner = await create_runner(db_session, "conn-events")

        # 创建一些连接事件
        event1 = ConnectionEvent(
            runner_id=runner.id, event_type="online", detail="测试上线"
        )
        event2 = ConnectionEvent(
            runner_id=runner.id, event_type="offline", detail="测试离线"
        )
        db_session.add_all([event1, event2])
        await db_session.commit()

        response = await client.get(
            f"/api/runners/{runner.id}/connection-events",
            headers=auth_headers(test_admin.id),
        )

        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) == 2
        # 按创建时间倒序，最新的在前
        assert data[0]["event_type"] == "offline"
        assert data[1]["event_type"] == "online"

    @pytest.mark.asyncio
    async def test_get_connection_events_empty(
        self, client: AsyncClient, db_session: AsyncSession, test_admin: User
    ):
        """没有连接事件时返回空列表"""
        runner = await create_runner(db_session, "conn-events-empty")

        response = await client.get(
            f"/api/runners/{runner.id}/connection-events",
            headers=auth_headers(test_admin.id),
        )

        assert response.status_code == 200
        data = response.json()
        assert data == []

    @pytest.mark.asyncio
    async def test_get_connection_events_runner_not_found(
        self, client: AsyncClient, test_admin: User
    ):
        """获取不存在 Runner 的连接事件应返回 404"""
        response = await client.get(
            "/api/runners/99999/connection-events",
            headers=auth_headers(test_admin.id),
        )

        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_get_connection_events_unauthorized(self, client: AsyncClient):
        """未认证获取连接事件应返回 401"""
        response = await client.get("/api/runners/1/connection-events")
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_get_connection_events_not_admin(
        self, client: AsyncClient, db_session: AsyncSession, test_user: User
    ):
        """普通用户获取连接事件应返回 403"""
        runner = await create_runner(db_session, "conn-events-not-admin")

        response = await client.get(
            f"/api/runners/{runner.id}/connection-events",
            headers=auth_headers(test_user.id),
        )

        assert response.status_code == 403


# ============================
# 5. ConnectionEvent auto-creation on heartbeat/offline
# ============================


class TestConnectionEventAutoCreate:
    """测试心跳/离线时自动创建 ConnectionEvent 记录"""

    @pytest.mark.asyncio
    async def test_connection_event_created_on_heartbeat(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """心跳上报后自动创建 event_type=online 的 ConnectionEvent"""
        runner = await create_runner(
            db_session, "hb-auto-event", status=RunnerStatus.APPROVED
        )

        response = await client.post(
            f"/api/runners/{runner.id}/heartbeat",
            json={"current_task": "task-42"},
        )

        assert response.status_code == 200

        # 验证 ConnectionEvent 已创建
        result = await db_session.execute(
            select(ConnectionEvent)
            .where(
                ConnectionEvent.runner_id == runner.id,
                ConnectionEvent.event_type == "online",
            )
            .order_by(ConnectionEvent.created_at.desc())
            .limit(1)
        )
        event = result.scalar_one_or_none()
        assert event is not None
        assert event.event_type == "online"
        assert event.detail == "心跳上报"

    @pytest.mark.asyncio
    async def test_connection_event_created_on_offline(
        self, client: AsyncClient, db_session: AsyncSession, test_admin: User
    ):
        """设置离线后自动创建 event_type=offline 的 ConnectionEvent"""
        runner = await create_runner(
            db_session, "off-auto-event", status=RunnerStatus.ONLINE
        )

        response = await client.post(
            f"/api/runners/{runner.id}/offline",
            headers=auth_headers(test_admin.id),
        )

        assert response.status_code == 200

        # 验证 ConnectionEvent 已创建
        result = await db_session.execute(
            select(ConnectionEvent)
            .where(
                ConnectionEvent.runner_id == runner.id,
                ConnectionEvent.event_type == "offline",
            )
            .order_by(ConnectionEvent.created_at.desc())
            .limit(1)
        )
        event = result.scalar_one_or_none()
        assert event is not None
        assert event.event_type == "offline"
        assert event.detail == "手动设置离线"

    @pytest.mark.asyncio
    async def test_multiple_heartbeats_create_multiple_events(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """多次心跳应创建多条 ConnectionEvent 记录"""
        runner = await create_runner(
            db_session, "hb-multi-event", status=RunnerStatus.APPROVED
        )

        for i in range(3):
            response = await client.post(
                f"/api/runners/{runner.id}/heartbeat",
                json={"current_task": f"task-{i}"},
            )
            assert response.status_code == 200

        # 验证有 3 条 online 事件
        result = await db_session.execute(
            select(ConnectionEvent).where(
                ConnectionEvent.runner_id == runner.id,
                ConnectionEvent.event_type == "online",
            )
        )
        events = result.scalars().all()
        assert len(events) == 3
