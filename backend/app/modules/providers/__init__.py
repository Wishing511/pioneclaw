"""
Provider 模块 - 多模型提供商管理

借鉴 CountBot 的 Provider 工厂设计，实现：
- Provider 工厂模式
- 多模型支持（OpenAI/Anthropic/本地模型）
- Thinking profiles（思考模式配置）
- Tool parser（工具调用解析）
"""

from .base import BaseProvider, ProviderConfig, ProviderType
from .factory import THINKING_PROFILES, ProviderFactory, get_provider_factory
from .registry import ProviderRegistry, register_provider
from .runtime import KeyRotator, ModelOverride, RuntimeConfig, get_key_rotator

__all__ = [
    "ProviderFactory",
    "ProviderRegistry",
    "BaseProvider",
    "ProviderConfig",
    "ProviderType",
    "KeyRotator",
    "ModelOverride",
    "RuntimeConfig",
    "get_key_rotator",
    "register_provider",
    "get_provider_factory",
    "THINKING_PROFILES",
]
