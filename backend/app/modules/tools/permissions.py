"""
4 层级联权限系统

Layer 1: Rule matching (allow/deny/ask patterns)
Layer 2: Mode-based defaults (yolo / plan / ask)
Layer 3: Tool-specific check_permissions()
Layer 4: User interactive prompt
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.modules.tools.types import (
    PermissionBehavior,
    PermissionMode,
    PermissionRequest,
    PermissionResult,
    PermissionRule,
    ToolContext,
    ToolDef,
)

logger = logging.getLogger(__name__)


async def resolve_permission(
    tool: ToolDef,
    input: dict[str, Any],
    ctx: ToolContext,
    rules: list[PermissionRule],
    mode: PermissionMode,
) -> PermissionResult:
    """4 层级联权限判定"""
    tool_id = tool.id

    # ---- Layer 1: Rule matching ----
    rule = match_rule(tool_id, input, rules)
    if rule and rule.behavior == PermissionBehavior.DENY:
        return PermissionResult(
            behavior=PermissionBehavior.DENY,
            reason=f"rule:{rule.source}",
            message=f"Denied by rule: {rule.pattern or tool_id}",
        )
    if rule and rule.behavior == PermissionBehavior.ALLOW:
        return PermissionResult(
            behavior=PermissionBehavior.ALLOW,
            reason=f"rule:{rule.source}",
        )

    # ---- Layer 2: Mode-based defaults ----
    if mode == "yolo":
        return PermissionResult(
            behavior=PermissionBehavior.ALLOW,
            reason="yolo_mode",
        )
    if mode == "plan":
        return PermissionResult(
            behavior=PermissionBehavior.DENY,
            reason="plan_mode",
            message="Tool use is not allowed in plan mode",
        )

    # ---- Layer 3: Tool-specific permission check ----
    check_permissions = getattr(tool, "check_permissions", None)
    if check_permissions is not None:
        result = check_permissions(input, ctx)
        if asyncio.iscoroutine(result):
            result = await result
        if result is None:
            result = PermissionResult(
                behavior=PermissionBehavior.ALLOW,
                reason="layer3_none_fallback",
            )
        if result.behavior != PermissionBehavior.ASK:
            return result

    # ---- Layer 4: Interactive user prompt ----
    ask_result = await ctx.ask(
        PermissionRequest(
            tool=tool_id,
            action=_describe_action(tool, input),
            metadata={"input": input},
        )
    )
    return ask_result


def match_rule(
    tool_id: str,
    input: dict[str, Any],
    rules: list[PermissionRule],
) -> PermissionRule | None:
    """结构化匹配：逐字段精确/前缀匹配，避免 JSON.stringify + includes 的误匹配"""
    for rule in rules:
        if rule.tool != tool_id and rule.tool != "*":
            continue
        if not rule.pattern:
            return rule
        if _field_matches(input, rule.pattern):
            return rule
    return None


def _field_matches(input: dict[str, Any], pattern: str) -> bool:
    is_prefix = pattern.endswith("*")
    plain = pattern[:-1] if is_prefix else pattern
    for value in input.values():
        if not isinstance(value, str):
            continue
        if is_prefix and value.startswith(plain):
            return True
        if value == pattern:
            return True
    return False


def _describe_action(tool: ToolDef, input: dict[str, Any]) -> str:
    base = tool.id
    if isinstance(input, dict):
        if "command" in input and isinstance(input["command"], str):
            return f"{base}: {input['command']}"
        if "file_path" in input and isinstance(input["file_path"], str):
            return f"{base}: {input['file_path']}"
    return base
