"""
TaskGetTool — Agent 查询任务详情
"""

import json
import logging

from app.modules.tools.base import BaseTool, ToolParameter
from app.modules.tools.task_store import get_task as _store_get_task

logger = logging.getLogger(__name__)


class TaskGetTool(BaseTool):
    """查询单个后台任务的详细信息"""

    name = "task_get"
    description = (
        "查询指定后台任务的详细信息，包括状态、进度、结果等。"
        "结果较长时会自动截断（默认 3000 字符）。"
    )
    parameters = {
        "task_id": ToolParameter(
            type="string",
            description="要查询的任务 ID",
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

            task = _store_get_task(task_id.strip())
            if not task:
                return json.dumps(
                    {
                        "success": False,
                        "error": f"任务不存在: '{task_id}'",
                    },
                    ensure_ascii=False,
                )

            result_info = task.copy()
            # 截断过长结果
            if result_info.get("result") and isinstance(result_info["result"], str):
                if len(result_info["result"]) > 3000:
                    result_info["result"] = result_info["result"][:3000] + "...(已截断)"

            return json.dumps({"success": True, **result_info}, ensure_ascii=False)

        except Exception as e:
            logger.error(f"TaskGetTool execution error: {e}")
            return json.dumps({"success": False, "error": str(e)}, ensure_ascii=False)
