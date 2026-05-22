"""
PluginLifecycle — 插件生命周期状态机

扩展状态：RETRYING, PAUSED, STOPPING, STOPPED, DISABLED
功能：状态转换验证、转换历史、健康检查、指数退避自动重试
"""

import asyncio
import logging
import random
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 状态转换规则表
# ---------------------------------------------------------------------------

# 允许的转入状态集合（按当前状态）
_ALLOWED_TRANSITIONS: dict[str, set] = {
    "unloaded": {"loading"},
    "loading": {"loaded", "error"},
    "loaded": {"unloading", "paused", "stopping", "disabled", "error"},
    "error": {"loading", "stopping", "disabled", "retrying"},
    "unloading": {"unloaded", "error"},
    # ---- 新增 ----
    "retrying": {"loading", "error", "stopping"},  # error = 超过 max_retries
    "paused": {"loaded", "stopping"},  # resume or stop
    "stopping": {"stopped", "error"},
    "stopped": {"loading", "disabled"},
    "disabled": {"unloaded"},  # re-enable = unloaded then load
}


# ---------------------------------------------------------------------------
# StateTransition
# ---------------------------------------------------------------------------


@dataclass
class StateTransition:
    """一次状态转换记录"""

    from_state: str
    to_state: str
    timestamp: str  # ISO 8601
    reason: str = ""


# ---------------------------------------------------------------------------
# PluginLifecycle
# ---------------------------------------------------------------------------


class PluginLifecycle:
    """插件生命周期管理器

    职责：
    - 校验并执行状态转换
    - 记录转换历史（最多保留 50 条）
    - 健康检查编排
    - 崩溃自动重试（指数退避 + 随机抖动）
    """

    # 指数退避参数（借鉴 recovery_recipes 退避策略）
    RETRY_BASE_MS = 2000  # 首次重试等待 2s
    RETRY_MAX_MS = 32000  # 最大等待 32s
    RETRY_MULTIPLIER = 4  # 2s → 8s → 32s

    def __init__(
        self,
        plugin_id: str,
        max_retries: int = 3,
        health_check_fn: Callable[[], bool] | None = None,
    ) -> None:
        self.plugin_id = plugin_id
        self.state: str = "unloaded"
        self.health_status: bool | None = None
        self.last_health_check: datetime | None = None
        self.retry_count: int = 0
        self.max_retries: int = max_retries
        self.error_history: list[dict[str, Any]] = []
        self._transitions: list[StateTransition] = []
        self._health_check_fn = health_check_fn
        self.paused_at: datetime | None = None
        self.stopped_at: datetime | None = None

    # ------------------------------------------------------------------
    # 属性
    # ------------------------------------------------------------------

    @property
    def transitions(self) -> list[StateTransition]:
        return list(self._transitions)

    @property
    def last_transition(self) -> StateTransition | None:
        return self._transitions[-1] if self._transitions else None

    @property
    def is_healthy(self) -> bool:
        """当前是否健康（最近一次检查结果）"""
        return self.health_status is True

    @property
    def should_auto_restart(self) -> bool:
        """是否应自动重启（RETRYING 状态且未超过最大重试次数）"""
        return self.state == "retrying" and self.retry_count < self.max_retries

    # ------------------------------------------------------------------
    # 状态转换
    # ------------------------------------------------------------------

    def can_transition(self, to_state: str) -> bool:
        """检查从当前状态是否可以转换到目标状态"""
        allowed = _ALLOWED_TRANSITIONS.get(self.state, set())
        return to_state in allowed

    def transition(self, to_state: str, reason: str = "") -> StateTransition:
        """执行状态转换

        Raises:
            ValueError: 非法转换
        """
        if not self.can_transition(to_state):
            raise ValueError(
                f"非法状态转换: {self.plugin_id} {self.state} -> {to_state}"
            )

        from_state = self.state
        self.state = to_state
        t = StateTransition(
            from_state=from_state,
            to_state=to_state,
            timestamp=datetime.now(timezone.utc).isoformat(),
            reason=reason,
        )
        self._transitions.append(t)

        # 修剪历史（保留最近 50 条）
        if len(self._transitions) > 50:
            self._transitions = self._transitions[-50:]

        # 记录错误历史
        if to_state == "error":
            self.error_history.append(
                {
                    "from_state": from_state,
                    "reason": reason,
                    "timestamp": t.timestamp,
                }
            )
            if len(self.error_history) > 20:
                self.error_history = self.error_history[-20:]

        # 状态特有处理
        if to_state == "paused":
            self.paused_at = datetime.now(timezone.utc)
        elif to_state == "stopped":
            self.stopped_at = datetime.now(timezone.utc)
            self.health_status = None
        elif to_state == "loaded":
            self.paused_at = None
            self.stopped_at = None

        logger.info(
            f"[PluginLifecycle] {self.plugin_id}: {from_state} -> {to_state}"
            + (f" ({reason})" if reason else "")
        )

        return t

    def record_error(self, error: str) -> None:
        """记录一次运行时错误（不改变状态）"""
        self.error_history.append(
            {
                "error": error,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )
        if len(self.error_history) > 20:
            self.error_history = self.error_history[-20:]

    # ------------------------------------------------------------------
    # 健康检查
    # ------------------------------------------------------------------

    async def run_health_check(self) -> bool:
        """执行健康检查"""
        self.last_health_check = datetime.now(timezone.utc)

        if self._health_check_fn is None:
            self.health_status = True
            return True

        try:
            result = self._health_check_fn()
            if asyncio.iscoroutine(result):
                result = await result
            self.health_status = bool(result)
        except Exception as exc:
            self.health_status = False
            self.record_error(f"health_check failed: {exc}")
            logger.warning(f"[PluginLifecycle] {self.plugin_id} 健康检查失败: {exc}")

        return self.health_status

    def set_health_check_fn(self, fn: Callable | None) -> None:
        """设置自定义健康检查函数"""
        self._health_check_fn = fn

    # ------------------------------------------------------------------
    # 自动重试
    # ------------------------------------------------------------------

    def compute_retry_delay_ms(self) -> int:
        """计算当前重试等待时间（指数退避 + jitter）"""
        base_ms = self.RETRY_BASE_MS
        exponential = min(
            base_ms * (self.RETRY_MULTIPLIER ** (self.retry_count - 1)),
            self.RETRY_MAX_MS,
        )
        jitter = int(exponential * 0.25 * (random.random() * 2 - 1))
        return min(self.RETRY_MAX_MS, max(1000, exponential + jitter))

    def reset_retry(self) -> None:
        """重置重试计数（加载成功后调用）"""
        self.retry_count = 0

    def to_dict(self) -> dict[str, Any]:
        """序列化为字典，用于 API 响应"""
        return {
            "state": self.state,
            "health_status": self.health_status,
            "last_health_check": (
                self.last_health_check.isoformat() if self.last_health_check else None
            ),
            "retry_count": self.retry_count,
            "max_retries": self.max_retries,
            "error_history": list(self.error_history[-5:]),
            "paused_at": (self.paused_at.isoformat() if self.paused_at else None),
            "stopped_at": (self.stopped_at.isoformat() if self.stopped_at else None),
            "last_transition": (
                {
                    "from": self.last_transition.from_state,
                    "to": self.last_transition.to_state,
                    "timestamp": self.last_transition.timestamp,
                    "reason": self.last_transition.reason,
                }
                if self.last_transition
                else None
            ),
        }
