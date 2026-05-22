"""
日志中心 API
"""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import get_current_active_user
from app.core.database import get_db
from app.models.models import ApiUsage, User

router = APIRouter(prefix="/logs", tags=["日志中心"])


class LogItem(BaseModel):
    id: int
    user_id: int
    model: str
    call_count: int
    input_tokens: int
    output_tokens: int
    total_tokens: int
    duration_ms: int
    is_success: bool
    error_message: str | None
    created_at: datetime

    class Config:
        from_attributes = True


class LogStats(BaseModel):
    total_calls: int
    success_calls: int
    failed_calls: int
    total_tokens: int
    avg_duration_ms: float


@router.get("", response_model=list[LogItem])
async def list_logs(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    model: str | None = None,
    is_success: bool | None = None,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """获取日志列表"""
    query = select(ApiUsage).where(ApiUsage.user_id == current_user.id)

    if model:
        query = query.where(ApiUsage.model == model)
    if is_success is not None:
        query = query.where(ApiUsage.is_success == is_success)
    if start_date:
        query = query.where(ApiUsage.created_at >= start_date)
    if end_date:
        query = query.where(ApiUsage.created_at <= end_date)

    query = query.order_by(desc(ApiUsage.created_at))
    query = query.offset((page - 1) * page_size).limit(page_size)

    result = await db.execute(query)
    return result.scalars().all()


@router.get("/stats", response_model=LogStats)
async def get_log_stats(
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """获取日志统计"""
    query = select(ApiUsage).where(ApiUsage.user_id == current_user.id)

    if start_date:
        query = query.where(ApiUsage.created_at >= start_date)
    if end_date:
        query = query.where(ApiUsage.created_at <= end_date)

    # 总调用数
    total_result = await db.execute(
        select(func.count(ApiUsage.id)).where(ApiUsage.user_id == current_user.id)
    )
    total_calls = total_result.scalar() or 0

    # 成功调用数
    success_result = await db.execute(
        select(func.count(ApiUsage.id))
        .where(ApiUsage.user_id == current_user.id)
        .where(ApiUsage.is_success)
    )
    success_calls = success_result.scalar() or 0

    # 失败调用数
    failed_calls = total_calls - success_calls

    # 总 Token
    tokens_result = await db.execute(
        select(func.coalesce(func.sum(ApiUsage.total_tokens), 0)).where(
            ApiUsage.user_id == current_user.id
        )
    )
    total_tokens = tokens_result.scalar() or 0

    # 平均耗时
    duration_result = await db.execute(
        select(func.coalesce(func.avg(ApiUsage.duration_ms), 0)).where(
            ApiUsage.user_id == current_user.id
        )
    )
    avg_duration = float(duration_result.scalar() or 0)

    return LogStats(
        total_calls=total_calls,
        success_calls=success_calls,
        failed_calls=failed_calls,
        total_tokens=total_tokens,
        avg_duration_ms=avg_duration,
    )


@router.get("/models")
async def get_used_models(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """获取使用过的模型列表"""
    result = await db.execute(
        select(ApiUsage.model).where(ApiUsage.user_id == current_user.id).distinct()
    )
    return [row[0] for row in result.all()]


@router.delete("/{log_id}")
async def delete_log(
    log_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """删除单条日志"""
    result = await db.execute(
        select(ApiUsage).where(
            ApiUsage.id == log_id, ApiUsage.user_id == current_user.id
        )
    )
    log = result.scalar_one_or_none()

    if not log:
        raise HTTPException(status_code=404, detail="日志不存在")

    await db.delete(log)
    await db.commit()
    return {"message": "日志已删除"}


@router.delete("")
async def clear_logs(
    before_date: datetime | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """清理日志"""
    query = select(ApiUsage).where(ApiUsage.user_id == current_user.id)

    if before_date:
        query = query.where(ApiUsage.created_at < before_date)

    result = await db.execute(query)
    logs = result.scalars().all()

    count = len(logs)
    for log in logs:
        await db.delete(log)

    await db.commit()
    return {"message": f"已清理 {count} 条日志"}
