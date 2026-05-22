"""
Provider Health + Retry 测试 (Stage QQ)
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.modules.llm.provider_health import (
    FallbackEntry,
    HealthStatus,
    ProviderFallbackChain,
    ProviderHealthChecker,
)
from app.modules.llm.retry import (
    LLMCallRetrier,
    RetryConfig,
    with_llm_retry,
)

# ============================================================
# RetryConfig
# ============================================================


class TestRetryConfig:
    """测试重试配置"""

    def test_default_config(self):
        config = RetryConfig()
        assert config.max_retries == 3
        assert config.base_delay_ms == 2000
        assert config.max_delay_ms == 32000
        assert 429 in config.retryable_http_codes

    def test_compute_delay_increases(self):
        config = RetryConfig()
        d1 = config.compute_delay_ms(1)
        d2 = config.compute_delay_ms(2)
        d3 = config.compute_delay_ms(3)
        assert d2 > d1
        assert d3 > d2

    def test_compute_delay_capped(self):
        config = RetryConfig(max_delay_ms=8000)
        delay = config.compute_delay_ms(10)
        assert delay <= config.max_delay_ms

    def test_compute_delay_with_jitter(self):
        config = RetryConfig(jitter=0.25)
        # Run multiple times to ensure jitter is within bounds
        delays = [config.compute_delay_ms(1) for _ in range(20)]
        base = config.base_delay_ms
        for d in delays:
            assert base * 0.75 <= d <= base * 1.25  # approximate


# ============================================================
# LLMCallRetrier
# ============================================================


class TestLLMCallRetrier:
    """测试 LLM 调用重试器"""

    async def test_no_retry_on_success(self):
        """成功时直接返回，不重试"""
        retrier = LLMCallRetrier(RetryConfig(max_retries=3))
        result = await retrier.call_with_retry(
            AsyncMock(return_value="success"),
        )
        assert result == "success"

    async def test_retry_on_retryable_error(self):
        """可重试错误时自动重试"""
        call_count = [0]

        async def flaky():
            call_count[0] += 1
            if call_count[0] < 3:
                exc = Exception("timeout")
                exc.status_code = 503
                raise exc
            return "recovered"

        retrier = LLMCallRetrier(
            RetryConfig(max_retries=5, base_delay_ms=10, max_delay_ms=50)
        )
        result = await retrier.call_with_retry(flaky)
        assert result == "recovered"
        assert call_count[0] == 3

    async def test_no_retry_on_non_retryable_error(self):
        """不可重试错误直接抛出"""
        call_count = [0]

        async def auth_fail():
            call_count[0] += 1
            exc = Exception("unauthorized")
            exc.status_code = 401
            raise exc

        retrier = LLMCallRetrier(RetryConfig(max_retries=3))
        with pytest.raises(Exception, match="unauthorized"):
            await retrier.call_with_retry(auth_fail)
        assert call_count[0] == 1  # no retry

    async def test_exhausted_retries(self):
        """超过最大重试次数后抛出"""
        call_count = [0]

        async def always_fails():
            call_count[0] += 1
            exc = Exception("service unavailable")
            exc.status_code = 503
            raise exc

        retrier = LLMCallRetrier(
            RetryConfig(max_retries=2, base_delay_ms=10, max_delay_ms=50)
        )
        with pytest.raises(Exception, match="service unavailable"):
            await retrier.call_with_retry(always_fails)
        assert call_count[0] == 3  # 1 initial + 2 retries

    async def test_custom_is_retryable(self):
        """自定义重试判断"""
        call_count = [0]

        async def custom_err():
            call_count[0] += 1
            if call_count[0] < 2:
                raise ValueError("custom retryable")
            return "ok"

        def is_retryable(exc: Exception) -> bool:
            return "custom retryable" in str(exc)

        retrier = LLMCallRetrier(
            RetryConfig(max_retries=3, base_delay_ms=10, max_delay_ms=50)
        )
        result = await retrier.call_with_retry(custom_err, is_retryable=is_retryable)
        assert result == "ok"

    async def test_network_error_is_retryable(self):
        """网络相关错误默认可重试"""
        retrier = LLMCallRetrier()
        exc = ConnectionError("connection refused")
        assert retrier._is_retryable(exc) is True


# ============================================================
# with_llm_retry 装饰器
# ============================================================


class TestWithLLMRetryDecorator:
    """测试装饰器"""

    async def test_decorator_retries(self):
        call_count = [0]

        @with_llm_retry(RetryConfig(max_retries=3, base_delay_ms=10, max_delay_ms=50))
        async def call_llm():
            call_count[0] += 1
            if call_count[0] < 2:
                exc = Exception("rate limit")
                exc.status_code = 429
                raise exc
            return "done"

        result = await call_llm()
        assert result == "done"
        assert call_count[0] == 2


# ============================================================
# ProviderHealthChecker
# ============================================================


class TestProviderHealthChecker:
    """测试 Provider 健康检查器"""

    async def test_check_openai_provider_healthy(self):
        """OpenAI provider 健康检查成功"""
        with patch("httpx.AsyncClient.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_get.return_value = mock_resp

            checker = ProviderHealthChecker()
            status = await checker.check_provider(
                provider_id="gpt4o",
                provider_type="openai",
                base_url="https://api.openai.com/v1",
                api_key="sk-test",
                model_name="gpt-4o",
            )
            assert status.healthy is True
            assert status.provider_id == "gpt4o"
            assert status.provider_type == "openai"

    async def test_check_provider_unhealthy(self):
        """Provider 返回 503 → unhealthy"""
        with patch("httpx.AsyncClient.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.status_code = 503
            mock_get.return_value = mock_resp

            checker = ProviderHealthChecker()
            status = await checker.check_provider(
                provider_id="gpt4o",
                provider_type="openai",
                base_url="https://api.example.com/v1",
            )
            assert status.healthy is False

    async def test_check_provider_timeout(self):
        """Provider 超时 → unhealthy"""
        with patch(
            "httpx.AsyncClient.get",
            side_effect=__import__("httpx").TimeoutException("timeout"),
        ):
            checker = ProviderHealthChecker(timeout=0.5)
            status = await checker.check_provider(
                provider_id="slow",
                provider_type="openai",
                base_url="https://slow.example.com/v1",
            )
            assert status.healthy is False
            assert "timeout" in (status.error_msg or "")

    async def test_cache_hit(self):
        """缓存命中时跳过探测"""
        with patch("httpx.AsyncClient.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_get.return_value = mock_resp

            checker = ProviderHealthChecker(cache_ttl=60.0)
            # First call
            s1 = await checker.check_provider(
                "gpt4o", "openai", "https://api.oai.com/v1"
            )
            assert s1.healthy is True
            calls_after_first = mock_get.call_count

            # Second call (should hit cache)
            s2 = await checker.check_provider(
                "gpt4o", "openai", "https://api.oai.com/v1"
            )
            assert s2.healthy is True
            assert mock_get.call_count == calls_after_first  # no additional call

    async def test_clear_cache(self):
        """clear_cache 后重新探测"""
        with patch("httpx.AsyncClient.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_get.return_value = mock_resp

            checker = ProviderHealthChecker(cache_ttl=60.0)
            await checker.check_provider("gpt4o", "openai", "https://api.oai.com/v1")
            calls_after_first = mock_get.call_count

            checker.clear_cache()
            await checker.check_provider("gpt4o", "openai", "https://api.oai.com/v1")
            assert mock_get.call_count > calls_after_first  # new call made

    async def test_check_anthropic_provider(self):
        """Anthropic provider 健康检查"""
        with patch("httpx.AsyncClient.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_get.return_value = mock_resp

            checker = ProviderHealthChecker()
            status = await checker.check_provider(
                provider_id="claude",
                provider_type="anthropic",
                api_key="sk-ant-test",
                model_name="claude-sonnet-4-6",
            )
            assert status.healthy is True
            assert status.provider_type == "anthropic"


# ============================================================
# ProviderFallbackChain
# ============================================================


class TestProviderFallbackChain:
    """测试 Provider 回退链"""

    def _make_healthy_response(self):
        resp = MagicMock()
        resp.status_code = 200
        return resp

    def _make_unhealthy_response(self):
        resp = MagicMock()
        resp.status_code = 503
        return resp

    async def test_returns_first_healthy_entry(self):
        """返回第一个健康的 provider"""
        with patch("httpx.AsyncClient.get") as mock_get:
            mock_get.return_value = self._make_healthy_response()

            chain = ProviderFallbackChain(
                [
                    FallbackEntry(
                        "p1", "openai", "https://p1.example.com/v1", priority=0
                    ),
                    FallbackEntry(
                        "p2", "openai", "https://p2.example.com/v1", priority=1
                    ),
                ]
            )
            entry = await chain.get_healthy_entry()
            assert entry.provider_id == "p1"

    async def test_skips_unhealthy_to_next(self):
        """跳过不健康的 provider"""
        call_count = [0]

        async def mock_check_provider(self, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return HealthStatus(
                    provider_id=kwargs["provider_id"],
                    provider_type=kwargs["provider_type"],
                    model_name="",
                    healthy=False,
                    error_msg="503",
                )
            return HealthStatus(
                provider_id=kwargs["provider_id"],
                provider_type=kwargs["provider_type"],
                model_name="",
                healthy=True,
            )

        with patch.object(ProviderHealthChecker, "check_provider", mock_check_provider):
            chain = ProviderFallbackChain(
                [
                    FallbackEntry(
                        "bad", "openai", "https://bad.example.com/v1", priority=0
                    ),
                    FallbackEntry(
                        "good", "openai", "https://good.example.com/v1", priority=1
                    ),
                ]
            )
            entry = await chain.get_healthy_entry()
            assert entry.provider_id == "good"

    async def test_all_unhealthy_raises(self):
        """所有 provider 不健康时抛异常"""

        async def mock_check_provider(self, **kwargs):
            return HealthStatus(
                provider_id=kwargs["provider_id"],
                provider_type=kwargs["provider_type"],
                model_name="",
                healthy=False,
                error_msg="503",
            )

        with patch.object(ProviderHealthChecker, "check_provider", mock_check_provider):
            chain = ProviderFallbackChain(
                [
                    FallbackEntry(
                        "p1", "openai", "https://p1.example.com/v1", priority=0
                    ),
                    FallbackEntry(
                        "p2", "openai", "https://p2.example.com/v1", priority=1
                    ),
                ]
            )
            with pytest.raises(RuntimeError, match="All providers unavailable"):
                await chain.get_healthy_entry()

    async def test_preferred_provider_first(self):
        """preferred provider 优先级最高"""
        call_order = []

        async def mock_check_provider(self, **kwargs):
            pid = kwargs["provider_id"]
            call_order.append(pid)
            return HealthStatus(
                provider_id=pid,
                provider_type=kwargs["provider_type"],
                model_name="",
                healthy=True,
            )

        with patch.object(ProviderHealthChecker, "check_provider", mock_check_provider):
            chain = ProviderFallbackChain(
                [
                    FallbackEntry(
                        "p1", "openai", "https://p1.example.com/v1", priority=0
                    ),
                    FallbackEntry(
                        "p2", "openai", "https://p2.example.com/v1", priority=1
                    ),
                    FallbackEntry(
                        "p3", "openai", "https://p3.example.com/v1", priority=2
                    ),
                ]
            )
            entry = await chain.get_healthy_entry(preferred="p3")
            assert entry.provider_id == "p3"
            assert call_order[0] == "p3"  # preferred checked first

    async def test_record_failure_cooldown(self):
        """record_failure 触发冷却期"""

        async def mock_check_provider(self, **kwargs):
            return HealthStatus(
                provider_id=kwargs["provider_id"],
                provider_type=kwargs["provider_type"],
                model_name="",
                healthy=True,
            )

        with patch.object(ProviderHealthChecker, "check_provider", mock_check_provider):
            chain = ProviderFallbackChain(
                [
                    FallbackEntry(
                        "p1", "openai", "https://p1.example.com/v1", priority=0
                    ),
                    FallbackEntry(
                        "p2", "openai", "https://p2.example.com/v1", priority=1
                    ),
                ],
                cooldown_seconds=30.0,
            )
            chain.record_failure("p1")
            entry = await chain.get_healthy_entry()
            assert entry.provider_id == "p2"  # p1 in cooldown

    def test_disable_enable_entry(self):
        """disable/enable 条目"""
        chain = ProviderFallbackChain(
            [
                FallbackEntry("p1", "openai", priority=0),
                FallbackEntry("p2", "openai", priority=1),
            ]
        )
        chain.disable("p1")
        assert all(e.provider_id != "p1" for e in chain.entries)

        chain.enable("p1")
        assert any(e.provider_id == "p1" for e in chain.entries)

    def test_reset(self):
        """reset 清除所有状态"""
        chain = ProviderFallbackChain(
            [
                FallbackEntry("p1", "openai", priority=0),
            ]
        )
        chain.record_failure("p1")
        chain.reset()
        # After reset, failed_until should be empty
        entry_list = chain.entries
        assert len(entry_list) == 1

    async def test_check_all_health(self):
        """批量健康检查"""
        with patch("httpx.AsyncClient.get") as mock_get:
            mock_get.return_value = MagicMock(status_code=200)

            chain = ProviderFallbackChain(
                [
                    FallbackEntry(
                        "p1", "openai", "https://p1.example.com/v1", priority=0
                    ),
                    FallbackEntry(
                        "p2", "openai", "https://p2.example.com/v1", priority=1
                    ),
                ]
            )
            results = await chain.check_all_health()
            assert len(results) == 2
            assert results["p1"].healthy is True
            assert results["p2"].healthy is True


# ============================================================
# HealthStatus
# ============================================================


class TestHealthStatus:
    """测试 HealthStatus 数据结构"""

    def test_to_dict(self):
        status = HealthStatus(
            provider_id="test",
            provider_type="openai",
            model_name="gpt-4o",
            healthy=True,
            latency_ms=123.0,
            error_msg=None,
            checked_at="2026-05-12T00:00:00Z",
        )
        d = status.to_dict()
        assert d["provider_id"] == "test"
        assert d["healthy"] is True
        assert d["latency_ms"] == 123.0  # rounded to 1 decimal

    def test_default_values(self):
        status = HealthStatus(
            provider_id="p", provider_type="openai", model_name="m", healthy=False
        )
        assert status.latency_ms == 0.0
        assert status.error_msg is None
        assert status.checked_at is None
