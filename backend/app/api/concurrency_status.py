"""
并发状态 API
"""

from fastapi import APIRouter, Depends

from app.api.auth import get_current_active_user
from app.core.concurrency import concurrency_manager
from app.models import User

router = APIRouter(prefix="/concurrency", tags=["并发管理"])


@router.get("/status")
async def get_concurrency_status(
    current_user: User = Depends(get_current_active_user),
):
    """获取当前并发状态"""
    return {
        "total_active": concurrency_manager.total_active,
        "max_global": concurrency_manager.max_global,
        "queue_size": concurrency_manager.queue_size(),
        "user_active": concurrency_manager.user_active(current_user.id),
        "user_queued": concurrency_manager.user_queued(current_user.id),
        "max_per_user": concurrency_manager.max_per_user,
    }


@router.get("/my-position")
async def get_my_position(
    task_id: str = "",
    current_user: User = Depends(get_current_active_user),
):
    """获取当前用户的排队位置"""
    pos = concurrency_manager.get_position(current_user.id, task_id)
    return {
        "queued": pos > 0,
        "position": pos,
        "queue_size": concurrency_manager.queue_size(),
    }


@router.post("/cancel")
async def cancel_wait(
    task_id: str = "",
    current_user: User = Depends(get_current_active_user),
):
    """取消排队"""
    ok = concurrency_manager.cancel_wait(current_user.id, task_id)
    return {"cancelled": ok}
