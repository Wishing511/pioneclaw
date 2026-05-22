"""
Anthropic Provider - Claude API 实现

支持：
- Claude 3.5 Sonnet
- Claude 3.5 Haiku
- Claude 3 Opus
- Extended Thinking（思考模式）
"""

import json
import logging
from collections.abc import AsyncIterator

import httpx

from .base import (
    BaseProvider,
    ChatMessage,
    ProviderConfig,
    ProviderType,
    StreamChunk,
    ToolDefinition,
)

logger = logging.getLogger(__name__)


class AnthropicProvider(BaseProvider):
    """Anthropic Claude Provider"""

    API_BASE = "https://api.anthropic.com/v1"

    def __init__(self, config: ProviderConfig):
        super().__init__(config)
        self.api_key = config.api_key
        self.api_base = config.api_base or self.API_BASE
        self.default_model = config.default_model or "claude-sonnet-4-20250514"
        self._client: httpx.AsyncClient | None = None

        # Anthropic 支持 Extended Thinking
        self.config.supports_thinking = True

    async def _get_client(self) -> httpx.AsyncClient:
        """获取 HTTP 客户端"""
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=self.config.timeout,
                headers={
                    "x-api-key": self.api_key,
                    "anthropic-version": "2023-06-01",
                    "Content-Type": "application/json",
                },
            )
        return self._client

    async def chat_stream(
        self,
        messages: list[ChatMessage],
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        tools: list[ToolDefinition] | None = None,
        thinking_enabled: bool = False,
        thinking_budget: int = 10000,
        **kwargs,
    ) -> AsyncIterator[StreamChunk]:
        """流式聊天"""
        client = await self._get_client()
        model = model or self.default_model

        # 构建请求体
        body = {
            "model": model,
            "messages": self._prepare_messages_anthropic(messages),
            "max_tokens": max_tokens,
            "stream": True,
        }

        # 添加系统提示（Anthropic 使用单独的 system 字段）
        system_prompt = kwargs.get("system")
        if system_prompt:
            body["system"] = system_prompt

        # 添加工具
        if tools:
            body["tools"] = self._prepare_tools_anthropic(tools)

        # Extended Thinking
        if thinking_enabled:
            body["thinking"] = {
                "type": "enabled",
                "budget_tokens": thinking_budget,
            }

        try:
            async with client.stream(
                "POST",
                f"{self.api_base}/messages",
                json=body,
            ) as response:
                response.raise_for_status()

                async for line in response.aiter_lines():
                    if not line:
                        continue

                    if line.startswith("data: "):
                        data_str = line[6:]
                        try:
                            data = json.loads(data_str)
                            chunk = self._parse_stream_event(data)
                            if chunk:
                                yield chunk
                        except json.JSONDecodeError:
                            continue

        except httpx.HTTPStatusError as e:
            logger.error(f"Anthropic API error: {e}")
            yield StreamChunk(
                delta={"content": f"\n\n[API 错误: {e.response.status_code}]"},
                finish_reason="error",
            )
        except Exception as e:
            logger.error(f"Anthropic stream error: {e}")
            yield StreamChunk(
                delta={"content": f"\n\n[错误: {e}]"},
                finish_reason="error",
            )

    async def chat(
        self,
        messages: list[ChatMessage],
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        tools: list[ToolDefinition] | None = None,
        **kwargs,
    ) -> dict:
        """非流式聊天"""
        client = await self._get_client()
        model = model or self.default_model

        body = {
            "model": model,
            "messages": self._prepare_messages_anthropic(messages),
            "max_tokens": max_tokens,
        }

        if kwargs.get("system"):
            body["system"] = kwargs["system"]

        if tools:
            body["tools"] = self._prepare_tools_anthropic(tools)

        try:
            response = await client.post(
                f"{self.api_base}/messages",
                json=body,
            )
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"Anthropic chat error: {e}")
            return {"error": str(e)}

    def _prepare_messages_anthropic(self, messages: list[ChatMessage]) -> list[dict]:
        """准备 Anthropic 格式的消息"""
        result = []
        for msg in messages:
            # 跳过 system 消息（Anthropic 使用单独的 system 字段）
            if msg.role == "system":
                continue

            item = {"role": msg.role, "content": msg.content}

            # 处理工具调用
            if msg.tool_calls:
                item["content"] = [
                    {"type": "text", "text": msg.content},
                ]
                for tc in msg.tool_calls:
                    item["content"].append(
                        {
                            "type": "tool_use",
                            "id": tc.get("id", ""),
                            "name": tc.get("function", {}).get("name", ""),
                            "input": tc.get("function", {}).get("arguments", {}),
                        }
                    )

            # 处理工具结果
            if msg.role == "tool":
                item = {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": msg.tool_call_id,
                            "content": msg.content,
                        }
                    ],
                }

            result.append(item)
        return result

    def _prepare_tools_anthropic(self, tools: list[ToolDefinition]) -> list[dict]:
        """准备 Anthropic 格式的工具"""
        result = []
        for t in tools:
            func = t.function
            result.append(
                {
                    "name": func.get("name", ""),
                    "description": func.get("description", ""),
                    "input_schema": func.get("parameters", {}),
                }
            )
        return result

    def _parse_stream_event(self, data: dict) -> StreamChunk | None:
        """解析 Anthropic 流式事件"""
        event_type = data.get("type")

        if event_type == "content_block_delta":
            delta = data.get("delta", {})
            index = data.get("index", 0)

            # 文本内容
            if delta.get("type") == "text_delta":
                return StreamChunk(
                    delta={"content": delta.get("text", "")},
                )

            # 思考内容
            if delta.get("type") == "thinking_delta":
                return StreamChunk(
                    delta={},
                    thinking=delta.get("thinking", ""),
                )

            # 工具调用
            if delta.get("type") == "input_json_delta":
                return StreamChunk(
                    delta={},
                    tool_calls=[
                        {
                            "index": index,
                            "function": {
                                "arguments": delta.get("partial_json", ""),
                            },
                        }
                    ],
                )

        elif event_type == "content_block_start":
            block = data.get("content_block", {})
            index = data.get("index", 0)

            # 工具调用开始
            if block.get("type") == "tool_use":
                return StreamChunk(
                    delta={},
                    tool_calls=[
                        {
                            "index": index,
                            "id": block.get("id", ""),
                            "type": "function",
                            "function": {
                                "name": block.get("name", ""),
                                "arguments": "",
                            },
                        }
                    ],
                )

        elif event_type == "message_stop":
            return StreamChunk(
                delta={},
                finish_reason="stop",
            )

        elif event_type == "message_delta":
            usage = data.get("usage", {})
            return StreamChunk(
                delta={},
                usage=usage,
                finish_reason=data.get("stop_reason"),
            )

        return None

    async def close(self) -> None:
        """关闭客户端"""
        if self._client:
            await self._client.aclose()
            self._client = None


# 注册 Provider
from .registry import register_provider  # noqa: E402

register_provider(ProviderType.ANTHROPIC, AnthropicProvider)
