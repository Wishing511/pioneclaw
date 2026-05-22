"""
批处理调度器 - 工具调用分区与并发执行

功能：
- partition_tool_calls: 按 is_read_only + is_concurrency_safe 将工具调用分区
- run_concurrent_batch: 池化并发执行（最大并发数可配置）
- run_serial_batch: 串行执行
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from app.modules.tools.types import ToolUse

logger = logging.getLogger(__name__)


@dataclass
class Batch:
    concurrent: bool
    tools: list[ToolUse]


# ------------------------------------------------------------------
# Minimal registry interface needed by the scheduler
# ------------------------------------------------------------------


class ToolLookup:
    """调度器所需的最小工具查询接口（避免对完整 ToolRegistry 的依赖）"""

    def get_tool(self, id: str) -> Any | None: ...


def get_max_concurrency() -> int:
    """最大并发数，默认 10，可通过环境变量 PIONECLAW_MAX_CONCURRENCY 覆盖"""
    env = os.getenv("PIONECLAW_MAX_CONCURRENCY", "")
    try:
        val = int(env)
        if val > 0:
            return min(val, 100)
    except ValueError:
        pass
    return 10


def partition_tool_calls(
    tool_uses: list[ToolUse],
    registry: Any,  # ToolLookup | ToolRegistry
) -> list[Batch]:
    """
    按 is_read_only + is_concurrency_safe 将工具调用分区。

    策略：
    - 只读 + 并发安全 → 同一批次（可并行）
    - 其他 → 各自独占批次（串行）
    """
    batches: list[Batch] = []
    current_batch: list[ToolUse] = []
    current_safe = True

    for use in tool_uses:
        tool = registry.get_tool(use.tool_id) if hasattr(registry, "get_tool") else None

        if tool is None:
            is_safe = False
            is_read_only = False
        else:
            is_safe = getattr(tool, "is_concurrency_safe", lambda _: False)(use.input)
            is_read_only = getattr(tool, "is_read_only", lambda _: False)(use.input)

        should_batch = is_safe and is_read_only

        if should_batch and current_safe:
            current_batch.append(use)
        else:
            if current_batch:
                batches.append(Batch(concurrent=True, tools=current_batch))
            batches.append(Batch(concurrent=False, tools=[use]))
            current_batch = []
            current_safe = True

    if current_batch:
        batches.append(Batch(concurrent=True, tools=current_batch))

    return batches


async def run_concurrent_batch(
    items: list[Any],
    executor: Callable[[Any], Awaitable[Any]],
) -> list[Any]:
    """池化并发执行，最大并发数 = get_max_concurrency()

    使用固定数量的 worker 协程从共享队列拉取任务，
    结果顺序与输入顺序一致。
    """
    if not items:
        return []

    limit = get_max_concurrency()
    results: list[Any] = [None] * len(items)
    cursor = 0
    lock = asyncio.Lock()

    async def worker() -> None:
        nonlocal cursor
        while True:
            async with lock:
                if cursor >= len(items):
                    return
                idx = cursor
                cursor += 1
            try:
                results[idx] = await executor(items[idx])
            except Exception as e:
                # 包装为结构化结果，避免调用方收到裸异常对象
                results[idx] = {
                    "_scheduler_error": True,
                    "type": type(e).__name__,
                    "message": str(e) or "(无详细错误信息)",
                }

    pool_size = min(limit, len(items))
    await asyncio.gather(*(worker() for _ in range(pool_size)))
    return results


async def run_serial_batch(
    items: list[Any],
    executor: Callable[[Any], Awaitable[Any]],
) -> list[Any]:
    """串行执行，保持顺序"""
    results = []
    for item in items:
        try:
            results.append(await executor(item))
        except Exception as e:
            results.append(
                {
                    "_scheduler_error": True,
                    "type": type(e).__name__,
                    "message": str(e) or "(无详细错误信息)",
                }
            )
    return results
