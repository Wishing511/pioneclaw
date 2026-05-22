"""
Task Store — 后台任务共享状态与访问器

供 RunBackgroundTool / CheckTaskTool 以及新的 Task 工具共享使用。
"""

import logging
from datetime import datetime

logger = logging.getLogger(__name__)

# ── 共享状态 ──────────────────────────────────────────────────

_background_tasks: dict[str, dict] = {}
# 每个条目结构：
# { task_id, label, tool_name, status, result, error, created_at, progress }

# 有效的任务状态
_VALID_STATUSES = {"pending", "running", "done", "failed", "cancelled"}

# 终态（不可转换）
_TERMINAL_STATUSES = {"done", "failed", "cancelled"}

# 允许的状态转换
_ALLOWED_TRANSITIONS = {
    "pending": {"running", "cancelled", "failed"},
    "running": {"done", "failed", "cancelled"},
    "done": set(),
    "failed": set(),
    "cancelled": set(),
}


def get_task(task_id: str) -> dict | None:
    """获取单个任务"""
    return _background_tasks.get(task_id)


def create_task(
    task_id: str,
    label: str,
    tool_name: str = "spawn",
    args: dict | None = None,
    parent_task_id: str = "",
) -> dict:
    """创建任务条目，返回创建的 dict"""
    entry = {
        "task_id": task_id,
        "label": label,
        "tool_name": tool_name,
        "status": "pending",
        "result": None,
        "error": None,
        "progress": 0,
        "created_at": datetime.now().isoformat(),
        "parent_task_id": parent_task_id if parent_task_id else None,
        "args": args or {},
    }
    _background_tasks[task_id] = entry
    logger.info(f"Task '{task_id}' ({label}) created")
    return entry


def update_task_status(task_id: str, status: str, **fields) -> bool:
    """更新任务状态（含状态转换校验）

    Returns:
        True 如果更新成功
    """
    task = _background_tasks.get(task_id)
    if not task:
        logger.warning(f"Task '{task_id}' not found for update")
        return False

    old_status = task.get("status", "pending")

    # 终态不可修改
    if old_status in _TERMINAL_STATUSES:
        logger.warning(
            f"Task '{task_id}' is in terminal state '{old_status}', cannot update"
        )
        return False

    # 状态转换校验
    if status != old_status:
        allowed = _ALLOWED_TRANSITIONS.get(old_status, set())
        if status not in allowed:
            logger.warning(
                f"Invalid status transition for task '{task_id}': {old_status} -> {status}. "
                f"Allowed: {allowed}"
            )
            return False

    task["status"] = status

    # 自动记录时间戳
    if status == "running" and "started_at" not in task:
        task["started_at"] = datetime.now().isoformat()
    if status in _TERMINAL_STATUSES:
        task["completed_at"] = datetime.now().isoformat()

    # 更新额外字段
    for key, value in fields.items():
        if key not in ("task_id", "created_at", "started_at", "completed_at"):
            task[key] = value

    return True


def list_tasks(status: str | None = None, tool_name: str | None = None) -> list[dict]:
    """列出任务，支持过滤

    Args:
        status: 过滤状态，None 或 "all" 表示全部
        tool_name: 过滤工具名
    """
    result = []
    for tid, t in _background_tasks.items():
        if status and status != "all" and t.get("status") != status:
            continue
        if tool_name and t.get("tool_name") != tool_name:
            continue
        result.append(
            {
                "task_id": tid,
                "label": t["label"],
                "tool_name": t["tool_name"],
                "status": t["status"],
                "progress": t.get("progress", 0),
                "created_at": t["created_at"],
                "parent_task_id": t.get("parent_task_id"),
            }
        )
    # 按创建时间倒序
    result.sort(key=lambda x: x["created_at"], reverse=True)
    return result


def remove_task(task_id: str) -> bool:
    """删除任务记录"""
    if task_id in _background_tasks:
        del _background_tasks[task_id]
        logger.info(f"Task '{task_id}' removed")
        return True
    return False


def get_task_count() -> dict:
    """获取任务统计"""
    counts = {"total": len(_background_tasks), "by_status": {}}
    for t in _background_tasks.values():
        s = t.get("status", "unknown")
        counts["by_status"][s] = counts["by_status"].get(s, 0) + 1
    return counts
