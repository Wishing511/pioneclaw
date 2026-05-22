"""
Handoff 统一委托机制

借鉴 PraisonAI Handoff 系统：
- LLM 驱动：Handoff 作为工具暴露给 LLM
- 编程式：直接 Python API 调用
- 上下文策略：ContextPolicy 控制共享范围
- 安全：循环检测 + 深度限制

与 OpenClaw spawn 模式对比：
- spawn：创建独立 SubagentTask，有完整状态机
- Handoff：轻量级委托，更接近工具调用语义

两者可并存：复杂场景用 spawn，简单委托用 Handoff
"""

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class ContextPolicy(Enum):
    """上下文共享策略

    借鉴 PraisonAI ContextPolicy：
    - FULL: 完整历史（可能过大）
    - SUMMARY: 摘要（默认，安全）
    - NONE: 不共享（隔离）
    - LAST_N: 最近 N 条（折中）
    """

    FULL = "full"
    SUMMARY = "summary"
    NONE = "none"
    LAST_N = "last_n"


@dataclass
class HandoffConfig:
    """Handoff 配置

    借鉴 PraisonAI HandoffConfig
    """

    context_policy: ContextPolicy = ContextPolicy.SUMMARY
    max_context_tokens: int = 4000
    max_context_messages: int = 10
    preserve_system: bool = True

    # 安全控制
    detect_cycles: bool = True
    max_depth: int = 10
    timeout_seconds: float = 300.0

    # 工具过滤
    tool_filters: list[Callable] = field(default_factory=list)


@dataclass
class HandoffResult:
    """委托结果"""

    target_agent_id: str
    target_agent_name: str
    result: Any
    context_used: list[dict] = field(default_factory=list)
    error: str | None = None
    execution_time: float = 0.0


class CycleDetectedError(Exception):
    """循环委托检测到"""

    pass


class HandoffDepthExceededError(Exception):
    """委托深度超限"""

    pass


class Handoff:
    """统一委托机制

    借鉴 PraisonAI Handoff：
    - LLM 驱动：Handoff 作为工具暴露给 LLM
    - 编程式：直接 Python API 调用
    - 上下文策略：ContextPolicy 控制共享范围
    - 安全：循环检测 + 深度限制
    """

    def __init__(
        self,
        target_agent: Any,  # Agent 或 AgentLoop 实例
        tool_name_override: str | None = None,
        tool_description_override: str | None = None,
        on_handoff: Callable | None = None,
        config: HandoffConfig | None = None,
    ):
        """
        Args:
            target_agent: 目标 Agent
            tool_name_override: 自定义工具名
            tool_description_override: 自定义工具描述
            on_handoff: 委托完成回调
            config: Handoff 配置
        """
        self.target_agent = target_agent
        self._target_id = getattr(target_agent, "id", None) or getattr(
            target_agent, "_agent_id", "unknown"
        )
        self._target_name = getattr(target_agent, "name", None) or getattr(
            target_agent, "_agent_name", "unknown"
        )

        self.tool_name = (
            tool_name_override
            or f"delegate_to_{self._target_name}".replace(" ", "_")
            .replace("-", "_")
            .lower()
        )
        self.tool_description = (
            tool_description_override or f"委托任务给 {self._target_name}"
        )
        self.on_handoff = on_handoff
        self.config = config or HandoffConfig()

        # 执行追踪（用于循环检测）
        self._execution_chain: list[str] = []

    async def execute(
        self,
        source_agent: Any,
        prompt: str,
        context: list[dict] | None = None,
        depth: int = 0,
    ) -> HandoffResult:
        """执行委托

        Args:
            source_agent: 源 Agent
            prompt: 委托的任务描述
            context: 当前对话上下文
            depth: 当前委托深度

        Returns:
            HandoffResult: 委托结果
        """
        import time

        start_time = time.time()

        # 1. 深度检查
        if depth >= self.config.max_depth:
            raise HandoffDepthExceededError(
                f"Handoff depth {depth} exceeds max_depth {self.config.max_depth}"
            )

        # 2. 循环检测
        source_id = getattr(source_agent, "id", None) or getattr(
            source_agent, "_agent_id", "unknown"
        )
        if self.config.detect_cycles:
            self._check_cycle(source_id)

        # 3. 应用上下文策略
        filtered_context = self._apply_context_policy(context or [])

        # 4. 执行目标 Agent
        try:
            result = await self._execute_target(prompt, filtered_context)

            # 5. 回调
            if self.on_handoff:
                try:
                    cb_result = self.on_handoff(source_agent, result)
                    if asyncio.iscoroutine(cb_result):
                        await cb_result
                except Exception as e:
                    logger.warning(f"Handoff callback failed: {e}")

            return HandoffResult(
                target_agent_id=self._target_id,
                target_agent_name=self._target_name,
                result=result,
                context_used=filtered_context,
                execution_time=time.time() - start_time,
            )

        except Exception as e:
            logger.error(f"Handoff to {self._target_name} failed: {e}")
            return HandoffResult(
                target_agent_id=self._target_id,
                target_agent_name=self._target_name,
                result=None,
                error=str(e),
                execution_time=time.time() - start_time,
            )

    async def _execute_target(self, prompt: str, context: list[dict]) -> Any:
        """执行目标 Agent"""
        import inspect

        target = self.target_agent

        # 优先检查 run 方法（最常见）
        if hasattr(target, "run"):
            method = target.run
            if callable(method):
                result = method(prompt, context=context)
                if inspect.isawaitable(result):
                    return await result
                return result

        # 其次检查 process_direct（AgentLoop 风格）
        if hasattr(target, "process_direct"):
            method = target.process_direct
            if callable(method):
                result = method(message=prompt)
                if inspect.isawaitable(result):
                    return await result
                return result

        # 最后检查 execute
        if hasattr(target, "execute"):
            method = target.execute
            if callable(method):
                result = method(prompt)
                if inspect.isawaitable(result):
                    return await result
                return result

        raise ValueError(
            f"Target agent {self._target_name} has no callable method (run, process_direct, or execute)"
        )

    def _check_cycle(self, source_id: str) -> None:
        """检测循环委托"""
        if not self.config.detect_cycles:
            return  # 禁用检测时直接返回

        # 检测直接循环：源 Agent == 目标 Agent
        if source_id == self._target_id:
            raise CycleDetectedError(
                f"Direct cycle detected: {source_id} -> {self._target_id}"
            )

        # 检测间接循环：源 Agent 已在执行链中
        if source_id in self._execution_chain:
            raise CycleDetectedError(
                f"Indirect cycle detected: {source_id} already in chain {self._execution_chain}"
            )

    def _apply_context_policy(self, messages: list[dict]) -> list[dict]:
        """应用上下文策略过滤"""
        if not messages:
            return []

        if self.config.context_policy == ContextPolicy.NONE:
            return []

        elif self.config.context_policy == ContextPolicy.LAST_N:
            n = self.config.max_context_messages
            if self.config.preserve_system:
                system = [m for m in messages if m.get("role") == "system"]
                others = [m for m in messages if m.get("role") != "system"]
                return system + others[-n:]
            return messages[-n:]

        elif self.config.context_policy == ContextPolicy.SUMMARY:
            # 保留 system + 最近几条消息
            system = [m for m in messages if m.get("role") == "system"]
            others = [m for m in messages if m.get("role") != "system"]
            return system + others[-3:]  # 默认保留最近3条

        else:  # FULL
            return list(messages)

    def to_tool(self) -> dict[str, Any]:
        """转换为 LLM 可调用的工具定义

        返回 OpenAI function calling 格式的工具定义
        """
        return {
            "type": "function",
            "function": {
                "name": self.tool_name,
                "description": self.tool_description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "prompt": {
                            "type": "string",
                            "description": f"要委托给 {self._target_name} 的任务描述",
                        }
                    },
                    "required": ["prompt"],
                },
            },
        }

    def __repr__(self) -> str:
        return f"Handoff(target={self._target_name}, policy={self.config.context_policy.value})"


# ==================== 预置上下文过滤器 ====================


class handoff_filters:
    """Handoff 上下文过滤器

    借鉴 PraisonAI handoff_filters
    """

    @staticmethod
    def remove_all_tools(messages: list[dict]) -> list[dict]:
        """移除所有工具调用消息"""
        return [
            m
            for m in messages
            if m.get("role") not in ("tool",) and not m.get("tool_call_id")
        ]

    @staticmethod
    def keep_last_n_messages(n: int) -> Callable:
        """保留最近 N 条消息"""

        def filter_fn(messages: list[dict]) -> list[dict]:
            return messages[-n:]

        return filter_fn

    @staticmethod
    def keep_only_user_assistant(messages: list[dict]) -> list[dict]:
        """只保留 user 和 assistant 消息"""
        return [m for m in messages if m.get("role") in ("user", "assistant")]

    @staticmethod
    def remove_system(messages: list[dict]) -> list[dict]:
        """移除 system 消息"""
        return [m for m in messages if m.get("role") != "system"]


# ==================== 并行委托 ====================


async def parallel_handoffs(
    source_agent: Any,
    targets: list[tuple],  # List[(agent, prompt)] 或 List[(agent, prompt, config)]
    max_concurrent: int = 5,
    default_config: HandoffConfig | None = None,
) -> list[HandoffResult]:
    """并行委托给多个 Agent

    借鉴 PraisonAI parallel_handoffs

    Args:
        source_agent: 源 Agent
        targets: 目标列表，每个元素是 (agent, prompt) 或 (agent, prompt, config)
        max_concurrent: 最大并发数
        default_config: 默认 Handoff 配置

    Returns:
        List[HandoffResult]: 各委托的结果列表

    Example:
        results = await parallel_handoffs(
            source=coordinator,
            targets=[
                (researcher, "研究 AI 趋势"),
                (analyst, "分析市场数据"),
                (writer, "写报告草稿"),
            ],
            max_concurrent=3,
        )
    """
    semaphore = asyncio.Semaphore(max_concurrent)
    default_config = default_config or HandoffConfig()

    async def run_one(item: tuple) -> HandoffResult:
        if len(item) == 2:
            agent, prompt = item
            config = default_config
        else:
            agent, prompt, config = item

        async with semaphore:
            handoff = Handoff(agent, config=config)
            return await handoff.execute(source_agent, prompt)

    return await asyncio.gather(*[run_one(t) for t in targets])


# ==================== Handoff 执行追踪 ====================


class HandoffTracker:
    """Handoff 执行追踪器

    用于跨多个 Handoff 共享执行链，检测间接循环
    """

    def __init__(self):
        self._chain: list[str] = []
        self._results: list[HandoffResult] = []

    def push(self, agent_id: str) -> None:
        """添加 Agent 到执行链"""
        self._chain.append(agent_id)

    def pop(self) -> str | None:
        """移除最后一个 Agent"""
        if self._chain:
            return self._chain.pop()
        return None

    def add_result(self, result: HandoffResult) -> None:
        """记录结果"""
        self._results.append(result)

    def check_cycle(self, target_id: str) -> bool:
        """检查是否存在循环"""
        return target_id in self._chain

    def get_chain(self) -> list[str]:
        return list(self._chain)

    def get_results(self) -> list[HandoffResult]:
        return list(self._results)

    def clear(self) -> None:
        self._chain.clear()
        self._results.clear()


# 全局追踪器（可选使用）
_global_tracker: HandoffTracker | None = None


def get_handoff_tracker() -> HandoffTracker:
    """获取全局 Handoff 追踪器"""
    global _global_tracker
    if _global_tracker is None:
        _global_tracker = HandoffTracker()
    return _global_tracker


def reset_handoff_tracker() -> None:
    """重置全局追踪器"""
    global _global_tracker
    _global_tracker = None
