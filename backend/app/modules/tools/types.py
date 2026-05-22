"""
分层工具接口 - 将 ai-agent-toolkit 架构移植到 Python

Layer 2: ToolDef — 核心工具定义（必需）
Layer 3: ToolDecorator — 可选增强（权限、并发、进度等）
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from app.modules.tools.base import ToolDefinition

# ============================================================================
# Permission System
# ============================================================================


class PermissionBehavior(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"


@dataclass
class PermissionResult:
    behavior: PermissionBehavior
    reason: str = ""
    message: str = ""
    updated_input: dict[str, Any] | None = None
    user_modified: bool = False


@dataclass
class PermissionRequest:
    tool: str
    action: str
    description: str = ""
    patterns: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class PermissionRule:
    tool: str  # 工具ID 或 "*"
    pattern: str | None = None  # 可选的命令/路径模式
    behavior: PermissionBehavior = PermissionBehavior.ALLOW
    source: str = "config"


PermissionMode = str  # "default" | "yolo" | "plan" | "ask"


# ============================================================================
# Execution Context & Result
# ============================================================================


@dataclass
class ToolContext:
    """执行上下文（替换散落在 AgentLoop 中的参数传递）"""

    session_id: str
    message_id: str
    working_dir: str
    agent_id: str = ""
    agent_name: str = ""
    abort_signal: Any = None  # asyncio.Event

    # 回调
    metadata_callback: Callable[[str, Any], None] | None = None
    ask_callback: Callable[[PermissionRequest], Any] | None = None  # 权限询问

    def metadata(self, key: str, value: Any) -> None:
        if self.metadata_callback:
            self.metadata_callback(key, value)

    async def ask(self, request: PermissionRequest) -> PermissionResult:
        if self.ask_callback:
            result = self.ask_callback(request)
            if asyncio.iscoroutine(result):
                return await result
            return result
        return PermissionResult(
            behavior=PermissionBehavior.DENY,
            reason="default_deny",
            message=f"Permission required: {request.tool}",
        )


@dataclass
class ToolResult:
    output: str
    metadata: dict[str, Any] = field(default_factory=dict)
    attachments: list[dict] = field(default_factory=list)


# ============================================================================
# Tool Use (from LLM)
# ============================================================================


@dataclass
class ToolUse:
    id: str  # tool_use_id from LLM
    tool_id: str  # which tool to call
    input: dict[str, Any]


# ============================================================================
# Hook System
# ============================================================================


class HookType(str, Enum):
    PRE_TOOL_USE = "pre_tool_use"
    POST_TOOL_USE = "post_tool_use"
    TRANSFORM_RESULT = "transform_result"


@dataclass
class HookContext:
    tool: Any  # Tool
    input: dict[str, Any]
    result: ToolResult | None = None
    ctx: ToolContext | None = None


@dataclass
class HookResult:
    block: bool = False
    message: str = ""
    transformed_result: ToolResult | None = None
    modified_args: dict[str, Any] | None = None


ToolHook = Callable[[HookContext], Any]  # returns HookResult | None | Awaitable


# ============================================================================
# Layer 2: Core Tool Definition
# ============================================================================


class ToolDef(ABC):
    """Layer 2: 工具定义（必需）

    所有工具必须实现这 4 个字段 + execute 方法。
    """

    id: str
    description: str
    parameters: dict[str, Any]  # JSON Schema 格式

    @abstractmethod
    async def execute(self, input: dict[str, Any], ctx: ToolContext) -> ToolResult:
        """核心执行逻辑"""
        ...

    def get_definition(self) -> ToolDefinition:
        """获取工具定义（兼容旧注册表）"""
        from app.modules.tools.base import ToolDefinition, ToolParameter

        params: dict[str, Any] = {}
        for pname, param in self.parameters.items():
            if isinstance(param, ToolParameter):
                params[pname] = {
                    "type": param.type,
                    "description": param.description,
                    **({"enum": param.enum} if param.enum else {}),
                    **({"default": param.default} if param.default is not None else {}),
                }
            elif isinstance(param, dict):
                params[pname] = param
            else:
                params[pname] = {"type": "string", "description": str(param)}

        return ToolDefinition(
            name=self.id,
            description=self.description,
            parameters=params,
            required=getattr(self, "required", []),
        )


# ============================================================================
# Layer 3: Tool Decorator (optional enhancements)
# ============================================================================


class ToolDecorator(Protocol):
    """Layer 3: 可选增强

    工具类可以按需实现这些方法来获得细粒度控制。
    """

    def check_permissions(
        self, input: dict[str, Any], ctx: ToolContext
    ) -> PermissionResult: ...

    def is_read_only(self, input: dict[str, Any]) -> bool: ...

    def is_concurrency_safe(self, input: dict[str, Any]) -> bool: ...

    def is_destructive(self, input: dict[str, Any]) -> bool: ...

    def on_progress(self, data: Any, ctx: ToolContext) -> None: ...

    max_result_size: int = 0


# Complete tool type = definition + optional decorator
Tool = ToolDef
