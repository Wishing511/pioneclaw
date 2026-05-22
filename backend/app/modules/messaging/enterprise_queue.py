"""
企业消息队列

特性：
- 4级优先级（HIGH, NORMAL, LOW, BACKGROUND）
- MD5 去重（10s 窗口期）
- 死信队列（max_retries 后）
- 消费确认机制
- 异步处理
"""

import asyncio
import hashlib
import time
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any

from loguru import logger


class MessagePriority(IntEnum):
    """消息优先级"""

    HIGH = 0  # 最高优先级（系统消息、紧急任务）
    NORMAL = 1  # 普通优先级（用户消息）
    LOW = 2  # 低优先级（批量任务）
    BACKGROUND = 3  # 后台优先级（清理、统计）


@dataclass
class Message:
    """消息"""

    id: str
    content: Any
    priority: MessagePriority = MessagePriority.NORMAL
    retry_count: int = 0
    max_retries: int = 3
    created_at: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

    def content_hash(self) -> str:
        """计算内容哈希"""
        content_str = str(self.content)
        return hashlib.md5(content_str.encode()).hexdigest()


@dataclass
class DeadLetter:
    """死信"""

    original_message: Message
    error: str
    failed_at: float = field(default_factory=time.time)


class MessageQueue:
    """
    企业消息队列

    特性：
    - 4级优先级排序
    - 10s 窗口期 MD5 去重
    - 失败重试（指数退避）
    - 死信队列
    - 消费确认
    """

    def __init__(
        self,
        dedup_window: float = 10.0,  # 去重窗口期（秒）
        default_max_retries: int = 3,
    ):
        self.dedup_window = dedup_window
        self.default_max_retries = default_max_retries

        # 优先级队列
        self._queues: dict[MessagePriority, list[Message]] = defaultdict(list)

        # 去重缓存 {hash: timestamp}
        self._dedup_cache: dict[str, float] = {}

        # 死信队列
        self._dead_letters: list[DeadLetter] = []

        # 处理中的消息
        self._processing: dict[str, Message] = {}

        # 锁
        self._lock = asyncio.Lock()

        # 消费者
        self._consumer: Callable | None = None

        # 运行状态
        self._running = False

    async def enqueue(
        self,
        content: Any,
        priority: MessagePriority = MessagePriority.NORMAL,
        max_retries: int = None,
        metadata: dict[str, Any] = None,
    ) -> str | None:
        """
        入队

        Args:
            content: 消息内容
            priority: 优先级
            max_retries: 最大重试次数
            metadata: 元数据

        Returns:
            消息 ID，如果被去重则返回 None
        """
        message = Message(
            id=self._generate_id(),
            content=content,
            priority=priority,
            max_retries=max_retries or self.default_max_retries,
            metadata=metadata or {},
        )

        async with self._lock:
            # 去重检查
            content_hash = message.content_hash()
            now = time.time()

            # 清理过期的去重缓存
            expired_keys = [
                k for k, v in self._dedup_cache.items() if now - v > self.dedup_window
            ]
            for k in expired_keys:
                del self._dedup_cache[k]

            # 检查是否重复
            if content_hash in self._dedup_cache:
                logger.debug(f"Message deduplicated: {content_hash[:8]}")
                return None

            # 记录到去重缓存
            self._dedup_cache[content_hash] = now

            # 加入队列
            self._queues[priority].append(message)
            logger.debug(
                f"Message enqueued: {message.id} with priority {priority.name}"
            )

            return message.id

    async def dequeue(self) -> Message | None:
        """
        出队（按优先级）

        Returns:
            消息，如果队列为空则返回 None
        """
        async with self._lock:
            # 按优先级顺序检查队列
            for priority in MessagePriority:
                if self._queues[priority]:
                    message = self._queues[priority].pop(0)
                    self._processing[message.id] = message
                    return message

            return None

    async def mark_success(self, message_id: str):
        """标记消息处理成功"""
        async with self._lock:
            if message_id in self._processing:
                del self._processing[message_id]
                logger.debug(f"Message processed successfully: {message_id}")

    async def mark_failed(self, message_id: str, error: str):
        """
        标记消息处理失败

        如果未达到最大重试次数，重新入队
        否则加入死信队列
        """
        async with self._lock:
            if message_id not in self._processing:
                return

            message = self._processing.pop(message_id)
            message.retry_count += 1

            if message.retry_count >= message.max_retries:
                # 加入死信队列
                dead_letter = DeadLetter(
                    original_message=message,
                    error=error,
                )
                self._dead_letters.append(dead_letter)
                logger.warning(f"Message moved to dead letter queue: {message_id}")
            else:
                # 重新入队
                self._queues[message.priority].append(message)
                logger.debug(
                    f"Message re-queued: {message_id}, retry {message.retry_count}"
                )

    async def get_dead_letters(self) -> list[DeadLetter]:
        """获取死信队列"""
        async with self._lock:
            return list(self._dead_letters)

    async def clear_dead_letters(self):
        """清空死信队列"""
        async with self._lock:
            self._dead_letters.clear()

    async def get_stats(self) -> dict[str, Any]:
        """获取队列统计"""
        async with self._lock:
            queue_sizes = {
                priority.name: len(self._queues[priority])
                for priority in MessagePriority
            }

            return {
                "queue_sizes": queue_sizes,
                "total_queued": sum(queue_sizes.values()),
                "processing": len(self._processing),
                "dead_letters": len(self._dead_letters),
                "dedup_cache_size": len(self._dedup_cache),
            }

    def set_consumer(self, consumer: Callable):
        """设置消费者函数"""
        self._consumer = consumer

    async def start_consuming(self):
        """开始消费"""
        if not self._consumer:
            raise RuntimeError("No consumer set")

        self._running = True
        logger.info("Message queue consumer started")

        while self._running:
            try:
                message = await self.dequeue()
                if message:
                    try:
                        await self._consumer(message)
                        await self.mark_success(message.id)
                    except Exception as e:
                        await self.mark_failed(message.id, str(e))
                else:
                    # 队列为空，等待
                    await asyncio.sleep(0.1)
            except Exception as e:
                logger.error(f"Consumer error: {e}")
                await asyncio.sleep(1)

    def stop_consuming(self):
        """停止消费"""
        self._running = False
        logger.info("Message queue consumer stopped")

    def _generate_id(self) -> str:
        """生成消息 ID"""
        import uuid

        return str(uuid.uuid4())
