"""
TaskUpdateTool — Agent 更新任务状态/进度
"""

import json
import logging

from app.modules.tools.base import BaseTool, ToolParameter
from app.modules.tools.task_store import get_task, update_task_status

logger = logging.getLogger(__name__)


class TaskUpdateTool(BaseTool):
    """更新后台任务的状态、进度或标签"""

    name = "task_update"
    description = (
        "更新后台任务的状态、进度或标签。"
        "终态任务（done/failed/cancelled）不可修改。"
        "状态转换规则：pending → running/cancelled/failed；running → done/failed/cancelled。"
    )
    parameters = {
        "task_id": ToolParameter(
            type="string",
            description="要更新的任务 ID",
        ),
        "status": ToolParameter(
            type="string",
            description="新状态：pending / running / done / failed / cancelled",
            default="",
        ),
        "progress": ToolParameter(
            type="integer",
            description="进度 0-100",
            default=-1,
        ),
        "label": ToolParameter(
            type="string",
            description="新的任务标签",
            default="",
        ),
    }
    required = ["task_id"]

    async def execute(
        self,
        task_id: str,
        status: str = "",
        progress: int = -1,
        label: str = "",
        **kwargs,
    ) -> str:
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

            old_status = task.get("status", "pending")

            # 检查是否终态
            if old_status in ("done", "failed", "cancelled"):
                return json.dumps(
                    {
                        "success": False,
                        "error": f"任务 '{task_id}' 已处于终态 ({old_status})，无法修改",
                    },
                    ensure_ascii=False,
                )

            # 验证 progress
            if progress >= 0:
                if progress > 100:
                    progress = 100
                task["progress"] = progress

            # 验证 label
            if label and label.strip():
                task["label"] = label.strip()

            # 更新状态
            if status and status.strip():
                ok = update_task_status(task_id.strip(), status.strip())
                if not ok:
                    return json.dumps(
                        {
                            "success": False,
                            "error": (
                                f"无效的状态转换: {old_status} → {status}。"
                                f"允许的转换: pending→running/cancelled/failed, "
                                f"running→done/failed/cancelled"
                            ),
                        },
                        ensure_ascii=False,
                    )

            return json.dumps(
                {
                    "success": True,
                    "task_id": task_id.strip(),
                    "old_status": old_status,
                    "new_status": task.get("status"),
                    "progress": task.get("progress", 0),
                    "label": task.get("label"),
                    "message": f"任务 '{task_id}' 已更新",
                },
                ensure_ascii=False,
            )

        except Exception as e:
            logger.error(f"TaskUpdateTool execution error: {e}")
            return json.dumps({"success": False, "error": str(e)}, ensure_ascii=False)
