"""
Task Manager API - 任务管理接口

提供任务创建、取消、查询等功能
"""

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.api.auth import get_current_active_user
from app.models.models import User
from app.modules.agent import (
    get_task_manager,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/task-manager", tags=["任务管理"])


# ==================== 请求模型 ====================


class CancelTaskRequest(BaseModel):
    """取消任务请求"""

    task_id: str | None = None
    session_id: str | None = None


# ==================== API 端点 ====================


@router.get("")
async def list_tasks(
    session_id: str | None = None,
    current_user: User = Depends(get_current_active_user),
):
    """获取任务列表"""
    manager = get_task_manager()

    if session_id:
        tasks = manager.get_session_tasks(session_id)
    else:
        tasks = manager.get_all_tasks()

    return {
        "tasks": [task.to_dict() for task in tasks],
        "total": len(tasks),
    }


@router.get("/stats")
async def get_task_stats(current_user: User = Depends(get_current_active_user)):
    """获取任务统计"""
    manager = get_task_manager()
    return manager.stats


@router.get("/{task_id}")
async def get_task(
    task_id: str,
    current_user: User = Depends(get_current_active_user),
):
    """获取任务详情"""
    manager = get_task_manager()
    task = manager.get_task(task_id)

    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    return task.to_dict()


@router.post("/cancel")
async def cancel_task(
    request: CancelTaskRequest,
    current_user: User = Depends(get_current_active_user),
):
    """取消任务"""
    manager = get_task_manager()

    if request.task_id:
        # 取消单个任务
        success = manager.cancel_task(request.task_id)
        if not success:
            raise HTTPException(status_code=400, detail="Failed to cancel task")
        return {"success": True, "message": f"Task {request.task_id} cancelled"}

    elif request.session_id:
        # 取消会话所有任务
        count = manager.cancel_session(request.session_id)
        return {"success": True, "cancelled_count": count}

    else:
        raise HTTPException(status_code=400, detail="task_id or session_id required")


@router.post("/cleanup")
async def cleanup_tasks(
    max_age_seconds: float = 3600,
    current_user: User = Depends(get_current_active_user),
):
    """清理已完成任务"""
    manager = get_task_manager()
    count = manager.cleanup_completed(max_age_seconds)
    return {"success": True, "cleaned_count": count}
