"""
Cron 工具测试：CronCreateTool, CronDeleteTool, CronListTool
"""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.modules.tools.builtin import (
    CronCreateTool,
    CronDeleteTool,
    CronListTool,
    CronTool,
)

# ── 辅助函数 ──────────────────────────────────────────────────


def _make_async_db_session_mock(return_scalar=None):
    """构造完整的异步 DB session mock 链"""
    if return_scalar is None:
        return_scalar = MagicMock(id=1)

    mock_result = MagicMock()
    mock_result.scalar_one_or_none = MagicMock(return_value=return_scalar)

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_session.add = MagicMock()
    mock_session.delete = MagicMock()
    mock_session.commit = AsyncMock()
    mock_session.refresh = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=None)

    return MagicMock(return_value=mock_session)


# ============================================================
# CronCreateTool 测试
# ============================================================


class TestCronCreateTool:
    """测试 CronCreateTool"""

    @pytest.fixture
    def tool(self):
        return CronCreateTool()

    @pytest.fixture
    def mock_scheduler(self):
        scheduler = MagicMock()
        scheduler.validate_cron_expr.return_value = True
        scheduler.add_job.return_value = True
        scheduler.get_job.return_value = {
            "job_id": "cron_test123",
            "cron_expr": "0 9 * * *",
            "enabled": True,
            "next_run": datetime(2026, 5, 11, 9, 0, 0),
            "last_run": None,
            "run_count": 0,
        }
        return scheduler

    @pytest.mark.asyncio
    async def test_create_success(self, tool, mock_scheduler):
        """创建任务成功"""
        db_mock = _make_async_db_session_mock()

        with (
            patch(
                "app.core.cron_scheduler.get_cron_scheduler",
                return_value=mock_scheduler,
            ),
            patch("app.core.database.async_session_maker", db_mock),
        ):
            result = await tool.execute(
                cron_expr="0 9 * * *",
                prompt="每天早上 9 点发送日报",
                description="每日报告任务",
            )

        assert '"success": true' in result.lower()
        assert "cron_" in result  # auto-generated job_id

    @pytest.mark.asyncio
    async def test_create_invalid_expr(self, tool, mock_scheduler):
        """无效 cron 表达式"""
        mock_scheduler.validate_cron_expr.return_value = False

        with patch(
            "app.core.cron_scheduler.get_cron_scheduler", return_value=mock_scheduler
        ):
            result = await tool.execute(
                cron_expr="invalid",
                prompt="测试",
            )

        assert '"success": false' in result.lower() or "无效" in result

    @pytest.mark.asyncio
    async def test_create_with_name(self, tool, mock_scheduler):
        """创建任务时指定名称"""
        db_mock = _make_async_db_session_mock()

        with (
            patch(
                "app.core.cron_scheduler.get_cron_scheduler",
                return_value=mock_scheduler,
            ),
            patch("app.core.database.async_session_maker", db_mock),
        ):
            await tool.execute(
                cron_expr="*/5 * * * *",
                prompt="每 5 分钟检查",
                name="my_check",
            )

        mock_scheduler.add_job.assert_called_once()
        call_args = mock_scheduler.add_job.call_args
        assert call_args[0][0] == "my_check"  # first arg is job_id

    @pytest.mark.asyncio
    async def test_create_scheduler_add_fails(self, tool, mock_scheduler):
        """调度器添加失败"""
        mock_scheduler.add_job.return_value = False

        with patch(
            "app.core.cron_scheduler.get_cron_scheduler", return_value=mock_scheduler
        ):
            result = await tool.execute(
                cron_expr="0 9 * * *",
                prompt="测试",
            )

        assert '"success": false' in result.lower()


# ============================================================
# CronDeleteTool 测试
# ============================================================


class TestCronDeleteTool:
    """测试 CronDeleteTool"""

    @pytest.fixture
    def tool(self):
        return CronDeleteTool()

    @pytest.fixture
    def mock_scheduler(self):
        scheduler = MagicMock()
        scheduler.remove_job.return_value = True
        return scheduler

    @pytest.mark.asyncio
    async def test_delete_existing_job(self, tool, mock_scheduler):
        """删除存在的任务"""
        db_mock = _make_async_db_session_mock()

        with (
            patch(
                "app.core.cron_scheduler.get_cron_scheduler",
                return_value=mock_scheduler,
            ),
            patch("app.core.database.async_session_maker", db_mock),
        ):
            result = await tool.execute(job_id="cron_test123")

        assert '"success": true' in result.lower()
        mock_scheduler.remove_job.assert_called_once_with("cron_test123")

    @pytest.mark.asyncio
    async def test_delete_nonexistent_job(self, tool, mock_scheduler):
        """删除不存在的任务"""
        mock_scheduler.remove_job.return_value = False
        db_mock = _make_async_db_session_mock(return_scalar=None)

        with (
            patch(
                "app.core.cron_scheduler.get_cron_scheduler",
                return_value=mock_scheduler,
            ),
            patch("app.core.database.async_session_maker", db_mock),
        ):
            result = await tool.execute(job_id="nonexistent")

        assert '"success": false' in result.lower()


# ============================================================
# CronListTool 测试
# ============================================================


class TestCronListTool:
    """测试 CronListTool"""

    @pytest.fixture
    def tool(self):
        return CronListTool()

    @pytest.fixture
    def mock_scheduler(self):
        scheduler = MagicMock()
        scheduler.list_jobs.return_value = [
            {
                "job_id": "job1",
                "cron_expr": "0 9 * * *",
                "enabled": True,
                "next_run": datetime(2026, 5, 11, 9, 0),
                "last_run": None,
                "run_count": 0,
            },
            {
                "job_id": "job2",
                "cron_expr": "*/5 * * * *",
                "enabled": False,
                "next_run": None,
                "last_run": None,
                "run_count": 0,
            },
        ]
        return scheduler

    @pytest.mark.asyncio
    async def test_list_all(self, tool, mock_scheduler):
        """列出所有任务"""
        with patch(
            "app.core.cron_scheduler.get_cron_scheduler", return_value=mock_scheduler
        ):
            result = await tool.execute()

        assert '"total": 2' in result
        assert "job1" in result
        assert "job2" in result

    @pytest.mark.asyncio
    async def test_list_enabled_only(self, tool, mock_scheduler):
        """仅列出启用的任务"""
        with patch(
            "app.core.cron_scheduler.get_cron_scheduler", return_value=mock_scheduler
        ):
            result = await tool.execute(enabled_only=True)

        assert '"total": 1' in result
        assert "job1" in result
        assert "job2" not in result

    @pytest.mark.asyncio
    async def test_list_empty(self, tool, mock_scheduler):
        """空任务列表"""
        mock_scheduler.list_jobs.return_value = []

        with patch(
            "app.core.cron_scheduler.get_cron_scheduler", return_value=mock_scheduler
        ):
            result = await tool.execute()

        assert '"total": 0' in result


# ============================================================
# CronTool 向后兼容测试
# ============================================================


class TestCronToolBackwardCompat:
    """确保原 CronTool 仍正常工作"""

    @pytest.fixture
    def tool(self):
        return CronTool()

    @pytest.fixture
    def mock_scheduler(self):
        scheduler = MagicMock()
        scheduler.list_jobs.return_value = []
        scheduler.get_job.return_value = None
        scheduler.validate_cron_expr.return_value = True
        scheduler.describe_cron_expr.return_value = "每天 9:00"
        scheduler.get_next_run.return_value = datetime(2026, 5, 11, 9, 0)
        return scheduler

    @pytest.mark.asyncio
    async def test_list_action(self, tool, mock_scheduler):
        with patch(
            "app.core.cron_scheduler.get_cron_scheduler", return_value=mock_scheduler
        ):
            result = await tool.execute(action="list")
        assert "jobs" in result

    @pytest.mark.asyncio
    async def test_validate_action(self, tool, mock_scheduler):
        with patch(
            "app.core.cron_scheduler.get_cron_scheduler", return_value=mock_scheduler
        ):
            result = await tool.execute(action="validate", cron_expr="0 9 * * *")
        assert '"valid": true' in result.lower()

    @pytest.mark.asyncio
    async def test_status_action(self, tool, mock_scheduler):
        with patch(
            "app.core.cron_scheduler.get_cron_scheduler", return_value=mock_scheduler
        ):
            result = await tool.execute(action="status")
        assert "total_jobs" in result

    @pytest.mark.asyncio
    async def test_get_nonexistent(self, tool, mock_scheduler):
        with patch(
            "app.core.cron_scheduler.get_cron_scheduler", return_value=mock_scheduler
        ):
            result = await tool.execute(action="get", job_id="nonexistent")
        assert '"success": false' in result.lower()

    @pytest.mark.asyncio
    async def test_unknown_action(self, tool):
        result = await tool.execute(action="unknown_action")
        assert "未知操作" in result or "error" in result.lower()
