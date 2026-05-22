"""
Mock LLM Provider — 用于测试 Agent 行为，不消耗真实 API 费用

借鉴 claude-code `src/llm/mock.rs`，提供：
- 脚本化响应（可编程序列响应队列）
- 规则匹配（regex pattern → response）
- 延迟模拟（simulated latency）
- 错误注入（rate limit, timeout, 500 error）
- Token 计数模拟
- Stream/Non-stream 双模式
- 调用追踪（call_count, call_history）
"""

import asyncio
import copy
import logging
import re
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# ==================== 数据类 ====================


@dataclass
class CallRecord:
    """单次 chat_stream 或 chat 调用的完整记录"""

    method: str  # "chat_stream" 或 "chat"
    messages: list[dict[str, Any]]  # 调用时传入的消息列表
    tools: list[dict] | None = None  # 工具定义
    model: str | None = None  # 模型名称
    temperature: float | None = None  # 温度参数
    max_tokens: int | None = None  # 最大 token 数
    timestamp: float = 0.0  # 调用时间戳
    response_chunks: list[dict] = field(default_factory=list)  # 响应的所有 chunk
    error: str | None = None  # 错误信息（如有）


@dataclass
class ResponseRule:
    """基于消息内容正则匹配的响应规则"""

    pattern: str  # 原始正则表达式字符串
    compiled: re.Pattern  # 编译后的正则
    responses: list[list[dict]]  # 匹配时使用的响应序列
    consumed: int = 0  # 已消费的响应数


# ==================== Mock LLM Provider ====================


class MockLLMProvider:
    """
    可复用 Mock LLM Provider

    用法:
        mock = MockLLMProvider()
        mock.add_response([{"content": "Hello", "finish_reason": "stop"}])
        async for chunk in mock.chat_stream(messages=[{"role": "user", "content": "hi"}]):
            print(chunk)

    特性:
    - 脚本化响应队列: add_response() → 按顺序消费
    - 规则匹配: add_rule() → 消息内容匹配时优先返回
    - 延迟模拟: set_latency(ms) → asyncio.sleep
    - 错误注入: inject_error(call_index, error) → 第N次调用抛异常
    - 调用追踪: call_count, call_history
    - 兼容 SimpleLLMProvider 的参数模式
    """

    def __init__(self, default_model: str = "mock-model"):
        self.default_model: str = default_model
        self.model: str = default_model
        self.api_key: str = "mock-api-key"
        self.api_base: str = "https://mock.api.local"
        self.temperature: float = 0.7
        self.max_tokens: int = 4096

        # 脚本化响应队列：每个元素是一个 chunk 列表
        self._scripted_responses: list[list[dict]] = []

        # 规则匹配
        self._rules: list[ResponseRule] = []

        # 延迟
        self._latency_ms: float = 0.0

        # 错误注入：call_index (1-based) → Exception
        self._error_injections: dict[int, Exception] = {}

        # 调用追踪
        self.call_count: int = 0
        self.call_history: list[CallRecord] = []

    # ==================== 响应队列 ====================

    def add_response(self, chunks: list[dict]) -> None:
        """
        向队列末尾添加一个脚本化响应

        Args:
            chunks: 要返回的 chunk 字典列表，例如:
                [
                    {"content": "Hello "},
                    {"content": "World", "finish_reason": "stop"},
                ]
        """
        if not chunks:
            chunks = [{"content": "", "finish_reason": "stop"}]
        self._scripted_responses.append(list(chunks))

    def add_responses(self, responses: list[list[dict]]) -> None:
        """批量添加多个脚本化响应"""
        for response in responses:
            self.add_response(response)

    def clear_responses(self) -> None:
        """清空所有待处理响应和规则"""
        self._scripted_responses.clear()
        self._rules.clear()
        self._error_injections.clear()

    # ==================== 规则匹配 ====================

    def add_rule(self, pattern: str, responses: list[list[dict]]) -> None:
        """
        添加基于消息内容的正则匹配规则

        当任何消息的 content 字段匹配 pattern 时，按顺序消费 response 列表。
        规则匹配优先级高于脚本化队列。

        Args:
            pattern: Python 正则表达式
            responses: 匹配时依次返回的响应列表
        """
        self._rules.append(
            ResponseRule(
                pattern=pattern,
                compiled=re.compile(pattern, re.DOTALL),
                responses=[list(r) for r in responses],
            )
        )

    # ==================== 延迟模拟 ====================

    def set_latency(self, ms: float) -> None:
        """设置模拟延迟（毫秒）"""
        self._latency_ms = ms

    # ==================== 错误注入 ====================

    def inject_error(self, call_index: int, error: Exception) -> None:
        """
        在指定调用次数时注入错误

        Args:
            call_index: 1-based 调用序号，在此序号时抛出 error
            error: 要抛出的异常
        """
        self._error_injections[call_index] = error

    # ==================== 调用追踪 ====================

    def reset_call_tracking(self) -> None:
        """重置调用计数和历史（保留脚本化响应和规则）"""
        self.call_count = 0
        self.call_history.clear()

    def get_last_call(self) -> CallRecord | None:
        """返回最近一次调用记录"""
        return self.call_history[-1] if self.call_history else None

    def get_calls_to_model(self, model: str) -> list[CallRecord]:
        """返回使用特定模型的所有调用记录"""
        return [c for c in self.call_history if c.model == model]

    # ==================== 核心: chat_stream ====================

    async def chat_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict] | None = None,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        **kwargs,
    ) -> AsyncIterator[dict[str, Any]]:
        """
        模拟流式 chat 调用

        响应解析顺序:
        1. 检查错误注入
        2. 检查规则匹配（第一个匹配获胜）
        3. 回退到脚本化响应队列
        4. 默认空响应
        """
        call_index = self.call_count + 1  # 1-based for error injection

        # 1. 错误注入
        if call_index in self._error_injections:
            self.call_count += 1
            raise self._error_injections[call_index]

        # 2. 延迟模拟
        if self._latency_ms > 0:
            await asyncio.sleep(self._latency_ms / 1000.0)

        timestamp = time.time()
        chunks: list[dict] = []
        matched_response: list[dict] | None = None

        # 3. 规则匹配（检查所有消息内容）
        for rule in self._rules:
            for msg in messages:
                content = msg.get("content", "")
                if isinstance(content, str) and rule.compiled.search(content):
                    if rule.consumed < len(rule.responses):
                        matched_response = rule.responses[rule.consumed]
                        rule.consumed += 1
                    break
            if matched_response is not None:
                break

        # 4. 脚本化队列
        if matched_response is None:
            if self._scripted_responses:
                matched_response = self._scripted_responses.pop(0)
            else:
                # 默认空响应
                matched_response = [{"content": "", "finish_reason": "stop"}]

        # 5. 产出 chunks
        for chunk in matched_response:
            chunk_copy = dict(chunk)  # 浅拷贝避免外部修改影响记录
            chunks.append(chunk_copy)
            yield dict(chunk)

        # 6. 记录
        self.call_count += 1
        self.call_history.append(
            CallRecord(
                method="chat_stream",
                messages=copy.deepcopy(messages),
                tools=list(tools) if tools else None,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                timestamp=timestamp,
                response_chunks=chunks,
            )
        )

    # ==================== 核心: chat (非流式) ====================

    async def chat(
        self,
        messages: list[dict[str, Any]],
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        tools: list[dict] | None = None,
        **kwargs,
    ) -> dict[str, Any]:
        """
        模拟非流式 chat 调用

        收集 chat_stream 的所有 chunk，拼接 content 返回 dict。
        """
        content_parts: list[str] = []
        tool_calls: list[dict] = []
        finish_reason: str | None = None
        chunks: list[dict] = []

        async for chunk in self.chat_stream(
            messages=messages,
            tools=tools,
            model=model,
            temperature=temperature or self.temperature,
            max_tokens=max_tokens or self.max_tokens,
            **kwargs,
        ):
            chunks.append(chunk)
            if "content" in chunk and chunk["content"]:
                content_parts.append(str(chunk["content"]))
            if "tool_call" in chunk:
                tc = chunk["tool_call"]
                if isinstance(tc, dict):
                    tool_calls.append(tc)
            if "finish_reason" in chunk:
                finish_reason = chunk["finish_reason"]

        return {
            "content": "".join(content_parts),
            "tool_calls": tool_calls if tool_calls else None,
            "finish_reason": finish_reason or "stop",
            "chunks": chunks,
        }

    # ==================== Token 计数 ====================

    def count_tokens(self, messages: list[dict[str, Any]]) -> int:
        """
        粗略估计 token 数: ~4 字符/token

        支持多模态 content（list of blocks），仅计数 text 块。
        """
        total_chars = 0
        for msg in messages:
            content = msg.get("content", "")
            if isinstance(content, str):
                total_chars += len(content)
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        total_chars += len(block.get("text", ""))
        return max(1, total_chars // 4)

    # ==================== 辅助方法 ====================

    def get_stats(self) -> dict[str, Any]:
        """获取 mock provider 当前状态"""
        return {
            "call_count": self.call_count,
            "scripted_pending": len(self._scripted_responses),
            "rule_count": len(self._rules),
            "error_injection_count": len(self._error_injections),
            "latency_ms": self._latency_ms,
        }
