"""
Agent Tracing 执行追踪系统

借鉴 LangGraph/LangSmith 的追踪能力：
- 记录 Agent 执行过程中的各个阶段
- 支持嵌套跨度（Span）形成调用树
- 统计 Token 消耗、执行时间
- 支持错误追踪和调试

使用场景：
- 执行流程可视化
- 性能分析
- 错误诊断
- 成本统计
"""

import asyncio
import logging
import time
import uuid
from collections.abc import Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class SpanKind(Enum):
    """追踪跨度类型"""

    TRACE = "trace"  # 根追踪
    AGENT = "agent"  # Agent 执行
    LLM = "llm"  # LLM 调用
    TOOL = "tool"  # 工具调用
    HANDOFF = "handoff"  # Handoff 委托
    GUARDRAIL = "guardrail"  # Guardrail 验证
    HOOK = "hook"  # Tool Hook
    RETRIEVAL = "retrieval"  # 知识检索
    EMBEDDING = "embedding"  # 向量嵌入


class SpanStatus(Enum):
    """跨度状态"""

    RUNNING = "running"
    SUCCESS = "success"
    ERROR = "error"
    CANCELLED = "cancelled"


@dataclass
class TokenUsage:
    """Token 使用统计"""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0

    def __add__(self, other: "TokenUsage") -> "TokenUsage":
        return TokenUsage(
            prompt_tokens=self.prompt_tokens + other.prompt_tokens,
            completion_tokens=self.completion_tokens + other.completion_tokens,
            total_tokens=self.total_tokens + other.total_tokens,
        )


@dataclass
class Span:
    """追踪跨度

    记录单个操作的开始、结束、输入输出等信息
    """

    id: str
    trace_id: str
    parent_id: str | None
    kind: SpanKind
    name: str

    # 时间
    start_time: float
    end_time: float | None = None
    duration_ms: int = 0

    # 状态
    status: SpanStatus = SpanStatus.RUNNING
    error: str | None = None
    error_stack: str | None = None

    # 数据
    input_data: dict[str, Any] = field(default_factory=dict)
    output_data: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    # Token 统计
    tokens: TokenUsage | None = None

    # 子跨度
    children: list["Span"] = field(default_factory=list)

    def __post_init__(self):
        if not self.id:
            self.id = str(uuid.uuid4())[:8]

    def finish(
        self,
        status: SpanStatus = SpanStatus.SUCCESS,
        output_data: dict[str, Any] = None,
        error: str = None,
        error_stack: str = None,
    ) -> None:
        """结束跨度"""
        self.end_time = time.time()
        self.duration_ms = int((self.end_time - self.start_time) * 1000)
        self.status = status

        if output_data:
            self.output_data = output_data
        if error:
            self.error = error
        if error_stack:
            self.error_stack = error_stack

    def add_child(self, child: "Span") -> None:
        """添加子跨度"""
        self.children.append(child)

    def to_dict(self) -> dict[str, Any]:
        """转换为字典"""
        return {
            "id": self.id,
            "trace_id": self.trace_id,
            "parent_id": self.parent_id,
            "kind": self.kind.value,
            "name": self.name,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "duration_ms": self.duration_ms,
            "status": self.status.value,
            "error": self.error,
            "input_data": self._truncate_data(self.input_data),
            "output_data": self._truncate_data(self.output_data),
            "metadata": self.metadata,
            "tokens": {
                "prompt": self.tokens.prompt_tokens,
                "completion": self.tokens.completion_tokens,
                "total": self.tokens.total_tokens,
            }
            if self.tokens
            else None,
            "children": [c.to_dict() for c in self.children],
        }

    def _truncate_data(self, data: dict, max_len: int = 500) -> dict:
        """截断数据避免过大"""
        result = {}
        for k, v in data.items():
            if isinstance(v, str) and len(v) > max_len:
                result[k] = v[:max_len] + "..."
            elif isinstance(v, dict):
                result[k] = self._truncate_data(v, max_len)
            else:
                result[k] = v
        return result


@dataclass
class Trace:
    """完整追踪链

    一个 Trace 包含多个嵌套的 Span
    """

    id: str
    name: str
    root_span: Span | None = None

    # 时间
    start_time: float = 0.0
    end_time: float | None = None
    duration_ms: int = 0

    # 统计
    total_tokens: int = 0
    total_cost: float = 0.0
    span_count: int = 0
    error_count: int = 0

    # 元数据
    agent_id: str = ""
    agent_name: str = ""
    session_id: str = ""
    user_id: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not self.id:
            self.id = str(uuid.uuid4())[:8]
        if not self.start_time:
            self.start_time = time.time()

    def finish(self) -> None:
        """结束追踪"""
        self.end_time = time.time()
        self.duration_ms = int((self.end_time - self.start_time) * 1000)

        # 统计
        if self.root_span:
            self._compute_stats(self.root_span)

    def _compute_stats(self, span: Span) -> None:
        """递归计算统计信息"""
        self.span_count += 1

        if span.tokens:
            self.total_tokens += span.tokens.total_tokens

        if span.status == SpanStatus.ERROR:
            self.error_count += 1

        for child in span.children:
            self._compute_stats(child)

    def to_dict(self) -> dict[str, Any]:
        """转换为字典"""
        return {
            "id": self.id,
            "name": self.name,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "duration_ms": self.duration_ms,
            "total_tokens": self.total_tokens,
            "total_cost": self.total_cost,
            "span_count": self.span_count,
            "error_count": self.error_count,
            "agent_id": self.agent_id,
            "agent_name": self.agent_name,
            "session_id": self.session_id,
            "user_id": self.user_id,
            "metadata": self.metadata,
            "root_span": self.root_span.to_dict() if self.root_span else None,
        }

    def flatten_spans(self) -> list[Span]:
        """展平所有跨度为列表"""
        result = []

        def collect(span: Span):
            result.append(span)
            for child in span.children:
                collect(child)

        if self.root_span:
            collect(self.root_span)
        return result


class AgentTracer:
    """Agent 执行追踪器

    管理 Trace 和 Span 的生命周期
    """

    def __init__(
        self,
        storage: Any | None = None,
        on_trace_complete: Callable | None = None,
    ):
        """
        Args:
            storage: 持久化存储（可选）
            on_trace_complete: 追踪完成回调
        """
        self.storage = storage
        self.on_trace_complete = on_trace_complete

        # 当前追踪状态
        self._current_trace: Trace | None = None
        self._span_stack: list[Span] = []

        # 历史追踪
        self._traces: dict[str, Trace] = {}

    @property
    def current_trace(self) -> Trace | None:
        """获取当前追踪"""
        return self._current_trace

    @property
    def current_span(self) -> Span | None:
        """获取当前跨度"""
        return self._span_stack[-1] if self._span_stack else None

    def start_trace(
        self,
        name: str,
        agent_id: str = "",
        agent_name: str = "",
        session_id: str = "",
        user_id: int | None = None,
        metadata: dict[str, Any] = None,
    ) -> Trace:
        """开始新追踪

        Args:
            name: 追踪名称
            agent_id: Agent ID
            agent_name: Agent 名称
            session_id: 会话 ID
            user_id: 用户 ID
            metadata: 元数据

        Returns:
            Trace: 创建的追踪
        """
        trace = Trace(
            id=str(uuid.uuid4())[:8],
            name=name,
            agent_id=agent_id,
            agent_name=agent_name,
            session_id=session_id,
            user_id=user_id,
            metadata=metadata or {},
        )

        self._current_trace = trace
        self._traces[trace.id] = trace

        logger.debug(f"Started trace {trace.id}: {name}")
        return trace

    def start_span(
        self,
        kind: SpanKind,
        name: str,
        input_data: dict[str, Any] = None,
        metadata: dict[str, Any] = None,
    ) -> Span:
        """开始新跨度

        Args:
            kind: 跨度类型
            name: 跨度名称
            input_data: 输入数据
            metadata: 元数据

        Returns:
            Span: 创建的跨度
        """
        if not self._current_trace:
            logger.warning("No active trace, creating span without trace")
            trace_id = ""
        else:
            trace_id = self._current_trace.id

        parent_id = self.current_span.id if self.current_span else None

        span = Span(
            id=str(uuid.uuid4())[:8],
            trace_id=trace_id,
            parent_id=parent_id,
            kind=kind,
            name=name,
            start_time=time.time(),
            input_data=input_data or {},
            metadata=metadata or {},
        )

        # 添加到父跨度或根跨度
        if self.current_span:
            self.current_span.add_child(span)
        elif self._current_trace:
            self._current_trace.root_span = span

        self._span_stack.append(span)

        logger.debug(f"Started span {span.id}: {kind.value} - {name}")
        return span

    def end_span(
        self,
        status: SpanStatus = SpanStatus.SUCCESS,
        output_data: dict[str, Any] = None,
        error: str = None,
        error_stack: str = None,
        tokens: TokenUsage = None,
    ) -> Span | None:
        """结束当前跨度

        Args:
            status: 跨度状态
            output_data: 输出数据
            error: 错误信息
            error_stack: 错误堆栈
            tokens: Token 使用量

        Returns:
            Span: 结束的跨度
        """
        if not self._span_stack:
            logger.warning("No active span to end")
            return None

        span = self._span_stack.pop()
        span.finish(
            status=status,
            output_data=output_data,
            error=error,
            error_stack=error_stack,
        )

        if tokens:
            span.tokens = tokens

        logger.debug(
            f"Ended span {span.id}: {span.status.value} ({span.duration_ms}ms)"
        )
        return span

    def end_trace(self) -> Trace | None:
        """结束当前追踪

        Returns:
            Trace: 结束的追踪
        """
        if not self._current_trace:
            logger.warning("No active trace to end")
            return None

        # 结束所有未关闭的跨度
        while self._span_stack:
            span = self._span_stack.pop()
            span.finish(status=SpanStatus.CANCELLED)

        self._current_trace.finish()

        # 持久化
        if self.storage:
            try:
                self.storage.save_trace(self._current_trace)
            except Exception as e:
                logger.warning(f"Failed to persist trace: {e}")

        # 回调
        if self.on_trace_complete:
            try:
                result = self.on_trace_complete(self._current_trace)
                if asyncio.iscoroutine(result):
                    asyncio.create_task(result)
            except Exception as e:
                logger.warning(f"Trace callback failed: {e}")

        logger.info(
            f"Ended trace {self._current_trace.id}: "
            f"{self._current_trace.duration_ms}ms, "
            f"{self._current_trace.span_count} spans, "
            f"{self._current_trace.total_tokens} tokens"
        )

        trace = self._current_trace
        self._current_trace = None
        return trace

    @asynccontextmanager
    async def trace_context(
        self,
        name: str,
        agent_id: str = "",
        agent_name: str = "",
        session_id: str = "",
        user_id: int | None = None,
        metadata: dict[str, Any] = None,
    ):
        """追踪上下文管理器

        Example:
            async with tracer.trace_context("MyAgent", agent_id="a1"):
                # Agent 执行
                pass
        """
        self.start_trace(
            name=name,
            agent_id=agent_id,
            agent_name=agent_name,
            session_id=session_id,
            user_id=user_id,
            metadata=metadata,
        )
        try:
            yield self
        except Exception as e:
            # 记录错误
            if self.current_span:
                import traceback

                self.end_span(
                    status=SpanStatus.ERROR,
                    error=str(e),
                    error_stack=traceback.format_exc(),
                )
            raise
        finally:
            self.end_trace()

    @asynccontextmanager
    async def span_context(
        self,
        kind: SpanKind,
        name: str,
        input_data: dict[str, Any] = None,
        metadata: dict[str, Any] = None,
    ):
        """跨度上下文管理器

        Example:
            async with tracer.span_context(SpanKind.TOOL, "search"):
                result = await tool.execute()
                return result
        """
        span = self.start_span(kind, name, input_data, metadata)
        try:
            yield span
        except Exception as e:
            import traceback

            span.finish(
                status=SpanStatus.ERROR,
                error=str(e),
                error_stack=traceback.format_exc(),
            )
            # 从栈中弹出
            if self._span_stack and self._span_stack[-1] == span:
                self._span_stack.pop()
            raise
        else:
            span.finish()

    # ==================== 查询方法 ====================

    def get_trace(self, trace_id: str) -> Trace | None:
        """获取追踪"""
        return self._traces.get(trace_id)

    def list_traces(
        self,
        agent_id: str | None = None,
        session_id: str | None = None,
        user_id: int | None = None,
        limit: int = 50,
    ) -> list[Trace]:
        """列出追踪"""
        result = []

        for trace in self._traces.values():
            # 过滤
            if agent_id and trace.agent_id != agent_id:
                continue
            if session_id and trace.session_id != session_id:
                continue
            if user_id and trace.user_id != user_id:
                continue

            result.append(trace)

        # 按时间倒序
        result.sort(key=lambda t: t.start_time, reverse=True)
        return result[:limit]

    def get_timeline(self, trace_id: str) -> list[dict[str, Any]]:
        """获取时间线数据（Gantt 图用）

        Returns:
            List: 时间线项列表，每项包含 id, name, kind, start, end, duration, depth
        """
        trace = self.get_trace(trace_id)
        if not trace or not trace.root_span:
            return []

        timeline = []

        def collect(span: Span, depth: int = 0):
            timeline.append(
                {
                    "id": span.id,
                    "name": span.name,
                    "kind": span.kind.value,
                    "start": span.start_time,
                    "end": span.end_time,
                    "duration_ms": span.duration_ms,
                    "status": span.status.value,
                    "depth": depth,
                }
            )

            for child in span.children:
                collect(child, depth + 1)

        collect(trace.root_span)

        # 计算相对时间
        if timeline:
            base_time = timeline[0]["start"]
            for item in timeline:
                item["start_offset_ms"] = int((item["start"] - base_time) * 1000)

        return timeline

    def clear_traces(self, keep_recent: int = 100) -> None:
        """清除历史追踪"""
        if len(self._traces) <= keep_recent:
            return

        # 按时间排序，保留最近的
        sorted_traces = sorted(
            self._traces.items(),
            key=lambda x: x[1].start_time,
            reverse=True,
        )

        self._traces = dict(sorted_traces[:keep_recent])


# ==================== 全局实例 ====================

_global_tracer: AgentTracer | None = None


def get_tracer() -> AgentTracer:
    """获取全局追踪器"""
    global _global_tracer
    if _global_tracer is None:
        _global_tracer = AgentTracer()
    return _global_tracer


def reset_tracer() -> None:
    """重置全局追踪器"""
    global _global_tracer
    _global_tracer = None


# ==================== 便捷函数 ====================


def trace_agent(name: str, **kwargs):
    """装饰器：追踪 Agent 执行"""

    def decorator(func):
        async def wrapper(*args, **func_kwargs):
            tracer = get_tracer()
            async with tracer.trace_context(name, **kwargs):
                return await func(*args, **func_kwargs)

        return wrapper

    return decorator


def trace_tool(name: str):
    """装饰器：追踪工具执行"""

    def decorator(func):
        async def wrapper(*args, **kwargs):
            tracer = get_tracer()
            async with tracer.span_context(SpanKind.TOOL, name):
                return await func(*args, **kwargs)

        return wrapper

    return decorator
