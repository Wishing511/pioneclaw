"""
Provider 注册表 - 管理所有可用的 Provider 类型

功能：
1. 注册 Provider 类型
2. 根据 ProviderType 获取 Provider 类
3. 支持动态注册自定义 Provider
"""

import logging
from collections.abc import Callable

from .base import BaseProvider, ProviderType

logger = logging.getLogger(__name__)

# 全局注册表
_provider_registry: dict[ProviderType, type[BaseProvider]] = {}
_provider_factories: dict[ProviderType, Callable] = {}


def register_provider(
    provider_type: ProviderType,
    provider_class: type[BaseProvider],
    factory: Callable | None = None,
) -> None:
    """
    注册 Provider

    Args:
        provider_type: Provider 类型
        provider_class: Provider 类
        factory: 可选的工厂函数（用于创建 Provider 实例）
    """
    _provider_registry[provider_type] = provider_class
    if factory:
        _provider_factories[provider_type] = factory
    logger.info(f"Registered provider: {provider_type.value}")


def get_provider_class(provider_type: ProviderType) -> type[BaseProvider] | None:
    """获取 Provider 类"""
    return _provider_registry.get(provider_type)


def get_provider_factory_func(provider_type: ProviderType) -> Callable | None:
    """获取 Provider 工厂函数"""
    return _provider_factories.get(provider_type)


def list_providers() -> list[ProviderType]:
    """列出所有已注册的 Provider 类型"""
    return list(_provider_registry.keys())


class ProviderRegistry:
    """
    Provider 注册表类

    提供更完整的注册和管理功能
    """

    def __init__(self):
        self._providers: dict[str, BaseProvider] = {}
        self._types: dict[ProviderType, type[BaseProvider]] = {}

    def register_type(
        self,
        provider_type: ProviderType,
        provider_class: type[BaseProvider],
    ) -> None:
        """注册 Provider 类型"""
        self._types[provider_type] = provider_class

    def create(
        self,
        provider_type: ProviderType,
        config: dict,
    ) -> BaseProvider | None:
        """创建 Provider 实例"""
        provider_class = self._types.get(provider_type)
        if not provider_class:
            return None

        from .base import ProviderConfig

        provider_config = ProviderConfig(**config)
        return provider_class(provider_config)

    def register_instance(self, provider: BaseProvider) -> None:
        """注册 Provider 实例"""
        self._providers[provider.provider_id] = provider

    def get(self, provider_id: str) -> BaseProvider | None:
        """获取 Provider 实例"""
        return self._providers.get(provider_id)

    def list_instances(self) -> list[BaseProvider]:
        """列出所有 Provider 实例"""
        return list(self._providers.values())

    def remove(self, provider_id: str) -> bool:
        """移除 Provider 实例"""
        if provider_id in self._providers:
            del self._providers[provider_id]
            return True
        return False


# 全局注册表实例
registry = ProviderRegistry()
