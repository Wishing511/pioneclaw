"""
工具注册表 - 增强版（ToolSet 组合 + 兼容旧接口）

功能：
- 工具注册 / 注销
- 工具定义获取（OpenAI Function Calling 格式）
- 工具执行（含参数验证 + 取消检查）
- ToolSet 组合系统（继承 + 钻石消解）
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from app.core.recovery_recipes import RecoverableToolError
from app.modules.tools.base import BaseTool

logger = logging.getLogger(__name__)


@dataclass
class ToolSet:
    """工具集：支持平台级启用/禁用（如 file、web、telegram）"""

    name: str
    extends: list[str] = field(default_factory=list)
    tools: set[str] = field(default_factory=set)


class ToolRegistry:
    """工具注册表（增强版）"""

    def __init__(self):
        self._tools: dict[str, BaseTool] = {}
        self._toolsets: dict[str, ToolSet] = {}
        self._enabled_toolsets: set[str] = {"default"}

        self._session_id: str | None = None
        self._channel: str | None = None
        self._cancel_token = None

        logger.debug("ToolRegistry initialized")

    # ------------------------------------------------------------------
    # 注册（兼容旧接口）
    # ------------------------------------------------------------------

    def register(self, tool: BaseTool) -> None:
        self._tools[tool.id] = tool
        logger.debug(f"Tool registered: {tool.id}")

    def register_class(self, tool_class: type[BaseTool]) -> None:
        tool = tool_class()
        self.register(tool)

    def unregister(self, name: str) -> bool:
        if name in self._tools:
            del self._tools[name]
            logger.debug(f"Tool unregistered: {name}")
            return True
        return False

    # ------------------------------------------------------------------
    # ToolSets（新增）
    # ------------------------------------------------------------------

    def define_toolset(self, name: str, tools=None, extends=None) -> None:
        self._toolsets[name] = ToolSet(
            name=name,
            extends=list(extends or []),
            tools=set(tools or []),
        )

    def enable_toolset(self, name: str) -> None:
        self._enabled_toolsets.add(name)

    def disable_toolset(self, name: str) -> None:
        self._enabled_toolsets.discard(name)

    # ------------------------------------------------------------------
    # 查询（兼容旧接口 + 新增）
    # ------------------------------------------------------------------

    def get_tool(self, name: str) -> BaseTool | None:
        return self._tools.get(name)

    def has_tool(self, name: str) -> bool:
        return name in self._tools

    def list_tools(self) -> list[str]:
        return list(self._tools.keys())

    def get_definitions(self) -> list[dict[str, Any]]:
        """获取所有已注册工具的定义（旧格式，OpenAI Function Calling）"""
        return [
            tool.get_definition().to_openai_format() for tool in self._tools.values()
        ]

    def get_available_tools(self) -> list[BaseTool]:
        """按启用的 toolsets 过滤后返回工具实例列表"""
        allowed = self._resolve_allowed_ids()
        return [t for t in self._tools.values() if t.id in allowed]

    def get_available_definitions(self) -> list[dict[str, Any]]:
        """按启用的 toolsets 过滤后返回 OpenAI 格式定义"""
        allowed = self._resolve_allowed_ids()
        return [
            t.get_definition().to_openai_format()
            for t in self._tools.values()
            if t.id in allowed
        ]

    def _resolve_allowed_ids(self) -> set[str]:
        """支持继承 + 钻石消解"""
        resolved: set[str] = set()
        visited: set[str] = set()

        def visit(name: str) -> None:
            if name in visited:
                return
            visited.add(name)
            ts = self._toolsets.get(name)
            if not ts:
                return
            for parent in ts.extends:
                visit(parent)
            resolved.update(ts.tools)

        for name in self._enabled_toolsets:
            visit(name)

        # 如果没有任何 toolset 定义，默认允许所有已注册工具
        if not resolved and "default" in self._enabled_toolsets:
            return set(self._tools.keys())

        return resolved

    # ------------------------------------------------------------------
    # 执行（兼容旧接口）
    # ------------------------------------------------------------------

    async def execute(
        self,
        name: str,
        arguments: dict[str, Any],
        auto_record: bool = True,
    ) -> str:
        """执行工具（旧接口，返回 str）"""
        tool = self.get_tool(name)
        if not tool:
            error_msg = f"Tool not found: {name}"
            logger.error(error_msg)
            return f"Error: {error_msg}"

        valid, error = tool.validate_arguments(arguments)
        if not valid:
            logger.error(f"Tool {name} validation failed: {error}")
            return f"Error: {error}"

        if self._cancel_token and getattr(self._cancel_token, "is_cancelled", False):
            return "Error: Execution cancelled"

        logger.info(f"Executing tool: {name} with arguments: {arguments}")

        try:
            result = await tool.execute(**arguments)
            display = (
                result[:100]
                if isinstance(result, str) and len(result) > 100
                else result
            )
            logger.debug(f"Tool {name} result: {display}")
            return result
        except RecoverableToolError:
            raise
        except Exception as e:
            error_msg = f"Tool execution failed: {name} - {e}"
            logger.error(error_msg)
            return f"Error: {e}"

    # ------------------------------------------------------------------
    # 会话管理（兼容旧接口）
    # ------------------------------------------------------------------

    def set_session_id(self, session_id: str) -> None:
        self._session_id = session_id

    def set_channel(self, channel: str | None) -> None:
        self._channel = channel

    def set_cancel_token(self, cancel_token) -> None:
        self._cancel_token = cancel_token

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: str) -> bool:
        return name in self._tools


# 全局工具注册表实例
_global_registry: ToolRegistry | None = None


def get_tool_registry() -> ToolRegistry:
    global _global_registry
    if _global_registry is None:
        _global_registry = ToolRegistry()
    return _global_registry


def register_tool(tool: BaseTool) -> None:
    get_tool_registry().register(tool)


def register_tool_class(tool_class: type[BaseTool]) -> None:
    get_tool_registry().register_class(tool_class)
