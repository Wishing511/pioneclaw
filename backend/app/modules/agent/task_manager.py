"""
Task Manager - 任务取消令牌和管理

借鉴自 CountBot 的 task_manager.py，实现协作式任务取消机制。

核心概念：
1. CancellationToken - 取消令牌，用于传递取消信号
2. CancellationTokenSource - 令牌源，用于创建和控制令牌
3. TaskManager - 任务管理器，管理所有会话任务
"""

import asyncio
import logging
import threading
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


class TaskState(Enum):
    """任务状态"""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class CancellationToken:
    """
    取消令牌 - 用于传递取消信号

    使用方式：
    ```python
    token = CancellationToken()

    # 在任务中检查
    if token.is_cancelled:
        return  # 提前退出

    # 或注册回调
    token.register_callback(lambda: cleanup())
    ```
    """

    _cancelled: bool = field(default=False, repr=False)
    _callbacks: list[Callable[[], None]] = field(default_factory=list, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @property
    def is_cancelled(self) -> bool:
        """是否已取消"""
        return self._cancelled

    def cancel(self) -> bool:
        """
        取消令牌

        Returns:
            True 如果是首次取消，False 如果已经取消
        """
        with self._lock:
            if self._cancelled:
                return False
            self._cancelled = True

            # 执行所有回调
            for callback in self._callbacks:
                try:
                    callback()
                except Exception as e:
                    logger.error(f"Cancellation callback error: {e}")

            return True

    def register_callback(self, callback: Callable[[], None]) -> None:
        """
        注册取消回调

        Args:
            callback: 取消时执行的回调函数
        """
        with self._lock:
            if self._cancelled:
                # 已取消，立即执行
                try:
                    callback()
                except Exception as e:
                    logger.error(f"Cancellation callback error: {e}")
            else:
                self._callbacks.append(callback)

    def throw_if_cancelled(self) -> None:
        """
        如果已取消则抛出异常

        Raises:
            CancelledError: 如果已取消
        """
        if self._cancelled:
            raise asyncio.CancelledError("Operation was cancelled")


@dataclass
class CancellationTokenSource:
    """
    取消令牌源 - 创建和控制 CancellationToken

    使用方式：
    ```python
    source = CancellationTokenSource()
    token = source.token

    # 启动任务
    task = asyncio.create_task(some_work(token))

    # 稍后取消
    source.cancel()
    ```
    """

    _token: CancellationToken | None = field(default=None, repr=False)
    _timeout: float | None = field(default=None, repr=False)

    @property
    def token(self) -> CancellationToken:
        """获取取消令牌"""
        if self._token is None:
            self._token = CancellationToken()
        return self._token

    def cancel(self) -> bool:
        """
        取消令牌

        Returns:
            True 如果是首次取消
        """
        return self.token.cancel()

    def cancel_after(self, delay: float) -> None:
        """
        延迟取消

        Args:
            delay: 延迟时间（秒）
        """
        self._timeout = delay

        async def _timeout_cancel():
            await asyncio.sleep(delay)
            if not self.token.is_cancelled:
                self.cancel()

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.create_task(_timeout_cancel())
        except RuntimeError:
            pass

    @property
    def is_cancellation_requested(self) -> bool:
        """是否请求取消"""
        return self.token.is_cancelled


@dataclass
class SessionTask:
    """
    会话任务 - 关联会话和任务的容器
    """

    task_id: str
    session_id: str
    task: asyncio.Task
    token: CancellationToken
    state: TaskState = TaskState.PENDING
    created_at: datetime = field(default_factory=datetime.now)
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def elapsed_seconds(self) -> float | None:
        """已运行时间（秒）"""
        if self.started_at is None:
            return None
        end = self.completed_at or datetime.now()
        return (end - self.started_at).total_seconds()

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "session_id": self.session_id,
            "state": self.state.value,
            "created_at": self.created_at.isoformat(),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat()
            if self.completed_at
            else None,
            "elapsed_seconds": self.elapsed_seconds,
            "error": self.error,
            "is_cancelled": self.token.is_cancelled,
        }


class TaskManager:
    """
    任务管理器 - 管理所有会话任务

    功能：
    1. 创建和跟踪任务
    2. 取消任务（通过 session_id 或 task_id）
    3. 查询任务状态
    4. 自动清理已完成任务
    """

    def __init__(self, cleanup_interval: float = 300.0):
        """
        初始化任务管理器

        Args:
            cleanup_interval: 清理间隔（秒），默认 5 分钟
        """
        self._tasks: dict[str, SessionTask] = {}
        self._session_tasks: dict[str, list[str]] = {}  # session_id -> [task_ids]
        self._lock = threading.Lock()
        self._cleanup_interval = cleanup_interval
        self._cleanup_task: asyncio.Task | None = None

    async def create_task(
        self,
        session_id: str,
        coro: Awaitable[T],
        metadata: dict | None = None,
    ) -> tuple[str, CancellationToken]:
        """
        创建任务

        Args:
            session_id: 会话 ID
            coro: 协程
            metadata: 任务元数据

        Returns:
            (task_id, cancellation_token)
        """
        task_id = str(uuid.uuid4())
        token = CancellationToken()

        # 创建 asyncio 任务
        async def _wrapped():
            session_task = self._tasks.get(task_id)
            if session_task:
                session_task.state = TaskState.RUNNING
                session_task.started_at = datetime.now()

            try:
                result = await coro
                if session_task:
                    session_task.state = TaskState.COMPLETED
                    session_task.completed_at = datetime.now()
                return result
            except asyncio.CancelledError:
                if session_task:
                    session_task.state = TaskState.CANCELLED
                    session_task.completed_at = datetime.now()
                raise
            except Exception as e:
                if session_task:
                    session_task.state = TaskState.FAILED
                    session_task.completed_at = datetime.now()
                    session_task.error = str(e)
                raise

        # 创建 asyncio 任务
        asyncio_task = asyncio.create_task(_wrapped())

        # 注册任务
        session_task = SessionTask(
            task_id=task_id,
            session_id=session_id,
            task=asyncio_task,
            token=token,
            metadata=metadata or {},
        )

        with self._lock:
            self._tasks[task_id] = session_task
            if session_id not in self._session_tasks:
                self._session_tasks[session_id] = []
            self._session_tasks[session_id].append(task_id)

        logger.debug(f"Created task {task_id} for session {session_id}")
        return task_id, token

    def cancel_task(self, task_id: str) -> bool:
        """
        取消任务

        Args:
            task_id: 任务 ID

        Returns:
            True 如果成功取消
        """
        with self._lock:
            session_task = self._tasks.get(task_id)
            if session_task is None:
                return False

            if session_task.state in (
                TaskState.COMPLETED,
                TaskState.FAILED,
                TaskState.CANCELLED,
            ):
                return False

            # 取消令牌
            session_task.token.cancel()

            # 取消 asyncio 任务
            if not session_task.task.done():
                session_task.task.cancel()

            session_task.state = TaskState.CANCELLED
            session_task.completed_at = datetime.now()

            logger.info(f"Cancelled task {task_id}")
            return True

    def cancel_session(self, session_id: str) -> int:
        """
        取消会话的所有任务

        Args:
            session_id: 会话 ID

        Returns:
            取消的任务数量
        """
        cancelled_count = 0
        with self._lock:
            task_ids = self._session_tasks.get(session_id, [])
            for task_id in task_ids:
                session_task = self._tasks.get(task_id)
                if session_task and session_task.state == TaskState.RUNNING:
                    session_task.token.cancel()
                    if not session_task.task.done():
                        session_task.task.cancel()
                    session_task.state = TaskState.CANCELLED
                    session_task.completed_at = datetime.now()
                    cancelled_count += 1

        logger.info(f"Cancelled {cancelled_count} tasks for session {session_id}")
        return cancelled_count

    def get_task(self, task_id: str) -> SessionTask | None:
        """获取任务"""
        return self._tasks.get(task_id)

    def get_session_tasks(self, session_id: str) -> list[SessionTask]:
        """获取会话的所有任务"""
        task_ids = self._session_tasks.get(session_id, [])
        return [self._tasks[tid] for tid in task_ids if tid in self._tasks]

    def get_all_tasks(self) -> list[SessionTask]:
        """获取所有任务"""
        return list(self._tasks.values())

    def cleanup_completed(self, max_age_seconds: float = 3600) -> int:
        """
        清理已完成的任务

        Args:
            max_age_seconds: 最大保留时间（秒）

        Returns:
            清理的任务数量
        """
        cleaned_count = 0
        now = datetime.now()

        with self._lock:
            to_remove = []
            for task_id, session_task in self._tasks.items():
                if session_task.state in (
                    TaskState.COMPLETED,
                    TaskState.FAILED,
                    TaskState.CANCELLED,
                ):
                    if session_task.completed_at:
                        age = (now - session_task.completed_at).total_seconds()
                        if age > max_age_seconds:
                            to_remove.append(task_id)

            for task_id in to_remove:
                session_task = self._tasks.pop(task_id)
                # 从 session_tasks 中移除
                session_id = session_task.session_id
                if session_id in self._session_tasks:
                    self._session_tasks[session_id] = [
                        tid for tid in self._session_tasks[session_id] if tid != task_id
                    ]
                cleaned_count += 1

        if cleaned_count > 0:
            logger.info(f"Cleaned up {cleaned_count} completed tasks")

        return cleaned_count

    async def start_cleanup_loop(self) -> None:
        """启动自动清理循环"""

        async def _cleanup_loop():
            while True:
                await asyncio.sleep(self._cleanup_interval)
                self.cleanup_completed()

        self._cleanup_task = asyncio.create_task(_cleanup_loop())

    def stop_cleanup_loop(self) -> None:
        """停止自动清理循环"""
        if self._cleanup_task and not self._cleanup_task.done():
            self._cleanup_task.cancel()

    @property
    def stats(self) -> dict:
        """获取统计信息"""
        with self._lock:
            states = {}
            for task in self._tasks.values():
                state = task.state.value
                states[state] = states.get(state, 0) + 1

            return {
                "total_tasks": len(self._tasks),
                "total_sessions": len(self._session_tasks),
                "by_state": states,
            }


# 全局任务管理器实例
_global_task_manager: TaskManager | None = None


def get_task_manager() -> TaskManager:
    """获取全局任务管理器"""
    global _global_task_manager
    if _global_task_manager is None:
        _global_task_manager = TaskManager()
    return _global_task_manager


def create_cancellation_token() -> CancellationToken:
    """创建取消令牌（便捷方法）"""
    return CancellationToken()
