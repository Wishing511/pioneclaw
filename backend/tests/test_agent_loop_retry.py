"""AgentLoop 重试体系集成测试

测试内容：
1. LLMCallRetrier 集成（指数退避重试）
2. Key 轮换（认证/限流错误时切换 key）
3. prompt_too_long 应急压缩后重试
4. 非可重试错误直接失败
"""

import pytest

from app.modules.agent.loop import AgentLoop
from app.modules.llm.retry import LLMCallRetrier, RetryConfig


class MockProvider:
    """模拟 LLM Provider，支持控制失败次数和错误类型"""

    def __init__(self, responses=None, fail_count=0, error_type="transient"):
        self.responses = responses or []
        self._call_count = 0
        self._fail_count = fail_count
        self._error_type = error_type
        self.api_key = "key-1"

    async def chat_stream(self, messages, tools=None, model=None, temperature=None, max_tokens=None):
        self._call_count += 1

        if self._call_count <= self._fail_count:
            if self._error_type == "transient":
                raise Exception("502 Bad Gateway")
            elif self._error_type == "rate_limit":
                exc = Exception("429 Too Many Requests")
                exc.status_code = 429
                raise exc
            elif self._error_type == "auth":
                exc = Exception("401 Unauthorized")
                exc.status_code = 401
                raise exc
            elif self._error_type == "prompt_too_long":
                # 返回一个包含 error 的 chunk（模拟 provider 返回错误）
                yield {"error": "prompt is too long", "finish_reason": "error"}
                return

        # 返回正常响应
        for response in self.responses:
            yield response


class TestLLMRetrierIntegration:
    """测试 LLMCallRetrier 集成到 AgentLoop"""

    @pytest.mark.asyncio
    async def test_agent_loop_initializes_retrier(self):
        """AgentLoop 初始化时应创建 LLMCallRetrier"""
        loop = AgentLoop(provider=MockProvider())

        assert loop._llm_retrier is not None
        assert loop._llm_retrier.config.max_retries == 5

    @pytest.mark.asyncio
    async def test_successful_call_no_retry(self):
        """正常调用不应触发重试"""
        provider = MockProvider(
            responses=[{"content": "Hello", "finish_reason": "stop"}]
        )
        loop = AgentLoop(provider=provider)

        chunks = []
        async for chunk in loop._call_llm_stream(
            messages=[{"role": "user", "content": "Hi"}],
            tools=[],
        ):
            chunks.append(chunk)

        assert provider._call_count == 1
        assert len(chunks) == 1
        assert chunks[0]["content"] == "Hello"

    @pytest.mark.asyncio
    async def test_transient_error_retry(self):
        """瞬态错误（502）应触发重试"""
        provider = MockProvider(
            responses=[{"content": "Hello", "finish_reason": "stop"}],
            fail_count=2,
            error_type="transient",
        )
        loop = AgentLoop(provider=provider)
        # 减少重试次数以加快测试
        loop._llm_retrier = LLMCallRetrier(RetryConfig(max_retries=3, base_delay_ms=100))

        chunks = []
        async for chunk in loop._call_llm_stream(
            messages=[{"role": "user", "content": "Hi"}],
            tools=[],
        ):
            chunks.append(chunk)

        assert provider._call_count == 3  # 2 次失败 + 1 次成功
        assert len(chunks) == 1
        assert chunks[0]["content"] == "Hello"

    @pytest.mark.asyncio
    async def test_max_retries_exhausted(self):
        """超过最大重试次数应返回错误"""
        provider = MockProvider(
            responses=[{"content": "Hello", "finish_reason": "stop"}],
            fail_count=10,
            error_type="transient",
        )
        loop = AgentLoop(provider=provider)
        loop._llm_retrier = LLMCallRetrier(RetryConfig(max_retries=2, base_delay_ms=100))

        chunks = []
        async for chunk in loop._call_llm_stream(
            messages=[{"role": "user", "content": "Hi"}],
            tools=[],
        ):
            chunks.append(chunk)

        assert provider._call_count == 3  # 初始 + 2 次重试
        assert len(chunks) == 1
        assert "error" in chunks[0]

    @pytest.mark.asyncio
    async def test_non_retryable_error_fails_fast(self):
        """非可重试错误（400 Bad Request）应直接失败"""

        class BadRequestProvider(MockProvider):
            async def chat_stream(self, **kwargs):
                self._call_count += 1
                exc = Exception("400 Bad Request")
                exc.status_code = 400
                raise exc

        provider = BadRequestProvider()
        loop = AgentLoop(provider=provider)

        chunks = []
        async for chunk in loop._call_llm_stream(
            messages=[{"role": "user", "content": "Hi"}],
            tools=[],
        ):
            chunks.append(chunk)

        assert provider._call_count == 1  # 不重试
        assert len(chunks) == 1
        assert "error" in chunks[0]


class TestKeyRotation:
    """测试 Key 轮换"""

    @pytest.mark.asyncio
    async def test_key_rotation_on_auth_error(self):
        """认证错误时应轮换 Key"""

        class AuthFailProvider(MockProvider):
            def __init__(self):
                super().__init__()
                self.api_key = "key-1"
                self._call_count = 0

            async def chat_stream(self, **kwargs):
                self._call_count += 1
                if self._call_count == 1:
                    exc = Exception("401 Unauthorized")
                    exc.status_code = 401
                    raise exc
                yield {"content": "Success after rotation", "finish_reason": "stop"}

        provider = AuthFailProvider()
        loop = AgentLoop(
            provider=provider,
            api_keys=["key-1", "key-2", "key-3"],
        )
        loop._llm_retrier = LLMCallRetrier(RetryConfig(max_retries=3, base_delay_ms=100))

        # 验证初始 key
        assert provider.api_key == "key-1"

        chunks = []
        async for chunk in loop._call_llm_stream(
            messages=[{"role": "user", "content": "Hi"}],
            tools=[],
        ):
            chunks.append(chunk)

        assert provider._call_count == 2
        assert provider.api_key == "key-2"  # 已轮换
        assert loop._key_rotation_count == 1
        assert chunks[0]["content"] == "Success after rotation"

    @pytest.mark.asyncio
    async def test_key_rotation_exhaustion(self):
        """Key 全部失败后返回错误"""

        class AlwaysAuthFailProvider(MockProvider):
            def __init__(self):
                super().__init__()
                self.api_key = "key-1"
                self._call_count = 0

            async def chat_stream(self, **kwargs):
                self._call_count += 1
                exc = Exception("401 Unauthorized")
                exc.status_code = 401
                raise exc

        provider = AlwaysAuthFailProvider()
        loop = AgentLoop(
            provider=provider,
            api_keys=["key-1", "key-2"],
        )
        loop._llm_retrier = LLMCallRetrier(RetryConfig(max_retries=5, base_delay_ms=100))

        chunks = []
        async for chunk in loop._call_llm_stream(
            messages=[{"role": "user", "content": "Hi"}],
            tools=[],
        ):
            chunks.append(chunk)

        assert len(chunks) == 1
        assert "error" in chunks[0]

    @pytest.mark.asyncio
    async def test_no_keys_no_rotation(self):
        """没有配置多 Key 时不应尝试轮换"""

        class FailProvider(MockProvider):
            def __init__(self):
                super().__init__()
                self.api_key = "single-key"
                self._call_count = 0

            async def chat_stream(self, **kwargs):
                self._call_count += 1
                exc = Exception("429 Too Many Requests")
                exc.status_code = 429
                raise exc

        provider = FailProvider()
        loop = AgentLoop(
            provider=provider,
            api_keys=[],  # 无多 Key
        )
        loop._llm_retrier = LLMCallRetrier(RetryConfig(max_retries=2, base_delay_ms=100))

        chunks = []
        async for chunk in loop._call_llm_stream(
            messages=[{"role": "user", "content": "Hi"}],
            tools=[],
        ):
            chunks.append(chunk)

        assert provider._call_count == 3  # 重试但不轮换
        assert loop._key_rotation_count == 0


class TestRetryConfig:
    """测试 RetryConfig"""

    def test_delay_computation(self):
        """测试指数退避延迟计算"""
        config = RetryConfig(base_delay_ms=2000, max_delay_ms=32000)

        # attempt=1: ~2s
        delay1 = config.compute_delay_ms(1)
        assert 1500 <= delay1 <= 2500

        # attempt=2: ~8s
        delay2 = config.compute_delay_ms(2)
        assert 6000 <= delay2 <= 10000

        # attempt=3: ~32s
        delay3 = config.compute_delay_ms(3)
        assert 24000 <= delay3 <= 40000
        assert delay3 <= 32000  # 不超过上限

    def test_jitter_range(self):
        """测试抖动范围"""
        config = RetryConfig(base_delay_ms=2000, jitter=0.25)

        delays = [config.compute_delay_ms(1) for _ in range(50)]
        base = 2000
        jitter_range = int(base * 0.25)  # 500

        assert all(base - jitter_range <= d <= base + jitter_range for d in delays)
        # 至少有一些变化（抖动生效）
        assert len(set(delays)) > 1

    def test_retryable_codes(self):
        """测试默认可重试状态码"""
        config = RetryConfig()
        assert 429 in config.retryable_http_codes
        assert 502 in config.retryable_http_codes
        assert 503 in config.retryable_http_codes
        assert 504 in config.retryable_http_codes
        assert 401 not in config.retryable_http_codes
        assert 400 not in config.retryable_http_codes


class TestLLMCallRetrier:
    """测试 LLMCallRetrier 基础功能"""

    @pytest.mark.asyncio
    async def test_success_on_first_attempt(self):
        """第一次成功"""
        retrier = LLMCallRetrier(RetryConfig(max_retries=2, base_delay_ms=100))

        async def success_fn():
            return "ok"

        result = await retrier.call_with_retry(success_fn)
        assert result == "ok"

    @pytest.mark.asyncio
    async def test_retry_then_success(self):
        """重试后成功"""
        retrier = LLMCallRetrier(RetryConfig(max_retries=3, base_delay_ms=100))

        call_count = 0

        async def flaky_fn():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                exc = Exception("503 Service Unavailable")
                exc.status_code = 503
                raise exc
            return "ok"

        result = await retrier.call_with_retry(flaky_fn)
        assert result == "ok"
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_custom_is_retryable(self):
        """自定义 is_retryable 函数"""
        retrier = LLMCallRetrier(RetryConfig(max_retries=2, base_delay_ms=100))

        call_count = 0

        async def special_fn():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ValueError("special error")
            return "ok"

        # 自定义：ValueError 可重试
        def retry_value_error(exc):
            return isinstance(exc, ValueError)

        result = await retrier.call_with_retry(
            special_fn, is_retryable=retry_value_error
        )
        assert result == "ok"
        assert call_count == 2
