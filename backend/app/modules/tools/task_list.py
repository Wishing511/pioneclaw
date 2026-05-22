"""
TaskListTool — Agent 列出后台任务
"""

import json
import logging

from app.modules.tools.base import BaseTool, ToolParameter
from app.modules.tools.task_store import list_tasks as _store_list_tasks

logger = logging.getLogger(__name__)


class TaskListTool(BaseTool):
    """列出所有后台任务，支持过滤"""

    name = "task_list"
    description = (
        "列出所有后台任务，可按状态、工具名过滤。"
        "返回任务摘要列表，包含 task_id、label、tool_name、status、progress、created_at。"
    )
    parameters = {
        "status": ToolParameter(
            type="string",
            description="按状态过滤：pending / running / done / failed / cancelled，默认 'all' 返回全部",
            default="all",
        ),
        "tool_name": ToolParameter(
            type="string",
            description="按工具名过滤（可选）",
            default="",
        ),
        "limit": ToolParameter(
            type="integer",
            description="最大返回数量，默认 50",
            default=50,
        ),
    }
    required = []

    async def execute(
        self, status: str = "all", tool_name: str = "", limit: int = 50, **kwargs
    ) -> str:
        try:
            tasks = _store_list_tasks(
                status=status if status else "all",
                tool_name=tool_name if tool_name else None,
            )

            # 应用 limit
            if len(tasks) > limit:
                tasks = tasks[:limit]

            return json.dumps(
                {
                    "success": True,
                    "total": len(tasks),
                    "tasks": tasks,
                },
                ensure_ascii=False,
            )

        except Exception as e:
            logger.error(f"TaskListTool execution error: {e}")
            return json.dumps({"success": False, "error": str(e)}, ensure_ascii=False)
