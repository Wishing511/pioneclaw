"""
TaskOutputTool — Agent 获取已完成任务的输出
"""

import json
import logging

from app.modules.tools.base import BaseTool, ToolParameter
from app.modules.tools.task_store import get_task as _store_get_task

logger = logging.getLogger(__name__)


class TaskOutputTool(BaseTool):
    """获取已完成后台任务的输出结果"""

    name = "task_output"
    description = (
        "获取已完成后台任务的输出结果。"
        "只对状态为 'done' 的任务返回结果；未完成的任务会返回错误和当前状态。"
        "输出较长时会自动截断（默认 5000 字符）。"
    )
    parameters = {
        "task_id": ToolParameter(
            type="string",
            description="要获取输出的任务 ID",
        ),
        "truncate": ToolParameter(
            type="integer",
            description="最大输出字符数，默认 5000",
            default=5000,
        ),
    }
    required = ["task_id"]

    async def execute(self, task_id: str, truncate: int = 5000, **kwargs) -> str:
        try:
            if not task_id or not task_id.strip():
                return json.dumps(
                    {
                        "success": False,
                        "error": "task_id 不能为空",
                    },
                    ensure_ascii=False,
                )

            task = _store_get_task(task_id.strip())
            if not task:
                return json.dumps(
                    {
                        "success": False,
                        "error": f"任务不存在: '{task_id}'",
                    },
                    ensure_ascii=False,
                )

            current_status = task.get("status", "unknown")

            if current_status != "done":
                return json.dumps(
                    {
                        "success": False,
                        "error": f"任务 '{task_id}' 尚未完成（当前状态: {current_status}）。"
                        f"请等待任务完成后再获取输出。",
                        "status": current_status,
                    },
                    ensure_ascii=False,
                )

            output = task.get("result", "")
            truncated = False
            if output and isinstance(output, str) and len(output) > truncate:
                output = output[:truncate] + "...(已截断)"
                truncated = True

            return json.dumps(
                {
                    "success": True,
                    "task_id": task_id.strip(),
                    "task_label": task.get("label", ""),
                    "output": output if output else "(无输出)",
                    "truncated": truncated,
                    "output_length": len(output) if output else 0,
                },
                ensure_ascii=False,
            )

        except Exception as e:
            logger.error(f"TaskOutputTool execution error: {e}")
            return json.dumps({"success": False, "error": str(e)}, ensure_ascii=False)
