"""
Provider 工厂 - 创建和管理 Provider 实例

功能：
1. 根据配置自动创建 Provider
2. Key 轮换管理
3. 模型覆盖配置
4. Thinking profiles
"""

import logging
from dataclasses import dataclass, field

from .base import BaseProvider, ProviderConfig, ProviderType
from .registry import get_provider_class, list_providers

logger = logging.getLogger(__name__)


# ==================== Key 轮换 ====================


@dataclass
class KeyRotator:
    """
    API Key 轮换器

    支持多个 API Key 轮换使用，自动跳过失效的 Key
    """

    keys: list[str] = field(default_factory=list)
    current_index: int = 0
    failed_keys: set = field(default_factory=set)

    def add_key(self, key: str) -> None:
        """添加 Key"""
        if key and key not in self.keys:
            self.keys.append(key)

    def get_key(self) -> str | None:
        """获取当前 Key"""
        if not self.keys:
            return None

        # 跳过已失败的 Key
        attempts = 0
        while attempts < len(self.keys):
            key = self.keys[self.current_index]
            self.current_index = (self.current_index + 1) % len(self.keys)

            if key not in self.failed_keys:
                return key
            attempts += 1

        return None

    def mark_failed(self, key: str) -> None:
        """标记 Key 失败"""
        self.failed_keys.add(key)
        logger.warning(f"API key marked as failed: {key[:8]}...")

    def reset_failed(self) -> None:
        """重置失败的 Key"""
        self.failed_keys.clear()

    def has_available_keys(self) -> bool:
        """是否有可用的 Key"""
        return len(self.keys) > len(self.failed_keys)


# ==================== 模型覆盖 ====================


@dataclass
class ModelOverride:
    """
    模型覆盖配置

    用于在运行时覆盖默认模型配置
    """

    model: str | None = None
    provider: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    api_key: str | None = None
    api_base: str | None = None

    # Thinking 模式
    thinking_enabled: bool | None = None
    thinking_budget: int | None = None  # 思考 token 预算

    def apply_to(self, config: dict) -> dict:
        """应用覆盖到配置"""
        result = config.copy()

        if self.model:
            result["model"] = self.model
        if self.provider:
            result["provider"] = self.provider
        if self.temperature is not None:
            result["temperature"] = self.temperature
        if self.max_tokens is not None:
            result["max_tokens"] = self.max_tokens
        if self.api_key:
            result["api_key"] = self.api_key
        if self.api_base:
            result["api_base"] = self.api_base
        if self.thinking_enabled is not None:
            result["thinking_enabled"] = self.thinking_enabled
        if self.thinking_budget is not None:
            result["thinking_budget"] = self.thinking_budget

        return result


# ==================== 运行时配置 ====================


@dataclass
class RuntimeConfig:
    """
    运行时配置

    包含 Provider、Key 轮换、模型覆盖等
    """

    provider_type: ProviderType
    model: str
    api_keys: list[str] = field(default_factory=list)
    api_base: str | None = None

    temperature: float = 0.7
    max_tokens: int = 4096

    # Key 轮换
    key_rotator: KeyRotator | None = None

    # 模型覆盖
    model_override: ModelOverride | None = None

    # Thinking
    thinking_enabled: bool = False
    thinking_budget: int = 10000

    def __post_init__(self):
        if self.api_keys and not self.key_rotator:
            self.key_rotator = KeyRotator(keys=self.api_keys)

    def get_api_key(self) -> str | None:
        """获取 API Key"""
        if self.model_override and self.model_override.api_key:
            return self.model_override.api_key
        if self.key_rotator:
            return self.key_rotator.get_key()
        return self.api_keys[0] if self.api_keys else None

    def get_model(self) -> str:
        """获取模型名称"""
        if self.model_override and self.model_override.model:
            return self.model_override.model
        return self.model

    def get_temperature(self) -> float:
        """获取温度"""
        if self.model_override and self.model_override.temperature is not None:
            return self.model_override.temperature
        return self.temperature

    def get_max_tokens(self) -> int:
        """获取最大 token"""
        if self.model_override and self.model_override.max_tokens is not None:
            return self.model_override.max_tokens
        return self.max_tokens


# ==================== Thinking Profiles ====================


@dataclass
class ThinkingProfile:
    """
    思考模式配置

    用于 Anthropic Extended Thinking 等支持思考的模型
    """

    name: str
    enabled: bool = True
    budget_tokens: int = 10000
    max_thinking_tokens: int = 5000

    # 输出控制
    show_thinking: bool = False  # 是否在响应中显示思考过程
    collapse_thinking: bool = True  # 是否折叠思考内容


# 预设的 Thinking Profiles
THINKING_PROFILES = {
    "default": ThinkingProfile(
        name="default",
        enabled=True,
        budget_tokens=10000,
    ),
    "deep": ThinkingProfile(
        name="deep",
        enabled=True,
        budget_tokens=32000,
        max_thinking_tokens=16000,
    ),
    "quick": ThinkingProfile(
        name="quick",
        enabled=True,
        budget_tokens=5000,
        max_thinking_tokens=2500,
    ),
    "disabled": ThinkingProfile(
        name="disabled",
        enabled=False,
    ),
}


# ==================== Provider 工厂 ====================


class ProviderFactory:
    """
    Provider 工厂

    根据 ProviderType 和配置创建 Provider 实例
    """

    def __init__(self):
        self._instances: dict[str, BaseProvider] = {}
        self._configs: dict[str, ProviderConfig] = {}
        self._key_rotators: dict[str, KeyRotator] = {}

    def register_config(self, config: ProviderConfig) -> None:
        """注册 Provider 配置"""
        self._configs[config.provider_id] = config

        # 如果有多个 API Key，创建 KeyRotator
        keys = config.extra.get("api_keys", [])
        if config.api_key:
            keys = [config.api_key] + keys
        if keys:
            self._key_rotators[config.provider_id] = KeyRotator(keys=keys)

    def create(self, provider_id: str) -> BaseProvider | None:
        """创建 Provider 实例"""
        # 检查缓存
        if provider_id in self._instances:
            return self._instances[provider_id]

        # 获取配置
        config = self._configs.get(provider_id)
        if not config:
            logger.error(f"Provider config not found: {provider_id}")
            return None

        # 获取 Provider 类
        provider_class = get_provider_class(config.provider_type)
        if not provider_class:
            logger.error(f"Provider class not found: {config.provider_type}")
            return None

        # 创建实例
        try:
            instance = provider_class(config)
            self._instances[provider_id] = instance
            logger.info(f"Created provider instance: {provider_id}")
            return instance
        except Exception as e:
            logger.error(f"Failed to create provider {provider_id}: {e}")
            return None

    def get(self, provider_id: str) -> BaseProvider | None:
        """获取 Provider 实例"""
        return self._instances.get(provider_id) or self.create(provider_id)

    def get_key_rotator(self, provider_id: str) -> KeyRotator | None:
        """获取 Key 轮换器"""
        return self._key_rotators.get(provider_id)

    def get_available_providers(self) -> list[str]:
        """获取可用的 Provider ID 列表"""
        return list(self._configs.keys())

    def get_supported_types(self) -> list[str]:
        """获取支持的 Provider 类型"""
        return [t.value for t in list_providers()]


# 全局工厂实例
_factory: ProviderFactory | None = None


def get_provider_factory() -> ProviderFactory:
    """获取全局 Provider 工厂"""
    global _factory
    if _factory is None:
        _factory = ProviderFactory()
    return _factory
