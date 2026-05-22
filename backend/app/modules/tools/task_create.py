"""
TaskCreateTool — Agent 创建后台任务
"""

import json
import logging
import uuid

from app.modules.tools.base import BaseTool, ToolParameter
from app.modules.tools.task_store import create_task as _store_create_task

logger = logging.getLogger(__name__)


class TaskCreateTool(BaseTool):
    """创建一个新的后台任务或子任务"""

    name = "task_create"
    description = (
        "创建一个新的后台任务或子任务用于异步执行。立即返回 task_id，任务将在后台运行。"
        "使用 task_get/task_list 跟踪进度，task_output 获取结果，task_stop 取消任务。"
        "适用场景：长时间运行的工作、并行任务、不需要阻塞当前对话的独立操作。"
    )
    parameters = {
        "label": ToolParameter(
            type="string",
            description="任务的可读名称/标签",
        ),
        "tool_name": ToolParameter(
            type="string",
            description="要执行的目标工具名称（如 exec、web_search、spawn 等），默认 'spawn'",
            default="spawn",
        ),
        "args": ToolParameter(
            type="string",
            description='传递给目标工具的 JSON 参数字符串，如 \'{"command": "dir"}\'',
            default="{}",
        ),
        "parent_task_id": ToolParameter(
            type="string",
            description="父任务 ID（创建子任务时使用）",
            default="",
        ),
    }
    required = ["label"]

    async def execute(
        self,
        label: str,
        tool_name: str = "spawn",
        args: str = "{}",
        parent_task_id: str = "",
        **kwargs,
    ) -> str:
        try:
            # 验证 label
            if not label or not label.strip():
                return json.dumps(
                    {
                        "success": False,
                        "error": "任务名称不能为空",
                    },
                    ensure_ascii=False,
                )

            # 解析 args JSON
            try:
                tool_args = json.loads(args)
                if not isinstance(tool_args, dict):
                    return json.dumps(
                        {
                            "success": False,
                            "error": "args 必须是 JSON 对象格式",
                        },
                        ensure_ascii=False,
                    )
            except json.JSONDecodeError as e:
                return json.dumps(
                    {
                        "success": False,
                        "error": f"args JSON 解析失败: {e}",
                    },
                    ensure_ascii=False,
                )

            # 生成 task_id
            task_id = str(uuid.uuid4())[:8]

            # 存储任务
            _store_create_task(
                task_id,
                label.strip(),
                tool_name,
                args=tool_args,
                parent_task_id=parent_task_id.strip() if parent_task_id else "",
            )

            return json.dumps(
                {
                    "success": True,
                    "task_id": task_id,
                    "label": label.strip(),
                    "tool_name": tool_name,
                    "status": "pending",
                    "parent_task_id": parent_task_id.strip()
                    if parent_task_id
                    else None,
                    "message": f"任务 '{label}' 已创建。使用 task_get(task_id='{task_id}') 查询状态。",
                },
                ensure_ascii=False,
            )

        except Exception as e:
            logger.error(f"TaskCreateTool execution error: {e}")
            return json.dumps({"success": False, "error": str(e)}, ensure_ascii=False)
