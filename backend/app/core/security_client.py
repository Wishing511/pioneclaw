"""
PioneerClaw 安全网关 HTTP Client

通过 HTTP 调用独立安全网关服务，实现 pre_input / post_llm / pre_tool 三个拦截点。
环境变量:
    SECURITY_GATEWAY_URL: 安全网关地址 (默认 http://localhost:8001)
    SECURITY_GATEWAY_ENABLED: 是否启用 (默认 true)
    SECURITY_GATEWAY_TIMEOUT: 超时秒数 (默认 5.0)
"""

import logging
import os
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger(__name__)

SECURITY_GATEWAY_URL = os.getenv("SECURITY_GATEWAY_URL", "http://localhost:8001")
SECURITY_GATEWAY_TIMEOUT = float(os.getenv("SECURITY_GATEWAY_TIMEOUT", "5.0"))


@dataclass
class FilterResult:
    """安全过滤结果"""

    action: str  # allow / block / sanitize / approve
    content: str | None = None
    reason: str | None = None
    risk_level: str = "low"
    matched_rules: list | None = None


class SecurityClient:
    """安全网关 HTTP Client 单例

    所有方法均为异步，支持 fail-open 降级策略。
    """

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._base_url = SECURITY_GATEWAY_URL.rstrip("/")
        self._timeout = SECURITY_GATEWAY_TIMEOUT
        self._enabled = os.getenv("SECURITY_GATEWAY_ENABLED", "true").lower() == "true"
        self._client: httpx.AsyncClient | None = None
        self._initialized = True

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self._timeout),
                limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
            )
        return self._client

    @property
    def enabled(self) -> bool:
        return self._enabled

    def set_enabled(self, value: bool):
        self._enabled = value

    async def filter_input(
        self, text: str, context: dict[str, Any] | None = None
    ) -> FilterResult:
        """pre_input_call: 输入过滤"""
        if not self._enabled:
            return FilterResult(action="allow")

        try:
            client = await self._get_client()
            resp = await client.post(
                f"{self._base_url}/api/v1/filter/input",
                json={"text": text, "context": context or {}},
            )
            resp.raise_for_status()
            data = resp.json()
            return FilterResult(**data)
        except Exception as e:
            logger.error(f"Security gateway filter_input failed: {e}")
            return FilterResult(
                action="allow", reason=f"安全网关异常(降级放行): {str(e)}"
            )

    async def filter_output(
        self, text: str, context: dict[str, Any] | None = None
    ) -> FilterResult:
        """post_llm_call: 输出过滤"""
        if not self._enabled:
            return FilterResult(action="allow")

        try:
            client = await self._get_client()
            resp = await client.post(
                f"{self._base_url}/api/v1/filter/output",
                json={"text": text, "context": context or {}},
            )
            resp.raise_for_status()
            data = resp.json()
            return FilterResult(**data)
        except Exception as e:
            logger.error(f"Security gateway filter_output failed: {e}")
            return FilterResult(
                action="allow", reason=f"安全网关异常(降级放行): {str(e)}"
            )

    async def check_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        context: dict[str, Any] | None = None,
    ) -> FilterResult:
        """pre_tool_call: 工具调用安全检查"""
        if not self._enabled:
            return FilterResult(action="allow")

        try:
            client = await self._get_client()
            resp = await client.post(
                f"{self._base_url}/api/v1/check/tool",
                json={
                    "tool_name": tool_name,
                    "arguments": arguments,
                    "context": context or {},
                },
            )
            resp.raise_for_status()
            data = resp.json()
            return FilterResult(**data)
        except Exception as e:
            logger.error(f"Security gateway check_tool failed: {e}")
            return FilterResult(
                action="allow", reason=f"安全网关异常(降级放行): {str(e)}"
            )

    async def close(self):
        """关闭 HTTP 连接池"""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None


async def apply_input_filter(
    security_client: SecurityClient,
    text: str,
    context: dict[str, Any] | None = None,
) -> tuple[str, dict[str, Any] | None]:
    """统一封装 pre_input_call 安全过滤逻辑

    将重复的 block/approve/sanitize 处理封装为 helper，供各路由复用。

    Returns:
        (filtered_text, error_response): 如果 error_response 非 None，应直接返回该响应
    """
    if not security_client.enabled:
        return text, None

    result = await security_client.filter_input(text, context)

    if result.action == "block":
        return text, {
            "success": False,
            "message": f"安全拦截: {result.reason}",
            "latency_ms": 0,
            "action": "block",
            "reason": result.reason,
            "risk_level": result.risk_level,
        }
    elif result.action == "approve":
        return text, {
            "success": False,
            "message": f"待审批: {result.reason}",
            "latency_ms": 0,
            "action": "approve",
            "reason": result.reason,
            "risk_level": result.risk_level,
        }
    elif result.action == "sanitize" and result.content:
        return result.content, None

    return text, None


# 全局实例
security_client = SecurityClient()
