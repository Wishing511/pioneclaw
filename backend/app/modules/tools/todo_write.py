"""
TodoWriteTool — Agent Session-scoped TODO 列表

Agent 可以创建、更新、清除自己的 TODO 列表。
按 session_id 隔离，不同会话的 TODO 列表互不干扰。
"""

import json
import logging

from app.modules.tools.base import BaseTool, ToolParameter

logger = logging.getLogger(__name__)

# Session-scoped TODO 存储
_agent_todos: dict[str, list[dict]] = {}

_VALID_TODO_STATUSES = {"pending", "in_progress", "completed", "cancelled"}


class TodoWriteTool(BaseTool):
    """Agent 管理自己的 TODO 列表"""

    name = "todo_write"
    description = (
        "管理当前会话的 TODO 列表。用于跟踪复杂任务的进度。\n"
        "每个 TODO 项包含：id（唯一标识）、subject（标题）、status（状态）、"
        "activeForm（进行中的描述文本，可选）。\n"
        "状态值：pending / in_progress / completed / cancelled。\n"
        "传入空数组可清空列表。\n"
        '示例: [{"id":"1","subject":"修复登录bug","status":"in_progress",'
        '"activeForm":"修复登录bug中"}]'
    )
    parameters = {
        "todos": ToolParameter(
            type="string",
            description=(
                "JSON 格式的 TODO 列表数组。每项含：id(必填)、subject(必填)、"
                "status(必填)、activeForm(可选)。"
            ),
        ),
        "session_id": ToolParameter(
            type="string",
            description="会话标识符（可选，默认使用 'default'）",
            default="default",
        ),
    }
    required = ["todos"]

    async def execute(self, todos: str, session_id: str = "default", **kwargs) -> str:
        try:
            # 解析 todos JSON
            try:
                parsed = json.loads(todos)
            except json.JSONDecodeError as e:
                return json.dumps(
                    {
                        "success": False,
                        "error": f"todos 参数不是有效的 JSON: {e}",
                    },
                    ensure_ascii=False,
                )

            if not isinstance(parsed, list):
                return json.dumps(
                    {
                        "success": False,
                        "error": "todos 必须是 JSON 数组",
                    },
                    ensure_ascii=False,
                )

            # 空数组 = 清空
            if len(parsed) == 0:
                _agent_todos[session_id] = []
                return json.dumps(
                    {
                        "success": True,
                        "message": "TODO 列表已清空",
                        "todos": [],
                    },
                    ensure_ascii=False,
                )

            # 验证每个 todo 项
            validated = []
            for i, item in enumerate(parsed):
                if not isinstance(item, dict):
                    return json.dumps(
                        {
                            "success": False,
                            "error": f"TODO #{i + 1} 不是有效的对象",
                        },
                        ensure_ascii=False,
                    )

                todo_id = item.get("id", "")
                subject = item.get("subject", "")
                todo_status = item.get("status", "pending")

                if not todo_id:
                    return json.dumps(
                        {
                            "success": False,
                            "error": f"TODO #{i + 1} 缺少 'id' 字段",
                        },
                        ensure_ascii=False,
                    )

                if not subject:
                    return json.dumps(
                        {
                            "success": False,
                            "error": f"TODO #{i + 1} 缺少 'subject' 字段",
                        },
                        ensure_ascii=False,
                    )

                if todo_status not in _VALID_TODO_STATUSES:
                    return json.dumps(
                        {
                            "success": False,
                            "error": f"TODO #{i + 1} 的 status 无效: '{todo_status}'。"
                            f"有效值: {', '.join(sorted(_VALID_TODO_STATUSES))}",
                        },
                        ensure_ascii=False,
                    )

                validated.append(
                    {
                        "id": str(todo_id),
                        "subject": str(subject),
                        "status": todo_status,
                        "activeForm": item.get("activeForm", ""),
                    }
                )

            # 存储
            _agent_todos[session_id] = validated

            # 统计
            by_status = {}
            for t in validated:
                s = t["status"]
                by_status[s] = by_status.get(s, 0) + 1

            return json.dumps(
                {
                    "success": True,
                    "todos": validated,
                    "total": len(validated),
                    "by_status": by_status,
                    "message": f"TODO 列表已更新：{len(validated)} 项",
                },
                ensure_ascii=False,
            )

        except Exception as e:
            logger.error(f"TodoWriteTool execution error: {e}")
            return json.dumps({"success": False, "error": str(e)}, ensure_ascii=False)
