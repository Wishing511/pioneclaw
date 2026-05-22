"""
TaskStopTool — Agent 停止/取消后台任务
"""

import json
import logging

from app.modules.tools.base import BaseTool, ToolParameter
from app.modules.tools.task_store import get_task, update_task_status

logger = logging.getLogger(__name__)


class TaskStopTool(BaseTool):
    """停止/取消正在运行的后台任务"""

    name = "task_stop"
    description = (
        "停止或取消一个正在运行的后台任务。"
        "终态任务（done/failed/cancelled）无需停止，会返回提示信息。"
    )
    parameters = {
        "task_id": ToolParameter(
            type="string",
            description="要停止的任务 ID",
        ),
    }
    required = ["task_id"]

    async def execute(self, task_id: str, **kwargs) -> str:
        try:
            if not task_id or not task_id.strip():
                return json.dumps(
                    {
                        "success": False,
                        "error": "task_id 不能为空",
                    },
                    ensure_ascii=False,
                )

            task = get_task(task_id.strip())
            if not task:
                return json.dumps(
                    {
                        "success": False,
                        "error": f"任务不存在: '{task_id}'",
                    },
                    ensure_ascii=False,
                )

            current_status = task.get("status", "unknown")

            # 终态不需要停止
            if current_status in ("done", "failed", "cancelled"):
                return json.dumps(
                    {
                        "success": False,
                        "error": f"任务 '{task_id}' 已处于终态 ({current_status})，无需停止",
                    },
                    ensure_ascii=False,
                )

            # 取消任务
            ok = update_task_status(task_id.strip(), "cancelled")
            if not ok:
                return json.dumps(
                    {
                        "success": False,
                        "error": f"无法取消任务 '{task_id}'（当前状态: {current_status}）",
                    },
                    ensure_ascii=False,
                )

            return json.dumps(
                {
                    "success": True,
                    "task_id": task_id.strip(),
                    "old_status": current_status,
                    "new_status": "cancelled",
                    "message": f"任务 '{task_id}' 已取消",
                },
                ensure_ascii=False,
            )

        except Exception as e:
            logger.error(f"TaskStopTool execution error: {e}")
            return json.dumps({"success": False, "error": str(e)}, ensure_ascii=False)
