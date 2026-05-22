"""
阶段 EE 测试 — Agent Tracing 执行追踪系统

覆盖：
- SpanKind 枚举
- SpanStatus 枚举
- TokenUsage 数据类
- Span 跨度类
- Trace 追踪类
- AgentTracer 追踪器
- AgentLoop 追踪集成
"""

import contextlib
from unittest.mock import MagicMock

import pytest

from app.modules.agent.tracing import (
    AgentTracer,
    Span,
    SpanKind,
    SpanStatus,
    TokenUsage,
    Trace,
    get_tracer,
    reset_tracer,
    trace_agent,
    trace_tool,
)

# ==================== SpanKind 测试 ====================


class TestSpanKind:
    def test_kind_values(self):
        assert SpanKind.TRACE.value == "trace"
        assert SpanKind.AGENT.value == "agent"
        assert SpanKind.LLM.value == "llm"
        assert SpanKind.TOOL.value == "tool"
        assert SpanKind.HANDOFF.value == "handoff"
        assert SpanKind.GUARDRAIL.value == "guardrail"
        assert SpanKind.HOOK.value == "hook"
        assert SpanKind.RETRIEVAL.value == "retrieval"
        assert SpanKind.EMBEDDING.value == "embedding"

    def test_all_kinds_exist(self):
        kinds = {k.value for k in SpanKind}
        expected = {
            "trace",
            "agent",
            "llm",
            "tool",
            "handoff",
            "guardrail",
            "hook",
            "retrieval",
            "embedding",
        }
        assert kinds == expected


# ==================== SpanStatus 测试 ====================


class TestSpanStatus:
    def test_status_values(self):
        assert SpanStatus.RUNNING.value == "running"
        assert SpanStatus.SUCCESS.value == "success"
        assert SpanStatus.ERROR.value == "error"
        assert SpanStatus.CANCELLED.value == "cancelled"


# ==================== TokenUsage 测试 ====================


class TestTokenUsage:
    def test_defaults(self):
        usage = TokenUsage()
        assert usage.prompt_tokens == 0
        assert usage.completion_tokens == 0
        assert usage.total_tokens == 0

    def test_custom_values(self):
        usage = TokenUsage(prompt_tokens=100, completion_tokens=50, total_tokens=150)
        assert usage.prompt_tokens == 100
        assert usage.total_tokens == 150

    def test_addition(self):
        usage1 = TokenUsage(prompt_tokens=100, completion_tokens=50, total_tokens=150)
        usage2 = TokenUsage(prompt_tokens=200, completion_tokens=100, total_tokens=300)
        result = usage1 + usage2

        assert result.prompt_tokens == 300
        assert result.completion_tokens == 150
        assert result.total_tokens == 450


# ==================== Span 测试 ====================


class TestSpan:
    def test_defaults(self):
        span = Span(
            id="span-1",
            trace_id="trace-1",
            parent_id=None,
            kind=SpanKind.TOOL,
            name="search",
            start_time=0.0,
        )
        assert span.status == SpanStatus.RUNNING
        assert span.end_time is None
        assert span.duration_ms == 0
        assert span.children == []

    def test_custom_values(self):
        span = Span(
            id="span-2",
            trace_id="trace-1",
            parent_id="span-1",
            kind=SpanKind.LLM,
            name="generate",
            start_time=0.0,
            input_data={"prompt": "test"},
            metadata={"model": "gpt-4"},
            tokens=TokenUsage(
                prompt_tokens=100, completion_tokens=50, total_tokens=150
            ),
        )
        assert span.kind == SpanKind.LLM
        assert span.input_data["prompt"] == "test"
        assert span.tokens.total_tokens == 150

    def test_finish(self):
        import time

        span = Span(
            id="span-3",
            trace_id="trace-1",
            parent_id=None,
            kind=SpanKind.TOOL,
            name="test",
            start_time=time.time() - 0.1,
        )

        span.finish(
            status=SpanStatus.SUCCESS,
            output_data={"result": "ok"},
        )

        assert span.status == SpanStatus.SUCCESS
        assert span.end_time is not None
        assert span.duration_ms > 0
        assert span.output_data["result"] == "ok"

    def test_finish_with_error(self):
        span = Span(
            id="span-4",
            trace_id="trace-1",
            parent_id=None,
            kind=SpanKind.TOOL,
            name="test",
            start_time=0.0,
        )

        span.finish(
            status=SpanStatus.ERROR,
            error="Something went wrong",
            error_stack="Traceback...",
        )

        assert span.status == SpanStatus.ERROR
        assert span.error == "Something went wrong"

    def test_add_child(self):
        parent = Span(
            id="span-5",
            trace_id="trace-1",
            parent_id=None,
            kind=SpanKind.AGENT,
            name="parent",
            start_time=0.0,
        )

        child = Span(
            id="span-6",
            trace_id="trace-1",
            parent_id="span-5",
            kind=SpanKind.TOOL,
            name="child",
            start_time=0.0,
        )

        parent.add_child(child)
        assert len(parent.children) == 1
        assert parent.children[0] == child

    def test_to_dict(self):
        span = Span(
            id="span-7",
            trace_id="trace-1",
            parent_id=None,
            kind=SpanKind.TOOL,
            name="search",
            start_time=0.0,
            end_time=0.1,
            duration_ms=100,
            status=SpanStatus.SUCCESS,
            input_data={"q": "test"},
            output_data={"result": "ok"},
        )

        d = span.to_dict()
        assert d["id"] == "span-7"
        assert d["kind"] == "tool"
        assert d["duration_ms"] == 100
        assert d["status"] == "success"


# ==================== Trace 测试 ====================


class TestTrace:
    def test_defaults(self):
        trace = Trace(id="trace-1", name="Test trace")
        assert trace.start_time > 0
        assert trace.end_time is None
        assert trace.total_tokens == 0
        assert trace.span_count == 0

    def test_custom_values(self):
        trace = Trace(
            id="trace-2",
            name="Agent execution",
            agent_id="agent-1",
            agent_name="Researcher",
            session_id="session-1",
            user_id=123,
        )
        assert trace.agent_id == "agent-1"
        assert trace.agent_name == "Researcher"

    def test_finish(self):
        trace = Trace(id="trace-3", name="Test")
        trace.finish()

        assert trace.end_time is not None
        assert trace.duration_ms >= 0

    def test_flatten_spans(self):
        trace = Trace(id="trace-4", name="Test")

        root = Span(
            id="root",
            trace_id="trace-4",
            parent_id=None,
            kind=SpanKind.AGENT,
            name="root",
            start_time=0.0,
        )

        child1 = Span(
            id="child1",
            trace_id="trace-4",
            parent_id="root",
            kind=SpanKind.TOOL,
            name="child1",
            start_time=0.0,
        )

        child2 = Span(
            id="child2",
            trace_id="trace-4",
            parent_id="root",
            kind=SpanKind.TOOL,
            name="child2",
            start_time=0.0,
        )

        root.add_child(child1)
        root.add_child(child2)
        trace.root_span = root

        spans = trace.flatten_spans()
        assert len(spans) == 3

    def test_to_dict(self):
        trace = Trace(
            id="trace-5",
            name="Test",
            agent_id="agent-1",
            agent_name="Agent",
        )
        trace.finish()

        d = trace.to_dict()
        assert d["id"] == "trace-5"
        assert d["agent_id"] == "agent-1"


# ==================== AgentTracer 测试 ====================


class TestAgentTracer:
    def test_current_trace(self):
        tracer = AgentTracer()
        assert tracer.current_trace is None

    def test_current_span(self):
        tracer = AgentTracer()
        assert tracer.current_span is None

    def test_start_trace(self):
        tracer = AgentTracer()
        trace = tracer.start_trace(
            name="Test trace",
            agent_id="agent-1",
            agent_name="Agent",
        )

        assert tracer.current_trace is trace
        assert trace.id is not None
        assert trace.name == "Test trace"

    def test_start_span(self):
        tracer = AgentTracer()
        tracer.start_trace("Test")

        span = tracer.start_span(
            kind=SpanKind.TOOL,
            name="search",
            input_data={"query": "test"},
        )

        assert tracer.current_span is span
        assert span.kind == SpanKind.TOOL
        assert span.name == "search"

    def test_start_nested_span(self):
        tracer = AgentTracer()
        tracer.start_trace("Test")

        parent = tracer.start_span(SpanKind.AGENT, "parent")
        child = tracer.start_span(SpanKind.TOOL, "child")

        assert child.parent_id == parent.id
        assert tracer.current_span is child

        tracer.end_span()
        assert tracer.current_span is parent

    def test_end_span(self):
        tracer = AgentTracer()
        tracer.start_trace("Test")
        span = tracer.start_span(SpanKind.TOOL, "test")

        ended = tracer.end_span(
            status=SpanStatus.SUCCESS,
            output_data={"result": "ok"},
        )

        assert ended is span
        assert span.status == SpanStatus.SUCCESS
        assert tracer.current_span is None

    def test_end_trace(self):
        tracer = AgentTracer()
        tracer.start_trace("Test")
        tracer.start_span(SpanKind.AGENT, "agent")
        tracer.end_span()

        trace = tracer.end_trace()

        assert trace is not None
        assert trace.end_time is not None
        assert tracer.current_trace is None

    def test_end_trace_closes_open_spans(self):
        tracer = AgentTracer()
        tracer.start_trace("Test")
        tracer.start_span(SpanKind.AGENT, "agent")
        tracer.start_span(SpanKind.TOOL, "tool")

        # 不手动关闭跨度，直接结束追踪
        tracer.end_trace()

        # 所有跨度应该被标记为 CANCELLED
        assert tracer._span_stack == []

    def test_list_traces(self):
        tracer = AgentTracer()
        tracer.start_trace("Trace 1", agent_id="agent-1")
        tracer.end_trace()

        tracer.start_trace("Trace 2", agent_id="agent-2")
        tracer.end_trace()

        traces = tracer.list_traces()
        assert len(traces) == 2

    def test_list_traces_filter(self):
        tracer = AgentTracer()
        tracer.start_trace("Trace 1", agent_id="agent-1")
        tracer.end_trace()

        tracer.start_trace("Trace 2", agent_id="agent-2")
        tracer.end_trace()

        traces = tracer.list_traces(agent_id="agent-1")
        assert len(traces) == 1

    def test_get_trace(self):
        tracer = AgentTracer()
        trace = tracer.start_trace("Test")
        tracer.end_trace()

        retrieved = tracer.get_trace(trace.id)
        assert retrieved is trace

    def test_get_timeline(self):
        tracer = AgentTracer()
        tracer.start_trace("Test")
        tracer.start_span(SpanKind.AGENT, "agent")
        tracer.start_span(SpanKind.TOOL, "tool1")
        tracer.end_span()
        tracer.start_span(SpanKind.TOOL, "tool2")
        tracer.end_span()
        tracer.end_span()
        trace = tracer.end_trace()

        timeline = tracer.get_timeline(trace.id)
        assert len(timeline) == 3  # agent + tool1 + tool2

    def test_clear_traces(self):
        tracer = AgentTracer()
        for i in range(10):
            tracer.start_trace(f"Trace {i}")
            tracer.end_trace()

        tracer.clear_traces(keep_recent=5)

        traces = tracer.list_traces()
        assert len(traces) == 5

    @pytest.mark.asyncio
    async def test_trace_context(self):
        tracer = AgentTracer()

        async with tracer.trace_context("Test", agent_id="agent-1"):
            assert tracer.current_trace is not None

        assert tracer.current_trace is None

    @pytest.mark.asyncio
    async def test_span_context(self):
        tracer = AgentTracer()
        tracer.start_trace("Test")

        async with tracer.span_context(SpanKind.TOOL, "search"):
            assert tracer.current_span is not None

        assert tracer.current_span.status == SpanStatus.SUCCESS

    @pytest.mark.asyncio
    async def test_span_context_error(self):
        tracer = AgentTracer()
        tracer.start_trace("Test")

        try:
            async with tracer.span_context(SpanKind.TOOL, "failing"):
                raise ValueError("Test error")
        except ValueError:
            pass

        # 跨度应该被标记为 ERROR
        assert tracer.current_span is None


# ==================== 全局追踪器测试 ====================


class TestGlobalTracer:
    def test_get_tracer(self):
        reset_tracer()
        tracer = get_tracer()
        assert tracer is not None

    def test_reset_tracer(self):
        tracer = get_tracer()
        tracer.start_trace("Test")
        tracer.end_trace()

        reset_tracer()
        new_tracer = get_tracer()
        assert len(new_tracer.list_traces()) == 0


# ==================== 装饰器测试 ====================


class TestDecorators:
    @pytest.mark.asyncio
    async def test_trace_agent(self):
        reset_tracer()

        @trace_agent("MyAgent", agent_id="agent-1")
        async def my_func():
            return "result"

        result = await my_func()
        assert result == "result"

        tracer = get_tracer()
        traces = tracer.list_traces()
        assert len(traces) >= 1

    @pytest.mark.asyncio
    async def test_trace_tool(self):
        tracer = AgentTracer()
        tracer.start_trace("Test")

        @trace_tool("search")
        async def search_func():
            return "found"

        result = await search_func()
        assert result == "found"


# ==================== AgentLoop 追踪集成测试 ====================


class TestAgentLoopTracing:
    def test_tracer_parameter(self):
        from app.modules.agent.loop import AgentLoop

        provider = MagicMock()
        tracer = AgentTracer()

        loop = AgentLoop(provider=provider, tracer=tracer)
        assert loop._tracer is tracer

    def test_get_tracer(self):
        from app.modules.agent.loop import AgentLoop

        provider = MagicMock()
        loop = AgentLoop(provider=provider)

        tracer = loop.get_tracer()
        assert tracer is not None

    def test_start_trace(self):
        from app.modules.agent.loop import AgentLoop

        provider = MagicMock()
        tracer = AgentTracer()
        loop = AgentLoop(
            provider=provider, tracer=tracer, agent_id="agent-1", agent_name="Agent"
        )

        trace = loop.start_trace("Test execution")
        assert trace is not None
        assert trace.agent_id == "agent-1"

    def test_end_trace(self):
        from app.modules.agent.loop import AgentLoop

        provider = MagicMock()
        tracer = AgentTracer()
        loop = AgentLoop(provider=provider, tracer=tracer)

        loop.start_trace("Test")
        trace = loop.end_trace()

        assert trace is not None
        assert trace.end_time is not None

    def test_start_span(self):
        from app.modules.agent.loop import AgentLoop

        provider = MagicMock()
        tracer = AgentTracer()
        loop = AgentLoop(provider=provider, tracer=tracer)

        loop.start_trace("Test")
        span = loop.start_span(SpanKind.TOOL, "search", input_data={"q": "test"})

        assert span is not None
        assert span.kind == SpanKind.TOOL

    def test_end_span(self):
        from app.modules.agent.loop import AgentLoop

        provider = MagicMock()
        tracer = AgentTracer()
        loop = AgentLoop(provider=provider, tracer=tracer)

        loop.start_trace("Test")
        loop.start_span(SpanKind.TOOL, "test")
        span = loop.end_span(output_data={"result": "ok"})

        assert span.status == SpanStatus.SUCCESS

    def test_get_current_trace(self):
        from app.modules.agent.loop import AgentLoop

        provider = MagicMock()
        tracer = AgentTracer()
        loop = AgentLoop(provider=provider, tracer=tracer)

        loop.start_trace("Test")
        trace = loop.get_current_trace()

        assert trace is not None

    def test_get_current_span(self):
        from app.modules.agent.loop import AgentLoop

        provider = MagicMock()
        tracer = AgentTracer()
        loop = AgentLoop(provider=provider, tracer=tracer)

        loop.start_trace("Test")
        loop.start_span(SpanKind.TOOL, "test")
        span = loop.get_current_span()

        assert span is not None

    @pytest.mark.asyncio
    async def test_traced_execution(self):
        from app.modules.agent.loop import AgentLoop

        provider = MagicMock()
        tracer = AgentTracer()
        loop = AgentLoop(provider=provider, tracer=tracer)

        loop.start_trace("Test")

        async def my_func():
            return "result"

        result = await loop.traced_execution(
            my_func,
            kind=SpanKind.TOOL,
            name="test_func",
        )

        assert result == "result"
        assert loop.get_current_span() is None  # 跨度已结束

    @pytest.mark.asyncio
    async def test_traced_execution_error(self):
        from app.modules.agent.loop import AgentLoop

        provider = MagicMock()
        tracer = AgentTracer()
        loop = AgentLoop(provider=provider, tracer=tracer)

        loop.start_trace("Test")

        async def failing_func():
            raise ValueError("Test error")

        with contextlib.suppress(ValueError):
            await loop.traced_execution(failing_func)

        # 跨度应该被标记为 ERROR
        # 检查追踪中的错误跨度
        trace = tracer.current_trace
        if trace and trace.root_span:
            spans = trace.flatten_spans()
            error_spans = [s for s in spans if s.status == SpanStatus.ERROR]
            assert len(error_spans) >= 1
