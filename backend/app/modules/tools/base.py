"""
工具基类 - 重构为 ToolDef + ToolDecorator 兼容层

所有工具继续继承 BaseTool，同时自动获得 Layer 2 (ToolDef) 和 Layer 3 (ToolDecorator) 能力。
旧接口 execute(**kwargs) -> str 保持不变；新接口 execute(input, ctx) -> ToolResult
在基类中做自动转换。
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

from app.modules.tools.types import (
    PermissionBehavior,
    PermissionResult,
    ToolContext,
    ToolDecorator,
    ToolDef,
    ToolResult,
)

logger = logging.getLogger(__name__)


class ToolParameter(BaseModel):
    """工具参数定义（保留兼容旧代码）"""

    type: str = "string"
    description: str = ""
    enum: list[str] | None = None
    default: Any | None = None


@dataclass
class ToolDefinition:
    """工具定义 - OpenAI Function Calling 格式（保留兼容旧代码）"""

    name: str
    description: str
    parameters: dict[str, Any]
    required: list[str] = field(default_factory=list)

    def to_openai_format(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": self.parameters,
                    "required": self.required,
                },
            },
        }


class BaseTool(ToolDef, ToolDecorator, ABC):
    """
    工具基类（重构后）

    同时兼容旧接口和新接口：
    - 旧: class MyTool(BaseTool): name = "x"; async def execute(self, **kwargs) -> str
    - 新: 自动提供 execute(input, ctx) -> ToolResult，内部调用旧的 execute(**input)

    Layer 3 (ToolDecorator) 的默认实现：
    - is_concurrency_safe / is_read_only 继承自类属性 is_parallel_safe
    """

    # Layer 2 必需字段
    id: str = "base_tool"
    description: str = "Base tool class"
    parameters: dict[str, Any] = {}  # JSON Schema 或 ToolParameter dict
    required: list[str] = []

    # Layer 3 可选字段（旧代码兼容）
    is_parallel_safe: bool = False
    max_result_size: int = 0

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        # 如果子类定义了 name 但没有定义 id（或 id 仍是基类默认值），
        # 自动将 id 同步为 name，保证注册表用 id 查找时正常工作
        if hasattr(cls, "name") and cls.name != "base_tool":
            if not hasattr(cls, "id") or cls.id == "base_tool":
                cls.id = cls.name

    # ------------------------------------------------------------------
    # 旧接口保留
    # ------------------------------------------------------------------

    def get_definition(self) -> ToolDefinition:
        """获取旧式 ToolDefinition（兼容旧注册表）"""
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
            required=self.required,
        )

    def to_openai_format(self) -> dict[str, Any]:
        """快捷方式，兼容旧调用"""
        return self.get_definition().to_openai_format()

    @abstractmethod
    async def execute(self, **kwargs) -> str:
        """旧接口：子类必须实现（签名会被基类自动适配）"""
        ...

    # ------------------------------------------------------------------
    # 新接口 (ToolDef)
    # ------------------------------------------------------------------

    async def execute_new(self, input: dict[str, Any], ctx: ToolContext) -> ToolResult:
        """新接口：自动调用旧 execute(**input) 并包装为 ToolResult"""
        try:
            result = await self.execute(**input)
            output = str(result) if result is not None else ""

            # 截断超长结果
            if self.max_result_size and self.max_result_size > 3:
                if len(output) > self.max_result_size:
                    output = output[: self.max_result_size - 3] + "..."

            return ToolResult(output=output)
        except Exception as exc:
            logger.error(f"Tool {self.id} execution error: {exc}")
            raise

    # ------------------------------------------------------------------
    # Layer 3 默认实现（从旧类属性映射）
    # ------------------------------------------------------------------

    def check_permissions(
        self, input: dict[str, Any], ctx: ToolContext
    ) -> PermissionResult:
        # 默认放行，由上层 PermissionChecker / resolve_permission 做主要拦截
        # 避免 Layer 3 默认 ask 导致所有工具被双重拦截
        return PermissionResult(
            behavior=PermissionBehavior.ALLOW,
            reason="default",
        )

    def is_read_only(self, input: dict[str, Any]) -> bool:
        """只读判断：默认与 is_parallel_safe 一致"""
        return self.is_parallel_safe

    def is_concurrency_safe(self, input: dict[str, Any]) -> bool:
        """并发安全判断：默认与 is_parallel_safe 一致"""
        return self.is_parallel_safe

    def is_destructive(self, input: dict[str, Any]) -> bool:
        return not self.is_parallel_safe

    def on_progress(self, data: Any, ctx: ToolContext) -> None:
        pass

    # ------------------------------------------------------------------
    # 参数验证（保留兼容旧代码）
    # ------------------------------------------------------------------

    def validate_arguments(self, arguments: dict[str, Any]) -> tuple[bool, str | None]:
        for req in self.required:
            if req not in arguments:
                param_info = self.parameters.get(req)
                desc = f"{req}"
                if isinstance(param_info, ToolParameter):
                    desc = f"{req}({param_info.type}): {param_info.description}"
                elif isinstance(param_info, dict):
                    desc = f"{req}({param_info.get('type', 'string')}): {param_info.get('description', '')}"
                return False, (
                    f"缺少必填参数 {desc}。请提供 '{req}' 参数后重试调用 {self.id}。"
                )
        return True, None
