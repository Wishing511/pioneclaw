from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import get_current_active_user
from app.core.cron_scheduler import CronScheduler
from app.core.database import get_db
from app.models.models import CronExecutionLog, CronJob, User
from app.schemas.schemas import (
    CronExecutionLogResponse,
    CronJobCreate,
    CronJobResponse,
    CronJobUpdate,
    MessageResponse,
)

router = APIRouter(prefix="/cron", tags=["定时任务"])


def validate_cron_expr(expr: str) -> bool:
    """使用 croniter 验证 cron 表达式"""
    return CronScheduler.validate_cron_expr(expr)


def get_next_run_hint(expr: str) -> str:
    """获取人类可读的下次运行描述"""
    return CronScheduler.describe_cron_expr(expr)


@router.get("", response_model=list[CronJobResponse])
async def list_cron_jobs(
    skip: int = 0,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """获取定时任务列表"""
    result = await db.execute(
        select(CronJob).order_by(CronJob.created_at.desc()).offset(skip).limit(limit)
    )
    jobs = result.scalars().all()

    # 转换字段
    return [
        CronJobResponse(
            id=job.id,
            name=job.name,
            cron_expr=job.schedule_value,
            agent_id=job.config.get("agent_id") if job.config else None,
            input_data=job.config.get("input_data") if job.config else None,
            description=job.description,
            is_active=job.is_enabled,
            last_run=job.last_run,
            next_run=job.next_run,
            run_count=job.run_count,
            created_at=job.created_at,
            updated_at=job.updated_at,
        )
        for job in jobs
    ]


@router.post("", response_model=CronJobResponse, status_code=status.HTTP_201_CREATED)
async def create_cron_job(
    job_data: CronJobCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """创建定时任务"""
    # 验证 cron 表达式
    if not validate_cron_expr(job_data.cron_expr):
        raise HTTPException(status_code=400, detail="无效的 Cron 表达式")

    job = CronJob(
        name=job_data.name,
        display_name=job_data.name,  # 使用 name 作为 display_name
        schedule_type="cron",
        schedule_value=job_data.cron_expr,
        description=job_data.description,
        is_enabled=job_data.is_active,
        config={"agent_id": job_data.agent_id, "input_data": job_data.input_data},
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)

    return CronJobResponse(
        id=job.id,
        name=job.name,
        cron_expr=job.schedule_value,
        agent_id=job.config.get("agent_id") if job.config else None,
        input_data=job.config.get("input_data") if job.config else None,
        description=job.description,
        is_active=job.is_enabled,
        last_run=job.last_run,
        next_run=job.next_run,
        run_count=job.run_count,
        created_at=job.created_at,
        updated_at=job.updated_at,
    )


@router.get("/{job_id}", response_model=CronJobResponse)
async def get_cron_job(
    job_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """获取定时任务详情"""
    result = await db.execute(select(CronJob).where(CronJob.id == job_id))
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="定时任务不存在")

    return CronJobResponse(
        id=job.id,
        name=job.name,
        cron_expr=job.schedule_value,
        agent_id=job.config.get("agent_id") if job.config else None,
        input_data=job.config.get("input_data") if job.config else None,
        description=job.description,
        is_active=job.is_enabled,
        last_run=job.last_run,
        next_run=job.next_run,
        run_count=job.run_count,
        created_at=job.created_at,
        updated_at=job.updated_at,
    )


@router.put("/{job_id}", response_model=CronJobResponse)
async def update_cron_job(
    job_id: int,
    job_data: CronJobUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """更新定时任务"""
    result = await db.execute(select(CronJob).where(CronJob.id == job_id))
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="定时任务不存在")

    if job_data.name is not None:
        job.name = job_data.name
        job.display_name = job_data.name
    if job_data.cron_expr is not None:
        if not validate_cron_expr(job_data.cron_expr):
            raise HTTPException(status_code=400, detail="无效的 Cron 表达式")
        job.schedule_value = job_data.cron_expr
    if job_data.description is not None:
        job.description = job_data.description
    if job_data.is_active is not None:
        job.is_enabled = job_data.is_active
    if job_data.agent_id is not None or job_data.input_data is not None:
        config = job.config or {}
        if job_data.agent_id is not None:
            config["agent_id"] = job_data.agent_id
        if job_data.input_data is not None:
            config["input_data"] = job_data.input_data
        job.config = config

    await db.commit()
    await db.refresh(job)

    return CronJobResponse(
        id=job.id,
        name=job.name,
        cron_expr=job.schedule_value,
        agent_id=job.config.get("agent_id") if job.config else None,
        input_data=job.config.get("input_data") if job.config else None,
        description=job.description,
        is_active=job.is_enabled,
        last_run=job.last_run,
        next_run=job.next_run,
        run_count=job.run_count,
        created_at=job.created_at,
        updated_at=job.updated_at,
    )


@router.delete("/{job_id}", response_model=MessageResponse)
async def delete_cron_job(
    job_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """删除定时任务"""
    result = await db.execute(select(CronJob).where(CronJob.id == job_id))
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="定时任务不存在")

    await db.delete(job)
    await db.commit()
    return MessageResponse(message="定时任务已删除")


@router.post("/{job_id}/toggle", response_model=CronJobResponse)
async def toggle_cron_job(
    job_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """启用/禁用定时任务"""
    result = await db.execute(select(CronJob).where(CronJob.id == job_id))
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="定时任务不存在")

    job.is_enabled = not job.is_enabled

    await db.commit()
    await db.refresh(job)

    return CronJobResponse(
        id=job.id,
        name=job.name,
        cron_expr=job.schedule_value,
        agent_id=job.config.get("agent_id") if job.config else None,
        input_data=job.config.get("input_data") if job.config else None,
        description=job.description,
        is_active=job.is_enabled,
        last_run=job.last_run,
        next_run=job.next_run,
        run_count=job.run_count,
        created_at=job.created_at,
        updated_at=job.updated_at,
    )


@router.post("/{job_id}/run", response_model=CronExecutionLogResponse)
async def run_cron_job_now(
    job_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """立即执行定时任务"""
    result = await db.execute(select(CronJob).where(CronJob.id == job_id))
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="定时任务不存在")

    from app.core.cron_scheduler import get_cron_scheduler

    scheduler = get_cron_scheduler()
    await scheduler.run_job_now(job.name)

    # 获取最新的执行日志
    log_result = await db.execute(
        select(CronExecutionLog)
        .where(CronExecutionLog.cron_job_id == job_id)
        .order_by(CronExecutionLog.started_at.desc())
        .limit(1)
    )
    log = log_result.scalar_one_or_none()
    if not log:
        raise HTTPException(status_code=500, detail="执行记录未找到")
    return log


@router.get("/{job_id}/executions", response_model=list[CronExecutionLogResponse])
async def list_cron_executions(
    job_id: int,
    limit: int = 20,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """获取定时任务执行历史"""
    result = await db.execute(
        select(CronExecutionLog)
        .where(CronExecutionLog.cron_job_id == job_id)
        .order_by(CronExecutionLog.started_at.desc())
        .limit(limit)
    )
    logs = result.scalars().all()
    return logs


@router.get(
    "/{job_id}/executions/latest", response_model=Optional[CronExecutionLogResponse]
)
async def get_latest_cron_execution(
    job_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """获取定时任务最近一次执行结果"""
    result = await db.execute(
        select(CronExecutionLog)
        .where(CronExecutionLog.cron_job_id == job_id)
        .order_by(CronExecutionLog.started_at.desc())
        .limit(1)
    )
    log = result.scalar_one_or_none()
    return log


@router.get("/scheduler/status")
async def scheduler_status(current_user: User = Depends(get_current_active_user)):
    """获取调度器状态"""
    from app.core.cron_scheduler import get_cron_scheduler

    scheduler = get_cron_scheduler()
    jobs = scheduler.list_jobs()
    return {
        "running": scheduler._running,
        "job_count": len(jobs),
        "jobs": jobs,
    }


@router.post("/scheduler/ensure-heartbeat")
async def ensure_heartbeat(
    schedule: str = "0 9,12,18 * * *",
    current_user: User = Depends(get_current_active_user),
):
    """确保 Heartbeat 任务已注册到调度器"""
    from app.core.cron_scheduler import get_cron_scheduler

    scheduler = get_cron_scheduler()
    job_id = scheduler.ensure_heartbeat_job(schedule=schedule)
    job_info = scheduler.get_job(job_id)
    return {
        "job_id": job_id,
        "schedule": schedule,
        "info": job_info,
    }


@router.get("/validate")
async def validate_cron(
    expr: str, current_user: User = Depends(get_current_active_user)
):
    """验证 cron 表达式并返回下次执行时间"""
    is_valid = CronScheduler.validate_cron_expr(expr)
    if not is_valid:
        return {"valid": False, "error": "无效的 cron 表达式"}

    scheduler = CronScheduler()
    next_run = scheduler.get_next_run(expr)
    description = CronScheduler.describe_cron_expr(expr)

    return {
        "valid": True,
        "description": description,
        "next_run": next_run.isoformat() if next_run else None,
    }
