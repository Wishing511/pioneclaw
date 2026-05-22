"""
认证中间件 + Cron 调度器测试
"""

import asyncio
from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest

from app.core.auth_middleware import (
    PUBLIC_PATHS,
    PUBLIC_PREFIXES,
    AuthMiddleware,
    get_client_type,
)
from app.core.cron_scheduler import CronScheduler

# ============================================================
# AuthMiddleware 测试
# ============================================================


class TestAuthMiddlewareHelpers:
    """测试 AuthMiddleware 辅助方法"""

    def test_public_paths(self):
        """测试公开路径检测"""
        middleware = AuthMiddleware(app=None)
        for path in PUBLIC_PATHS:
            assert middleware._is_public_path(path) is True

    def test_public_prefixes(self):
        """测试公开前缀检测"""
        middleware = AuthMiddleware(app=None)
        for prefix in PUBLIC_PREFIXES:
            assert middleware._is_public_path(prefix) is True
            assert middleware._is_public_path(prefix + "/extra") is True

    def test_non_public_path(self):
        """测试非公开路径"""
        middleware = AuthMiddleware(app=None)
        assert middleware._is_public_path("/api/agents") is False
        assert middleware._is_public_path("/api/tasks") is False

    def test_custom_public_paths(self):
        """测试自定义公开路径"""
        middleware = AuthMiddleware(
            app=None,
            public_paths={"/custom"},
            public_prefixes={"/api/public"},
        )
        assert middleware._is_public_path("/custom") is True
        assert middleware._is_public_path("/api/public/stuff") is True
        assert middleware._is_public_path("/api/agents") is False

    def test_local_bypass_disabled(self):
        """测试禁用本地直通"""
        middleware = AuthMiddleware(app=None, local_bypass=False)
        # 创建模拟请求
        mock_request = MagicMock()
        mock_request.client = MagicMock()
        mock_request.client.host = "127.0.0.1"
        assert middleware._is_local_request(mock_request) is False

    def test_is_local_127(self):
        """测试 127.0.0.1 识别"""
        middleware = AuthMiddleware(app=None, local_bypass=True)
        mock_request = MagicMock()
        mock_request.client = MagicMock()
        mock_request.client.host = "127.0.0.1"
        assert middleware._is_local_request(mock_request) is True

    def test_is_local_localhost(self):
        """测试 localhost 识别"""
        middleware = AuthMiddleware(app=None, local_bypass=True)
        mock_request = MagicMock()
        mock_request.client = MagicMock()
        mock_request.client.host = "localhost"
        assert middleware._is_local_request(mock_request) is True

    def test_is_local_ipv6(self):
        """测试 IPv6 本地识别"""
        middleware = AuthMiddleware(app=None, local_bypass=True)
        mock_request = MagicMock()
        mock_request.client = MagicMock()
        mock_request.client.host = "::1"
        assert middleware._is_local_request(mock_request) is True

    def test_is_remote(self):
        """测试远程请求"""
        middleware = AuthMiddleware(app=None, local_bypass=True)
        mock_request = MagicMock()
        mock_request.client = MagicMock()
        mock_request.client.host = "192.168.1.100"
        assert middleware._is_local_request(mock_request) is False

    def test_is_local_forwarded_for(self):
        """测试 X-Forwarded-For 头"""
        middleware = AuthMiddleware(app=None, local_bypass=True)
        mock_request = MagicMock()
        mock_request.client = MagicMock()
        mock_request.client.host = "10.0.0.1"
        mock_request.headers = {"X-Forwarded-For": "127.0.0.1, 10.0.0.1"}
        assert middleware._is_local_request(mock_request) is True

    def test_extract_token_bearer(self):
        """测试提取 Bearer Token"""
        middleware = AuthMiddleware(app=None)
        mock_request = MagicMock()
        mock_request.headers = {"Authorization": "Bearer my-token-123"}
        token = middleware._extract_token(mock_request)
        assert token == "my-token-123"

    def test_extract_token_missing(self):
        """测试缺少 Token"""
        middleware = AuthMiddleware(app=None)
        mock_request = MagicMock()
        mock_request.headers = {}
        token = middleware._extract_token(mock_request)
        assert token is None

    def test_extract_token_wrong_scheme(self):
        """测试错误的认证方案"""
        middleware = AuthMiddleware(app=None)
        mock_request = MagicMock()
        mock_request.headers = {"Authorization": "Basic abc123"}
        token = middleware._extract_token(mock_request)
        assert token is None


class TestGetClientType:
    """测试客户端类型判断"""

    def test_local_client(self):
        """测试本地客户端"""
        mock_request = MagicMock()
        mock_request.client = MagicMock()
        mock_request.client.host = "127.0.0.1"
        assert get_client_type(mock_request) == "local"

    def test_remote_client(self):
        """测试远程客户端"""
        mock_request = MagicMock()
        mock_request.client = MagicMock()
        mock_request.client.host = "10.0.0.1"
        assert get_client_type(mock_request) == "remote"


# ============================================================
# CronScheduler 测试
# ============================================================


class TestCronSchedulerValidation:
    """测试 cron 表达式验证"""

    def test_valid_expressions(self):
        """测试有效表达式"""
        assert CronScheduler.validate_cron_expr("*/5 * * * *") is True
        assert CronScheduler.validate_cron_expr("0 9 * * *") is True
        assert CronScheduler.validate_cron_expr("30 14 1 * *") is True
        assert CronScheduler.validate_cron_expr("0 0 * * 1") is True
        assert CronScheduler.validate_cron_expr("0 9,12,18 * * *") is True

    def test_invalid_expressions(self):
        """测试无效表达式"""
        assert CronScheduler.validate_cron_expr("") is False
        assert CronScheduler.validate_cron_expr("* * *") is False
        assert CronScheduler.validate_cron_expr("999 * * * *") is False
        assert CronScheduler.validate_cron_expr("abc def ghi jkl mno") is False


class TestCronSchedulerDescribe:
    """测试 cron 表达式描述"""

    def test_every_minute(self):
        """测试每分钟描述"""
        desc = CronScheduler.describe_cron_expr("* * * * *")
        assert "每分钟" in desc

    def test_every_hour(self):
        """测试每小时描述"""
        desc = CronScheduler.describe_cron_expr("30 * * * *")
        assert "每小时" in desc

    def test_daily(self):
        """测试每天描述"""
        desc = CronScheduler.describe_cron_expr("0 9 * * *")
        assert "每天" in desc or "9" in desc

    def test_invalid_expr(self):
        """测试无效表达式描述"""
        desc = CronScheduler.describe_cron_expr("invalid")
        assert "无效" in desc or "错误" in desc


class TestCronSchedulerAddJob:
    """测试添加任务"""

    def test_add_job(self):
        """测试添加任务"""
        scheduler = CronScheduler()
        result = scheduler.add_job("test", "*/5 * * * *", lambda: None)
        assert result is True
        job = scheduler.get_job("test")
        assert job is not None
        assert job["job_id"] == "test"
        assert job["enabled"] is True

    def test_add_job_invalid_expr(self):
        """测试无效表达式添加"""
        scheduler = CronScheduler()
        result = scheduler.add_job("bad", "invalid", lambda: None)
        assert result is False

    def test_add_job_disabled(self):
        """测试添加禁用任务"""
        scheduler = CronScheduler()
        result = scheduler.add_job("disabled", "0 9 * * *", lambda: None, enabled=False)
        assert result is True
        job = scheduler.get_job("disabled")
        assert job["enabled"] is False


class TestCronSchedulerRemoveJob:
    """测试移除任务"""

    def test_remove_existing(self):
        """测试移除存在的任务"""
        scheduler = CronScheduler()
        scheduler.add_job("to_remove", "0 9 * * *", lambda: None)
        result = scheduler.remove_job("to_remove")
        assert result is True
        assert scheduler.get_job("to_remove") is None

    def test_remove_nonexistent(self):
        """测试移除不存在的任务"""
        scheduler = CronScheduler()
        result = scheduler.remove_job("nonexistent")
        assert result is False


class TestCronSchedulerEnableDisable:
    """测试启用/禁用"""

    def test_enable_job(self):
        """测试启用任务"""
        scheduler = CronScheduler()
        scheduler.add_job("test", "0 9 * * *", lambda: None, enabled=False)
        result = scheduler.enable_job("test")
        assert result is True
        job = scheduler.get_job("test")
        assert job["enabled"] is True

    def test_disable_job(self):
        """测试禁用任务"""
        scheduler = CronScheduler()
        scheduler.add_job("test", "0 9 * * *", lambda: None)
        result = scheduler.disable_job("test")
        assert result is True
        job = scheduler.get_job("test")
        assert job["enabled"] is False

    def test_enable_nonexistent(self):
        """测试启用不存在的任务"""
        scheduler = CronScheduler()
        result = scheduler.enable_job("nonexistent")
        assert result is False


class TestCronSchedulerNextRun:
    """测试下次执行时间计算"""

    def test_get_next_run(self):
        """测试计算下次执行时间"""
        scheduler = CronScheduler()
        next_run = scheduler.get_next_run("0 9 * * *")
        assert next_run is not None
        assert isinstance(next_run, datetime)
        assert next_run.hour == 9
        assert next_run.minute == 0

    def test_get_next_run_every_minute(self):
        """测试每分钟的下次执行"""
        scheduler = CronScheduler()
        next_run = scheduler.get_next_run("* * * * *")
        assert next_run is not None

    def test_get_next_run_invalid(self):
        """测试无效表达式的下次执行"""
        scheduler = CronScheduler()
        next_run = scheduler.get_next_run("invalid")
        assert next_run is None


class TestCronSchedulerListJobs:
    """测试列出任务"""

    def test_list_empty(self):
        """测试空列表"""
        scheduler = CronScheduler()
        jobs = scheduler.list_jobs()
        assert jobs == []

    def test_list_multiple(self):
        """测试多任务列表"""
        scheduler = CronScheduler()
        scheduler.add_job("a", "0 9 * * *", lambda: None)
        scheduler.add_job("b", "0 18 * * *", lambda: None)
        jobs = scheduler.list_jobs()
        assert len(jobs) == 2
        ids = [j["job_id"] for j in jobs]
        assert "a" in ids
        assert "b" in ids


class TestCronSchedulerHeartbeat:
    """测试 Heartbeat 集成"""

    def test_ensure_heartbeat_default(self):
        """测试默认 Heartbeat 注册"""
        scheduler = CronScheduler()
        job_id = scheduler.ensure_heartbeat_job()
        assert job_id == "heartbeat_default"
        job = scheduler.get_job(job_id)
        assert job is not None
        assert job["enabled"] is True

    def test_ensure_heartbeat_custom_schedule(self):
        """测试自定义 Heartbeat 调度"""
        scheduler = CronScheduler()
        job_id = scheduler.ensure_heartbeat_job(schedule="0 8,20 * * *")
        job = scheduler.get_job(job_id)
        assert job["cron_expr"] == "0 8,20 * * *"

    def test_ensure_heartbeat_idempotent(self):
        """测试重复调用不重复注册"""
        scheduler = CronScheduler()
        scheduler.ensure_heartbeat_job(schedule="0 9 * * *")
        scheduler.ensure_heartbeat_job(schedule="0 9 * * *")
        jobs = scheduler.list_jobs()
        heartbeat_jobs = [j for j in jobs if j["job_id"] == "heartbeat_default"]
        assert len(heartbeat_jobs) == 1


class TestCronSchedulerStartStop:
    """测试启动/停止"""

    @pytest.mark.asyncio
    async def test_start_stop(self):
        """测试启动和停止"""
        scheduler = CronScheduler()
        await scheduler.start()
        assert scheduler._running is True
        await scheduler.stop()
        assert scheduler._running is False

    @pytest.mark.asyncio
    async def test_double_start(self):
        """测试重复启动"""
        scheduler = CronScheduler()
        await scheduler.start()
        await scheduler.start()  # 不应报错
        assert scheduler._running is True
        await scheduler.stop()

    @pytest.mark.asyncio
    async def test_job_execution(self):
        """测试任务执行"""
        scheduler = CronScheduler()
        call_count = 0

        def callback():
            nonlocal call_count
            call_count += 1

        # 使用 cron 表达式并手动设置 next_run 为过去的时间，确保立即触发
        scheduler.add_job("test", "* * * * *", callback)
        # 将 next_run 设为过去，触发立即执行
        scheduler._jobs["test"]["next_run"] = scheduler._now() - timedelta(seconds=5)

        await scheduler.start()
        await asyncio.sleep(2)
        await scheduler.stop()

        assert call_count >= 1


class TestCronSchedulerAsyncCallback:
    """测试异步回调"""

    @pytest.mark.asyncio
    async def test_async_callback(self):
        """测试异步回调执行"""
        scheduler = CronScheduler()
        results = []

        async def async_callback():
            results.append("async")

        scheduler.add_job("async_test", "* * * * *", async_callback)
        # 将 next_run 设为过去，触发立即执行
        scheduler._jobs["async_test"]["next_run"] = scheduler._now() - timedelta(
            seconds=5
        )

        await scheduler.start()
        await asyncio.sleep(2)
        await scheduler.stop()

        assert len(results) >= 1
