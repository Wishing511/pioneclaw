"""
插件事件类型定义

借鉴 OpenClaw plugin-sdk event_types

定义插件可订阅的事件类型，以及 PluginEvent 数据类。
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class EventType(str, Enum):
    """事件类型枚举

    借鉴 OpenClaw plugin-sdk EventType
    """

    # Agent 生命周期
    AGENT_START = "agent.start"
    AGENT_COMPLETE = "agent.complete"
    AGENT_ERROR = "agent.error"

    # 工具调用
    TOOL_START = "tool.start"
    TOOL_COMPLETE = "tool.complete"
    TOOL_ERROR = "tool.error"
    TOOL_BLOCKED = "tool.blocked"

    # 工作流
    WORKFLOW_START = "workflow.start"
    WORKFLOW_STEP = "workflow.step"
    WORKFLOW_COMPLETE = "workflow.complete"
    WORKFLOW_WAITING = "workflow.waiting"

    # 技能
    SKILL_LOADED = "skill.loaded"
    SKILL_EXECUTED = "skill.executed"

    # 插件
    PLUGIN_LOADED = "plugin.loaded"
    PLUGIN_UNLOADED = "plugin.unloaded"
    PLUGIN_ERROR = "plugin.error"

    # 系统
    SYSTEM_STARTUP = "system.startup"
    SYSTEM_SHUTDOWN = "system.shutdown"

    # 自定义
    CUSTOM = "custom"


@dataclass
class PluginEvent:
    """插件事件数据类

    借鉴 OpenClaw plugin-sdk PluginEvent
    """

    type: str
    data: dict[str, Any] = field(default_factory=dict)
    source: str = ""  # 事件来源（如 plugin_id）
    timestamp: str | None = None
    event_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "data": self.data,
            "source": self.source,
            "timestamp": self.timestamp,
            "event_id": self.event_id,
        }
