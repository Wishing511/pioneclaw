"""
Subagent API - 子 Agent 任务管理接口

功能：
- 创建后台任务
- 查询任务状态
- 取消任务
- 任务列表
"""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import get_current_active_user
from app.core import get_db
from app.models import AIModelConfig, User
from app.modules.agent.subagent import SubagentManager, SubagentTask

router = APIRouter(prefix="/subagent", tags=["子智能体"])


# ==================== 请求/响应模型 ====================


class TaskCreateRequest(BaseModel):
    """创建任务请求"""

    label: str
    message: str
    task_type: str = "general"  # general, research, build
    session_id: str | None = None
    system_prompt: str | None = None
    enable_tools: bool = True
    model_config_id: int | None = None
    max_retries: int = 2
    # OpenClaw 借鉴：深度与角色
    depth: int = 0
    parent_task_id: str | None = None
    agent_id: str | None = None
    lane_type: str = "subagent"  # nested, subagent, cron


class TaskResponse(BaseModel):
    """任务响应"""

    task_id: str
    label: str
    task_type: str
    status: str
    progress: int
    result: str | None = None
    error: str | None = None
    retry_count: int = 0
    created_at: str
    started_at: str | None = None
    completed_at: str | None = None
    # OpenClaw 借鉴：深度与角色
    depth: int = 0
    role: str = "main"
    parent_task_id: str | None = None
    can_spawn: bool = True


class TaskListResponse(BaseModel):
    """任务列表响应"""

    tasks: list[TaskResponse]
    total: int


class TaskStatsResponse(BaseModel):
    """任务统计响应"""

    total: int
    pending: int
    running: int
    completed: int
    failed: int
    cancelled: int
    by_type: dict[str, dict[str, int]] | None = None


# ==================== 辅助函数 ====================


def _task_to_response(task) -> TaskResponse:
    """将 SubagentTask 转换为 TaskResponse"""
    from app.modules.agent import resolve_subagent_capabilities

    capabilities = resolve_subagent_capabilities(task.role)
    return TaskResponse(
        task_id=task.task_id,
        label=task.label,
        task_type=task.task_type.value,
        status=task.status.value,
        progress=task.progress,
        result=task.result,
        error=task.error,
        retry_count=task.retry_count,
        created_at=task.created_at.isoformat(),
        started_at=task.started_at.isoformat() if task.started_at else None,
        completed_at=task.completed_at.isoformat() if task.completed_at else None,
        depth=task.depth,
        role=task.role.value,
        parent_task_id=task.parent_task_id,
        can_spawn=capabilities["can_spawn"],
    )


# ==================== 全局 SubagentManager ====================

# 全局任务存储（简化实现）
_global_tasks: dict[str, SubagentTask] = {}
_global_managers: dict[str, SubagentManager] = {}


def register_manager(manager_id: str, manager: SubagentManager):
    """注册 manager"""
    _global_managers[manager_id] = manager


def get_global_task(task_id: str):
    """从全局存储获取任务"""
    return _global_tasks.get(task_id)


def register_task(task: SubagentTask):
    """注册任务到全局存储"""
    _global_tasks[task.task_id] = task


def get_all_tasks():
    """获取所有任务"""
    return list(_global_tasks.values())


async def get_subagent_manager(
    db: AsyncSession,
    model_config_id: int | None,
    current_user: User,
):
    """获取或创建 SubagentManager"""
    from app.modules.agent import AgentLoop, SubagentManager
    from app.modules.tools import ToolRegistry, register_builtin_tools

    # 获取模型配置
    if model_config_id:
        result = await db.execute(
            select(AIModelConfig).where(AIModelConfig.id == model_config_id)
        )
        config = result.scalar_one_or_none()
    else:
        result = await db.execute(select(AIModelConfig).where(AIModelConfig.is_default))
        config = result.scalar_one_or_none()

    if not config:
        raise HTTPException(status_code=400, detail="没有可用的 AI 模型配置")

    # 创建 AgentLoop
    tool_registry = ToolRegistry()
    register_builtin_tools(tool_registry)

    from app.api.chat import SimpleLLMProvider

    provider = SimpleLLMProvider(config=config)

    from app.core.security_client import security_client

    agent_loop = AgentLoop(
        provider=provider,
        tools=tool_registry,
        model=config.model_name,
        max_iterations=10,
        temperature=config.temperature,
        max_tokens=config.max_tokens,
        user_role=current_user.role,
        security_client=security_client,
    )

    # 创建 SubagentManager
    manager = SubagentManager(agent_loop=agent_loop)

    return manager


# ==================== API 端点 ====================


@router.post("/tasks", response_model=TaskResponse)
async def create_task(
    request: TaskCreateRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """
    创建后台任务

    任务将在后台异步执行
    """
    manager = await get_subagent_manager(db, request.model_config_id, current_user)

    # 创建任务
    from app.modules.agent import LaneType, TaskType

    try:
        task_type = TaskType(request.task_type)
    except ValueError:
        task_type = TaskType.GENERAL

    try:
        lane_type = LaneType(request.lane_type)
    except ValueError:
        lane_type = LaneType.SUBAGENT

    task_id = manager.create_task(
        label=request.label,
        message=request.message,
        task_type=task_type,
        session_id=request.session_id,
        system_prompt=request.system_prompt,
        enable_tools=request.enable_tools,
        max_retries=request.max_retries,
        depth=request.depth,
        parent_task_id=request.parent_task_id,
        agent_id=request.agent_id,
        lane_type=lane_type,
    )

    # 注册到全局存储
    task = manager.get_task(task_id)
    register_task(task)

    # 后台执行
    background_tasks.add_task(manager.execute_task, task_id)
    return _task_to_response(task)


@router.get("/tasks/{task_id}", response_model=TaskResponse)
async def get_task(
    task_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """获取任务状态"""
    task = get_global_task(task_id)
    if task:
        return _task_to_response(task)

    raise HTTPException(status_code=404, detail="任务不存在")


@router.get("/tasks", response_model=TaskListResponse)
async def list_tasks(
    status: str | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """列出所有任务"""
    from app.modules.agent import TaskStatus

    all_tasks = get_all_tasks()

    # 按状态过滤
    if status:
        filter_status = TaskStatus(status)
        all_tasks = [t for t in all_tasks if t.status == filter_status]

    # 按创建时间倒序
    all_tasks.sort(key=lambda t: t.created_at, reverse=True)

    return TaskListResponse(
        tasks=[_task_to_response(t) for t in all_tasks],
        total=len(all_tasks),
    )


@router.post("/tasks/{task_id}/cancel")
async def cancel_task(
    task_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """取消任务"""
    task = get_global_task(task_id)
    if task:
        # 由于我们没有保存 manager，直接更新状态
        from app.modules.agent import TaskStatus

        task.status = TaskStatus.CANCELLED
        task.error = "用户取消"
        from datetime import datetime

        task.completed_at = datetime.now()
        return {"success": True, "message": "任务已取消"}

    raise HTTPException(status_code=404, detail="任务不存在")


@router.delete("/tasks/{task_id}")
async def delete_task(
    task_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """删除任务"""
    global _global_tasks
    if task_id in _global_tasks:
        del _global_tasks[task_id]
        return {"success": True, "message": "任务已删除"}

    raise HTTPException(status_code=404, detail="任务不存在")


@router.get("/stats", response_model=TaskStatsResponse)
async def get_stats(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """获取任务统计"""
    from app.modules.agent import TaskStatus

    all_tasks = get_all_tasks()

    return TaskStatsResponse(
        total=len(all_tasks),
        pending=len([t for t in all_tasks if t.status == TaskStatus.PENDING]),
        running=len([t for t in all_tasks if t.status == TaskStatus.RUNNING]),
        completed=len([t for t in all_tasks if t.status == TaskStatus.COMPLETED]),
        failed=len([t for t in all_tasks if t.status == TaskStatus.FAILED]),
        cancelled=len([t for t in all_tasks if t.status == TaskStatus.CANCELLED]),
    )
