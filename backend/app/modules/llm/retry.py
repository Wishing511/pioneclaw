"""
LLM 调用重试模块

提供：
- RetryConfig: 可配置的重试策略
- LLMCallRetrier: 可复用的重试器实例
- with_llm_retry: 装饰器，为 LLM 调用添加指数退避重试

重试策略（与 recovery_recipes 退避公式一致）：
- 指数退避：2s → 8s → 32s（base=2s, multiplier=4x）
- 随机抖动：±25% 防止雷鸣群效应
- 区分可重试错误（429, 502, 503, 504）和不可重试错误（401, 403, 400）

借鉴 claw-code retry + exponential backoff 模式。
"""

import asyncio
import functools
import logging
import random
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, TypeVar

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable)


# ---------------------------------------------------------------------------
# RetryConfig
# ---------------------------------------------------------------------------


@dataclass
class RetryConfig:
    """重试配置"""

    max_retries: int = 3
    base_delay_ms: int = 2000  # 首次重试等待 2s
    max_delay_ms: int = 32000  # 最大等待 32s
    jitter: float = 0.25  # 随机抖动比例
    retryable_http_codes: set[int] = field(default_factory=lambda: {429, 502, 503, 504})

    def compute_delay_ms(self, attempt: int) -> int:
        """计算第 attempt 次重试的延迟（指数退避 + jitter）

        attempt=1: ~2s, attempt=2: ~8s, attempt=3: ~32s
        """
        exponential = min(
            self.base_delay_ms * (4 ** (attempt - 1)),
            self.max_delay_ms,
        )
        jitter_range = int(exponential * self.jitter)
        jitter_amount = random.randint(-jitter_range, jitter_range)
        return min(self.max_delay_ms, max(1000, exponential + jitter_amount))


# ---------------------------------------------------------------------------
# LLMCallRetrier
# ---------------------------------------------------------------------------


class LLMCallRetrier:
    """LLM 调用重试器

    用法:
        retrier = LLMCallRetrier(RetryConfig(max_retries=3))
        result = await retrier.call_with_retry(
            my_async_llm_fn, arg1, arg2, kw1=val
        )
    """

    def __init__(self, config: RetryConfig | None = None) -> None:
        self.config = config or RetryConfig()

    async def call_with_retry(
        self,
        fn: Callable[..., Any],
        *args: Any,
        is_retryable: Callable[[Exception], bool] | None = None,
        **kwargs: Any,
    ) -> Any:
        """执行调用并自动重试

        Args:
            fn: 异步可调用对象
            *args, **kwargs: 传递给 fn 的参数
            is_retryable: 自定义判断函数，返回 True 表示该错误可重试；
                          默认：检查 HTTP 状态码是否在 retryable_http_codes 中

        Returns:
            fn 的返回值

        Raises:
            最后一次失败时的异常（所有重试耗尽后）
        """
        last_error: Exception | None = None

        for attempt in range(self.config.max_retries + 1):
            try:
                result = await fn(*args, **kwargs)
                if attempt > 0:
                    logger.info(f"[LLMRetry] succeeded on attempt {attempt + 1}")
                return result

            except Exception as exc:
                last_error = exc

                # 判断是否可重试
                retryable = self._is_retryable(exc, is_retryable)
                if not retryable:
                    logger.debug(f"[LLMRetry] non-retryable error, not retrying: {exc}")
                    raise

                if attempt >= self.config.max_retries:
                    logger.error(
                        f"[LLMRetry] max retries ({self.config.max_retries}) exhausted: {exc}"
                    )
                    raise

                delay_ms = self.config.compute_delay_ms(attempt + 1)
                logger.warning(
                    f"[LLMRetry] attempt {attempt + 1}/{self.config.max_retries + 1} "
                    f"failed, retrying in {delay_ms}ms: {exc}"
                )
                await asyncio.sleep(delay_ms / 1000.0)

        # 不应到达这里
        assert last_error is not None
        raise last_error

    def _is_retryable(
        self,
        exc: Exception,
        custom_fn: Callable[[Exception], bool] | None = None,
    ) -> bool:
        """判断错误是否可重试"""
        if custom_fn is not None:
            return custom_fn(exc)

        # 检查 HTTP 状态码
        status_code = getattr(exc, "status_code", None)
        if status_code is None and hasattr(exc, "response"):
            resp = getattr(exc, "response", None)
            if resp is not None:
                status_code = getattr(resp, "status_code", None)

        if status_code is not None:
            return int(status_code) in self.config.retryable_http_codes

        # httpx.HTTPStatusError
        if hasattr(exc, "response") and hasattr(exc.response, "status_code"):
            return exc.response.status_code in self.config.retryable_http_codes

        # 网络错误通常是可重试的
        error_str = str(exc).lower()
        retryable_keywords = [
            "timeout",
            "connection",
            "reset",
            "refused",
            "too many requests",
            "service unavailable",
        ]
        return any(kw in error_str for kw in retryable_keywords)


# ---------------------------------------------------------------------------
# 装饰器
# ---------------------------------------------------------------------------


def with_llm_retry(config: RetryConfig | None = None):
    """装饰器：为异步 LLM 调用添加指数退避重试

    用法:
        @with_llm_retry(RetryConfig(max_retries=3))
        async def call_llm(messages):
            ...

        # 或使用默认配置
        @with_llm_retry()
        async def call_llm(messages):
            ...
    """
    retrier = LLMCallRetrier(config=config)

    def decorator(func: F) -> F:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            return await retrier.call_with_retry(func, *args, **kwargs)

        return wrapper  # type: ignore[return-value]

    return decorator


# ---------------------------------------------------------------------------
# 便捷函数
# ---------------------------------------------------------------------------


def create_default_retrier() -> LLMCallRetrier:
    """创建默认配置的重试器"""
    return LLMCallRetrier(RetryConfig())
