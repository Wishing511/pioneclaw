"""
插件运行时 API

借鉴 OpenClaw plugin-sdk runtime

提供插件可调用的运行时功能：事件总线、配置、数据库会话。
"""

from typing import Any

# 全局运行时上下文（由 PluginManager 在加载时设置）
_runtime_context: dict[str, Any] = {}


def set_runtime_context(
    event_bus=None, config: dict | None = None, db_session_factory=None
) -> None:
    """设置运行时上下文（PluginManager 调用）"""
    _runtime_context["event_bus"] = event_bus
    _runtime_context["config"] = config or {}
    _runtime_context["db_session_factory"] = db_session_factory


def get_event_bus():
    """获取事件总线实例"""
    return _runtime_context.get("event_bus")


def get_config(key: str = "", default: Any = None) -> Any:
    """获取插件配置

    Args:
        key: 配置键，空字符串返回全部配置
        default: 默认值
    """
    config = _runtime_context.get("config", {})
    if not key:
        return config
    return config.get(key, default)


def get_db_session():
    """获取数据库会话工厂"""
    return _runtime_context.get("db_session_factory")


def clear_runtime_context() -> None:
    """清除运行时上下文（测试用）"""
    _runtime_context.clear()
