"""
标准化 Hook 管理器 - 三阶段生命周期

支持：
- pre_tool_use:   拦截或阻断执行前
- post_tool_use:  执行后观察
- transform_result: 修改结果后返回给 LLM

兼容旧 ToolHookRunner（backend/app/modules/agent/tool_hooks.py）的 Hook 事件类型，
新代码优先使用本 HookManager。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from app.modules.tools.types import HookContext, HookResult, HookType, ToolHook

logger = logging.getLogger(__name__)


class HookManager:
    """标准化三阶段 Hook 管理器"""

    def __init__(self) -> None:
        self._hooks: dict[HookType, list[ToolHook]] = {
            HookType.PRE_TOOL_USE: [],
            HookType.POST_TOOL_USE: [],
            HookType.TRANSFORM_RESULT: [],
        }

    def register(self, type: HookType, hook: ToolHook) -> HookManager:
        self._hooks[type].append(hook)
        return self

    def unregister(self, type: HookType, hook: ToolHook) -> HookManager:
        lst = self._hooks.get(type, [])
        if hook in lst:
            lst.remove(hook)
        return self

    def clear(self, type: HookType | None = None) -> None:
        if type is None:
            for t in HookType:
                self._hooks[t] = []
        else:
            self._hooks[type] = []

    async def run(self, type: HookType, context: HookContext) -> HookResult:
        """运行指定类型的所有 Hook

        - pre_tool_use: 第一个 block=True 立即返回
        - transform_result: 链式转换，最后一个生效
        """
        hooks = self._hooks.get(type, [])
        result = HookResult()

        for hook in hooks:
            try:
                hook_result = hook(context)
                if asyncio.iscoroutine(hook_result):
                    hook_result = await hook_result

                if hook_result is None:
                    continue

                if hook_result.block:
                    result.block = True
                    result.message = hook_result.message or "Blocked by hook"
                    return result

                if hook_result.transformed_result is not None:
                    result.transformed_result = hook_result.transformed_result
                    context.result = hook_result.transformed_result

            except Exception as exc:
                # Hooks are fail-open: errors don't block execution
                logger.warning(f"[HookManager] Hook error ({type.value}): {exc}")

        return result

    # ------------------------------------------------------------------
    # Convenience methods for backward-compatible event names
    # ------------------------------------------------------------------

    async def run_pre(self, context: HookContext) -> HookResult:
        return await self.run(HookType.PRE_TOOL_USE, context)

    async def run_post(self, context: HookContext) -> HookResult:
        return await self.run(HookType.POST_TOOL_USE, context)

    async def run_transform(self, context: HookContext) -> HookResult:
        return await self.run(HookType.TRANSFORM_RESULT, context)


# ------------------------------------------------------------------
# Legacy adapter: wrap old ToolHookRunner events into HookManager
# ------------------------------------------------------------------


def adapt_legacy_hook(
    event: str,
    callback: Callable[[Any], Any],
    tool_filter: list[str] | None = None,
) -> ToolHook:
    """将旧式 ToolHook 回调适配为新的 ToolHook 签名

    event: "before_tool" | "after_tool" | "on_error"
    """

    async def wrapper(ctx: HookContext) -> HookResult | None:
        if tool_filter is not None:
            tool_id = getattr(ctx.tool, "id", "")
            if tool_id not in tool_filter:
                return None

        # 构造旧 HookContext（backend/app/modules/agent/tool_hooks.py 中的类型）
        # 由于这里不能直接导入避免循环依赖，使用兼容的 dict
        legacy_ctx = {
            "tool_name": getattr(ctx.tool, "id", ""),
            "tool_args": ctx.input,
            "tool_result": ctx.result.output if ctx.result else None,
            "agent_id": ctx.ctx.agent_id if ctx.ctx else None,
            "agent_name": ctx.ctx.agent_name if ctx.ctx else None,
            "conversation_id": ctx.ctx.session_id if ctx.ctx else None,
        }

        try:
            result = callback(legacy_ctx)
            if asyncio.iscoroutine(result):
                result = await result

            if result is None:
                return None

            # 处理旧 HookResult dict
            if isinstance(result, dict):
                hr = HookResult()
                if result.get("skip_execution"):
                    hr.block = True
                    hr.message = result.get("message", "Blocked by legacy hook")
                if "modified_args" in result:
                    # legacy hook 修改参数：在当前架构中需要上层配合
                    hr.modified_args = result["modified_args"]
                if "modified_result" in result:
                    hr.transformed_result = ctx.result
                    if hr.transformed_result:
                        hr.transformed_result.output = str(result["modified_result"])
                return hr

            return None
        except Exception as exc:
            logger.warning(f"Legacy hook error: {exc}")
            return None

    return wrapper
