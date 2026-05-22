"""
P2: Cron 执行日志持久化 + 启动恢复测试

测试：
- CronScheduler.run_job_now() 手动触发
- _make_cron_callback 回调工厂
- reconcile_cron_jobs 启动恢复
- CronExecutionLog API 端点 (GET /executions, GET /executions/latest, POST /run)
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.cron_scheduler import (
    CronScheduler,
    _make_cron_callback,
    reconcile_cron_jobs,
)

# ============================================================
# CronScheduler.run_job_now 测试
# ============================================================


class TestCronRunJobNow:
    """测试手动触发任务执行"""

    @pytest.fixture
    def scheduler(self):
        sched = CronScheduler()
        sched._jobs.clear()
        return sched

    @pytest.mark.asyncio
    async def test_run_job_now_nonexistent(self, scheduler):
        result = await scheduler.run_job_now("nonexistent")
        assert result["success"] is False
        assert "不存在" in result["error"]

    @pytest.mark.asyncio
    async def test_run_job_now_success(self, scheduler):
        call_tracker = []

        def callback():
            call_tracker.append("executed")
            return "OK"

        scheduler.add_job("test_job", "0 9 * * *", callback)
        result = await scheduler.run_job_now("test_job")

        assert len(call_tracker) == 1
        assert result["success"] is True
        assert result["status"] == "completed"

        # 验证计数器
        job = scheduler.get_job("test_job")
        assert job["run_count"] == 1
        assert job["last_run"] is not None

    @pytest.mark.asyncio
    async def test_run_job_now_callback_raises(self, scheduler):
        def failing_callback():
            raise ValueError("test error")

        scheduler.add_job("failing_job", "0 9 * * *", failing_callback)
        result = await scheduler.run_job_now("failing_job")

        assert result["success"] is False
        assert result["status"] == "failed"

    @pytest.mark.asyncio
    async def test_run_job_now_async_callback(self, scheduler):
        results = []

        async def async_callback():
            results.append("async_done")
            return "async_result"

        scheduler.add_job("async_job", "0 9 * * *", async_callback)
        result = await scheduler.run_job_now("async_job")

        assert len(results) == 1
        assert result["success"] is True


# ============================================================
# _make_cron_callback 测试
# ============================================================


class TestMakeCronCallback:
    """测试回调工厂函数"""

    def test_make_callback_returns_callable(self):
        callback = _make_cron_callback("test_job", {"prompt": "hello"})
        assert callable(callback)

    def test_make_callback_no_prompt(self):
        callback = _make_cron_callback("test_job", {})
        assert callable(callback)

    def test_make_callback_with_input_data(self):
        callback = _make_cron_callback(
            "test_job", {"input_data": {"message": "run this"}}
        )
        assert callable(callback)


# ============================================================
# reconcile_cron_jobs 测试
# ============================================================


class TestReconcileCronJobs:
    """测试启动恢复"""

    @pytest.mark.asyncio
    async def test_reconcile_empty_db(self):
        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session_maker = MagicMock(return_value=mock_session)

        with patch("app.core.database.async_session_maker", mock_session_maker):
            result = await reconcile_cron_jobs()
            assert result["registered"] == 0
            assert result["skipped"] == 0

    @pytest.mark.asyncio
    async def test_reconcile_db_error(self):
        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)
        mock_session.execute = AsyncMock(side_effect=Exception("DB connection failed"))
        mock_session_maker = MagicMock(return_value=mock_session)

        with patch("app.core.database.async_session_maker", mock_session_maker):
            result = await reconcile_cron_jobs()
            assert result["registered"] == 0

    @pytest.mark.asyncio
    async def test_reconcile_registers_enabled_jobs(self):
        from app.models.models import CronJob

        mock_job = MagicMock(spec=CronJob)
        mock_job.name = "recovered_job"
        mock_job.schedule_value = "0 9 * * *"
        mock_job.config = {"prompt": "daily report"}

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [mock_job]
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session_maker = MagicMock(return_value=mock_session)

        with patch("app.core.database.async_session_maker", mock_session_maker):
            result = await reconcile_cron_jobs()
            assert result["registered"] == 1

        # 验证任务已注册到调度器
        from app.core.cron_scheduler import get_cron_scheduler

        scheduler = get_cron_scheduler()
        job = scheduler.get_job("recovered_job")
        assert job is not None
        assert job["enabled"] is True

        # 清理
        scheduler.remove_job("recovered_job")

    @pytest.mark.asyncio
    async def test_reconcile_skips_already_registered(self):
        from app.core.cron_scheduler import get_cron_scheduler
        from app.models.models import CronJob

        scheduler = get_cron_scheduler()
        scheduler.add_job("existing_job", "*/5 * * * *", lambda: None, enabled=True)

        mock_job = MagicMock(spec=CronJob)
        mock_job.name = "existing_job"
        mock_job.schedule_value = "0 9 * * *"
        mock_job.config = {}

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [mock_job]
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session_maker = MagicMock(return_value=mock_session)

        with patch("app.core.database.async_session_maker", mock_session_maker):
            result = await reconcile_cron_jobs()
            assert result["skipped"] == 1

        # 清理
        scheduler.remove_job("existing_job")


# ============================================================
# CronExecutionLog HTTP API 测试
# ============================================================


class TestCronExecutionAPI:
    """测试执行日志 API 端点"""

    @pytest.mark.asyncio
    async def test_list_executions_empty(self, client, test_user):
        """在没有 job 时列出执行历史"""
        headers = {"Authorization": f"Bearer {_make_token(test_user.id)}"}
        response = await client.get("/api/cron/99999/executions", headers=headers)
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) == 0

    @pytest.mark.asyncio
    async def test_list_executions_with_job(self, client, test_user, db_session):
        """创建 job 后查询执行历史"""
        from app.models import CronExecutionLog
        from app.models.models import CronJob

        # 创建 CronJob
        db_job = CronJob(
            name="api_test_job",
            display_name="API Test Job",
            schedule_type="cron",
            schedule_value="0 9 * * *",
            is_enabled=True,
            config={"prompt": "test"},
        )
        db_session.add(db_job)
        await db_session.commit()
        await db_session.refresh(db_job)

        # 创建执行日志
        log = CronExecutionLog(
            cron_job_id=db_job.id,
            started_at=datetime.now(timezone.utc),
            finished_at=datetime.now(timezone.utc),
            status="completed",
            result='{"ok": true}',
            duration_ms=150,
        )
        db_session.add(log)
        await db_session.commit()

        headers = {"Authorization": f"Bearer {_make_token(test_user.id)}"}
        response = await client.get(
            f"/api/cron/{db_job.id}/executions", headers=headers
        )
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) >= 1
        assert data[0]["status"] == "completed"

    @pytest.mark.asyncio
    async def test_get_latest_execution(self, client, test_user, db_session):
        """获取最近一次执行结果"""
        from app.models import CronExecutionLog
        from app.models.models import CronJob

        db_job = CronJob(
            name="latest_test_job",
            display_name="Latest Test",
            schedule_type="cron",
            schedule_value="*/5 * * * *",
            is_enabled=True,
            config={},
        )
        db_session.add(db_job)
        await db_session.commit()
        await db_session.refresh(db_job)

        log = CronExecutionLog(
            cron_job_id=db_job.id,
            started_at=datetime.now(timezone.utc),
            finished_at=datetime.now(timezone.utc),
            status="failed",
            error_message="Something went wrong",
            duration_ms=500,
        )
        db_session.add(log)
        await db_session.commit()

        headers = {"Authorization": f"Bearer {_make_token(test_user.id)}"}
        response = await client.get(
            f"/api/cron/{db_job.id}/executions/latest", headers=headers
        )
        assert response.status_code == 200
        data = response.json()
        assert data is not None
        assert data["status"] == "failed"
        assert data["error_message"] == "Something went wrong"

    @pytest.mark.asyncio
    async def test_get_latest_execution_none(self, client, test_user, db_session):
        """在没有执行历史时获取最近执行"""
        from app.models.models import CronJob

        db_job = CronJob(
            name="no_exec_job",
            display_name="No Exec",
            schedule_type="cron",
            schedule_value="0 9 * * *",
            is_enabled=True,
            config={},
        )
        db_session.add(db_job)
        await db_session.commit()
        await db_session.refresh(db_job)

        headers = {"Authorization": f"Bearer {_make_token(test_user.id)}"}
        response = await client.get(
            f"/api/cron/{db_job.id}/executions/latest", headers=headers
        )
        assert response.status_code == 200
        assert response.json() is None

    @pytest.mark.asyncio
    async def test_run_job_now_not_found(self, client, test_user):
        """手动运行不存在的任务"""
        headers = {"Authorization": f"Bearer {_make_token(test_user.id)}"}
        response = await client.post("/api/cron/99999/run", headers=headers)
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_toggle_cron_job(self, client, test_user, db_session):
        """测试启用/禁用定时任务"""
        from app.models.models import CronJob

        db_job = CronJob(
            name="toggle_test_job",
            display_name="Toggle Test",
            schedule_type="cron",
            schedule_value="0 9 * * *",
            is_enabled=True,
            config={},
        )
        db_session.add(db_job)
        await db_session.commit()
        await db_session.refresh(db_job)

        headers = {"Authorization": f"Bearer {_make_token(test_user.id)}"}

        # 禁用
        response = await client.post(f"/api/cron/{db_job.id}/toggle", headers=headers)
        assert response.status_code == 200
        data = response.json()
        assert data["is_active"] is False

        # 重新启用
        response = await client.post(f"/api/cron/{db_job.id}/toggle", headers=headers)
        assert response.status_code == 200
        data = response.json()
        assert data["is_active"] is True


def _make_token(user_id: int) -> str:
    from app.core.security import create_access_token

    return create_access_token(data={"sub": str(user_id)})
