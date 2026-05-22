"""
Tools 模块 - 工具系统（重构后）

包含：
- BaseTool: 工具基类（兼容旧接口 + 新 Layer 2/3 接口）
- ToolRegistry: 工具注册表（增强 ToolSet 组合）
- types: 分层类型定义（ToolDef, ToolDecorator, ToolContext, ToolResult...）
- permissions: 4 层级联权限
- scheduler: 批处理调度器
- hooks: 标准化三阶段 Hook 管理器
- executor: 独立工具执行器（暂不接入，待权限系统就绪后启用）
"""

from app.modules.tools.base import (
    BaseTool,
    ToolDefinition,
    ToolParameter,
)
from app.modules.tools.builtin import (
    CalculatorTool,
    CurrentTimeTool,
    ReadFileTool,
    WriteFileTool,
    register_builtin_tools,
)
from app.modules.tools.hooks import HookManager
from app.modules.tools.permissions import match_rule, resolve_permission
from app.modules.tools.registry import (
    ToolRegistry,
    ToolSet,
    get_tool_registry,
    register_tool,
    register_tool_class,
)
from app.modules.tools.scheduler import (
    Batch,
    get_max_concurrency,
    partition_tool_calls,
    run_concurrent_batch,
    run_serial_batch,
)
from app.modules.tools.task_create import TaskCreateTool
from app.modules.tools.task_get import TaskGetTool
from app.modules.tools.task_list import TaskListTool
from app.modules.tools.task_output import TaskOutputTool
from app.modules.tools.task_stop import TaskStopTool
from app.modules.tools.task_update import TaskUpdateTool
from app.modules.tools.todo_write import TodoWriteTool
from app.modules.tools.types import (
    HookContext,
    HookResult,
    HookType,
    PermissionBehavior,
    PermissionMode,
    PermissionRequest,
    PermissionResult,
    PermissionRule,
    ToolContext,
    ToolDecorator,
    ToolDef,
    ToolHook,
    ToolResult,
    ToolUse,
)
from app.modules.tools.web import WebSearchTool

__all__ = [
    # Base
    "BaseTool",
    "ToolParameter",
    "ToolDefinition",
    # Registry
    "ToolRegistry",
    "ToolSet",
    "get_tool_registry",
    "register_tool",
    "register_tool_class",
    # Types (new Layer 2/3)
    "ToolContext",
    "ToolDef",
    "ToolDecorator",
    "ToolResult",
    "ToolUse",
    "PermissionResult",
    "PermissionRequest",
    "PermissionRule",
    "PermissionBehavior",
    "PermissionMode",
    "HookType",
    "HookContext",
    "HookResult",
    "ToolHook",
    # Permissions
    "resolve_permission",
    "match_rule",
    # Scheduler
    "Batch",
    "partition_tool_calls",
    "run_concurrent_batch",
    "run_serial_batch",
    "get_max_concurrency",
    # Hooks
    "HookManager",
    # Built-in tools
    "CurrentTimeTool",
    "CalculatorTool",
    "WebSearchTool",
    "ReadFileTool",
    "WriteFileTool",
    "register_builtin_tools",
    # Task tools
    "TaskCreateTool",
    "TaskGetTool",
    "TaskListTool",
    "TaskUpdateTool",
    "TaskStopTool",
    "TaskOutputTool",
    "TodoWriteTool",
]
