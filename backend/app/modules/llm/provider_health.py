"""
Provider 健康检查 + 自动故障转移链

支持：
- Provider 预检：启动时 / 按需检查 LLM provider 可用性
- 回退链：主 → 备1 → 备2 自动切换
- 结果缓存（TTL 60s）
- 失败标记 + 临时降级

借鉴 claw-code preflight check + fallback chain 模式。
"""

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# HealthStatus
# ---------------------------------------------------------------------------


@dataclass
class HealthStatus:
    """Provider 健康检查结果"""

    provider_id: str
    provider_type: str  # openai / anthropic / azure / custom
    model_name: str
    healthy: bool
    latency_ms: float = 0.0
    error_msg: str | None = None
    checked_at: str | None = None  # ISO 8601

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "provider_type": self.provider_type,
            "model_name": self.model_name,
            "healthy": self.healthy,
            "latency_ms": round(self.latency_ms, 1),
            "error_msg": self.error_msg,
            "checked_at": self.checked_at,
        }


# ---------------------------------------------------------------------------
# ProviderHealthChecker
# ---------------------------------------------------------------------------


class ProviderHealthChecker:
    """Provider 健康检查器

    用法:
        checker = ProviderHealthChecker()
        status = await checker.check_provider(
            provider_id="openai-gpt4o",
            provider_type="openai",
            base_url="https://api.openai.com/v1",
            api_key="sk-...",
        )
    """

    # 各 provider 类型的健康探测端点
    PROBE_ENDPOINTS = {
        "openai": "/models",
        "azure": "/models?api-version=2024-02-15-preview",
        "custom": "/models",
    }
    # Anthropic 没有 /models 端点，使用基础 connectivity probe
    ANTHROPIC_PROBE_URL = "https://api.anthropic.com"

    def __init__(self, timeout: float = 10.0, cache_ttl: float = 60.0) -> None:
        """
        Args:
            timeout: 单次探测超时（秒）
            cache_ttl: 结果缓存 TTL（秒）
        """
        self._timeout = timeout
        self._cache_ttl = cache_ttl
        self._cache: dict[str, tuple[float, HealthStatus]] = {}

    # ------------------------------------------------------------------
    # 单个检查
    # ------------------------------------------------------------------

    async def check_provider(
        self,
        provider_id: str,
        provider_type: str = "openai",
        base_url: str | None = None,
        api_key: str | None = None,
        model_name: str = "",
    ) -> HealthStatus:
        """检查单个 provider 的健康状态

        对 OpenAI-compatible provider 发 GET /v1/models
        对 Anthropic 发 connectivity probe
        """
        cache_key = f"{provider_type}:{base_url or ''}"
        if cache_key in self._cache:
            cached_at, cached_status = self._cache[cache_key]
            if time.monotonic() - cached_at < self._cache_ttl:
                return cached_status

        start = time.monotonic()
        status = HealthStatus(
            provider_id=provider_id,
            provider_type=provider_type,
            model_name=model_name,
            healthy=False,
        )

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                if provider_type == "anthropic":
                    status = await self._probe_anthropic(client, status, api_key)
                else:
                    url = self._build_probe_url(provider_type, base_url)
                    status = await self._probe_openai_compatible(
                        client, url, status, api_key
                    )

        except httpx.TimeoutException:
            status.error_msg = f"timeout after {self._timeout}s"
        except Exception as exc:
            status.error_msg = str(exc)
            logger.debug(f"[ProviderHealth] {provider_id} probe error: {exc}")

        status.latency_ms = (time.monotonic() - start) * 1000
        status.checked_at = datetime.now(timezone.utc).isoformat()

        self._cache[cache_key] = (time.monotonic(), status)
        return status

    # ------------------------------------------------------------------
    # 批量检查
    # ------------------------------------------------------------------

    async def check_all(
        self,
        configs: list[dict[str, Any]],
    ) -> dict[str, HealthStatus]:
        """批量检查所有 provider

        Args:
            configs: 列表，每项包含 provider_id, provider_type, base_url, api_key, model_name

        Returns:
            dict: provider_id -> HealthStatus
        """
        tasks = []
        for cfg in configs:
            tasks.append(
                self.check_provider(
                    provider_id=cfg.get("provider_id", cfg.get("name", "")),
                    provider_type=cfg.get(
                        "provider_type", cfg.get("provider", "openai")
                    ),
                    base_url=cfg.get("base_url"),
                    api_key=cfg.get("api_key"),
                    model_name=cfg.get("model_name", cfg.get("model", "")),
                )
            )

        results = await asyncio.gather(*tasks, return_exceptions=True)

        statuses: dict[str, HealthStatus] = {}
        for cfg, result in zip(configs, results, strict=False):
            pid = cfg.get("provider_id", cfg.get("name", ""))
            if isinstance(result, Exception):
                statuses[pid] = HealthStatus(
                    provider_id=pid,
                    provider_type=cfg.get("provider_type", cfg.get("provider", "")),
                    model_name=cfg.get("model_name", ""),
                    healthy=False,
                    error_msg=str(result),
                    checked_at=datetime.now(timezone.utc).isoformat(),
                )
            else:
                statuses[pid] = result

        return statuses

    # ------------------------------------------------------------------
    # 缓存管理
    # ------------------------------------------------------------------

    def clear_cache(self) -> None:
        """清除健康检查缓存"""
        self._cache.clear()

    def invalidate(
        self, provider_id: str, provider_type: str, base_url: str | None = None
    ) -> None:
        """使特定 provider 的缓存失效"""
        cache_key = f"{provider_type}:{base_url or ''}"
        self._cache.pop(cache_key, None)

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _build_probe_url(self, provider_type: str, base_url: str | None) -> str:
        """构建探测 URL"""
        base = (base_url or "").rstrip("/")
        if not base:
            if provider_type == "openai":
                base = "https://api.openai.com/v1"
            elif provider_type == "azure":
                base = ""  # Azure 必须有自定义 URL
        endpoint = self.PROBE_ENDPOINTS.get(provider_type, "/models")
        return f"{base}{endpoint}"

    async def _probe_openai_compatible(
        self,
        client: httpx.AsyncClient,
        url: str,
        status: HealthStatus,
        api_key: str | None,
    ) -> HealthStatus:
        """探测 OpenAI-compatible endpoint"""
        headers: dict[str, str] = {}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        resp = await client.get(url, headers=headers)
        if resp.status_code < 500:
            # 200 OK 或 401（认证失败但端点可达）都算"可达"
            status.healthy = True
            if resp.status_code == 401:
                status.error_msg = "endpoint reachable but auth failed (401)"
        else:
            status.error_msg = f"HTTP {resp.status_code}"
        return status

    async def _probe_anthropic(
        self,
        client: httpx.AsyncClient,
        status: HealthStatus,
        api_key: str | None,
    ) -> HealthStatus:
        """探测 Anthropic endpoint（无 /models 端点，用基础连通性）"""
        headers: dict[str, str] = {}
        if api_key:
            headers["x-api-key"] = api_key
            headers["anthropic-version"] = "2023-06-01"

        resp = await client.get(self.ANTHROPIC_PROBE_URL, headers=headers)
        if resp.status_code < 500:
            status.healthy = True
            if resp.status_code == 401:
                status.error_msg = "endpoint reachable but auth failed (401)"
        else:
            status.error_msg = f"HTTP {resp.status_code}"
        return status


# ---------------------------------------------------------------------------
# ProviderFallbackChain
# ---------------------------------------------------------------------------


@dataclass
class FallbackEntry:
    """回退链中的一项"""

    provider_id: str
    provider_type: str
    base_url: str | None = None
    api_key: str | None = None
    model_name: str = ""
    priority: int = 0  # 越小越优先
    disabled: bool = False


class ProviderFallbackChain:
    """Provider 自动故障转移链

    用法:
        chain = ProviderFallbackChain([
            FallbackEntry(provider_id="claude", provider_type="anthropic", priority=0),
            FallbackEntry(provider_id="gpt4o", provider_type="openai", priority=1),
        ])

        # 获取当前健康的 provider
        entry = await chain.get_healthy_entry()

        # 标记失败
        chain.record_failure("claude")
    """

    def __init__(
        self,
        entries: list[FallbackEntry],
        health_checker: ProviderHealthChecker | None = None,
        cooldown_seconds: float = 30.0,
    ) -> None:
        self._entries = sorted(entries, key=lambda e: e.priority)
        self._checker = health_checker or ProviderHealthChecker()
        self._cooldown = cooldown_seconds
        self._failed_until: dict[str, float] = {}  # provider_id -> 冷却结束时间戳

    @property
    def entries(self) -> list[FallbackEntry]:
        """返回排序后的回退链列表（不含 disabled）"""
        return [e for e in self._entries if not e.disabled]

    # ------------------------------------------------------------------
    # 核心方法
    # ------------------------------------------------------------------

    async def get_healthy_entry(
        self,
        preferred: str | None = None,
    ) -> FallbackEntry:
        """获取第一个健康的 provider entry

        如果指定 preferred，优先检查该 provider；不可用则按优先级遍历。

        Raises:
            RuntimeError: 所有 provider 均不可用
        """
        # 如果指定 preferred，排到最前面
        ordered = list(self.entries)
        if preferred:
            preferred_entries = [e for e in ordered if e.provider_id == preferred]
            other_entries = [e for e in ordered if e.provider_id != preferred]
            ordered = preferred_entries + other_entries

        errors: list[str] = []
        now = time.monotonic()

        for entry in ordered:
            # 检查冷却期
            if entry.provider_id in self._failed_until:
                if now < self._failed_until[entry.provider_id]:
                    errors.append(f"{entry.provider_id}: in cooldown")
                    continue
                else:
                    del self._failed_until[entry.provider_id]

            # 健康检查
            status = await self._checker.check_provider(
                provider_id=entry.provider_id,
                provider_type=entry.provider_type,
                base_url=entry.base_url,
                api_key=entry.api_key,
                model_name=entry.model_name,
            )

            if status.healthy:
                logger.info(
                    f"[FallbackChain] selected {entry.provider_id} "
                    f"(type={entry.provider_type}, latency={status.latency_ms:.0f}ms)"
                )
                return entry

            errors.append(f"{entry.provider_id}: {status.error_msg or 'unhealthy'}")
            self._failed_until[entry.provider_id] = now + self._cooldown

        raise RuntimeError(f"All providers unavailable: {'; '.join(errors)}")

    async def check_all_health(self) -> dict[str, HealthStatus]:
        """检查所有 provider 健康状态"""
        configs = [
            {
                "provider_id": e.provider_id,
                "provider_type": e.provider_type,
                "base_url": e.base_url,
                "api_key": e.api_key,
                "model_name": e.model_name,
            }
            for e in self.entries
        ]
        return await self._checker.check_all(configs)

    def record_failure(self, provider_id: str) -> None:
        """手动标记 provider 失败（冷却期内不会选中）"""
        self._failed_until[provider_id] = time.monotonic() + self._cooldown
        logger.warning(
            f"[FallbackChain] {provider_id} marked failed, cooldown {self._cooldown}s"
        )

    def reset(self) -> None:
        """重置所有冷却和失败标记"""
        self._failed_until.clear()
        self._checker.clear_cache()
        logger.info("[FallbackChain] all state reset")

    def disable(self, provider_id: str) -> None:
        """从回退链中禁用 provider"""
        for entry in self._entries:
            if entry.provider_id == provider_id:
                entry.disabled = True
                logger.info(f"[FallbackChain] {provider_id} disabled")
                return

    def enable(self, provider_id: str) -> None:
        """重新启用 provider"""
        for entry in self._entries:
            if entry.provider_id == provider_id:
                entry.disabled = False
                logger.info(f"[FallbackChain] {provider_id} enabled")
                return


# ---------------------------------------------------------------------------
# 启动预检
# ---------------------------------------------------------------------------


async def run_startup_preflight(
    db_session_factory: Callable | None = None,
) -> list[HealthStatus]:
    """启动时预检所有已配置的 AI model provider

    从数据库读取 is_active=True 的 AIModelConfig，逐个探测连通性。

    Args:
        db_session_factory: async DB session 工厂（可选，默认使用 app.core.database.async_session_maker）

    Returns:
        所有 provider 的健康状态列表
    """
    try:
        if db_session_factory is None:
            from app.core.database import async_session_maker

            db_session_factory = async_session_maker
    except ImportError:
        logger.warning(
            "[Preflight] cannot import database; skipping provider preflight"
        )
        return []

    checker = ProviderHealthChecker(timeout=10.0, cache_ttl=60.0)

    try:
        async with db_session_factory() as session:
            from sqlalchemy import select

            from app.models.models import AIModelConfig

            result = await session.execute(
                select(AIModelConfig).where(AIModelConfig.is_active)
            )
            configs = result.scalars().all()
    except Exception as exc:
        logger.error(f"[Preflight] failed to load provider configs: {exc}")
        return []

    configs_list = [
        {
            "provider_id": cfg.name,
            "provider_type": cfg.provider,
            "base_url": cfg.base_url,
            "api_key": cfg.api_key,
            "model_name": cfg.model_name,
        }
        for cfg in configs
    ]

    statuses = await checker.check_all(configs_list)

    healthy_count = sum(1 for s in statuses.values() if s.healthy)
    unhealthy = [pid for pid, s in statuses.items() if not s.healthy]

    if unhealthy:
        logger.warning(
            f"[Preflight] {healthy_count}/{len(statuses)} providers healthy. "
            f"Unhealthy: {unhealthy}"
        )
    else:
        logger.info(f"[Preflight] All {len(statuses)} provider(s) healthy")

    return list(statuses.values())
