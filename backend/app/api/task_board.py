"""
TaskBoard API 端点
任务看板管理接口
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import get_current_active_user
from app.models import User

from ..core.database import get_db
from ..modules.agent.task_board import (
    TaskBoardService,
    TaskHeartbeatService,
    TaskScope,
    TaskType,
    run_task_heartbeat,
)

router = APIRouter(prefix="/task-board", tags=["TaskBoard"])


# ==================== 任务 CRUD ====================


@router.get("/stats")
async def get_stats(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """获取任务统计"""
    service = TaskBoardService(db)
    return await service.get_stats()


@router.get("/running")
async def get_running_tasks(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """获取运行中的任务"""
    service = TaskBoardService(db)
    tasks = await service.get_running_tasks()
    return {"tasks": tasks, "count": len(tasks)}


@router.get("/pending")
async def get_pending_tasks(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """获取等待中的任务"""
    service = TaskBoardService(db)
    tasks = await service.get_pending_tasks()
    return {"tasks": tasks, "count": len(tasks)}


@router.get("/recent")
async def get_recent_tasks(
    limit: int = 20,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """获取最近的任务"""
    service = TaskBoardService(db)
    tasks = await service.get_recent_tasks(limit)
    return {"tasks": tasks, "count": len(tasks)}


@router.get("/by-status/{status}")
async def get_tasks_by_status(
    status: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """按状态获取任务"""
    service = TaskBoardService(db)
    tasks = await service.get_tasks_by_status(status)
    return {"tasks": tasks, "count": len(tasks)}


# ==================== 任务操作 ====================


@router.post("/{task_id}/start")
async def start_task(
    task_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """开始任务"""
    service = TaskBoardService(db)
    task = await service.start_task(task_id)

    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    return {"success": True, "task": task}


@router.post("/{task_id}/complete")
async def complete_task(
    task_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """完成任务"""
    service = TaskBoardService(db)
    task = await service.complete_task(task_id)

    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    return {"success": True, "task": task}


@router.post("/{task_id}/fail")
async def fail_task(
    task_id: str,
    error_message: str = "",
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """标记任务失败"""
    service = TaskBoardService(db)
    task = await service.fail_task(task_id, error_message)

    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    return {"success": True, "task": task}


@router.post("/{task_id}/cancel")
async def cancel_task(
    task_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """取消任务"""
    service = TaskBoardService(db)
    task = await service.cancel_task(task_id)

    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    return {"success": True, "task": task}


@router.put("/{task_id}/progress")
async def update_progress(
    task_id: str,
    progress: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """更新任务进度"""
    service = TaskBoardService(db)
    task = await service.update_progress(task_id, progress)

    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    return {"success": True, "task": task}


# ==================== 心跳检测 ====================


@router.post("/heartbeat/scan")
async def scan_timeout_tasks(
    timeout_minutes: int = 30,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """扫描超时任务"""
    service = TaskHeartbeatService(db)
    result = await service.scan_running_tasks(timeout_minutes)
    return {"success": True, **result}


@router.post("/heartbeat/check-waiting")
async def check_long_waiting(
    wait_minutes: int = 10,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """检测长时间等待的任务"""
    service = TaskHeartbeatService(db)
    result = await service.check_long_waiting_tasks(wait_minutes)
    return {"success": True, **result}


@router.post("/heartbeat/cleanup")
async def cleanup_completed(
    days: int = 7,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """清理已完成的旧任务"""
    service = TaskHeartbeatService(db)
    count = await service.cleanup_completed_tasks(days)
    return {"success": True, "cleaned": count}


@router.post("/heartbeat/run")
async def run_heartbeat(
    current_user: User = Depends(get_current_active_user),
):
    """运行完整心跳检测（供 Cron 调用）"""
    from ..core.database import AsyncSessionLocal

    result = await run_task_heartbeat(AsyncSessionLocal)
    return {"success": True, **result}


# ==================== 类型定义 ====================


@router.get("/scopes")
async def get_task_scopes(
    current_user: User = Depends(get_current_active_user),
):
    """获取任务范围类型"""
    return {"scopes": [s.value for s in TaskScope]}


@router.get("/types")
async def get_task_types(
    current_user: User = Depends(get_current_active_user),
):
    """获取任务类型"""
    return {"types": [t.value for t in TaskType]}
