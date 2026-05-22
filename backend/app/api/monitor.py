"""
Monitor 监控 API — 系统运行状态聚合
"""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import get_current_active_user
from app.core import get_db
from app.models import Agent, AgentExecution, ApiUsage, Runner, RunnerStatus, Task, User

router = APIRouter(prefix="/monitor", tags=["系统监控"])


@router.get("/stats")
async def monitor_stats(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """系统运行统计总览"""
    if not current_user.is_super_admin:
        from fastapi import HTTPException

        raise HTTPException(status_code=403, detail="仅超级管理员")

    now = datetime.now(tz=timezone.utc)

    # Agents
    agent_online = (
        await db.execute(select(func.count(Agent.id)).where(Agent.status == "active"))
    ).scalar() or 0
    agent_total = (await db.execute(select(func.count(Agent.id)))).scalar() or 0

    # Runners
    runner_online = (
        await db.execute(
            select(func.count(Runner.id)).where(Runner.status == RunnerStatus.ONLINE)
        )
    ).scalar() or 0
    runner_total = (await db.execute(select(func.count(Runner.id)))).scalar() or 0

    # Tasks today
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    tasks_today = (
        await db.execute(select(func.count(Task.id)).where(Task.created_at >= today))
    ).scalar() or 0

    # Executions today
    executions_today = (
        await db.execute(
            select(func.count(AgentExecution.id)).where(
                AgentExecution.created_at >= today
            )
        )
    ).scalar() or 0

    # 24h API calls
    day_ago = now - timedelta(hours=24)
    api_calls = (
        await db.execute(
            select(func.count(ApiUsage.id)).where(ApiUsage.created_at >= day_ago)
        )
    ).scalar() or 0

    # Running tasks
    running = (
        await db.execute(
            select(func.count(Task.id)).where(
                Task.status.in_(["in_progress", "pending_approval"])
            )
        )
    ).scalar() or 0

    return {
        "agents": {"online": agent_online, "total": agent_total},
        "runners": {"online": runner_online, "total": runner_total},
        "tasks_today": tasks_today,
        "executions_today": executions_today,
        "api_calls_24h": api_calls,
        "running_tasks": running,
    }


@router.get("/executions")
async def monitor_executions(
    limit: int = 20,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """最近执行记录"""
    if not current_user.is_super_admin:
        from fastapi import HTTPException

        raise HTTPException(status_code=403, detail="仅超级管理员")

    result = await db.execute(
        select(AgentExecution).order_by(AgentExecution.created_at.desc()).limit(limit)
    )
    execs = result.scalars().all()
    return [
        {
            "id": e.id,
            "agent_id": e.agent_id,
            "user_id": e.user_id,
            "status": e.status,
            "model_name": e.model_name,
            "total_tokens": e.total_tokens,
            "latency_ms": e.latency_ms,
            "iterations": e.total_iterations,
            "tool_calls": e.total_tool_calls,
            "error_message": e.error_message[:100] if e.error_message else None,
            "created_at": e.created_at.isoformat(),
        }
        for e in execs
    ]


@router.get("/executions/{execution_id}")
async def monitor_execution_detail(
    execution_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """执行详情"""
    if not current_user.is_super_admin:
        from fastapi import HTTPException

        raise HTTPException(status_code=403, detail="仅超级管理员")

    result = await db.execute(
        select(AgentExecution).where(AgentExecution.id == execution_id)
    )
    e = result.scalar_one_or_none()
    if not e:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="执行记录不存在")

    return {
        "id": e.id,
        "agent_id": e.agent_id,
        "user_id": e.user_id,
        "message": e.message,
        "response": e.response,
        "status": e.status,
        "model_name": e.model_name,
        "total_tokens": e.total_tokens,
        "input_tokens": e.input_tokens,
        "output_tokens": e.output_tokens,
        "latency_ms": e.latency_ms,
        "iterations": e.total_iterations,
        "tool_calls": e.tool_calls,
        "error_message": e.error_message,
        "started_at": e.started_at.isoformat() if e.started_at else None,
        "completed_at": e.completed_at.isoformat() if e.completed_at else None,
    }


@router.get("/runners/status")
async def monitor_runner_status(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Runner 状态汇总"""
    if not current_user.is_super_admin:
        from fastapi import HTTPException

        raise HTTPException(status_code=403, detail="仅超级管理员")

    runners = (await db.execute(select(Runner))).scalars().all()
    return {
        "total": len(runners),
        "online": sum(1 for r in runners if r.status == RunnerStatus.ONLINE),
        "offline": sum(1 for r in runners if r.status == RunnerStatus.OFFLINE),
        "pending": sum(1 for r in runners if r.status == RunnerStatus.PENDING),
        "total_tasks": sum(r.total_tasks or 0 for r in runners),
        "success_rate": round(
            sum(r.success_tasks or 0 for r in runners)
            / max(sum(r.total_tasks or 0 for r in runners), 1)
            * 100,
            1,
        ),
    }


@router.get("/running-tasks")
async def monitor_running_tasks(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """运行中任务"""
    if not current_user.is_super_admin:
        from fastapi import HTTPException

        raise HTTPException(status_code=403, detail="仅超级管理员")

    tasks = (
        (
            await db.execute(
                select(Task)
                .where(Task.status.in_(["in_progress", "pending_approval"]))
                .limit(50)
            )
        )
        .scalars()
        .all()
    )
    return [
        {
            "id": t.id,
            "title": t.title,
            "status": t.status,
            "priority": t.priority,
            "assignee_id": t.assignee_id,
            "started_at": t.started_at.isoformat() if t.started_at else None,
        }
        for t in tasks
    ]
