"""
OpenAI Provider - OpenAI 兼容 API 实现

支持：
- OpenAI GPT 系列
- Azure OpenAI
- 其他兼容 OpenAI API 的服务
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


class OpenAIProvider(BaseProvider):
    """OpenAI 兼容 API Provider"""

    def __init__(self, config: ProviderConfig):
        super().__init__(config)
        self.api_key = config.api_key
        self.api_base = config.api_base or "https://api.openai.com/v1"
        self.default_model = config.default_model or "gpt-4o"
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        """获取 HTTP 客户端"""
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=self.config.timeout,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
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
        **kwargs,
    ) -> AsyncIterator[StreamChunk]:
        """流式聊天"""
        client = await self._get_client()
        model = model or self.default_model

        # 构建请求体
        body = {
            "model": model,
            "messages": self._prepare_messages(messages),
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
        }

        # 添加工具
        if tools:
            body["tools"] = self._prepare_tools(tools)

        # 扩展参数
        for key in ["top_p", "presence_penalty", "frequency_penalty", "stop"]:
            if key in kwargs:
                body[key] = kwargs[key]

        try:
            async with client.stream(
                "POST",
                f"{self.api_base}/chat/completions",
                json=body,
            ) as response:
                response.raise_for_status()

                async for line in response.aiter_lines():
                    if not line or line == "data: [DONE]":
                        continue

                    if line.startswith("data: "):
                        data_str = line[6:]
                        try:
                            data = json.loads(data_str)
                            chunk = self._parse_stream_chunk(data)
                            if chunk:
                                yield chunk
                        except json.JSONDecodeError:
                            continue

        except httpx.HTTPStatusError as e:
            logger.error(f"OpenAI API error: {e}")
            yield StreamChunk(
                delta={"content": f"\n\n[API 错误: {e.response.status_code}]"},
                finish_reason="error",
            )
        except Exception as e:
            logger.error(f"OpenAI stream error: {e}")
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
            "messages": self._prepare_messages(messages),
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }

        if tools:
            body["tools"] = self._prepare_tools(tools)

        try:
            response = await client.post(
                f"{self.api_base}/chat/completions",
                json=body,
            )
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"OpenAI chat error: {e}")
            return {"error": str(e)}

    def _parse_stream_chunk(self, data: dict) -> StreamChunk | None:
        """解析流式响应块"""
        choices = data.get("choices", [])
        if not choices:
            return None

        choice = choices[0]
        delta = choice.get("delta", {})
        finish_reason = choice.get("finish_reason")

        return StreamChunk(
            delta=delta,
            finish_reason=finish_reason,
            usage=data.get("usage"),
            tool_calls=delta.get("tool_calls"),
        )

    async def close(self) -> None:
        """关闭客户端"""
        if self._client:
            await self._client.aclose()
            self._client = None


class AzureOpenAIProvider(OpenAIProvider):
    """Azure OpenAI Provider"""

    def __init__(self, config: ProviderConfig):
        super().__init__(config)
        # Azure 使用不同的认证方式
        self.api_version = config.api_version or "2024-02-15-preview"

    async def chat_stream(
        self,
        messages: list[ChatMessage],
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        tools: list[ToolDefinition] | None = None,
        **kwargs,
    ) -> AsyncIterator[StreamChunk]:
        """Azure 流式聊天"""
        # Azure 使用 deployment name 作为 model
        deployment = model or self.default_model

        # 修改 API base 格式
        # https://{resource}.openai.azure.com/openai/deployments/{deployment}/chat/completions?api-version={version}
        url = f"{self.api_base}/openai/deployments/{deployment}/chat/completions"

        client = await self._get_client()

        body = {
            "messages": self._prepare_messages(messages),
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
        }

        if tools:
            body["tools"] = self._prepare_tools(tools)

        # Azure 使用 api-key header
        headers = {"api-key": self.api_key}

        try:
            async with client.stream(
                "POST",
                f"{url}?api-version={self.api_version}",
                json=body,
                headers=headers,
            ) as response:
                response.raise_for_status()

                async for line in response.aiter_lines():
                    if not line or line == "data: [DONE]":
                        continue

                    if line.startswith("data: "):
                        data_str = line[6:]
                        try:
                            data = json.loads(data_str)
                            chunk = self._parse_stream_chunk(data)
                            if chunk:
                                yield chunk
                        except json.JSONDecodeError:
                            continue

        except Exception as e:
            logger.error(f"Azure OpenAI stream error: {e}")
            yield StreamChunk(
                delta={"content": f"\n\n[错误: {e}]"},
                finish_reason="error",
            )


# 注册 Provider
from .registry import register_provider  # noqa: E402

register_provider(ProviderType.OPENAI, OpenAIProvider)
register_provider(ProviderType.AZURE, AzureOpenAIProvider)
