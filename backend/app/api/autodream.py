"""
AutoDream API - 记忆自动整理接口

提供手动触发、配置管理、日志查询等功能。
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import async_session_maker, get_db
from app.core.permissions import PermissionChecker
from app.models import AIModelConfig
from app.models.autodream import AutoDreamConfig, AutoDreamLog
from app.modules.llm import SimpleLLMProvider
from app.modules.memory import create_memory_manager, get_current_memory_manager
from app.modules.memory.autodream import AutoDreamEngine
from app.schemas.autodream import (
    AutoDreamConfigResponse,
    AutoDreamConfigUpdate,
    AutoDreamLogListResponse,
    AutoDreamLogResponse,
    AutoDreamStatusResponse,
    AutoDreamTriggerResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/autodream", tags=["记忆自动整理"])

# 运行锁（进程内，防止同一进程内并发执行）
_autodream_lock = asyncio.Lock()

# 记忆根目录
MEMORY_ROOT = Path(__file__).resolve().parent.parent.parent / "memory"


async def _get_or_create_config(db: AsyncSession) -> AutoDreamConfig:
    """获取配置，不存在则创建默认配置"""
    result = await db.execute(select(AutoDreamConfig).limit(1))
    config = result.scalar_one_or_none()
    if config is None:
        config = AutoDreamConfig()
        db.add(config)
        await db.commit()
        await db.refresh(config)
        logger.info("[AutoDream] 自动创建默认配置")
    return config


async def _get_recent_running_log(db: AsyncSession) -> AutoDreamLog | None:
    """获取最近一条 running 状态的日志（未超时）"""
    result = await db.execute(
        select(AutoDreamLog)
        .where(AutoDreamLog.status == "running")
        .order_by(desc(AutoDreamLog.triggered_at))
        .limit(1)
    )
    log = result.scalar_one_or_none()
    if log is None:
        return None
    # 超时检查：超过 30 分钟视为僵死任务
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=30)
    if log.triggered_at.replace(tzinfo=timezone.utc) < cutoff:
        return None
    return log


# ==================== 手动触发 ====================

@router.post(
    "/trigger",
    response_model=AutoDreamTriggerResponse,
    dependencies=[Depends(PermissionChecker("autodream:manage"))],
)
async def trigger_autodream(db: AsyncSession = Depends(get_db)):
    """手动触发一次记忆整理

    返回 202 Accepted，实际执行在后台进行。
    """
    # 检查是否已有运行中实例
    recent = await _get_recent_running_log(db)
    if recent:
        return AutoDreamTriggerResponse(
            log_id=recent.id,
            status="skipped",
            message="已有运行中的整理任务",
        )

    # 创建 running 日志
    log = AutoDreamLog(triggered_by="manual", status="running")
    db.add(log)
    await db.commit()
    await db.refresh(log)

    # 后台执行（Phase 2 填充实际逻辑）
    asyncio.create_task(_run_autodream_task(log.id))

    return AutoDreamTriggerResponse(
        log_id=log.id,
        status="accepted",
        message="记忆整理任务已启动",
    )


async def _run_autodream_task(log_id: int):
    """后台执行记忆整理"""
    async with async_session_maker() as db:
        try:
            # 1. 加载 log
            result = await db.execute(
                select(AutoDreamLog).where(AutoDreamLog.id == log_id)
            )
            log = result.scalar_one()

            # 2. 加载 config
            config_result = await db.execute(select(AutoDreamConfig).limit(1))
            config = config_result.scalar_one_or_none()
            if config is None:
                config = AutoDreamConfig()
                db.add(config)
                await db.flush()

            # 3. 获取 memory_manager
            mm = get_current_memory_manager()
            if mm is None:
                mm = create_memory_manager(str(MEMORY_ROOT))

            # 4. 获取 LLM provider
            model_result = await db.execute(
                select(AIModelConfig).where(AIModelConfig.is_default)
            )
            model_config = model_result.scalar_one_or_none()
            if not model_config:
                model_result = await db.execute(select(AIModelConfig).limit(1))
                model_config = model_result.scalar_one_or_none()

            if not model_config:
                raise RuntimeError("没有可用的 AI 模型配置")

            provider = SimpleLLMProvider(config=model_config)

            # 5. 执行引擎
            engine = AutoDreamEngine(
                llm_provider=provider,
                memory_manager=mm,
                config=config,
            )
            await engine.run(log)

            await db.commit()
            logger.info(f"[AutoDream] 任务 #{log_id} 完成")

        except Exception as e:
            logger.error(f"[AutoDream] 任务 #{log_id} 失败: {e}", exc_info=True)
            result = await db.execute(
                select(AutoDreamLog).where(AutoDreamLog.id == log_id)
            )
            log = result.scalar_one()
            if log.status == "running":
                log.status = "failed"
                log.error_message = str(e)
                log.duration_seconds = (
                    datetime.now(timezone.utc) - log.triggered_at
                ).total_seconds()
                await db.commit()


# ==================== 日志查询 ====================

@router.get(
    "/logs",
    response_model=AutoDreamLogListResponse,
    dependencies=[Depends(PermissionChecker("autodream:manage"))],
)
async def list_autodream_logs(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """分页查询整理日志"""
    result = await db.execute(
        select(AutoDreamLog)
        .order_by(desc(AutoDreamLog.triggered_at))
        .offset(skip)
        .limit(limit)
    )
    items = result.scalars().all()

    total_result = await db.execute(select(func.count(AutoDreamLog.id)))
    total = total_result.scalar_one()

    return AutoDreamLogListResponse(
        items=[AutoDreamLogResponse.model_validate(i) for i in items],
        total=total,
    )


@router.get(
    "/logs/{log_id}",
    response_model=AutoDreamLogResponse,
    dependencies=[Depends(PermissionChecker("autodream:manage"))],
)
async def get_autodream_log(log_id: int, db: AsyncSession = Depends(get_db)):
    """获取单次整理日志详情"""
    result = await db.execute(
        select(AutoDreamLog).where(AutoDreamLog.id == log_id)
    )
    log = result.scalar_one_or_none()
    if not log:
        raise HTTPException(status_code=404, detail="日志不存在")
    return AutoDreamLogResponse.model_validate(log)


# ==================== 配置管理 ====================

@router.get(
    "/config",
    response_model=AutoDreamConfigResponse,
    dependencies=[Depends(PermissionChecker("autodream:manage"))],
)
async def get_autodream_config(db: AsyncSession = Depends(get_db)):
    """获取 AutoDream 配置（不存在则自动创建默认）"""
    config = await _get_or_create_config(db)
    return AutoDreamConfigResponse.model_validate(config)


@router.put(
    "/config",
    response_model=AutoDreamConfigResponse,
    dependencies=[Depends(PermissionChecker("autodream:manage"))],
)
async def update_autodream_config(
    update: AutoDreamConfigUpdate,
    db: AsyncSession = Depends(get_db),
):
    """更新 AutoDream 配置"""
    config = await _get_or_create_config(db)

    update_data = update.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(config, key, value)

    await db.commit()
    await db.refresh(config)
    return AutoDreamConfigResponse.model_validate(config)


# ==================== 状态查询 ====================

@router.get(
    "/status",
    response_model=AutoDreamStatusResponse,
    dependencies=[Depends(PermissionChecker("autodream:manage"))],
)
async def get_autodream_status(db: AsyncSession = Depends(get_db)):
    """获取当前运行状态"""
    config = await _get_or_create_config(db)

    # 查询最近一次日志
    result = await db.execute(
        select(AutoDreamLog).order_by(desc(AutoDreamLog.triggered_at)).limit(1)
    )
    last_log = result.scalar_one_or_none()

    is_running = False
    if last_log and last_log.status == "running":
        # 检查是否超时
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=30)
        if last_log.triggered_at.replace(tzinfo=timezone.utc) >= cutoff:
            is_running = True

    return AutoDreamStatusResponse(
        is_running=is_running,
        last_run=AutoDreamLogResponse.model_validate(last_log) if last_log else None,
        config=AutoDreamConfigResponse.model_validate(config),
    )
