"""
子代理系统测试
"""

import time
from datetime import datetime, timedelta

import pytest

from app.modules.agent.subagent import (
    SubagentManager,
    SubagentTask,
    TaskStatus,
    TaskType,
)


class TestTaskType:
    """测试任务类型"""

    def test_task_types(self):
        """测试任务类型枚举"""
        assert TaskType.GENERAL.value == "general"
        assert TaskType.RESEARCH.value == "research"
        assert TaskType.BUILD.value == "build"


class TestTaskStatus:
    """测试任务状态"""

    def test_task_statuses(self):
        """测试任务状态枚举"""
        assert TaskStatus.PENDING.value == "pending"
        assert TaskStatus.RUNNING.value == "running"
        assert TaskStatus.COMPLETED.value == "completed"
        assert TaskStatus.FAILED.value == "failed"
        assert TaskStatus.CANCELLED.value == "cancelled"


class TestSubagentTask:
    """测试子代理任务"""

    def test_task_creation(self):
        """测试任务创建"""
        task = SubagentTask(
            task_id="test-123",
            label="Test Task",
            message="Do something",
        )
        assert task.task_id == "test-123"
        assert task.label == "Test Task"
        assert task.task_type == TaskType.GENERAL
        assert task.status == TaskStatus.PENDING
        assert task.max_retries == 2
        assert task.retry_count == 0

    def test_task_with_type(self):
        """测试带类型的任务创建"""
        task = SubagentTask(
            task_id="test-456",
            label="Research Task",
            message="Research something",
            task_type=TaskType.RESEARCH,
        )
        assert task.task_type == TaskType.RESEARCH

    def test_task_to_dict(self):
        """测试任务转字典"""
        task = SubagentTask(
            task_id="test-789",
            label="Build Task",
            message="Build something",
            task_type=TaskType.BUILD,
        )
        d = task.to_dict()
        assert d["task_id"] == "test-789"
        assert d["task_type"] == "build"
        assert d["status"] == "pending"
        assert d["retry_count"] == 0


class TestSubagentManager:
    """测试子代理管理器"""

    def test_create_task(self):
        """测试创建任务"""
        manager = SubagentManager()
        task_id = manager.create_task(
            label="Test Task",
            message="Do something",
        )
        assert task_id is not None
        task = manager.get_task(task_id)
        assert task is not None
        assert task.label == "Test Task"

    def test_create_task_with_type(self):
        """测试创建带类型的任务"""
        manager = SubagentManager()
        task_id = manager.create_task(
            label="Research Task",
            message="Research something",
            task_type=TaskType.RESEARCH,
        )
        task = manager.get_task(task_id)
        assert task.task_type == TaskType.RESEARCH

    def test_create_task_with_max_retries(self):
        """测试创建带重试次数的任务"""
        manager = SubagentManager()
        task_id = manager.create_task(
            label="Retry Task",
            message="Do something",
            max_retries=3,
        )
        task = manager.get_task(task_id)
        assert task.max_retries == 3

    @pytest.mark.asyncio
    async def test_execute_task_without_agent_loop(self):
        """测试无 AgentLoop 时执行任务"""
        manager = SubagentManager(timeout_seconds=10)

        task_id = manager.create_task(
            label="Simple Task",
            message="Hello",
        )
        await manager.execute_task(task_id)

        # 等待任务完成
        task = await manager.wait_for_task(task_id, timeout=5)
        assert task is not None
        assert task.status == TaskStatus.COMPLETED
        assert task.result is not None

    @pytest.mark.asyncio
    async def test_cancel_task(self):
        """测试取消任务"""
        manager = SubagentManager(timeout_seconds=60)

        task_id = manager.create_task(
            label="Long Task",
            message="Do something long",
        )
        await manager.execute_task(task_id)

        # 尝试取消（可能已经完成，取决于执行速度）
        task = manager.get_task(task_id)
        if task.status in [TaskStatus.PENDING, TaskStatus.RUNNING]:
            result = await manager.cancel_task(task_id)
            assert result is True
            assert task.status == TaskStatus.CANCELLED

    @pytest.mark.asyncio
    async def test_cancel_nonexistent_task(self):
        """测试取消不存在的任务"""
        manager = SubagentManager()
        result = await manager.cancel_task("nonexistent")
        assert result is False

    def test_list_tasks(self):
        """测试列出任务"""
        manager = SubagentManager()

        manager.create_task(label="Task 1", message="M1", task_type=TaskType.GENERAL)
        manager.create_task(label="Task 2", message="M2", task_type=TaskType.RESEARCH)
        manager.create_task(label="Task 3", message="M3", task_type=TaskType.BUILD)

        tasks = manager.list_tasks()
        assert len(tasks) == 3

    def test_list_tasks_by_type(self):
        """测试按类型列出任务"""
        manager = SubagentManager()

        manager.create_task(label="Task 1", message="M1", task_type=TaskType.GENERAL)
        manager.create_task(label="Task 2", message="M2", task_type=TaskType.RESEARCH)
        manager.create_task(label="Task 3", message="M3", task_type=TaskType.BUILD)

        research_tasks = manager.list_tasks(task_type=TaskType.RESEARCH)
        assert len(research_tasks) == 1
        assert research_tasks[0].task_type == TaskType.RESEARCH

    def test_list_tasks_by_status(self):
        """测试按状态列出任务"""
        manager = SubagentManager()

        manager.create_task(label="Task 1", message="M1")
        manager.create_task(label="Task 2", message="M2")

        pending_tasks = manager.list_tasks(status=TaskStatus.PENDING)
        assert len(pending_tasks) == 2

    def test_get_stats(self):
        """测试获取统计"""
        manager = SubagentManager()

        manager.create_task(label="Task 1", message="M1", task_type=TaskType.GENERAL)
        manager.create_task(label="Task 2", message="M2", task_type=TaskType.RESEARCH)

        stats = manager.get_stats()
        assert stats["total"] == 2
        assert stats["pending"] == 2
        assert "by_type" in stats

    def test_delete_task(self):
        """测试删除任务"""
        manager = SubagentManager()

        task_id = manager.create_task(label="To Delete", message="M")
        result = manager.delete_task(task_id)
        assert result is True
        assert manager.get_task(task_id) is None

    def test_delete_nonexistent_task(self):
        """测试删除不存在的任务"""
        manager = SubagentManager()
        result = manager.delete_task("nonexistent")
        assert result is False

    @pytest.mark.asyncio
    async def test_concurrent_limit(self):
        """测试并发限制"""
        manager = SubagentManager(max_concurrent=2, timeout_seconds=30)

        # 创建 3 个任务
        ids = []
        for i in range(3):
            task_id = manager.create_task(
                label=f"Concurrent Task {i}",
                message=f"Task {i}",
            )
            ids.append(task_id)
            await manager.execute_task(task_id)

        # 等待所有完成
        for task_id in ids:
            task = await manager.wait_for_task(task_id, timeout=10)
            assert task is not None
            assert task.status == TaskStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_heartbeat_monitor(self):
        """测试心跳监控"""
        manager = SubagentManager(
            heartbeat_interval=0.1,
            heartbeat_timeout=0.5,
            timeout_seconds=10,
        )

        # 启动心跳
        await manager.start_heartbeat()

        # 创建并执行一个短任务
        task_id = manager.create_task(label="Heartbeat Task", message="Test")
        await manager.execute_task(task_id)

        # 等待完成
        task = await manager.wait_for_task(task_id, timeout=5)
        assert task is not None

        # 停止心跳
        await manager.stop_heartbeat()

    def test_update_heartbeat(self):
        """测试更新心跳"""
        manager = SubagentManager()

        task_id = manager.create_task(label="Test", message="M")
        old_hb = manager.get_task(task_id).last_heartbeat

        time.sleep(0.01)
        manager.update_heartbeat(task_id)
        new_hb = manager.get_task(task_id).last_heartbeat

        assert new_hb >= old_hb

    @pytest.mark.asyncio
    async def test_build_default_prompt(self):
        """测试构建默认提示词"""
        manager = SubagentManager()

        task_general = SubagentTask(
            task_id="g1", label="General", message="M", task_type=TaskType.GENERAL
        )
        prompt_general = manager._build_default_prompt(task_general)
        assert "通用" in prompt_general

        task_research = SubagentTask(
            task_id="r1", label="Research", message="M", task_type=TaskType.RESEARCH
        )
        prompt_research = manager._build_default_prompt(task_research)
        assert "研究" in prompt_research

        task_build = SubagentTask(
            task_id="b1", label="Build", message="M", task_type=TaskType.BUILD
        )
        prompt_build = manager._build_default_prompt(task_build)
        assert "构建" in prompt_build

    @pytest.mark.asyncio
    async def test_cleanup_old_tasks(self):
        """测试清理旧任务"""
        manager = SubagentManager()

        task_id = manager.create_task(label="Old Task", message="M")
        task = manager.get_task(task_id)
        task.status = TaskStatus.COMPLETED
        task.completed_at = datetime.now() - timedelta(hours=25)

        cleaned = await manager.cleanup_old_tasks(max_age_hours=24)
        assert cleaned == 1
        assert manager.get_task(task_id) is None
