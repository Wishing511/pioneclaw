"""
Mock 助手函数 — 简化测试中 MockLLMProvider 的使用

提供：
- create_echo_mock(): 自动回显最后一条用户消息
- create_scripted_mock(): 按顺序返回预设文本
- assert_tool_called(): 断言某工具被调用
- assert_agent_says(): 断言某次 LLM 响应包含预期文本
"""

from app.modules.llm import MockLLMProvider


def create_echo_mock(delay_ms: float = 0.0) -> MockLLMProvider:
    """
    创建一个回显 Mock — 每次调用返回最后一条 user 消息的内容

    用法:
        mock = create_echo_mock()
        result = await mock.chat(messages=[{"role": "user", "content": "hello"}])
        assert result["content"] == "hello"
    """
    import asyncio

    mock = MockLLMProvider()
    _latency_ms = delay_ms

    async def echo_chat_stream(
        self,
        messages,
        tools=None,
        model=None,
        temperature=None,
        max_tokens=None,
        **kwargs,
    ):
        if _latency_ms > 0:
            await asyncio.sleep(_latency_ms / 1000.0)
        # 提取最后一条 user 消息
        last_user = ""
        for msg in reversed(messages):
            if msg.get("role") == "user":
                c = msg.get("content", "")
                last_user = c if isinstance(c, str) else str(c)
                break
        yield {"content": last_user, "finish_reason": "stop"}

    import types

    mock.chat_stream = types.MethodType(echo_chat_stream, mock)
    return mock


def create_scripted_mock(script: list[str], delay_ms: float = 0.0) -> MockLLMProvider:
    """
    创建一个脚本化 Mock — 按顺序返回预设文本

    用法:
        mock = create_scripted_mock(["Hello", "How are you?", "Goodbye"])
        r1 = await mock.chat(messages=[{"role": "user", "content": "hi"}])
        assert r1["content"] == "Hello"
    """
    mock = MockLLMProvider()
    for text in script:
        mock.add_response([{"content": text, "finish_reason": "stop"}])
    if delay_ms > 0:
        mock.set_latency(delay_ms)
    return mock


def assert_tool_called(
    provider: MockLLMProvider,
    tool_name: str,
    call_index: int = -1,
) -> None:
    """
    断言指定工具在 LLM 响应中被调用

    Args:
        provider: MockLLMProvider 实例
        tool_name: 预期被调用的工具名称
        call_index: 检查第几次 chat 调用（-1 = 最后一次），0-based from call_history
    """
    if not provider.call_history:
        raise AssertionError("No calls recorded in provider.call_history")

    record = provider.call_history[call_index]

    # 收集该次调用的所有 tool_call
    observed = set()
    for chunk in record.response_chunks:
        tc = chunk.get("tool_call")
        if isinstance(tc, dict) and tc.get("name"):
            observed.add(tc["name"])

    if tool_name not in observed:
        raise AssertionError(
            f"Expected tool '{tool_name}' not called in call #{call_index}. "
            f"Tools observed: {observed or 'none'}"
        )


def assert_agent_says(
    provider: MockLLMProvider,
    expected_text: str,
    call_index: int = -1,
    contains: bool = True,
) -> None:
    """
    断言 LLM 响应包含预期文本

    Args:
        provider: MockLLMProvider 实例
        expected_text: 预期文本
        call_index: 检查第几次 chat 调用（-1 = 最后一次），0-based from call_history
        contains: True → 子串匹配，False → 精确匹配
    """
    if not provider.call_history:
        raise AssertionError("No calls recorded in provider.call_history")

    record = provider.call_history[call_index]
    full_content = "".join(
        chunk.get("content", "") or "" for chunk in record.response_chunks
    )

    if contains:
        if expected_text not in full_content:
            raise AssertionError(
                f"Expected '{expected_text}' not found in response: '{full_content}'"
            )
    else:
        if expected_text != full_content:
            raise AssertionError(
                f"Expected exact match: '{expected_text}' != '{full_content}'"
            )
