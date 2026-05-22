"""
DeferredInit — 通用延迟初始化模式

首次访问时才创建资源，支持：
- 超时控制（timeout）
- 失败重试（max_retries + 指数退避）
- 并发安全（asyncio.Lock）
- 重置（强制重新初始化）

借鉴 claw-code 的 deferred 模式。
"""

import asyncio
import logging
import random
import time
from collections.abc import Callable
from typing import Any, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")
_SENTINEL = object()


class DeferredInitError(Exception):
    """延迟初始化失败"""

    def __init__(self, name: str, attempts: int, last_error: Exception | None = None):
        self.name = name
        self.attempts = attempts
        self.last_error = last_error
        super().__init__(
            f"Deferred resource '{name}' failed after {attempts} attempt(s)"
            + (f": {last_error}" if last_error else "")
        )


class DeferredInit:
    """延迟初始化容器

    用法:
        embedding = DeferredInit(
            factory=lambda: SentenceTransformer("BAAI/bge-small-zh-v1.5"),
            name="embedding_model",
            timeout=60.0,
            max_retries=2,
        )

        model = await embedding.get()
    """

    def __init__(
        self,
        factory: Callable[..., Any],
        name: str = "deferred",
        timeout: float = 30.0,
        max_retries: int = 2,
    ) -> None:
        """
        Args:
            factory: 工厂函数（sync 或 async），在首次 get() 时调用
            name: 资源名称（用于日志和错误信息）
            timeout: 单次初始化超时（秒）
            max_retries: 失败后最大重试次数（0 = 不重试）
        """
        self._factory = factory
        self._name = name
        self._timeout = timeout
        self._max_retries = max_retries
        self._value: Any = _SENTINEL
        self._initialized = False
        self._lock = asyncio.Lock()
        self._error: str | None = None

    # ------------------------------------------------------------------
    # 属性
    # ------------------------------------------------------------------

    @property
    def is_ready(self) -> bool:
        """资源是否已成功初始化"""
        return self._initialized and self._value is not _SENTINEL

    @property
    def error(self) -> str | None:
        """上次初始化的错误信息"""
        return self._error

    @property
    def name(self) -> str:
        return self._name

    # ------------------------------------------------------------------
    # 核心方法
    # ------------------------------------------------------------------

    async def get(self) -> Any:
        """获取资源（首次访问时自动初始化）

        并发安全：多个协程同时调用 get() 时，只有第一个触发初始化，
        其余等待锁释放后直接返回已初始化的值。

        Raises:
            DeferredInitError: 所有重试均失败
        """
        if self._initialized and self._value is not _SENTINEL:
            return self._value

        async with self._lock:
            # 双重检查（锁内）
            if self._initialized and self._value is not _SENTINEL:
                return self._value

            last_error: Exception | None = None
            for attempt in range(1, self._max_retries + 2):
                try:
                    result = await asyncio.wait_for(
                        self._call_factory(),
                        timeout=self._timeout,
                    )
                    self._value = result
                    self._initialized = True
                    self._error = None
                    logger.info(
                        f"[DeferredInit] '{self._name}' initialized successfully"
                        + (f" (attempt {attempt})" if attempt > 1 else "")
                    )
                    return result

                except asyncio.TimeoutError:
                    last_error = TimeoutError(
                        f"Deferred resource '{self._name}' timed out "
                        f"after {self._timeout}s (attempt {attempt})"
                    )
                    logger.warning(f"[DeferredInit] {last_error}")

                except Exception as exc:
                    last_error = exc
                    logger.warning(
                        f"[DeferredInit] '{self._name}' failed "
                        f"(attempt {attempt}/{self._max_retries + 1}): {exc}"
                    )

                if attempt <= self._max_retries:
                    delay = self._compute_backoff_ms(attempt) / 1000.0
                    logger.info(
                        f"[DeferredInit] '{self._name}' retrying in {delay:.1f}s"
                    )
                    await asyncio.sleep(delay)

            # 所有重试耗尽
            self._error = str(last_error) if last_error else "unknown"
            raise DeferredInitError(
                name=self._name,
                attempts=self._max_retries + 1,
                last_error=last_error,
            )

    def get_sync(self) -> Any:
        """同步获取资源（用于非 async 上下文）

        注意：如果工厂函数是 async 的，此方法将直接抛错（不重试）。
        """
        if self._initialized and self._value is not _SENTINEL:
            return self._value

        # 检查工厂是否为 async（不重试，直接报错）
        result = self._factory()
        if asyncio.iscoroutine(result):
            result.close()  # 清理 coroutine 避免 warning
            raise RuntimeError(
                f"Deferred resource '{self._name}' has async factory, "
                f"use get() instead of get_sync()"
            )

        last_error: Exception | None = None
        for attempt in range(1, self._max_retries + 2):
            try:
                self._value = result if attempt == 1 else self._factory()
                if asyncio.iscoroutine(self._value):
                    self._value.close()
                    raise RuntimeError(
                        f"Deferred resource '{self._name}' has async factory"
                    )
                self._initialized = True
                self._error = None
                return self._value
            except Exception as exc:
                last_error = exc
                logger.warning(
                    f"[DeferredInit] '{self._name}' sync init failed "
                    f"(attempt {attempt}/{self._max_retries + 1}): {exc}"
                )
                if attempt <= self._max_retries:
                    time.sleep(self._compute_backoff_ms(attempt) / 1000.0)

        self._error = str(last_error) if last_error else "unknown"
        raise DeferredInitError(
            name=self._name,
            attempts=self._max_retries + 1,
            last_error=last_error,
        )

    def reset(self) -> None:
        """重置资源（下次 get() 将重新初始化）"""
        self._value = _SENTINEL
        self._initialized = False
        self._error = None
        logger.info(f"[DeferredInit] '{self._name}' reset")

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    async def _call_factory(self) -> Any:
        """调用工厂函数（支持 sync 和 async）"""
        result = self._factory()
        if asyncio.iscoroutine(result):
            result = await result
        return result

    def _call_factory_sync(self) -> Any:
        """同步调用工厂函数"""
        result = self._factory()
        if asyncio.iscoroutine(result):
            raise RuntimeError(
                f"Deferred resource '{self._name}' has async factory, "
                f"use get() instead of get_sync()"
            )
        return result

    @staticmethod
    def _compute_backoff_ms(
        attempt: int, base_ms: int = 2000, max_ms: int = 32000
    ) -> int:
        """指数退避 + jitter（与 recovery_recipes 公式一致）"""
        exponential = min(base_ms * (4 ** (attempt - 1)), max_ms)
        jitter = int(exponential * 0.25 * (random.random() * 2 - 1))
        return min(max_ms, max(1000, exponential + jitter))
