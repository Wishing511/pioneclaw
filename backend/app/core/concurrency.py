"""
并发管理器 — 限制用户和全局 AgentLoop 并发数，超配额任务进入等待队列
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class AcquireResult:
    acquired: bool = False  # 直接获得执行权
    queued: bool = False  # 进入排队
    rejected: bool = False  # 排队已满，拒绝
    position: int = 0  # 排队位置（从1开始）
    estimated_wait_ms: int = 0  # 预估等待时间
    wait_future: asyncio.Future | None = None  # 排队等待的 Future


@dataclass
class QueuedTask:
    user_id: int
    task_id: str
    message_len: int  # 用于优先级排序（短任务优先）
    enqueued_at: float = field(default_factory=time.time)
    wait_future: asyncio.Future = field(default_factory=asyncio.Future)


class ConcurrencyManager:
    """全局并发管理器（进程级单例）"""

    def __init__(self):
        self.max_per_user: int = 3
        self.max_global: int = 20
        self.max_queue_per_user: int = 5
        self.queue_timeout_seconds: int = 180  # 3分钟超时

        self._active: dict[int, int] = {}  # user_id → 活跃数
        self._total_active: int = 0
        self._queue: list[QueuedTask] = []  # 全局等待队列

    @property
    def total_active(self) -> int:
        return self._total_active

    def user_active(self, user_id: int) -> int:
        return self._active.get(user_id, 0)

    def user_queued(self, user_id: int) -> int:
        return sum(1 for t in self._queue if t.user_id == user_id)

    def queue_size(self) -> int:
        return len(self._queue)

    async def acquire(
        self, user_id: int, task_id: str, message_len: int = 100
    ) -> AcquireResult:
        """申请执行配额。返回 AcquireResult"""
        now = time.time()

        # 清理超时任务
        self._queue = [
            t
            for t in self._queue
            if not t.wait_future.done()
            and (now - t.enqueued_at) < self.queue_timeout_seconds
        ]

        user_active = self._active.get(user_id, 0)
        user_queue = sum(1 for t in self._queue if t.user_id == user_id)

        # 可以直接执行？
        if user_active < self.max_per_user and self._total_active < self.max_global:
            self._active[user_id] = user_active + 1
            self._total_active += 1
            logger.info(
                f"[Concurrency] Acquired: user={user_id}, task={task_id}, "
                f"user_active={self._active[user_id]}, total={self._total_active}"
            )
            return AcquireResult(acquired=True)

        # 检查排队是否已满
        if user_queue >= self.max_queue_per_user:
            logger.warning(f"[Concurrency] Rejected: user={user_id}, queue full")
            return AcquireResult(rejected=True)

        # 加入排队
        wait_future = asyncio.Future()
        task = QueuedTask(
            user_id=user_id,
            task_id=task_id,
            message_len=message_len,
            enqueued_at=now,
            wait_future=wait_future,
        )
        self._queue.append(task)
        # 排序：短消息优先，同长度按入队时间
        self._queue.sort(key=lambda t: (t.message_len, t.enqueued_at))

        position = sum(
            1
            for t in self._queue
            if (t.message_len, t.enqueued_at) <= (task.message_len, task.enqueued_at)
        )

        # 预估等待：假设每个活跃任务平均 30s
        ahead = position
        estimated_ms = ahead * 30000

        logger.info(
            f"[Concurrency] Queued: user={user_id}, task={task_id}, "
            f"position={position}/{len(self._queue)}, wait_ms={estimated_ms}"
        )

        return AcquireResult(
            queued=True,
            position=position,
            estimated_wait_ms=estimated_ms,
            wait_future=wait_future,
        )

    def release(self, user_id: int):
        """释放执行配额，并唤醒下一个排队任务"""
        self._active[user_id] = max(0, self._active.get(user_id, 1) - 1)
        self._total_active = max(0, self._total_active - 1)
        logger.info(
            f"[Concurrency] Released: user={user_id}, "
            f"user_active={self._active.get(user_id, 0)}, total={self._total_active}"
        )

        # 唤醒下一个
        self._wake_next()

    def _wake_next(self):
        """唤醒队列中优先级最高的任务"""
        if not self._queue:
            return
        # 找第一个满足条件的任务
        for i, task in enumerate(self._queue):
            if task.wait_future.done():
                continue
            user_active = self._active.get(task.user_id, 0)
            if user_active < self.max_per_user and self._total_active < self.max_global:
                # 可以唤醒
                self._queue.pop(i)
                self._active[task.user_id] = user_active + 1
                self._total_active += 1
                task.wait_future.set_result(True)
                logger.info(
                    f"[Concurrency] Woke: user={task.user_id}, "
                    f"task={task.task_id}, waited={time.time() - task.enqueued_at:.1f}s"
                )
                return
        # 没有可唤醒的任务（都在排队，各自等前面的释放）

    def cancel_wait(self, user_id: int, task_id: str):
        """取消排队"""
        for task in self._queue:
            if task.user_id == user_id and task.task_id == task_id:
                if not task.wait_future.done():
                    task.wait_future.set_result(False)
                    self._queue.remove(task)
                    return True
        return False

    def get_position(self, user_id: int, task_id: str) -> int:
        """获取排队位置"""
        for i, task in enumerate(self._queue):
            if task.user_id == user_id and task.task_id == task_id:
                return i + 1
        return 0


# 全局单例
concurrency_manager = ConcurrencyManager()
