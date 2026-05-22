from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import get_current_active_user
from app.core import get_db
from app.models import (
    Agent,
    AgentStatus,
    ApiUsage,
    Skill,
    Task,
    User,
)
from app.models.approval import Approval, ApprovalStatus
from app.models.layered_memory import LayeredMemory
from app.schemas import DashboardStats

router = APIRouter(prefix="/dashboard", tags=["仪表盘"])


@router.get("/stats", response_model=DashboardStats)
async def get_dashboard_stats(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """获取仪表盘统计数据（24小时内）"""
    now = datetime.now(tz=timezone.utc)
    yesterday = now - timedelta(hours=24)

    # 查询 API 用量
    result = await db.execute(
        select(
            func.count(ApiUsage.id).label("total_calls"),
            func.coalesce(func.sum(ApiUsage.total_tokens), 0).label("total_tokens"),
            func.coalesce(func.sum(ApiUsage.input_tokens), 0).label("input_tokens"),
            func.coalesce(func.sum(ApiUsage.output_tokens), 0).label("output_tokens"),
            func.coalesce(func.avg(ApiUsage.duration_ms), 0).label("avg_duration"),
        )
        .where(ApiUsage.user_id == current_user.id)
        .where(ApiUsage.created_at >= yesterday)
    )
    stats = result.one()

    # 查询失败调用数
    failed_result = await db.execute(
        select(func.count(ApiUsage.id))
        .where(ApiUsage.user_id == current_user.id)
        .where(ApiUsage.created_at >= yesterday)
        .where(not ApiUsage.is_success)
    )
    failed_calls = failed_result.scalar() or 0

    # 查询模型分布
    result = await db.execute(
        select(
            ApiUsage.model,
            func.count(ApiUsage.id).label("calls"),
            func.coalesce(func.sum(ApiUsage.total_tokens), 0).label("tokens"),
        )
        .where(ApiUsage.user_id == current_user.id)
        .where(ApiUsage.created_at >= yesterday)
        .group_by(ApiUsage.model)
    )
    model_dist = {
        row.model: {"calls": row.calls, "tokens": row.tokens} for row in result.all()
    }

    # 24h hourly breakdown
    hourly_result = await db.execute(
        select(
            func.strftime("%H", ApiUsage.created_at).label("hour"),
            func.count(ApiUsage.id).label("calls"),
        )
        .where(ApiUsage.user_id == current_user.id)
        .where(ApiUsage.created_at >= yesterday)
        .group_by("hour")
        .order_by("hour")
    )
    hourly_map = {row.hour: row.calls for row in hourly_result.all()}
    hourly_calls = [
        {"hour": f"{h:02d}", "calls": hourly_map.get(f"{h:02d}", 0)} for h in range(24)
    ]

    return DashboardStats(
        total_calls=stats.total_calls or 0,
        total_tokens=stats.total_tokens or 0,
        input_tokens=stats.input_tokens or 0,
        output_tokens=stats.output_tokens or 0,
        avg_duration_ms=float(stats.avg_duration or 0),
        failed_calls=failed_calls,
        model_distribution=model_dist,
        hourly_calls=hourly_calls,
    )


@router.get("/counts")
async def get_counts(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """获取各模块数量统计 + 仪表盘概览数据"""
    now = datetime.now(tz=timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    # Center 端运行状态（服务本身运行中即为在线）
    center_online = 1  # 能响应请求说明服务在线
    center_total = 1

    # 在线智能体
    agent_online = await db.execute(
        select(func.count(Agent.id)).where(Agent.status == AgentStatus.ACTIVE)
    )
    agent_total = await db.execute(select(func.count(Agent.id)))

    # 今日任务
    task_today = await db.execute(
        select(func.count(Task.id)).where(Task.created_at >= today_start)
    )
    task_status_counts = await db.execute(
        select(Task.status, func.count(Task.id).label("count"))
        .where(Task.created_at >= today_start)
        .group_by(Task.status)
    )
    task_today_by_status = {row.status: row.count for row in task_status_counts.all()}

    # API 调用次数（今日）
    api_calls_today = await db.execute(
        select(func.count(ApiUsage.id))
        .where(ApiUsage.user_id == current_user.id)
        .where(ApiUsage.created_at >= today_start)
    )

    # API 调用失败次数（今日）
    api_failed_today = await db.execute(
        select(func.count(ApiUsage.id))
        .where(ApiUsage.user_id == current_user.id)
        .where(ApiUsage.created_at >= today_start)
        .where(not ApiUsage.is_success)
    )

    # 总模块数量
    skill_count = await db.execute(select(func.count(Skill.id)).where(Skill.is_active))
    memory_count = await db.execute(
        select(func.count(LayeredMemory.id))
        .where(LayeredMemory.user_id == current_user.id)
        .where(LayeredMemory.is_active)
    )

    # 最近任务（5条）
    recent_tasks_result = await db.execute(
        select(Task).order_by(Task.created_at.desc()).limit(5)
    )
    recent_tasks = []
    for t in recent_tasks_result.scalars().all():
        recent_tasks.append(
            {
                "id": t.id,
                "title": t.title,
                "status": t.status,
                "priority": t.priority,
                "task_type": t.task_type,
                "created_at": t.created_at.isoformat() if t.created_at else None,
                "completed_at": t.completed_at.isoformat() if t.completed_at else None,
            }
        )

    # 最近关键日志（5条，优先显示失败的）
    recent_logs_result = await db.execute(
        select(ApiUsage)
        .where(ApiUsage.user_id == current_user.id)
        .order_by(ApiUsage.created_at.desc())
        .limit(5)
    )
    recent_logs = []
    for log in recent_logs_result.scalars().all():
        recent_logs.append(
            {
                "id": log.id,
                "model": log.model,
                "is_success": log.is_success,
                "total_tokens": log.total_tokens,
                "duration_ms": log.duration_ms,
                "error_message": log.error_message,
                "created_at": log.created_at.isoformat() if log.created_at else None,
            }
        )

    # 待审批数量（仅管理员可见）
    pending_approvals = 0
    if current_user.is_super_admin or current_user.is_org_admin:
        result = await db.execute(
            select(func.count(Approval.id)).where(
                Approval.status == ApprovalStatus.PENDING
            )
        )
        pending_approvals = result.scalar() or 0

    return {
        # Center 端运行状态
        "gateway_total": center_total,
        "gateway_online": center_online,
        # 待审批
        "pending_approvals": pending_approvals,
        # 在线智能体
        "agents_online": agent_online.scalar() or 0,
        "agents_total": agent_total.scalar() or 0,
        # 今日任务
        "tasks_today": task_today.scalar() or 0,
        "tasks_today_by_status": task_today_by_status,
        # API 调用次数
        "api_calls_today": api_calls_today.scalar() or 0,
        "api_failed_today": api_failed_today.scalar() or 0,
        # 其他数量
        "skills": skill_count.scalar() or 0,
        "memories": memory_count.scalar() or 0,
        # 最近任务
        "recent_tasks": recent_tasks,
        # 最近日志
        "recent_logs": recent_logs,
    }
