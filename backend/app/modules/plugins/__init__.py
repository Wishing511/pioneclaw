"""
插件系统

提供插件发现、加载、卸载、事件订阅/发布功能。
"""

from .event_bus import EventBus, EventHandler
from .lifecycle import PluginLifecycle, StateTransition
from .manager import PluginInfo, PluginManager, PluginState
from .sdk import (
    EventType,
    PioneClawPlugin,
    PluginEvent,
    clear_runtime_context,
    get_config,
    get_db_session,
    get_event_bus,
    plugin_metadata,
    set_runtime_context,
)

__all__ = [
    "EventBus",
    "EventHandler",
    "PluginManager",
    "PluginInfo",
    "PluginState",
    "PluginLifecycle",
    "StateTransition",
    # SDK
    "PioneClawPlugin",
    "plugin_metadata",
    "PluginEvent",
    "EventType",
    "get_event_bus",
    "get_config",
    "get_db_session",
    "set_runtime_context",
    "clear_runtime_context",
]
