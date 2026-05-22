"""
Provider 基类 - 所有模型提供商的抽象基类

设计原则：
1. 统一接口：所有 Provider 实现相同的 chat_stream 方法
2. 配置分离：通过 ProviderConfig 注入配置
3. 流式响应：支持 async for 迭代
4. 工具调用：统一解析格式
"""

import logging
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class ProviderType(str, Enum):
    """Provider 类型"""

    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    GOOGLE = "google"
    AZURE = "azure"
    LOCAL = "local"  # 本地模型（Ollama 等）
    CUSTOM = "custom"


@dataclass
class ProviderConfig:
    """Provider 配置"""

    provider_id: str
    provider_type: ProviderType
    name: str

    # API 配置
    api_key: str | None = None
    api_base: str | None = None
    api_version: str | None = None

    # 默认模型
    default_model: str | None = None

    # 请求配置
    timeout: float = 60.0
    max_retries: int = 3

    # 功能支持
    supports_streaming: bool = True
    supports_tools: bool = True
    supports_vision: bool = False
    supports_thinking: bool = False  # Anthropic Extended Thinking

    # 扩展配置
    extra: dict = field(default_factory=dict)

    def validate(self) -> tuple[bool, str]:
        """验证配置"""
        if not self.provider_id:
            return False, "provider_id is required"
        if not self.provider_type:
            return False, "provider_type is required"
        return True, ""


@dataclass
class ChatMessage:
    """聊天消息"""

    role: str  # system/user/assistant/tool
    content: str
    name: str | None = None
    tool_calls: list | None = None
    tool_call_id: str | None = None


@dataclass
class ToolDefinition:
    """工具定义"""

    type: str = "function"
    function: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict) -> "ToolDefinition":
        return cls(
            type=data.get("type", "function"),
            function=data.get("function", {}),
        )


@dataclass
class StreamChunk:
    """流式响应块"""

    delta: dict  # 内容增量
    finish_reason: str | None = None
    usage: dict | None = None
    tool_calls: list | None = None
    thinking: str | None = None  # 思考内容（Anthropic）


class BaseProvider(ABC):
    """
    Provider 抽象基类

    所有模型提供商必须继承此类并实现抽象方法
    """

    def __init__(self, config: ProviderConfig):
        self.config = config
        self._client: Any = None

    @property
    def provider_id(self) -> str:
        return self.config.provider_id

    @property
    def provider_type(self) -> ProviderType:
        return self.config.provider_type

    @abstractmethod
    async def chat_stream(
        self,
        messages: list[ChatMessage],
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        tools: list[ToolDefinition] | None = None,
        **kwargs,
    ) -> AsyncIterator[StreamChunk]:
        """
        流式聊天

        Args:
            messages: 消息列表
            model: 模型名称（可选，使用默认）
            temperature: 温度参数
            max_tokens: 最大 token 数
            tools: 工具定义列表
            **kwargs: 扩展参数

        Yields:
            StreamChunk: 流式响应块
        """
        pass

    @abstractmethod
    async def chat(
        self,
        messages: list[ChatMessage],
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        tools: list[ToolDefinition] | None = None,
        **kwargs,
    ) -> dict:
        """
        非流式聊天（一次性返回完整响应）

        Returns:
            dict: 完整响应
        """
        pass

    async def count_tokens(
        self,
        messages: list[ChatMessage],
        model: str | None = None,
    ) -> int:
        """计算 token 数量（可选实现）"""
        # 默认估算：每 4 个字符约 1 个 token
        total = 0
        for msg in messages:
            total += len(msg.content) // 4
        return total

    def get_info(self) -> dict:
        """获取 Provider 信息"""
        return {
            "provider_id": self.provider_id,
            "provider_type": self.provider_type.value,
            "name": self.config.name,
            "default_model": self.config.default_model,
            "supports_streaming": self.config.supports_streaming,
            "supports_tools": self.config.supports_tools,
            "supports_vision": self.config.supports_vision,
            "supports_thinking": self.config.supports_thinking,
        }

    def _prepare_messages(self, messages: list[ChatMessage]) -> list[dict]:
        """准备消息格式"""
        result = []
        for msg in messages:
            item = {"role": msg.role, "content": msg.content}
            if msg.name:
                item["name"] = msg.name
            if msg.tool_calls:
                item["tool_calls"] = msg.tool_calls
            if msg.tool_call_id:
                item["tool_call_id"] = msg.tool_call_id
            result.append(item)
        return result

    def _prepare_tools(self, tools: list[ToolDefinition] | None) -> list[dict] | None:
        """准备工具格式"""
        if not tools:
            return None
        return [{"type": t.type, "function": t.function} for t in tools]
