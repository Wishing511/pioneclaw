"""
PioneClaw 任务看板服务
提供任务创建、更新、查询功能
与 Cron、Subagent、Workflow 等系统集成

借鉴: AIE task_board.py
"""

import uuid
from datetime import datetime, timedelta, timezone
from enum import Enum

from loguru import logger
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import Task


class TaskScope(str, Enum):
    """任务范围"""

    SESSION = "session"  # 会话级任务
    SYSTEM = "system"  # 系统级任务


class TaskStatus(str, Enum):
    """任务状态"""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TaskType(str, Enum):
    """任务类型"""

    CHAT = "chat"  # 对话任务
    WORKFLOW = "workflow"  # 工作流任务
    CRON = "cron"  # 定时任务
    SUBAGENT = "subagent"  # 子 Agent 任务
    HEARTBEAT = "heartbeat"  # 心跳检测任务


class TaskBoardService:
    """
    任务看板服务

    用于与 Cron、Subagent、Workflow 等系统集成
    """

    def __init__(self, db: AsyncSession):
        self.db = db

    # ==================== 创建任务 ====================

    async def create_task(
        self,
        title: str,
        task_scope: str,
        task_type: str,
        session_id: str | None = None,
        parent_id: str | None = None,
        description: str = "",
        cron_id: str | None = None,
        cron_expression: str | None = None,
        estimated_duration: int | None = None,
    ) -> Task:
        """创建新任务"""
        task = Task(
            id=str(uuid.uuid4()),
            title=title,
            description=description,
            status=TaskStatus.PENDING.value,
            progress=0,
        )

        self.db.add(task)
        await self.db.commit()
        await self.db.refresh(task)

        logger.info(f"[TaskBoard] Created task: {task.id} - {title}")
        return task

    async def create_session_task(
        self,
        title: str,
        task_type: str,
        session_id: str,
        description: str = "",
        parent_id: str | None = None,
        estimated_duration: int | None = None,
    ) -> Task:
        """创建会话级任务"""
        return await self.create_task(
            title=title,
            task_scope=TaskScope.SESSION.value,
            task_type=task_type,
            session_id=session_id,
            parent_id=parent_id,
            description=description,
            estimated_duration=estimated_duration,
        )

    async def create_system_task(
        self,
        title: str,
        task_type: str,
        cron_id: str,
        cron_expression: str,
        description: str = "",
    ) -> Task:
        """创建系统级周期任务"""
        return await self.create_task(
            title=title,
            task_scope=TaskScope.SYSTEM.value,
            task_type=task_type,
            cron_id=cron_id,
            cron_expression=cron_expression,
            description=description,
        )

    # ==================== 更新任务 ====================

    async def update_status(
        self,
        task_id: str,
        status: str,
        error_message: str | None = None,
    ) -> Task | None:
        """更新任务状态"""
        task = await self.get_task(task_id)
        if not task:
            return None

        task.status = status

        if status == TaskStatus.RUNNING.value:
            task.started_at = datetime.now(tz=timezone.utc)

        if status == TaskStatus.COMPLETED.value:
            task.completed_at = datetime.now(tz=timezone.utc)
            if task.started_at:
                (task.completed_at - task.started_at).total_seconds()
                # 可以存储实际耗时

        if status == TaskStatus.FAILED.value and error_message:
            task.error = error_message

        await self.db.commit()
        await self.db.refresh(task)

        return task

    async def update_progress(
        self,
        task_id: str,
        progress: int,
    ) -> Task | None:
        """更新任务进度"""
        task = await self.get_task(task_id)
        if not task:
            return None

        task.progress = min(100, max(0, progress))
        await self.db.commit()
        await self.db.refresh(task)
        return task

    async def start_task(self, task_id: str) -> Task | None:
        """开始任务"""
        return await self.update_status(task_id, TaskStatus.RUNNING.value)

    async def complete_task(self, task_id: str) -> Task | None:
        """完成任务"""
        return await self.update_status(task_id, TaskStatus.COMPLETED.value)

    async def fail_task(
        self,
        task_id: str,
        error_message: str = "",
    ) -> Task | None:
        """标记任务失败"""
        return await self.update_status(task_id, TaskStatus.FAILED.value, error_message)

    async def cancel_task(self, task_id: str) -> Task | None:
        """取消任务"""
        return await self.update_status(task_id, TaskStatus.CANCELLED.value)

    # ==================== 查询任务 ====================

    async def get_task(self, task_id: str) -> Task | None:
        """获取任务"""
        result = await self.db.execute(select(Task).where(Task.id == task_id))
        return result.scalar_one_or_none()

    async def get_running_tasks(self) -> list[Task]:
        """获取所有进行中的任务"""
        result = await self.db.execute(
            select(Task)
            .where(Task.status == TaskStatus.RUNNING.value)
            .order_by(Task.created_at.desc())
        )
        tasks = result.scalars().all()
        return list(tasks) if tasks else []

    async def get_pending_tasks(self) -> list[Task]:
        """获取所有等待中的任务"""
        result = await self.db.execute(
            select(Task)
            .where(Task.status == TaskStatus.PENDING.value)
            .order_by(Task.created_at.asc())
        )
        tasks = result.scalars().all()
        return list(tasks) if tasks else []

    async def get_recent_tasks(self, limit: int = 20) -> list[Task]:
        """获取最近的任务"""
        result = await self.db.execute(
            select(Task).order_by(Task.created_at.desc()).limit(limit)
        )
        tasks = result.scalars().all()
        return list(tasks) if tasks else []

    async def get_tasks_by_status(self, status: str) -> list[Task]:
        """按状态获取任务"""
        result = await self.db.execute(
            select(Task).where(Task.status == status).order_by(Task.created_at.desc())
        )
        tasks = result.scalars().all()
        return list(tasks) if tasks else []

    # ==================== 统计 ====================

    async def get_stats(self) -> dict:
        """获取任务统计"""
        # 获取所有任务
        result = await self.db.execute(select(Task))
        tasks = list(result.scalars().all())

        # 统计各状态数量
        stats = {
            "total": len(tasks),
            "pending": 0,
            "running": 0,
            "completed": 0,
            "failed": 0,
            "cancelled": 0,
        }

        for task in tasks:
            status = task.status
            if status == TaskStatus.PENDING.value:
                stats["pending"] += 1
            elif status == TaskStatus.RUNNING.value:
                stats["running"] += 1
            elif status == TaskStatus.COMPLETED.value:
                stats["completed"] += 1
            elif status == TaskStatus.FAILED.value:
                stats["failed"] += 1
            elif status == TaskStatus.CANCELLED.value:
                stats["cancelled"] += 1

        return stats


class TaskHeartbeatService:
    """
    任务心跳服务

    检测超时任务、卡死任务
    """

    def __init__(self, db: AsyncSession):
        self.db = db
        self.task_board = TaskBoardService(db)

    async def scan_running_tasks(
        self,
        timeout_minutes: int = 30,
    ) -> dict:
        """
        扫描运行中的任务，检测超时

        Args:
            timeout_minutes: 超时时间（分钟）

        Returns:
            扫描结果
        """
        running_tasks = await self.task_board.get_running_tasks()

        timeout_tasks = []
        now = datetime.now(tz=timezone.utc)
        timeout_threshold = now - timedelta(minutes=timeout_minutes)

        for task in running_tasks:
            if task.started_at and task.started_at < timeout_threshold:
                timeout_tasks.append(task)

        # 标记超时任务
        for task in timeout_tasks:
            await self.task_board.fail_task(
                task.id, f"任务超时（超过 {timeout_minutes} 分钟）"
            )

        return {
            "scanned": len(running_tasks),
            "timeout": len(timeout_tasks),
            "timeout_task_ids": [t.id for t in timeout_tasks],
        }

    async def check_long_waiting_tasks(
        self,
        wait_minutes: int = 10,
    ) -> dict:
        """
        检测长时间等待的任务

        Args:
            wait_minutes: 等待时间（分钟）

        Returns:
            检测结果
        """
        pending_tasks = await self.task_board.get_pending_tasks()

        long_waiting = []
        now = datetime.now(tz=timezone.utc)
        wait_threshold = now - timedelta(minutes=wait_minutes)

        for task in pending_tasks:
            if task.created_at < wait_threshold:
                long_waiting.append(task)

        return {
            "checked": len(pending_tasks),
            "long_waiting": len(long_waiting),
            "task_ids": [t.id for t in long_waiting],
        }

    async def cleanup_completed_tasks(
        self,
        days: int = 7,
    ) -> int:
        """
        清理已完成的旧任务

        Args:
            days: 保留天数

        Returns:
            清理数量
        """
        threshold = datetime.now(tz=timezone.utc) - timedelta(days=days)

        result = await self.db.execute(
            select(Task).where(
                and_(
                    Task.status.in_(
                        [
                            TaskStatus.COMPLETED.value,
                            TaskStatus.FAILED.value,
                            TaskStatus.CANCELLED.value,
                        ]
                    ),
                    Task.completed_at < threshold,
                )
            )
        )

        tasks = list(result.scalars().all())

        for task in tasks:
            await self.db.delete(task)

        await self.db.commit()

        if tasks:
            logger.info(f"[TaskHeartbeat] Cleaned up {len(tasks)} completed tasks")

        return len(tasks)


# ==================== 便捷函数 ====================


async def run_task_heartbeat(db_session_factory):
    """
    运行任务心跳检测

    供 Cron 调用
    """
    async with db_session_factory() as db:
        service = TaskHeartbeatService(db)

        # 扫描超时任务
        scan_result = await service.scan_running_tasks()

        # 检测长时间等待
        long_waiting = await service.check_long_waiting_tasks()

        # 清理已完成任务
        archived_count = await service.cleanup_completed_tasks()

        return {
            "scan_result": scan_result,
            "long_waiting": long_waiting,
            "archived": archived_count,
        }


# Task Heartbeat Job ID
TASK_HEARTBEAT_JOB_ID = "builtin:task_heartbeat"

# Task Heartbeat 默认 schedule: 每 5 分钟
TASK_HEARTBEAT_SCHEDULE = "*/5 * * * *"
