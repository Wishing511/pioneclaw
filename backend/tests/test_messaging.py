"""
消息队列模块测试
"""

import asyncio

import pytest

from app.modules.messaging import (
    DeadLetter,
    Message,
    MessagePriority,
    MessageQueue,
    RateLimiter,
    TokenBucket,
)


class TestMessagePriority:
    """测试消息优先级"""

    def test_priority_order(self):
        """测试优先级顺序"""
        assert MessagePriority.HIGH < MessagePriority.NORMAL
        assert MessagePriority.NORMAL < MessagePriority.LOW
        assert MessagePriority.LOW < MessagePriority.BACKGROUND

    def test_priority_values(self):
        """测试优先级值"""
        assert MessagePriority.HIGH.value == 0
        assert MessagePriority.NORMAL.value == 1
        assert MessagePriority.LOW.value == 2
        assert MessagePriority.BACKGROUND.value == 3


class TestMessage:
    """测试消息"""

    def test_message_creation(self):
        """测试消息创建"""
        msg = Message(
            id="test-123",
            content={"text": "Hello"},
            priority=MessagePriority.HIGH,
        )
        assert msg.id == "test-123"
        assert msg.content == {"text": "Hello"}
        assert msg.priority == MessagePriority.HIGH
        assert msg.retry_count == 0
        assert msg.max_retries == 3

    def test_content_hash(self):
        """测试内容哈希"""
        msg1 = Message(id="1", content="same content")
        msg2 = Message(id="2", content="same content")
        msg3 = Message(id="3", content="different content")

        assert msg1.content_hash() == msg2.content_hash()
        assert msg1.content_hash() != msg3.content_hash()

    def test_content_hash_dict(self):
        """测试字典内容哈希"""
        msg1 = Message(id="1", content={"key": "value", "num": 123})
        msg2 = Message(id="2", content={"key": "value", "num": 123})

        # 字典顺序可能不同，但内容相同
        assert msg1.content_hash() == msg2.content_hash()


class TestDeadLetter:
    """测试死信"""

    def test_dead_letter_creation(self):
        """测试死信创建"""
        msg = Message(id="test-123", content="test")
        dead_letter = DeadLetter(
            original_message=msg,
            error="Processing failed",
        )

        assert dead_letter.original_message == msg
        assert dead_letter.error == "Processing failed"
        assert dead_letter.failed_at > 0


class TestMessageQueue:
    """测试消息队列"""

    @pytest.mark.asyncio
    async def test_enqueue_dequeue(self):
        """测试入队和出队"""
        queue = MessageQueue()

        # 入队
        msg_id = await queue.enqueue("test message")
        assert msg_id is not None

        # 出队
        msg = await queue.dequeue()
        assert msg is not None
        assert msg.content == "test message"
        assert msg.id == msg_id

    @pytest.mark.asyncio
    async def test_priority_ordering(self):
        """测试优先级排序"""
        queue = MessageQueue()

        # 按非优先级顺序入队
        await queue.enqueue("low", priority=MessagePriority.LOW)
        await queue.enqueue("high", priority=MessagePriority.HIGH)
        await queue.enqueue("normal", priority=MessagePriority.NORMAL)
        await queue.enqueue("background", priority=MessagePriority.BACKGROUND)

        # 出队顺序应该是 HIGH -> NORMAL -> LOW -> BACKGROUND
        msg1 = await queue.dequeue()
        assert msg1.content == "high"

        msg2 = await queue.dequeue()
        assert msg2.content == "normal"

        msg3 = await queue.dequeue()
        assert msg3.content == "low"

        msg4 = await queue.dequeue()
        assert msg4.content == "background"

    @pytest.mark.asyncio
    async def test_deduplication(self):
        """测试去重"""
        queue = MessageQueue(dedup_window=10.0)

        # 相同内容入队两次
        id1 = await queue.enqueue("duplicate content")
        id2 = await queue.enqueue("duplicate content")

        # 第一次成功，第二次被去重
        assert id1 is not None
        assert id2 is None

    @pytest.mark.asyncio
    async def test_dedup_window_expiry(self):
        """测试去重窗口过期"""
        queue = MessageQueue(dedup_window=0.1)  # 0.1秒窗口

        # 第一次入队
        id1 = await queue.enqueue("content")
        assert id1 is not None

        # 等待窗口过期
        await asyncio.sleep(0.15)

        # 再次入队相同内容，应该成功
        id2 = await queue.enqueue("content")
        assert id2 is not None

    @pytest.mark.asyncio
    async def test_mark_success(self):
        """测试标记成功"""
        queue = MessageQueue()

        await queue.enqueue("test")
        msg = await queue.dequeue()

        # 标记成功
        await queue.mark_success(msg.id)

        # 处理中的消息应该被移除
        stats = await queue.get_stats()
        assert stats["processing"] == 0

    @pytest.mark.asyncio
    async def test_mark_failed_retry(self):
        """测试失败重试"""
        queue = MessageQueue(default_max_retries=3)

        await queue.enqueue("test")
        msg = await queue.dequeue()

        # 第一次失败
        await queue.mark_failed(msg.id, "Error 1")

        # 消息应该重新入队
        stats = await queue.get_stats()
        assert stats["total_queued"] == 1

        # 出队检查重试次数
        msg2 = await queue.dequeue()
        assert msg2.retry_count == 1

    @pytest.mark.asyncio
    async def test_dead_letter_queue(self):
        """测试死信队列"""
        queue = MessageQueue(default_max_retries=2)

        await queue.enqueue("test", max_retries=2)

        # 模拟多次失败 (max_retries=2, 所以第2次失败后进入死信)
        for i in range(3):
            msg = await queue.dequeue()
            if msg:
                await queue.mark_failed(msg.id, f"Error {i}")

        # 检查死信队列
        dead_letters = await queue.get_dead_letters()
        assert len(dead_letters) == 1
        # 第2次失败后进入死信（retry_count从0开始，达到max_retries=2时进入死信）
        assert dead_letters[0].error == "Error 1"

    @pytest.mark.asyncio
    async def test_get_stats(self):
        """测试获取统计"""
        queue = MessageQueue()

        await queue.enqueue("high", priority=MessagePriority.HIGH)
        await queue.enqueue("normal", priority=MessagePriority.NORMAL)
        await queue.enqueue("low", priority=MessagePriority.LOW)

        stats = await queue.get_stats()

        assert stats["queue_sizes"]["HIGH"] == 1
        assert stats["queue_sizes"]["NORMAL"] == 1
        assert stats["queue_sizes"]["LOW"] == 1
        assert stats["total_queued"] == 3
        assert stats["processing"] == 0
        assert stats["dead_letters"] == 0

    @pytest.mark.asyncio
    async def test_clear_dead_letters(self):
        """测试清空死信队列"""
        queue = MessageQueue(default_max_retries=1)

        await queue.enqueue("test", max_retries=1)
        msg = await queue.dequeue()
        await queue.mark_failed(msg.id, "Error")

        # 清空
        await queue.clear_dead_letters()
        dead_letters = await queue.get_dead_letters()
        assert len(dead_letters) == 0

    @pytest.mark.asyncio
    async def test_consumer(self):
        """测试消费者"""
        queue = MessageQueue()
        processed = []

        async def consumer(message: Message):
            processed.append(message.content)

        queue.set_consumer(consumer)

        # 入队消息
        await queue.enqueue("msg1")
        await queue.enqueue("msg2")

        # 手动消费（不启动后台任务）
        msg1 = await queue.dequeue()
        await consumer(msg1)
        await queue.mark_success(msg1.id)

        msg2 = await queue.dequeue()
        await consumer(msg2)
        await queue.mark_success(msg2.id)

        assert processed == ["msg1", "msg2"]


class TestTokenBucket:
    """测试令牌桶"""

    def test_token_bucket_creation(self):
        """测试令牌桶创建"""
        bucket = TokenBucket(capacity=100)
        assert bucket.capacity == 100
        assert bucket.tokens == 100  # 初始填满

    def test_token_bucket_refill_rate(self):
        """测试填充速率"""
        bucket = TokenBucket(capacity=60)
        # 默认每分钟填满，即每秒1个令牌
        assert bucket.refill_rate == 1.0


class TestRateLimiter:
    """测试限流器"""

    @pytest.mark.asyncio
    async def test_acquire_success(self):
        """测试获取令牌成功"""
        limiter = RateLimiter(default_capacity=10)

        result = await limiter.acquire("user1")
        assert result is True

        # 检查令牌数减少（允许小数误差）
        tokens = await limiter.get_tokens("user1")
        assert tokens == pytest.approx(9, abs=0.01)

    @pytest.mark.asyncio
    async def test_acquire_multiple_tokens(self):
        """测试获取多个令牌"""
        limiter = RateLimiter(default_capacity=10)

        result = await limiter.acquire("user1", tokens=5)
        assert result is True

        tokens = await limiter.get_tokens("user1")
        assert tokens == pytest.approx(5, abs=0.01)

    @pytest.mark.asyncio
    async def test_acquire_insufficient_tokens(self):
        """测试令牌不足"""
        limiter = RateLimiter(default_capacity=5)

        # 先消耗所有令牌
        await limiter.acquire("user1", tokens=5)

        # 再次尝试获取
        result = await limiter.acquire("user1")
        assert result is False

    @pytest.mark.asyncio
    async def test_multiple_keys(self):
        """测试多键独立限流"""
        limiter = RateLimiter(default_capacity=5)

        # 用户1消耗令牌
        await limiter.acquire("user1", tokens=5)

        # 用户2应该仍然有令牌
        result = await limiter.acquire("user2")
        assert result is True

    @pytest.mark.asyncio
    async def test_token_refill(self):
        """测试令牌填充"""
        limiter = RateLimiter(default_capacity=10, default_refill_rate=10.0)  # 每秒10个

        # 消耗所有令牌
        await limiter.acquire("user1", tokens=10)

        # 等待一小段时间
        await asyncio.sleep(0.1)

        # 应该有一些令牌被填充
        tokens = await limiter.get_tokens("user1")
        assert tokens > 0

    @pytest.mark.asyncio
    async def test_wait_and_acquire(self):
        """测试等待并获取"""
        limiter = RateLimiter(default_capacity=5, default_refill_rate=10.0)

        # 消耗所有令牌
        await limiter.acquire("user1", tokens=5)

        # 等待并获取（应该成功，因为会等待填充）
        result = await limiter.wait_and_acquire("user1", max_wait=1.0)
        assert result is True

    @pytest.mark.asyncio
    async def test_wait_and_acquire_timeout(self):
        """测试等待超时"""
        limiter = RateLimiter(default_capacity=5, default_refill_rate=1.0)  # 每秒1个

        # 消耗所有令牌
        await limiter.acquire("user1", tokens=5)

        # 尝试获取10个令牌，但只等待0.5秒，应该超时
        result = await limiter.wait_and_acquire("user1", tokens=10, max_wait=0.5)
        assert result is False

    @pytest.mark.asyncio
    async def test_get_wait_time(self):
        """测试获取等待时间"""
        limiter = RateLimiter(default_capacity=5, default_refill_rate=1.0)

        # 消耗所有令牌
        await limiter.acquire("user1", tokens=5)

        # 获取等待时间
        wait_time = await limiter.get_wait_time("user1", tokens=3)
        assert wait_time == pytest.approx(3.0, abs=0.1)  # 需要3秒填充3个令牌

    @pytest.mark.asyncio
    async def test_reset(self):
        """测试重置"""
        limiter = RateLimiter(default_capacity=5)

        # 消耗令牌
        await limiter.acquire("user1", tokens=5)

        # 重置
        await limiter.reset("user1")

        # 应该重新填满
        tokens = await limiter.get_tokens("user1")
        assert tokens == 5

    @pytest.mark.asyncio
    async def test_reset_all(self):
        """测试重置所有"""
        limiter = RateLimiter(default_capacity=5)

        # 多个用户消耗令牌
        await limiter.acquire("user1", tokens=5)
        await limiter.acquire("user2", tokens=5)

        # 重置所有
        await limiter.reset_all()

        # 所有用户应该重新填满
        tokens1 = await limiter.get_tokens("user1")
        tokens2 = await limiter.get_tokens("user2")
        assert tokens1 == 5
        assert tokens2 == 5

    @pytest.mark.asyncio
    async def test_get_stats(self):
        """测试获取统计"""
        limiter = RateLimiter(default_capacity=10)

        await limiter.acquire("user1", tokens=3)
        await limiter.acquire("user2", tokens=5)

        stats = await limiter.get_stats()

        assert "user1" in stats
        assert "user2" in stats
        assert stats["user1"]["tokens"] == pytest.approx(7, abs=0.01)
        assert stats["user2"]["tokens"] == pytest.approx(5, abs=0.01)

    @pytest.mark.asyncio
    async def test_custom_capacity(self):
        """测试自定义容量"""
        limiter = RateLimiter(default_capacity=10)

        # 使用自定义容量
        await limiter.acquire("user1", capacity=100)

        stats = await limiter.get_stats()
        assert stats["user1"]["capacity"] == 100

    @pytest.mark.asyncio
    async def test_custom_refill_rate(self):
        """测试自定义填充速率"""
        limiter = RateLimiter(default_capacity=10)

        # 使用自定义填充速率
        await limiter.acquire("user1", refill_rate=5.0)

        stats = await limiter.get_stats()
        assert stats["user1"]["refill_rate"] == 5.0
