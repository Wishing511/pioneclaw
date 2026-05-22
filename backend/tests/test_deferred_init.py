"""
DeferredInit 测试 (Stage QQ)
"""

import asyncio

import pytest

from app.core.deferred_init import DeferredInit, DeferredInitError

# ============================================================
# Basic initialization
# ============================================================


class TestDeferredInitBasic:
    """测试基本延迟初始化"""

    def test_factory_called_on_first_get(self):
        """首次 get() 时调用工厂函数"""
        called = [0]

        async def factory():
            called[0] += 1
            return "resource"

        deferred = DeferredInit(factory=factory, name="test")
        assert deferred.is_ready is False

        result = asyncio.run(deferred.get())
        assert result == "resource"
        assert called[0] == 1
        assert deferred.is_ready is True

    def test_second_get_returns_cached(self):
        """第二次 get() 返回缓存值，不重新调用工厂"""
        called = [0]

        async def factory():
            called[0] += 1
            return "resource"

        deferred = DeferredInit(factory=factory, name="test")
        asyncio.run(deferred.get())
        result = asyncio.run(deferred.get())
        assert result == "resource"
        assert called[0] == 1  # factory only called once

    def test_sync_factory(self):
        """同步工厂函数"""
        deferred = DeferredInit(factory=lambda: 42, name="test")
        result = asyncio.run(deferred.get())
        assert result == 42

    def test_get_sync(self):
        """同步 get_sync()"""
        deferred = DeferredInit(factory=lambda: "sync_val", name="test")
        result = deferred.get_sync()
        assert result == "sync_val"
        assert deferred.is_ready is True

    def test_get_sync_async_factory_fails(self):
        """同步 get_sync() + async 工厂 → RuntimeError"""

        async def factory():
            return "async_val"

        deferred = DeferredInit(factory=factory, name="test")
        with pytest.raises(RuntimeError, match="async factory"):
            deferred.get_sync()

    def test_reset(self):
        """reset() 后重新初始化"""
        called = [0]

        async def factory():
            called[0] += 1
            return f"val_{called[0]}"

        deferred = DeferredInit(factory=factory, name="test")
        val1 = asyncio.run(deferred.get())
        assert val1 == "val_1"

        deferred.reset()
        assert deferred.is_ready is False

        val2 = asyncio.run(deferred.get())
        assert val2 == "val_2"
        assert called[0] == 2

    def test_error_property(self):
        """error 属性记录失败信息"""

        async def factory():
            raise ValueError("test error")

        deferred = DeferredInit(factory=factory, name="test", max_retries=0)
        with pytest.raises(DeferredInitError):
            asyncio.run(deferred.get())
        assert deferred.error is not None
        assert "test error" in deferred.error


# ============================================================
# Retry
# ============================================================


class TestDeferredInitRetry:
    """测试重试机制"""

    def test_retry_succeeds_on_second_attempt(self):
        """第一次失败第二次成功"""
        attempts = [0]

        async def factory():
            attempts[0] += 1
            if attempts[0] < 2:
                raise ValueError("fail")
            return "ok"

        deferred = DeferredInit(factory=factory, name="test", max_retries=2)
        result = asyncio.run(deferred.get())
        assert result == "ok"
        assert attempts[0] == 2

    def test_retry_exhausted(self):
        """所有重试耗尽 → DeferredInitError"""

        async def factory():
            raise ValueError("always fail")

        deferred = DeferredInit(factory=factory, name="test", max_retries=2)
        with pytest.raises(DeferredInitError) as exc_info:
            asyncio.run(deferred.get())

        assert exc_info.value.attempts == 3  # 1 initial + 2 retries
        assert exc_info.value.name == "test"

    def test_no_retry_when_max_retries_zero(self):
        """max_retries=0 不重试"""

        async def factory():
            raise ValueError("fail")

        deferred = DeferredInit(factory=factory, name="test", max_retries=0)
        with pytest.raises(DeferredInitError) as exc_info:
            asyncio.run(deferred.get())
        assert exc_info.value.attempts == 1


# ============================================================
# Concurrency
# ============================================================


class TestDeferredInitConcurrency:
    """测试并发安全"""

    def test_concurrent_get_calls_factory_once(self):
        """多个并发 get() 调用只初始化一次"""
        called = [0]

        async def factory():
            await asyncio.sleep(0.05)  # simulate slow init
            called[0] += 1
            return "resource"

        deferred = DeferredInit(factory=factory, name="test")

        async def run():
            tasks = [deferred.get() for _ in range(10)]
            results = await asyncio.gather(*tasks)
            return results

        results = asyncio.run(run())
        assert all(r == "resource" for r in results)
        assert called[0] == 1  # factory called exactly once
        assert deferred.is_ready is True


# ============================================================
# Timeout
# ============================================================


class TestDeferredInitTimeout:
    """测试超时"""

    def test_timeout_triggers_retry(self):
        """超时后触发重试"""
        attempts = [0]

        async def factory():
            attempts[0] += 1
            if attempts[0] < 2:
                await asyncio.sleep(0.2)  # longer than timeout
            return "ok"

        deferred = DeferredInit(
            factory=factory,
            name="test",
            timeout=0.05,
            max_retries=1,
        )
        result = asyncio.run(deferred.get())
        assert result == "ok"
        assert attempts[0] == 2
