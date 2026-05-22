"""
LLM 用量统计 API
"""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import get_current_active_user
from app.core import get_db
from app.models import ApiUsage, User

router = APIRouter(prefix="/llm", tags=["LLM用量"])


@router.get("/usage/summary")
async def llm_usage_summary(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """用量汇总（24h + 按模型）"""
    now = datetime.now(tz=timezone.utc)
    day_ago = now - timedelta(hours=24)

    # 总量
    total_result = await db.execute(
        select(
            func.count(ApiUsage.id),
            func.coalesce(func.sum(ApiUsage.total_tokens), 0),
            func.coalesce(func.sum(ApiUsage.input_tokens), 0),
            func.coalesce(func.sum(ApiUsage.output_tokens), 0),
            func.coalesce(func.avg(ApiUsage.duration_ms), 0),
        ).where(ApiUsage.created_at >= day_ago)
    )
    calls, total_tokens, input_t, output_t, avg_ms = total_result.one()

    # 按用户
    by_user = await db.execute(
        select(
            User.username,
            func.count(ApiUsage.id).label("calls"),
            func.coalesce(func.sum(ApiUsage.total_tokens), 0).label("tokens"),
        )
        .join(User, ApiUsage.user_id == User.id)
        .where(ApiUsage.created_at >= day_ago)
        .group_by(User.username)
        .order_by(func.sum(ApiUsage.total_tokens).desc())
    )
    user_stats = [
        {"username": r.username, "calls": r.calls, "tokens": r.tokens}
        for r in by_user.all()
    ]

    # 按模型
    by_model = await db.execute(
        select(
            ApiUsage.model,
            func.count(ApiUsage.id).label("calls"),
            func.coalesce(func.sum(ApiUsage.total_tokens), 0).label("tokens"),
        )
        .where(ApiUsage.created_at >= day_ago)
        .group_by(ApiUsage.model)
        .order_by(func.sum(ApiUsage.total_tokens).desc())
    )
    model_stats = [
        {"model": r.model, "calls": r.calls, "tokens": r.tokens} for r in by_model.all()
    ]

    return {
        "period": "24h",
        "total_calls": calls or 0,
        "total_tokens": total_tokens or 0,
        "input_tokens": input_t or 0,
        "output_tokens": output_t or 0,
        "avg_duration_ms": round(float(avg_ms or 0), 0),
        "by_user": user_stats,
        "by_model": model_stats,
    }


@router.get("/usage/logs")
async def llm_usage_logs(
    limit: int = 50,
    user_id: int = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """调用日志"""
    query = select(ApiUsage).order_by(ApiUsage.created_at.desc()).limit(limit)
    if user_id:
        query = query.where(ApiUsage.user_id == user_id)
    result = await db.execute(query)
    logs = result.scalars().all()
    return [
        {
            "id": log.id,
            "user_id": log.user_id,
            "model": log.model,
            "total_tokens": log.total_tokens,
            "duration_ms": log.duration_ms,
            "is_success": log.is_success,
            "created_at": log.created_at.isoformat(),
        }
        for log in logs
    ]


@router.get("/usage/hourly")
async def llm_usage_hourly(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """24h 小时级用量"""
    now = datetime.now(tz=timezone.utc)
    day_ago = now - timedelta(hours=24)

    result = await db.execute(
        select(
            func.strftime("%H", ApiUsage.created_at).label("hour"),
            func.count(ApiUsage.id).label("calls"),
            func.coalesce(func.sum(ApiUsage.total_tokens), 0).label("tokens"),
        )
        .where(ApiUsage.created_at >= day_ago)
        .group_by("hour")
        .order_by("hour")
    )
    hourly_map = {r.hour: {"calls": r.calls, "tokens": r.tokens} for r in result.all()}
    return [
        {
            "hour": f"{h:02d}",
            "calls": hourly_map.get(f"{h:02d}", {}).get("calls", 0),
            "tokens": hourly_map.get(f"{h:02d}", {}).get("tokens", 0),
        }
        for h in range(24)
    ]
