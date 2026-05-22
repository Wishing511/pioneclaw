"""
Error Recovery Recipes — 借鉴 claw-code recovery_recipes.rs

核心架构:
  FailureScenario → RecoveryStep → RecoveryRecipe → RecoveryExecutor
  一次自动恢复后升级（escalate），防止无限重试

使用示例:
    from app.core.recovery_recipes import (
        RecoverableToolError, ToolTimeoutError, classify_error,
        recipe_for, RecoveryExecutor, RecoveryContext,
    )

    try:
        await tool.execute(...)
    except RecoverableToolError as e:
        scenario = classify_error(e)
        recipe = recipe_for(scenario)
        executor = RecoveryExecutor()
        result = executor.attempt_recovery(scenario, RecoveryContext())
        if result.should_retry:
            await asyncio.sleep(result.wait_ms / 1000)
            ...
"""

import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


# ==================== Recoverable Error Hierarchy ====================


class RecoverableToolError(Exception):
    """可恢复的工具错误基类

    子类化具体错误类型，ToolRegistry 透传这些异常而不字符串化。
    借鉴 claw-code WorkerFailureKind → FailureScenario 的分层设计。
    """

    def __init__(self, message: str, original_error: Exception | None = None):
        super().__init__(message)
        self.original_error = original_error


class ToolTimeoutError(RecoverableToolError):
    """工具执行超时"""

    pass


class ConnectionError_(RecoverableToolError):
    """网络连接错误（httpx.ConnectError, ConnectionRefused 等）"""

    pass


class ApiRateLimitError(RecoverableToolError):
    """API 限流（429 / rate limit / too many requests）"""

    pass


class ProviderFailureError(RecoverableToolError):
    """Provider 内部错误（500/502/503）"""

    pass


class AuthError(RecoverableToolError):
    """认证错误（401 / unauthorized / invalid api key）"""

    pass


class GitLockError(RecoverableToolError):
    """Git 锁文件冲突（.git/index.lock）"""

    pass


class DiskFullError(RecoverableToolError):
    """磁盘空间不足"""

    pass


class FileNotFound_(RecoverableToolError):
    """文件不存在（可让 LLM 修正路径）"""

    def __init__(
        self, message: str, path: str = "", original_error: Exception | None = None
    ):
        super().__init__(message, original_error)
        self.path = path


# ==================== Failure Scenario ====================


class FailureScenario(Enum):
    """失败场景 —— 借鉴 claw-code FailureScenario enum"""

    API_RATE_LIMITED = "api_rate_limited"
    PROVIDER_FAILURE = "provider_failure"
    TOOL_TIMEOUT = "tool_timeout"
    CONNECTION_ERROR = "connection_error"
    GIT_LOCK = "git_lock"
    DISK_FULL = "disk_full"
    AUTH_ERROR = "auth_error"
    FILE_NOT_FOUND = "file_not_found"
    UNKNOWN = "unknown"


# 错误消息正则（字符串匹配 fallback）—— 借鉴 claw-code 的 error classification markers
_ERROR_PATTERNS: dict[FailureScenario, list[str]] = {
    FailureScenario.API_RATE_LIMITED: [
        r"rate.limit",
        r"too many requests",
        r"429",
        r"quota.*exceeded",
        r"insufficient_quota",
        r"capacity.*exceeded",
        r"overloaded",
    ],
    FailureScenario.PROVIDER_FAILURE: [
        r"500.*internal server",
        r"502.*bad gateway",
        r"503.*service unavailable",
        r"server error",
        r"provider.*error",
        r"internal.*error",
    ],
    FailureScenario.TOOL_TIMEOUT: [
        r"timeout",
        r"timed out",
        r"TimeoutError",
    ],
    FailureScenario.CONNECTION_ERROR: [
        r"connection refused",
        r"connection.*error",
        r"connect.*timeout",
        r"dns.*resolve",
        r"name.*resolution",
        r"network.*unreachable",
        r"ConnectError",
        r"ConnectionError",
    ],
    FailureScenario.GIT_LOCK: [
        r"\.git[/\\]index\.lock",
        r"Unable to create.*\.lock.*File exists",
        r"lock.*file.*exists",
    ],
    FailureScenario.DISK_FULL: [
        r"no space left",
        r"disk.*full",
        r"ENOSPC",
        r"insufficient.*disk",
    ],
    FailureScenario.AUTH_ERROR: [
        r"401",
        r"unauthorized",
        r"invalid api key",
        r"authentication.*failed",
        r"invalid.*token",
        r"access denied",
        r"forbidden",
        r"account.*deactivated",
        r"insufficient_quota",
    ],
    FailureScenario.FILE_NOT_FOUND: [
        r"no such file",
        r"file.*not found",
        r"FileNotFoundError",
        r"ENOENT",
        r"cannot find.*file",
    ],
}


def classify_error(error: Exception) -> FailureScenario:
    """分类错误到场景 —— 借鉴 claw-code WorkerFailureKind → FailureScenario

    优先使用显式类型匹配，fallback 到错误消息字符串匹配。
    """
    # 显式类型匹配
    if isinstance(error, ApiRateLimitError):
        return FailureScenario.API_RATE_LIMITED
    if isinstance(error, ProviderFailureError):
        return FailureScenario.PROVIDER_FAILURE
    if isinstance(error, ToolTimeoutError):
        return FailureScenario.TOOL_TIMEOUT
    if isinstance(error, ConnectionError_):
        return FailureScenario.CONNECTION_ERROR
    if isinstance(error, GitLockError):
        return FailureScenario.GIT_LOCK
    if isinstance(error, DiskFullError):
        return FailureScenario.DISK_FULL
    if isinstance(error, AuthError):
        return FailureScenario.AUTH_ERROR
    if isinstance(error, FileNotFound_):
        return FailureScenario.FILE_NOT_FOUND

    # 字符串匹配 fallback（处理被字符串化的错误 / 未显式分类型的异常）
    error_text = str(error).lower()
    for scenario, patterns in _ERROR_PATTERNS.items():
        for pattern in patterns:
            if re.search(pattern, error_text, re.IGNORECASE):
                return scenario

    return FailureScenario.UNKNOWN


# ==================== Recovery Step ====================


class RecoveryStep(Enum):
    """恢复步骤 —— 借鉴 claw-code RecoveryStep enum"""

    WAIT_AND_RETRY = "wait_and_retry"  # 等待后重试
    ROTATE_KEY = "rotate_key"  # 轮换 API key
    AUTO_FIX = "auto_fix"  # 自动修复（安全、无副作用的）
    RETRY_WITH_LONGER_TIMEOUT = "retry_with_longer_timeout"  # 延长超时后重试
    REPORT_TO_LLM = "report_to_llm"  # 报告给 LLM，让 LLM 调整
    ESCALATE_TO_HUMAN = "escalate_to_human"  # 升级到人工处理


# ==================== Recovery Recipe ====================


class EscalationPolicy(Enum):
    """升级策略 —— 借鉴 claw-code EscalationPolicy"""

    ALERT_HUMAN = "alert_human"  # 创建 InterruptPoint
    LOG_AND_CONTINUE = "log_and_continue"  # 记录日志，继续执行
    ABORT = "abort"  # 中止，不重试


@dataclass
class RecoveryRecipe:
    """恢复配方 —— 借鉴 claw-code RecoveryRecipe

    Attributes:
        scenario: 失败场景
        steps: 恢复步骤序列（按顺序执行）
        max_attempts: 最大自动恢复尝试次数（达到后升级）
        escalation_policy: 升级策略
        description: 人类可读描述
    """

    scenario: FailureScenario
    steps: list[RecoveryStep]
    max_attempts: int = 1
    escalation_policy: EscalationPolicy = EscalationPolicy.ALERT_HUMAN
    description: str = ""

    @property
    def can_auto_fix(self) -> bool:
        """是否包含自动修复步骤"""
        return RecoveryStep.AUTO_FIX in self.steps

    @property
    def should_escalate_immediately(self) -> bool:
        """是否立即升级（max_attempts=0）"""
        return self.max_attempts == 0


# ==================== Built-in Recipes ====================

_BUILTIN_RECIPES: dict[FailureScenario, RecoveryRecipe] = {
    FailureScenario.API_RATE_LIMITED: RecoveryRecipe(
        scenario=FailureScenario.API_RATE_LIMITED,
        steps=[RecoveryStep.WAIT_AND_RETRY],
        max_attempts=3,
        escalation_policy=EscalationPolicy.LOG_AND_CONTINUE,
        description="API 限流 — 指数退避重试（2s → 8s → 32s）",
    ),
    FailureScenario.PROVIDER_FAILURE: RecoveryRecipe(
        scenario=FailureScenario.PROVIDER_FAILURE,
        steps=[RecoveryStep.WAIT_AND_RETRY, RecoveryStep.ROTATE_KEY],
        max_attempts=2,
        escalation_policy=EscalationPolicy.ALERT_HUMAN,
        description="Provider 故障 — 重试后轮换 API key",
    ),
    FailureScenario.TOOL_TIMEOUT: RecoveryRecipe(
        scenario=FailureScenario.TOOL_TIMEOUT,
        steps=[RecoveryStep.RETRY_WITH_LONGER_TIMEOUT],
        max_attempts=1,
        escalation_policy=EscalationPolicy.LOG_AND_CONTINUE,
        description="工具超时 — 延长超时后重试一次",
    ),
    FailureScenario.CONNECTION_ERROR: RecoveryRecipe(
        scenario=FailureScenario.CONNECTION_ERROR,
        steps=[RecoveryStep.WAIT_AND_RETRY],
        max_attempts=2,
        escalation_policy=EscalationPolicy.LOG_AND_CONTINUE,
        description="网络连接错误 — 等待后重试",
    ),
    FailureScenario.GIT_LOCK: RecoveryRecipe(
        scenario=FailureScenario.GIT_LOCK,
        steps=[RecoveryStep.AUTO_FIX],
        max_attempts=1,
        escalation_policy=EscalationPolicy.LOG_AND_CONTINUE,
        description="Git 锁文件冲突 — 自动移除 .git/index.lock",
    ),
    FailureScenario.DISK_FULL: RecoveryRecipe(
        scenario=FailureScenario.DISK_FULL,
        steps=[RecoveryStep.ESCALATE_TO_HUMAN],
        max_attempts=0,
        escalation_policy=EscalationPolicy.ALERT_HUMAN,
        description="磁盘空间不足 — 立即升级到人工处理",
    ),
    FailureScenario.AUTH_ERROR: RecoveryRecipe(
        scenario=FailureScenario.AUTH_ERROR,
        steps=[RecoveryStep.ROTATE_KEY],
        max_attempts=1,
        escalation_policy=EscalationPolicy.ALERT_HUMAN,
        description="认证失败 — 轮换 API key",
    ),
    FailureScenario.FILE_NOT_FOUND: RecoveryRecipe(
        scenario=FailureScenario.FILE_NOT_FOUND,
        steps=[RecoveryStep.REPORT_TO_LLM],
        max_attempts=0,
        escalation_policy=EscalationPolicy.LOG_AND_CONTINUE,
        description="文件不存在 — 报告给 LLM 修正路径",
    ),
}


def recipe_for(scenario: FailureScenario) -> RecoveryRecipe | None:
    """获取场景对应的恢复配方 —— 借鉴 claw-code recipe_for()

    Returns:
        RecoveryRecipe 如果找到，None 如果 UNKNOWN
    """
    return _BUILTIN_RECIPES.get(scenario)


def get_all_recipes() -> dict[str, RecoveryRecipe]:
    """获取所有内置配方（给 API 用）"""
    return {s.value: r for s, r in _BUILTIN_RECIPES.items()}


# ==================== Recovery Event ====================


class RecoveryEventType(Enum):
    """恢复事件类型 —— 借鉴 claw-code RecoveryEvent"""

    ATTEMPTED = "recovery_attempted"
    SUCCEEDED = "recovery_succeeded"
    FAILED = "recovery_failed"
    ESCALATED = "escalated"


@dataclass
class RecoveryEvent:
    """恢复事件 —— 借鉴 claw-code 的结构化事件"""

    event_type: RecoveryEventType
    scenario: FailureScenario
    step: RecoveryStep | None = None
    attempt: int = 0
    max_attempts: int = 0
    timestamp: float = 0.0
    detail: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = time.time()

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.event_type.value,
            "scenario": self.scenario.value,
            "step": self.step.value if self.step else None,
            "attempt": self.attempt,
            "max_attempts": self.max_attempts,
            "timestamp": self.timestamp,
            "detail": self.detail,
        }


# ==================== Recovery Context ====================


@dataclass
class RecoveryContext:
    """恢复上下文 —— 借鉴 claw-code RecoveryContext

    追踪每个场景的尝试次数和事件日志。
    """

    scenario: FailureScenario = FailureScenario.UNKNOWN
    attempt_count: int = 0
    events: list[RecoveryEvent] = field(default_factory=list)
    fail_at_step: int | None = None  # 模拟失败（测试用）
    metadata: dict[str, Any] = field(default_factory=dict)

    def record_event(self, event: RecoveryEvent):
        self.events.append(event)

    def should_escalate(self, recipe: RecoveryRecipe) -> bool:
        """是否应该升级 —— 借鉴 claw-code 一次自动恢复后升级"""
        if recipe.max_attempts == 0:
            return True
        return self.attempt_count >= recipe.max_attempts

    def increment_attempt(self):
        self.attempt_count += 1


# ==================== Recovery Result ====================


@dataclass
class RecoveryResult:
    """恢复结果"""

    recovered: bool = False
    should_retry: bool = False
    escalate: bool = False
    wait_ms: int = 0
    modified_args: dict[str, Any] | None = None
    auto_fix_command: str | None = None
    detail: str = ""
    events: list[RecoveryEvent] = field(default_factory=list)

    @classmethod
    def retry(cls, wait_ms: int = 1000, detail: str = "") -> "RecoveryResult":
        return cls(should_retry=True, wait_ms=wait_ms, detail=detail)

    @classmethod
    def escalate_to_human(cls, detail: str = "") -> "RecoveryResult":
        return cls(escalate=True, detail=detail)

    @classmethod
    def abort(cls, detail: str = "") -> "RecoveryResult":
        return cls(detail=detail)

    @classmethod
    def auto_fix(cls, command: str, detail: str = "") -> "RecoveryResult":
        return cls(
            recovered=True, should_retry=True, auto_fix_command=command, detail=detail
        )

    @classmethod
    def report_to_llm(cls, detail: str = "") -> "RecoveryResult":
        return cls(detail=detail)


# ==================== Recovery Executor ====================


class RecoveryExecutor:
    """恢复执行器 —— 借鉴 claw-code attempt_recovery()

    执行恢复配方中的步骤序列，强制一次自动恢复后升级。
    """

    def __init__(self, auto_fix_enabled: bool = True):
        self.auto_fix_enabled = auto_fix_enabled
        self._auto_fix_handlers: dict[str, Callable[[], bool]] = {}

    def register_auto_fix(self, scenario: FailureScenario, handler: Callable[[], bool]):
        """注册自定义自动修复处理函数"""
        self._auto_fix_handlers[scenario.value] = handler

    def attempt_recovery(
        self,
        scenario: FailureScenario,
        context: RecoveryContext | None = None,
        error: Exception | None = None,
    ) -> RecoveryResult:
        """尝试恢复 —— 借鉴 claw-code attempt_recovery()

        Args:
            scenario: 失败场景
            context: 恢复上下文（追踪尝试次数）
            error: 原始错误

        Returns:
            RecoveryResult with should_retry / escalate / auto_fix
        """
        ctx = context or RecoveryContext(scenario=scenario)
        recipe = _BUILTIN_RECIPES.get(scenario)

        if recipe is None:
            # UNKNOWN 场景：无配方
            event = RecoveryEvent(
                event_type=RecoveryEventType.FAILED,
                scenario=scenario,
                attempt=ctx.attempt_count,
                detail="No recipe for scenario",
            )
            ctx.record_event(event)
            return RecoveryResult.abort(detail="No recovery recipe for this scenario")

        # 检查是否应该升级
        if ctx.should_escalate(recipe):
            # max_attempts=0 但有非升级步骤（如 REPORT_TO_LLM）→ 执行步骤而不升级
            if (
                recipe.max_attempts == 0
                and RecoveryStep.ESCALATE_TO_HUMAN not in recipe.steps
            ):
                for step in recipe.steps:
                    return self._execute_step(step, recipe, ctx, error)

            # 否则：升级
            event = RecoveryEvent(
                event_type=RecoveryEventType.ESCALATED,
                scenario=scenario,
                attempt=ctx.attempt_count,
                max_attempts=recipe.max_attempts,
                detail=f"Max attempts ({recipe.max_attempts}) reached, escalating",
            )
            ctx.record_event(event)
            return RecoveryResult.escalate_to_human(
                detail=f"已尝试 {ctx.attempt_count}/{recipe.max_attempts} 次自动恢复，需要人工处理"
            )

        ctx.increment_attempt()

        # 执行恢复步骤
        result = RecoveryResult()
        for i, step in enumerate(recipe.steps):
            # 模拟失败（测试用）
            if ctx.fail_at_step is not None and i >= ctx.fail_at_step:
                event = RecoveryEvent(
                    event_type=RecoveryEventType.FAILED,
                    scenario=scenario,
                    step=step,
                    attempt=ctx.attempt_count,
                    max_attempts=recipe.max_attempts,
                    detail=f"Simulated failure at step {i}",
                )
                ctx.record_event(event)
                return self._handle_step_failure(scenario, recipe, ctx, step)

            try:
                step_result = self._execute_step(step, recipe, ctx, error)
                if step_result.should_retry or step_result.escalate:
                    return step_result
                # step succeeded, merge events and continue
                result.events.extend(step_result.events)
            except Exception as e:
                logger.warning(f"Recovery step {step.value} failed: {e}")
                event = RecoveryEvent(
                    event_type=RecoveryEventType.FAILED,
                    scenario=scenario,
                    step=step,
                    attempt=ctx.attempt_count,
                    max_attempts=recipe.max_attempts,
                    detail=f"Step {step.value} failed: {e}",
                )
                ctx.record_event(event)
                return self._handle_step_failure(scenario, recipe, ctx, step)

        # 所有步骤成功
        succeeded_event = RecoveryEvent(
            event_type=RecoveryEventType.SUCCEEDED,
            scenario=scenario,
            attempt=ctx.attempt_count,
            max_attempts=recipe.max_attempts,
            detail="All recovery steps completed",
        )
        ctx.record_event(succeeded_event)
        result.events.append(succeeded_event)
        result.recovered = True
        return result

    def _handle_step_failure(
        self,
        scenario: FailureScenario,
        recipe: RecoveryRecipe,
        context: RecoveryContext,
        failed_step: RecoveryStep,
    ) -> RecoveryResult:
        """处理步骤失败后的升级逻辑"""
        if context.should_escalate(recipe):
            esc_event = RecoveryEvent(
                event_type=RecoveryEventType.ESCALATED,
                scenario=scenario,
                attempt=context.attempt_count,
                max_attempts=recipe.max_attempts,
                detail=f"Step {failed_step.value} failed and max attempts reached",
            )
            context.record_event(esc_event)
            return RecoveryResult.escalate_to_human(
                detail=f"恢复步骤 {failed_step.value} 失败，已达最大尝试次数"
            )

        # 还可以重试
        return RecoveryResult.retry(
            wait_ms=_compute_backoff_ms(context.attempt_count),
            detail=f"Step {failed_step.value} failed, but can retry",
        )

    def _execute_step(
        self,
        step: RecoveryStep,
        recipe: RecoveryRecipe,
        context: RecoveryContext,
        error: Exception | None = None,
    ) -> RecoveryResult:
        """执行单个恢复步骤"""
        event = RecoveryEvent(
            event_type=RecoveryEventType.ATTEMPTED,
            scenario=recipe.scenario,
            step=step,
            attempt=context.attempt_count,
            max_attempts=recipe.max_attempts,
            detail=f"Executing {step.value}",
        )
        context.record_event(event)
        result = RecoveryResult()
        result.events.append(event)

        if step == RecoveryStep.WAIT_AND_RETRY:
            wait_ms = _compute_backoff_ms(context.attempt_count)
            return RecoveryResult.retry(
                wait_ms=wait_ms,
                detail=f"等待 {wait_ms}ms 后重试（第 {context.attempt_count}/{recipe.max_attempts} 次）",
            )

        elif step == RecoveryStep.RETRY_WITH_LONGER_TIMEOUT:
            # 延长超时：原超时 * (attempt + 2)
            return RecoveryResult.retry(
                wait_ms=1000,
                detail=f"延长超时后重试（第 {context.attempt_count}/{recipe.max_attempts} 次）",
            )

        elif step == RecoveryStep.ROTATE_KEY:
            # Key rotation — 依赖外部注入的 handler
            handler = self._auto_fix_handlers.get("rotate_key")
            if handler:
                try:
                    handler()
                    return RecoveryResult.retry(
                        wait_ms=1000,
                        detail="API key 已轮换，准备重试",
                    )
                except Exception as e:
                    return RecoveryResult.escalate_to_human(
                        detail=f"API key 轮换失败: {e}"
                    )
            # 无 handler 时，直接升级
            return RecoveryResult.escalate_to_human(detail="需要手动轮换 API key")

        elif step == RecoveryStep.AUTO_FIX:
            if not self.auto_fix_enabled:
                return RecoveryResult.escalate_to_human(detail="自动修复已禁用")
            # Git lock 专用的 auto-fix
            if recipe.scenario == FailureScenario.GIT_LOCK:
                return RecoveryResult.auto_fix(
                    command="rm -f .git/index.lock",
                    detail="自动移除 .git/index.lock",
                )
            return RecoveryResult.escalate_to_human(
                detail=f"无自动修复方案: {recipe.scenario.value}"
            )

        elif step == RecoveryStep.REPORT_TO_LLM:
            return RecoveryResult.report_to_llm(
                detail="错误已报告给 LLM，让 LLM 调整参数"
            )

        elif step == RecoveryStep.ESCALATE_TO_HUMAN:
            return RecoveryResult.escalate_to_human(
                detail=f"场景 {recipe.scenario.value} 需要人工处理"
            )

        return RecoveryResult.abort(detail=f"未知恢复步骤: {step.value}")


def _compute_backoff_ms(attempt: int, base_ms: int = 2000, max_ms: int = 32000) -> int:
    """计算指数退避延迟 —— 借鉴 claw-code exponential backoff

    attempt 1 → 2s, attempt 2 → 8s, attempt 3 → 32s
    带 ±25% 抖动防止雷鸣群效应。
    """
    import random

    exponential = min(base_ms * (4 ** (attempt - 1)), max_ms)
    jitter = int(exponential * 0.25 * (random.random() * 2 - 1))
    return min(max_ms, max(1000, exponential + jitter))


# ==================== Global Event History ====================

_recent_events: list[RecoveryEvent] = []  # 最近 200 条事件
_MAX_EVENTS = 200


def record_recovery_event(event: RecoveryEvent):
    """记录恢复事件（供 API 查询）"""
    global _recent_events
    _recent_events.append(event)
    if len(_recent_events) > _MAX_EVENTS:
        _recent_events = _recent_events[-_MAX_EVENTS:]


def get_recent_events(limit: int = 50) -> list[dict[str, Any]]:
    """获取最近恢复事件"""
    return [e.to_dict() for e in _recent_events[-limit:]]
