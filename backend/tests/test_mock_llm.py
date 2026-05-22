"""
MockLLMProvider 单元测试（Stage NN）

覆盖：脚本化响应、规则匹配、延迟模拟、错误注入、Stream/Non-stream、调用追踪
"""

import asyncio
import time

import pytest

from app.modules.llm import MockLLMProvider

# ==================== 脚本化响应 ====================


class TestScriptedResponses:
    """测试 add_response / add_responses 脚本化响应队列"""

    @pytest.mark.asyncio
    async def test_single_response_yielded(self):
        mock = MockLLMProvider()
        mock.add_response(
            [
                {"content": "Hello, world!", "finish_reason": "stop"},
            ]
        )
        result = await mock.chat(messages=[{"role": "user", "content": "hi"}])
        assert result["content"] == "Hello, world!"
        assert result["finish_reason"] == "stop"

    @pytest.mark.asyncio
    async def test_multi_chunk_response(self):
        mock = MockLLMProvider()
        mock.add_response(
            [
                {"content": "Hello "},
                {"content": "World", "finish_reason": "stop"},
            ]
        )
        result = await mock.chat(messages=[{"role": "user", "content": "hi"}])
        assert result["content"] == "Hello World"

    @pytest.mark.asyncio
    async def test_multi_round_consumption(self):
        mock = MockLLMProvider()
        mock.add_response([{"content": "first", "finish_reason": "stop"}])
        mock.add_response([{"content": "second", "finish_reason": "stop"}])

        r1 = await mock.chat(messages=[{"role": "user", "content": "q1"}])
        r2 = await mock.chat(messages=[{"role": "user", "content": "q2"}])
        assert r1["content"] == "first"
        assert r2["content"] == "second"

    @pytest.mark.asyncio
    async def test_exhaustion_fallback_to_empty(self):
        mock = MockLLMProvider()
        mock.add_response([{"content": "only", "finish_reason": "stop"}])

        r1 = await mock.chat(messages=[{"role": "user", "content": "q1"}])
        r2 = await mock.chat(messages=[{"role": "user", "content": "q2"}])
        assert r1["content"] == "only"
        assert r2["content"] == ""
        assert r2["finish_reason"] == "stop"

    @pytest.mark.asyncio
    async def test_add_empty_chunks_defaults_to_stop(self):
        mock = MockLLMProvider()
        mock.add_response(
            []
        )  # empty list → defaults to [{"content": "", "finish_reason": "stop"}]
        result = await mock.chat(messages=[{"role": "user", "content": "hi"}])
        assert result["content"] == ""
        assert result["finish_reason"] == "stop"

    @pytest.mark.asyncio
    async def test_batch_add_responses(self):
        mock = MockLLMProvider()
        mock.add_responses(
            [
                [{"content": "a", "finish_reason": "stop"}],
                [{"content": "b", "finish_reason": "stop"}],
                [{"content": "c", "finish_reason": "stop"}],
            ]
        )
        for expected in ["a", "b", "c"]:
            r = await mock.chat(messages=[{"role": "user", "content": "x"}])
            assert r["content"] == expected

    @pytest.mark.asyncio
    async def test_clear_responses(self):
        mock = MockLLMProvider()
        mock.add_response([{"content": "will be cleared", "finish_reason": "stop"}])
        mock.clear_responses()
        result = await mock.chat(messages=[{"role": "user", "content": "hi"}])
        assert result["content"] == ""


# ==================== 规则匹配 ====================


class TestRuleBasedMatching:
    """测试 add_rule 正则匹配响应"""

    @pytest.mark.asyncio
    async def test_regex_match_triggers_rule(self):
        mock = MockLLMProvider()
        mock.add_rule(
            pattern=r"weather",
            responses=[[{"content": "It's sunny!", "finish_reason": "stop"}]],
        )
        result = await mock.chat(
            messages=[{"role": "user", "content": "What's the weather?"}]
        )
        assert result["content"] == "It's sunny!"

    @pytest.mark.asyncio
    async def test_rule_priority_over_queue(self):
        mock = MockLLMProvider()
        mock.add_response([{"content": "queue response", "finish_reason": "stop"}])
        mock.add_rule(
            pattern=r"urgent",
            responses=[[{"content": "rule response", "finish_reason": "stop"}]],
        )
        # Even though queue has a response, rule matches first
        result = await mock.chat(
            messages=[{"role": "user", "content": "urgent message"}]
        )
        assert result["content"] == "rule response"
        # Queue response still available
        result2 = await mock.chat(messages=[{"role": "user", "content": "normal"}])
        assert result2["content"] == "queue response"

    @pytest.mark.asyncio
    async def test_no_match_falls_back_to_queue(self):
        mock = MockLLMProvider()
        mock.add_rule(
            pattern=r"weather",
            responses=[[{"content": "sunny", "finish_reason": "stop"}]],
        )
        mock.add_response([{"content": "default", "finish_reason": "stop"}])

        result = await mock.chat(
            messages=[{"role": "user", "content": "tell me a joke"}]
        )
        assert result["content"] == "default"

    @pytest.mark.asyncio
    async def test_rule_consumed_sequentially(self):
        mock = MockLLMProvider()
        mock.add_rule(
            pattern=r"weather",
            responses=[
                [{"content": "sunny", "finish_reason": "stop"}],
                [{"content": "rainy", "finish_reason": "stop"}],
            ],
        )
        r1 = await mock.chat(messages=[{"role": "user", "content": "weather?"}])
        r2 = await mock.chat(messages=[{"role": "user", "content": "weather again?"}])
        assert r1["content"] == "sunny"
        assert r2["content"] == "rainy"


# ==================== 延迟模拟 ====================


class TestLatencySimulation:
    """测试 set_latency 延迟模拟"""

    @pytest.mark.asyncio
    async def test_latency_delays_response(self):
        mock = MockLLMProvider()
        mock.set_latency(100)  # 100ms
        mock.add_response([{"content": "delayed", "finish_reason": "stop"}])

        start = time.monotonic()
        result = await mock.chat(messages=[{"role": "user", "content": "hi"}])
        elapsed_ms = (time.monotonic() - start) * 1000

        assert result["content"] == "delayed"
        assert elapsed_ms >= 90  # allow small timer variance


# ==================== 错误注入 ====================


class TestErrorInjection:
    """测试 inject_error 错误注入"""

    @pytest.mark.asyncio
    async def test_injected_error_on_specific_call(self):
        mock = MockLLMProvider()
        mock.add_response([{"content": "ok1", "finish_reason": "stop"}])
        mock.add_response([{"content": "ok2", "finish_reason": "stop"}])
        mock.inject_error(2, RuntimeError("rate limit exceeded"))

        r1 = await mock.chat(messages=[{"role": "user", "content": "q1"}])
        assert r1["content"] == "ok1"

        with pytest.raises(RuntimeError, match="rate limit exceeded"):
            await mock.chat(messages=[{"role": "user", "content": "q2"}])

    @pytest.mark.asyncio
    async def test_other_calls_unaffected(self):
        mock = MockLLMProvider()
        mock.add_response([{"content": "ok1", "finish_reason": "stop"}])
        mock.add_response([{"content": "ok3", "finish_reason": "stop"}])
        mock.inject_error(2, TimeoutError("timeout"))

        r1 = await mock.chat(messages=[{"role": "user", "content": "q1"}])
        assert r1["content"] == "ok1"

        with pytest.raises(TimeoutError):
            await mock.chat(messages=[{"role": "user", "content": "q2"}])

        r3 = await mock.chat(messages=[{"role": "user", "content": "q3"}])
        assert r3["content"] == "ok3"


# ==================== Stream / Non-stream ====================


class TestStreamAndNonStream:
    """测试 chat_stream 和 chat 双模式"""

    @pytest.mark.asyncio
    async def test_chat_stream_yields_individual_chunks(self):
        mock = MockLLMProvider()
        mock.add_response(
            [
                {"content": "Hello "},
                {"content": "World"},
                {"content": "", "finish_reason": "stop"},
            ]
        )

        chunks = []
        async for chunk in mock.chat_stream(
            messages=[{"role": "user", "content": "hi"}]
        ):
            chunks.append(chunk)

        assert len(chunks) == 3
        assert chunks[0]["content"] == "Hello "
        assert chunks[1]["content"] == "World"
        assert chunks[2]["finish_reason"] == "stop"

    @pytest.mark.asyncio
    async def test_chat_merges_all_chunks(self):
        mock = MockLLMProvider()
        mock.add_response(
            [
                {"content": "part1 "},
                {"content": "part2 "},
                {"content": "part3", "finish_reason": "stop"},
            ]
        )

        result = await mock.chat(messages=[{"role": "user", "content": "hi"}])
        assert result["content"] == "part1 part2 part3"
        assert result["finish_reason"] == "stop"
        assert len(result["chunks"]) == 3

    @pytest.mark.asyncio
    async def test_chat_stream_keyword_args_passthrough(self):
        mock = MockLLMProvider()
        mock.add_response([{"content": "ok", "finish_reason": "stop"}])

        async for _chunk in mock.chat_stream(
            messages=[{"role": "user", "content": "hi"}],
            tools=[{"type": "function", "function": {"name": "search"}}],
            model="gpt-4",
            temperature=0.5,
            max_tokens=100,
        ):
            pass

        last = mock.get_last_call()
        assert last.method == "chat_stream"
        assert last.model == "gpt-4"
        assert last.temperature == 0.5
        assert last.max_tokens == 100
        assert len(last.tools) == 1

    @pytest.mark.asyncio
    async def test_chat_with_tool_calls(self):
        mock = MockLLMProvider()
        mock.add_response(
            [
                {
                    "tool_call": {
                        "id": "call_1",
                        "name": "search",
                        "arguments": {"query": "test"},
                    }
                },
                {"content": "", "finish_reason": "tool_calls"},
            ]
        )

        result = await mock.chat(
            messages=[{"role": "user", "content": "search for test"}]
        )
        assert result["tool_calls"] == [
            {"id": "call_1", "name": "search", "arguments": {"query": "test"}}
        ]
        assert result["finish_reason"] == "tool_calls"


# ==================== 调用追踪 ====================


class TestCallTracking:
    """测试 call_count / call_history 调用追踪"""

    @pytest.mark.asyncio
    async def test_call_count_increments(self):
        mock = MockLLMProvider()
        mock.add_responses(
            [
                [{"content": "a", "finish_reason": "stop"}],
                [{"content": "b", "finish_reason": "stop"}],
                [{"content": "c", "finish_reason": "stop"}],
            ]
        )
        assert mock.call_count == 0
        await mock.chat(messages=[{"role": "user", "content": "1"}])
        assert mock.call_count == 1
        await mock.chat(messages=[{"role": "user", "content": "2"}])
        assert mock.call_count == 2
        await mock.chat(messages=[{"role": "user", "content": "3"}])
        assert mock.call_count == 3

    @pytest.mark.asyncio
    async def test_call_history_full_record(self):
        mock = MockLLMProvider()
        mock.add_response([{"content": "response", "finish_reason": "stop"}])

        async for _ in mock.chat_stream(
            messages=[{"role": "user", "content": "hello"}],
            tools=[{"type": "function", "function": {"name": "test"}}],
            model="custom-model",
        ):
            pass

        record = mock.get_last_call()
        assert record is not None
        assert record.method == "chat_stream"
        assert record.messages[0]["content"] == "hello"
        assert record.model == "custom-model"
        assert len(record.tools) == 1
        assert record.timestamp > 0
        assert len(record.response_chunks) == 1
        assert record.response_chunks[0]["content"] == "response"

    @pytest.mark.asyncio
    async def test_reset_call_tracking(self):
        mock = MockLLMProvider()
        mock.add_responses(
            [
                [{"content": "a", "finish_reason": "stop"}],
                [{"content": "b", "finish_reason": "stop"}],
            ]
        )
        await mock.chat(messages=[{"role": "user", "content": "1"}])
        await mock.chat(messages=[{"role": "user", "content": "2"}])
        assert mock.call_count == 2

        mock.reset_call_tracking()
        assert mock.call_count == 0
        assert len(mock.call_history) == 0

    @pytest.mark.asyncio
    async def test_get_calls_to_model(self):
        mock = MockLLMProvider()
        mock.add_responses(
            [
                [{"content": "a", "finish_reason": "stop"}],
                [{"content": "b", "finish_reason": "stop"}],
                [{"content": "c", "finish_reason": "stop"}],
            ]
        )
        await mock.chat(messages=[{"role": "user", "content": "1"}], model="gpt-4")
        await mock.chat(messages=[{"role": "user", "content": "2"}], model="claude")
        await mock.chat(messages=[{"role": "user", "content": "3"}], model="gpt-4")

        gpt4_calls = mock.get_calls_to_model("gpt-4")
        assert len(gpt4_calls) == 2
        claude_calls = mock.get_calls_to_model("claude")
        assert len(claude_calls) == 1

    @pytest.mark.asyncio
    async def test_error_calls_are_recorded(self):
        mock = MockLLMProvider()
        mock.inject_error(1, RuntimeError("boom"))

        with pytest.raises(RuntimeError, match="boom"):
            await mock.chat(messages=[{"role": "user", "content": "hi"}])

        assert mock.call_count == 1
        # Error happens before CallRecord is appended, so call_history is empty
        assert len(mock.call_history) == 0


# ==================== Token 计数 ====================


class TestCountTokens:
    """测试 count_tokens 估计"""

    def test_simple_text_count(self):
        mock = MockLLMProvider()
        msg = "Hello world, this is a test"  # 27 chars / 4 = 6
        tokens = mock.count_tokens([{"role": "user", "content": msg}])
        assert tokens == 6

    def test_multimodal_content(self):
        mock = MockLLMProvider()
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Describe this image"},
                    {
                        "type": "image_url",
                        "image_url": {"url": "https://example.com/img.png"},
                    },
                ],
            }
        ]
        tokens = mock.count_tokens(messages)
        assert tokens >= 4

    def test_minimum_one_token(self):
        mock = MockLLMProvider()
        tokens = mock.count_tokens([{"role": "user", "content": ""}])
        assert tokens == 1


# ==================== 辅助方法 ====================


class TestStats:
    """测试 get_stats"""

    @pytest.mark.asyncio
    async def test_get_stats_snapshot(self):
        mock = MockLLMProvider()
        mock.add_response([{"content": "ok", "finish_reason": "stop"}])
        mock.add_rule(r"test", [[{"content": "rule", "finish_reason": "stop"}]])
        mock.set_latency(50)
        mock.inject_error(3, RuntimeError("err"))

        await mock.chat(messages=[{"role": "user", "content": "hi"}])

        stats = mock.get_stats()
        assert stats["call_count"] == 1
        assert stats["scripted_pending"] == 0
        assert stats["rule_count"] == 1
        assert stats["error_injection_count"] == 1
        assert stats["latency_ms"] == 50


# ==================== Mock 助手函数 ====================


class TestMockHelpers:
    """测试 mock_helpers 工厂函数和断言工具"""

    @pytest.mark.asyncio
    async def test_create_echo_mock_returns_last_user_message(self):
        from tests.mock_helpers import create_echo_mock

        mock = create_echo_mock()
        result = await mock.chat(
            messages=[
                {"role": "system", "content": "You are helpful."},
                {"role": "user", "content": "What is the weather?"},
            ]
        )
        assert result["content"] == "What is the weather?"

    @pytest.mark.asyncio
    async def test_echo_mock_with_delay(self):
        import time

        from tests.mock_helpers import create_echo_mock

        mock = create_echo_mock(delay_ms=50)
        start = time.monotonic()
        result = await mock.chat(messages=[{"role": "user", "content": "hi"}])
        elapsed = (time.monotonic() - start) * 1000
        assert result["content"] == "hi"
        assert elapsed >= 40

    @pytest.mark.asyncio
    async def test_create_scripted_mock(self):
        from tests.mock_helpers import create_scripted_mock

        mock = create_scripted_mock(["first", "second", "third"])
        r1 = await mock.chat(messages=[{"role": "user", "content": "q1"}])
        r2 = await mock.chat(messages=[{"role": "user", "content": "q2"}])
        r3 = await mock.chat(messages=[{"role": "user", "content": "q3"}])
        assert r1["content"] == "first"
        assert r2["content"] == "second"
        assert r3["content"] == "third"

    def test_assert_tool_called_passes(self):
        from tests.mock_helpers import assert_tool_called

        mock = MockLLMProvider()
        mock.add_response(
            [
                {"tool_call": {"id": "1", "name": "search", "arguments": {"q": "x"}}},
                {"content": "", "finish_reason": "tool_calls"},
            ]
        )
        asyncio.run(mock.chat(messages=[{"role": "user", "content": "search x"}]))
        assert_tool_called(mock, "search")  # should not raise

    def test_assert_tool_called_raises_on_missing(self):
        from tests.mock_helpers import assert_tool_called

        mock = MockLLMProvider()
        mock.add_response([{"content": "no tools here", "finish_reason": "stop"}])
        asyncio.run(mock.chat(messages=[{"role": "user", "content": "hi"}]))
        with pytest.raises(AssertionError, match="not called"):
            assert_tool_called(mock, "search")

    def test_assert_agent_says_contains(self):
        from tests.mock_helpers import assert_agent_says

        mock = MockLLMProvider()
        mock.add_response(
            [{"content": "The weather is sunny today", "finish_reason": "stop"}]
        )
        asyncio.run(mock.chat(messages=[{"role": "user", "content": "weather?"}]))
        assert_agent_says(mock, "sunny")  # should not raise

    def test_assert_agent_says_exact_match(self):
        from tests.mock_helpers import assert_agent_says

        mock = MockLLMProvider()
        mock.add_response([{"content": "exact match", "finish_reason": "stop"}])
        asyncio.run(mock.chat(messages=[{"role": "user", "content": "hi"}]))
        assert_agent_says(mock, "exact match", contains=False)

    def test_assert_agent_says_raises_on_missing(self):
        from tests.mock_helpers import assert_agent_says

        mock = MockLLMProvider()
        mock.add_response([{"content": "hello world", "finish_reason": "stop"}])
        asyncio.run(mock.chat(messages=[{"role": "user", "content": "hi"}]))
        with pytest.raises(AssertionError, match="not found"):
            assert_agent_says(mock, "missing text")


# ==================== 集成测试 ====================


class TestIntegration:
    """测试 MockLLMProvider 与 Agent 组件的集成"""

    @pytest.mark.asyncio
    async def test_mock_feeds_agent_loop_pattern(self):
        """验证 MockProvider 可以驱动 AgentLoop 的典型消费模式"""
        mock = MockLLMProvider()
        # 模拟典型的 Agent 一轮对话：先产出 tool_call，再产出最终回复
        mock.add_response(
            [
                {
                    "tool_call": {
                        "id": "call_1",
                        "name": "read_file",
                        "arguments": {"path": "/test.txt"},
                    }
                },
                {"content": "", "finish_reason": "tool_calls"},
            ]
        )
        mock.add_response(
            [
                {"content": "The file contains: hello world", "finish_reason": "stop"},
            ]
        )

        chunks = []
        async for chunk in mock.chat_stream(
            messages=[{"role": "user", "content": "read /test.txt"}]
        ):
            chunks.append(chunk)

        # 第一个响应应该包含 tool_call
        assert any("tool_call" in c for c in chunks)
        tool_chunks = [c for c in chunks if "tool_call" in c]
        assert tool_chunks[0]["tool_call"]["name"] == "read_file"

        # 第二个响应应该是文本回复
        r2 = await mock.chat(
            messages=[
                {"role": "user", "content": "read /test.txt"},
                {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "name": "read_file",
                            "arguments": {"path": "/test.txt"},
                        }
                    ],
                },
                {"role": "tool", "tool_call_id": "call_1", "content": "hello world"},
            ]
        )
        assert "hello world" in r2["content"]

    @pytest.mark.asyncio
    async def test_mock_preserves_messages_for_call_history(self):
        """验证消息在 call_history 中完整保留（用于断言）"""
        mock = MockLLMProvider()
        mock.add_response([{"content": "ok", "finish_reason": "stop"}])

        messages = [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Hello!"},
        ]
        await mock.chat(messages=messages, model="gpt-4", temperature=0.3)

        record = mock.get_last_call()
        assert len(record.messages) == 2
        assert record.messages[0]["role"] == "system"
        assert record.messages[1]["role"] == "user"
        assert record.model == "gpt-4"
        assert record.temperature == 0.3
