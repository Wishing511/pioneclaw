"""
令牌桶限流器

特性：
- 令牌桶算法
- 平滑限流
- 支持突发流量
- 异步安全
"""

import asyncio
import time
from dataclasses import dataclass, field

from loguru import logger


@dataclass
class TokenBucket:
    """令牌桶"""

    capacity: float  # 桶容量
    tokens: float = field(default=0.0)  # 当前令牌数
    last_refill: float = field(default_factory=time.time)  # 上次填充时间
    refill_rate: float = field(default=0.0)  # 每秒填充令牌数

    def __post_init__(self):
        if self.tokens == 0.0:
            self.tokens = self.capacity
        if self.refill_rate == 0.0:
            self.refill_rate = self.capacity / 60.0  # 默认每分钟填满


class RateLimiter:
    """
        令牌桶限流器

        特性：
    - 多 key 独立限流
        - 平滑填充
        - 突发流量支持
        - 异步安全
    """

    def __init__(
        self,
        default_capacity: int = 60,  # 默认桶容量（每分钟请求数）
        default_refill_rate: float | None = None,  # 默认填充速率（令牌/秒）
    ):
        self.default_capacity = default_capacity
        self.default_refill_rate = default_refill_rate or (default_capacity / 60.0)

        # 令牌桶缓存 {key: TokenBucket}
        self._buckets: dict[str, TokenBucket] = {}

        # 锁
        self._lock = asyncio.Lock()

    async def acquire(
        self,
        key: str,
        tokens: int = 1,
        capacity: int | None = None,
        refill_rate: float | None = None,
    ) -> bool:
        """
        获取令牌

        Args:
            key: 限流键（如用户ID、IP等）
            tokens: 需要的令牌数
            capacity: 桶容量（首次创建时使用）
            refill_rate: 填充速率（首次创建时使用）

        Returns:
            是否获取成功
        """
        async with self._lock:
            bucket = self._get_or_create_bucket(key, capacity, refill_rate)

            # 填充令牌
            self._refill(bucket)

            # 检查令牌是否足够
            if bucket.tokens >= tokens:
                bucket.tokens -= tokens
                return True

            return False

    async def wait_and_acquire(
        self,
        key: str,
        tokens: int = 1,
        capacity: int | None = None,
        refill_rate: float | None = None,
        max_wait: float = 60.0,
    ) -> bool:
        """
        等待并获取令牌

        Args:
            key: 限流键
            tokens: 需要的令牌数
            capacity: 桶容量
            refill_rate: 填充速率
            max_wait: 最大等待时间（秒）

        Returns:
            是否获取成功
        """
        start_time = time.time()

        while True:
            if await self.acquire(key, tokens, capacity, refill_rate):
                return True

            # 检查是否超时
            elapsed = time.time() - start_time
            if elapsed >= max_wait:
                return False

            # 计算需要等待的时间
            async with self._lock:
                bucket = self._get_or_create_bucket(key, capacity, refill_rate)
                needed_tokens = tokens - bucket.tokens
                wait_time = needed_tokens / bucket.refill_rate

            # 等待一小段时间后重试
            wait_time = min(wait_time, 0.5)  # 最多等待0.5秒
            await asyncio.sleep(wait_time)

    async def get_tokens(self, key: str) -> float:
        """获取当前令牌数"""
        async with self._lock:
            bucket = self._buckets.get(key)
            if not bucket:
                return self.default_capacity

            # 填充并返回
            self._refill(bucket)
            return bucket.tokens

    async def get_wait_time(self, key: str, tokens: int = 1) -> float:
        """获取需要等待的时间（秒）"""
        async with self._lock:
            bucket = self._buckets.get(key)
            if not bucket:
                return 0.0

            self._refill(bucket)

            if bucket.tokens >= tokens:
                return 0.0

            needed = tokens - bucket.tokens
            return needed / bucket.refill_rate

    async def reset(self, key: str):
        """重置指定键的令牌桶"""
        async with self._lock:
            if key in self._buckets:
                del self._buckets[key]
                logger.debug(f"Rate limiter reset for key: {key}")

    async def reset_all(self):
        """重置所有令牌桶"""
        async with self._lock:
            self._buckets.clear()
            logger.debug("Rate limiter reset all buckets")

    async def get_stats(self) -> dict[str, dict[str, float]]:
        """获取所有限流器的统计信息"""
        async with self._lock:
            stats = {}
            for key, bucket in self._buckets.items():
                self._refill(bucket)
                stats[key] = {
                    "tokens": bucket.tokens,
                    "capacity": bucket.capacity,
                    "refill_rate": bucket.refill_rate,
                }
            return stats

    def _get_or_create_bucket(
        self,
        key: str,
        capacity: int | None,
        refill_rate: float | None,
    ) -> TokenBucket:
        """获取或创建令牌桶"""
        if key not in self._buckets:
            cap = capacity or self.default_capacity
            rate = refill_rate or self.default_refill_rate

            self._buckets[key] = TokenBucket(
                capacity=cap,
                tokens=cap,  # 初始填满
                refill_rate=rate,
            )
            logger.debug(
                f"Created new token bucket for key: {key}, capacity: {cap}, rate: {rate}"
            )

        return self._buckets[key]

    def _refill(self, bucket: TokenBucket):
        """填充令牌"""
        now = time.time()
        elapsed = now - bucket.last_refill

        if elapsed > 0:
            # 计算应填充的令牌数
            tokens_to_add = elapsed * bucket.refill_rate
            bucket.tokens = min(bucket.capacity, bucket.tokens + tokens_to_add)
            bucket.last_refill = now
