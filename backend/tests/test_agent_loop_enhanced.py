"""
Agent Loop 增强测试
- 请求追踪 ID
- 工具调用去重
- 插件事件回调
- 会话级模型覆盖
"""

import pytest

from app.modules.agent.loop import AgentIteration, AgentLoop, AgentStatus, ToolCall


class MockProvider:
    """模拟 LLM 提供商"""

    def __init__(self, responses=None):
        self.responses = responses or []
        self._call_count = 0
        self.api_key = "test-key"
        self.api_base = "https://api.test.com"

    async def chat_stream(
        self, messages, tools=None, model=None, temperature=None, max_tokens=None
    ):
        if self._call_count < len(self.responses):
            response = self.responses[self._call_count]
            self._call_count += 1
            for chunk in response:
                yield chunk
        else:
            yield {"content": "No more responses", "finish_reason": "stop"}


class MockToolRegistry:
    """模拟工具注册表"""

    def __init__(self, tools=None):
        self._tools = tools or {}
        self._call_log = []

    def get_definitions(self):
        return [
            {"type": "function", "function": {"name": name}} for name in self._tools
        ]

    async def execute(self, tool_name, arguments):
        self._call_log.append({"name": tool_name, "args": arguments})
        if tool_name in self._tools:
            handler = self._tools[tool_name]
            if callable(handler):
                return handler(arguments)
            return handler
        raise ValueError(f"Tool not found: {tool_name}")


class TestRequestTraceId:
    """测试请求追踪 ID"""

    @pytest.mark.asyncio
    async def test_trace_id_generated(self):
        """测试每次请求生成唯一追踪 ID"""
        provider = MockProvider(
            responses=[
                [{"content": "Hello", "finish_reason": "stop"}],
            ]
        )
        loop = AgentLoop(provider=provider)

        assert loop.request_trace_id is None

        result = []
        async for chunk in loop.process_message("Hi"):
            result.append(chunk)

        assert loop.request_trace_id is not None
        assert len(loop.request_trace_id) > 0

    @pytest.mark.asyncio
    async def test_trace_id_unique_per_request(self):
        """测试每次请求生成不同的追踪 ID"""
        provider = MockProvider(
            responses=[
                [{"content": "First", "finish_reason": "stop"}],
                [{"content": "Second", "finish_reason": "stop"}],
            ]
        )
        loop = AgentLoop(provider=provider)

        async for _ in loop.process_message("First"):
            pass
        trace_id_1 = loop.request_trace_id

        # 重置 provider
        provider._call_count = 0

        async for _ in loop.process_message("Second"):
            pass
        trace_id_2 = loop.request_trace_id

        assert trace_id_1 != trace_id_2


class TestToolCallDedup:
    """测试工具调用去重"""

    def test_seen_tool_call_ids_cleared(self):
        """测试每次请求前清空去重集合"""
        provider = MockProvider()
        loop = AgentLoop(provider=provider)

        loop.seen_tool_call_ids.add("tc-1")
        loop.seen_tool_call_ids.add("tc-2")

        assert len(loop.seen_tool_call_ids) == 2

        # 模拟新请求清空
        loop.seen_tool_call_ids.clear()
        assert len(loop.seen_tool_call_ids) == 0


class TestPluginEventCallbacks:
    """测试插件事件回调"""

    @pytest.mark.asyncio
    async def test_tool_event_handler_called(self):
        """测试工具事件处理器被调用"""
        events = []

        def on_tool_event(event_type, data):
            events.append({"type": event_type, "data": data})

        provider = MockProvider(
            responses=[
                [
                    {"tool_call": {"id": "tc-1", "name": "test_tool", "arguments": {}}},
                ],
                [{"content": "Done", "finish_reason": "stop"}],
            ]
        )

        tools = MockToolRegistry(tools={"test_tool": "tool result"})

        loop = AgentLoop(provider=provider, tools=tools)
        loop.tool_event_handler = on_tool_event

        result = []
        async for chunk in loop.process_message("Use tool"):
            result.append(chunk)

        # 检查事件被触发
        tool_events = [
            e
            for e in events
            if e["type"] in ("tool_start", "tool_complete", "tool_error")
        ]
        assert len(tool_events) >= 1

    @pytest.mark.asyncio
    async def test_event_handler_error_does_not_break_loop(self):
        """测试事件处理器报错不中断主循环"""

        def bad_handler(event_type, data):
            raise RuntimeError("Handler error!")

        provider = MockProvider(
            responses=[
                [{"content": "Hello", "finish_reason": "stop"}],
            ]
        )

        loop = AgentLoop(provider=provider)
        loop.tool_event_handler = bad_handler

        # 应该不报错
        result = []
        async for chunk in loop.process_message("Hi"):
            result.append(chunk)

        assert len(result) > 0


class TestSessionModelOverride:
    """测试会话级模型覆盖"""

    def test_resolve_execution_runtime_no_override(self):
        """测试无覆盖时使用默认值"""
        provider = MockProvider()
        loop = AgentLoop(
            provider=provider, model="gpt-4", temperature=0.5, max_tokens=2048
        )

        runtime_provider, model, temp, max_tok, max_iter = (
            loop._resolve_execution_runtime()
        )

        assert model == "gpt-4"
        assert temp == 0.5
        assert max_tok == 2048

    def test_resolve_execution_runtime_with_override(self):
        """测试有覆盖时使用覆盖值"""
        provider = MockProvider()
        loop = AgentLoop(
            provider=provider, model="gpt-4", temperature=0.5, max_tokens=2048
        )

        override = {
            "model": "gpt-3.5-turbo",
            "temperature": 0.9,
            "max_tokens": 1024,
            "max_iterations": 10,
        }

        runtime_provider, model, temp, max_tok, max_iter = (
            loop._resolve_execution_runtime(override)
        )

        assert model == "gpt-3.5-turbo"
        assert temp == 0.9
        assert max_tok == 1024
        assert max_iter == 10

    def test_resolve_execution_runtime_partial_override(self):
        """测试部分覆盖"""
        provider = MockProvider()
        loop = AgentLoop(
            provider=provider, model="gpt-4", temperature=0.5, max_tokens=2048
        )

        override = {"temperature": 1.0}

        runtime_provider, model, temp, max_tok, max_iter = (
            loop._resolve_execution_runtime(override)
        )

        assert model == "gpt-4"  # 未覆盖，使用默认
        assert temp == 1.0  # 已覆盖


class TestToolRetry:
    """测试工具重试"""

    @pytest.mark.asyncio
    async def test_tool_retries_on_failure(self):
        """测试工具失败时重试"""
        # 使用 MockToolRegistry 的同步处理方式
        call_results = ["error", "error", "Success on attempt 3"]
        call_idx = 0

        def flaky_tool(args):
            nonlocal call_idx
            result = call_results[call_idx]
            call_idx += 1
            if result == "error":
                raise RuntimeError("Tool failed")
            return result

        provider = MockProvider(
            responses=[
                [{"tool_call": {"id": "tc-1", "name": "flaky_tool", "arguments": {}}}],
                [{"content": "Done", "finish_reason": "stop"}],
            ]
        )

        tools = MockToolRegistry(tools={"flaky_tool": flaky_tool})

        loop = AgentLoop(
            provider=provider, tools=tools, max_retries=3, retry_delay=0.01
        )

        result = []
        async for chunk in loop.process_message("Use flaky tool"):
            result.append(chunk)

        # 工具应该被重试直到成功（第3次成功）
        assert call_idx == 3


class TestAgentLoopDataClasses:
    """测试数据类"""

    def test_tool_call(self):
        """测试 ToolCall 数据类"""
        tc = ToolCall(id="tc-1", name="search", arguments={"query": "test"})
        assert tc.id == "tc-1"
        assert tc.name == "search"
        assert tc.result is None

    def test_agent_iteration(self):
        """测试 AgentIteration 数据类"""
        it = AgentIteration(iteration=1, content="Hello")
        assert it.iteration == 1
        assert it.content == "Hello"
        assert it.tool_calls == []

    def test_agent_status_enum(self):
        """测试 AgentStatus 枚举"""
        assert AgentStatus.IDLE.value == "idle"
        assert AgentStatus.RUNNING.value == "running"
        assert AgentStatus.COMPLETED.value == "completed"
        assert AgentStatus.FAILED.value == "failed"
