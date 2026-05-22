import secrets
import uuid as _uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import get_current_active_user
from app.core import get_db, settings
from app.core.crypto import encrypt
from app.core.permissions import PermissionChecker
from app.core.security import decode_access_token
from app.models import ApiUsage, ConnectionEvent, Runner, RunnerStatus, User
from app.schemas import (
    BindUserRequest,
    ConnectionEventResponse,
    DiagnosticsResponse,
    MessageResponse,
    RotateTokenResponse,
    RunnerApprove,
    RunnerCreate,
    RunnerHeartbeat,
    RunnerResponse,
    RunnerUpdate,
    SetDefaultRunnerRequest,
)

router = APIRouter(prefix="/runners", tags=["Runner管理"])


@router.get("")
async def list_runners(
    skip: int = 0,
    limit: int = 20,
    status_filter: RunnerStatus | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """获取 Runner 列表"""
    query = select(Runner)

    if status_filter:
        query = query.where(Runner.status == status_filter)

    query = query.offset(skip).limit(limit).order_by(Runner.applied_at.desc())
    result = await db.execute(query)
    runners = result.scalars().all()

    # 填充关联用户名
    user_ids = [r.user_id for r in runners if r.user_id]
    username_map = {}
    if user_ids:
        user_result = await db.execute(select(User).where(User.id.in_(user_ids)))
        username_map = {u.id: u.username for u in user_result.scalars().all()}

    runner_list = []
    for r in runners:
        d = {
            "id": r.id,
            "name": r.name,
            "display_name": r.display_name,
            "description": r.description,
            "status": r.status,
            "host": r.host,
            "port": r.port,
            "api_key": r.api_key,
            "capabilities": r.capabilities,
            "version": r.version,
            "platform": r.platform,
            "last_heartbeat": r.last_heartbeat,
            "current_task": r.current_task,
            "total_tasks": r.total_tasks,
            "success_tasks": r.success_tasks,
            "failed_tasks": r.failed_tasks,
            "applied_at": r.applied_at,
            "approved_at": r.approved_at,
            "approved_by": r.approved_by,
            "user_id": r.user_id,
            "username": username_map.get(r.user_id) if r.user_id else None,
            "reject_reason": r.reject_reason,
            "created_at": r.created_at,
            "updated_at": r.updated_at,
        }
        runner_list.append(RunnerResponse(**d))

    return runner_list


@router.get("/pending", response_model=list[RunnerResponse])
async def list_pending_runners(
    skip: int = 0,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """获取待审批的 Runner 列表"""
    result = await db.execute(
        select(Runner)
        .where(Runner.status == RunnerStatus.PENDING)
        .offset(skip)
        .limit(limit)
    )
    return result.scalars().all()


@router.get("/model-usage")
async def get_model_usage(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """获取模型使用统计"""
    now = datetime.now(tz=timezone.utc)
    yesterday = now - timedelta(hours=24)

    # 总览统计
    result = await db.execute(
        select(
            func.count(ApiUsage.id).label("total_calls"),
            func.coalesce(func.sum(ApiUsage.total_tokens), 0).label("total_tokens"),
            func.coalesce(func.avg(ApiUsage.duration_ms), 0).label("avg_duration"),
        ).where(ApiUsage.created_at >= yesterday)
    )
    overview = result.one()

    # 成功/失败
    failed = await db.execute(
        select(func.count(ApiUsage.id))
        .where(not ApiUsage.is_success)
        .where(ApiUsage.created_at >= yesterday)
    )
    failed_calls = failed.scalar() or 0

    # 按模型分组统计
    by_model = await db.execute(
        select(
            ApiUsage.model,
            func.count(ApiUsage.id).label("calls"),
            func.coalesce(func.sum(ApiUsage.total_tokens), 0).label("tokens"),
            func.coalesce(func.avg(ApiUsage.duration_ms), 0).label("avg_ms"),
            func.count(ApiUsage.id).filter(not ApiUsage.is_success).label("failures"),
        )
        .where(ApiUsage.created_at >= yesterday)
        .group_by(ApiUsage.model)
        .order_by(func.count(ApiUsage.id).desc())
    )
    models = []
    for row in by_model.all():
        models.append(
            {
                "model": row.model,
                "calls": row.calls,
                "tokens": row.tokens,
                "avg_duration_ms": round(float(row.avg_ms), 0),
                "failures": row.failures or 0,
                "success_rate": round((1 - (row.failures or 0) / row.calls) * 100, 1)
                if row.calls > 0
                else 100,
            }
        )

    return {
        "total_calls": overview.total_calls or 0,
        "total_tokens": overview.total_tokens or 0,
        "avg_duration_ms": round(float(overview.avg_duration or 0), 0),
        "failed_calls": failed_calls,
        "models": models,
    }


@router.get("/stats")
async def get_runner_stats(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """获取 Runner 统计"""
    # 各状态数量
    result = await db.execute(
        select(Runner.status, func.count(Runner.id)).group_by(Runner.status)
    )
    status_counts = {row[0].value: row[1] for row in result.all()}

    # 在线数量
    result = await db.execute(
        select(func.count(Runner.id)).where(Runner.status == RunnerStatus.ONLINE)
    )
    online_count = result.scalar() or 0

    # 总任务数
    result = await db.execute(select(func.sum(Runner.total_tasks)))
    total_tasks = result.scalar() or 0

    # 成功率
    result = await db.execute(select(func.sum(Runner.success_tasks)))
    success_tasks = result.scalar() or 0

    success_rate = (success_tasks / total_tasks * 100) if total_tasks > 0 else 0

    pending = status_counts.get("pending", 0)
    return {
        "status_counts": status_counts,
        "online_count": online_count,
        "pending_count": pending,
        "total_runners": sum(status_counts.values()),
        "total_tasks": total_tasks,
        "success_rate": round(success_rate, 1),
    }


@router.get("/center-info")
async def get_center_info(
    request: Request, current_user: User = Depends(get_current_active_user)
):
    """获取中心节点连接信息"""
    # 使用实际请求的 host，兼容本地开发和 Docker 部署
    host = request.headers.get(
        "host", request.client.host if request.client else "localhost"
    )
    base = f"http://{host}"
    return {
        "http_address": base,
        "apply_endpoint": f"{base}/api/runners/apply",
        "api_prefix": settings.API_PREFIX,
    }


@router.get("/my-bindings", response_model=list[RunnerResponse])
async def get_my_bindings(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """获取当前用户绑定的所有 Runner"""
    result = await db.execute(select(Runner).where(Runner.user_id == current_user.id))
    return result.scalars().all()


@router.put("/my-default", response_model=MessageResponse)
async def set_default_runner(
    data: SetDefaultRunnerRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """设置用户的默认 Runner"""
    # 验证 Runner 存在且属于当前用户
    result = await db.execute(select(Runner).where(Runner.id == data.runner_id))
    runner = result.scalar_one_or_none()
    if not runner:
        raise HTTPException(status_code=404, detail="Runner 不存在")

    if runner.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="该 Runner 不属于您")

    # 更新用户的默认 Runner
    user_result = await db.execute(select(User).where(User.id == current_user.id))
    user = user_result.scalar_one()
    user.default_runner_id = data.runner_id
    await db.commit()

    return MessageResponse(message="默认 Runner 设置成功")


@router.get("/apply/status")
async def get_apply_status(runner_name: str, db: AsyncSession = Depends(get_db)):
    """Runner 端查询申请状态（公开接口）"""
    result = await db.execute(select(Runner).where(Runner.name == runner_name))
    runner = result.scalar_one_or_none()
    if not runner:
        raise HTTPException(status_code=404, detail="Runner 不存在")
    return {"name": runner.name, "status": runner.status.value}


@router.get("/pending/count")
async def get_pending_count(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """获取待审批 Runner 数量"""
    result = await db.execute(
        select(func.count(Runner.id)).where(Runner.status == RunnerStatus.PENDING)
    )
    count = result.scalar() or 0
    return {"pending_count": count}


@router.get("/connected/list", response_model=list[RunnerResponse])
async def list_connected_runners(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """获取在线 Runner 列表"""
    result = await db.execute(
        select(Runner).where(Runner.status == RunnerStatus.ONLINE)
    )
    return result.scalars().all()


@router.get("/{runner_id}", response_model=RunnerResponse)
async def get_runner(
    runner_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """获取单个 Runner 详情"""
    result = await db.execute(select(Runner).where(Runner.id == runner_id))
    runner = result.scalar_one_or_none()

    if not runner:
        raise HTTPException(status_code=404, detail="Runner 不存在")

    return runner


@router.post(
    "",
    response_model=RunnerResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(PermissionChecker("runner:create"))],
)
async def create_runner(
    runner_data: RunnerCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """手动注册 Runner"""
    # 检查名称是否已存在
    result = await db.execute(select(Runner).where(Runner.name == runner_data.name))
    if result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Runner 名称已存在")

    runner = Runner(
        name=runner_data.name,
        display_name=runner_data.display_name,
        description=runner_data.description,
        host=runner_data.host,
        port=runner_data.port,
        api_key=runner_data.api_key,
        capabilities=runner_data.capabilities,
        version=runner_data.version,
        platform=runner_data.platform,
        status=RunnerStatus.APPROVED,  # 手动注册直接批准
        approved_at=datetime.now(tz=timezone.utc),
        approved_by=current_user.id,
    )
    db.add(runner)
    await db.commit()
    await db.refresh(runner)

    return runner


@router.post(
    "/apply", response_model=RunnerResponse, status_code=status.HTTP_201_CREATED
)
async def apply_runner(runner_data: RunnerCreate, db: AsyncSession = Depends(get_db)):
    """Runner 申请接入（公开接口，Runner 端调用）"""
    # 检查名称是否已存在
    result = await db.execute(select(Runner).where(Runner.name == runner_data.name))
    if result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Runner 名称已存在")

    # 如果携带 user_token，自动关联用户
    owner_id = None
    if runner_data.user_token:
        payload = decode_access_token(runner_data.user_token)
        if payload:
            user_id = payload.get("sub")
            if user_id:
                user_result = await db.execute(
                    select(User).where(User.id == int(user_id))
                )
                if user_result.scalar_one_or_none():
                    owner_id = int(user_id)

    runner = Runner(
        name=runner_data.name,
        display_name=runner_data.display_name,
        description=runner_data.description,
        host=runner_data.host,
        port=runner_data.port,
        api_key=runner_data.api_key,
        capabilities=runner_data.capabilities,
        version=runner_data.version,
        platform=runner_data.platform,
        status=RunnerStatus.PENDING,
        user_id=owner_id,
    )
    db.add(runner)
    await db.commit()
    await db.refresh(runner)

    return runner


@router.put(
    "/{runner_id}",
    response_model=RunnerResponse,
    dependencies=[Depends(PermissionChecker("runner:update"))],
)
async def update_runner(
    runner_id: int,
    runner_data: RunnerUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """更新 Runner"""
    result = await db.execute(select(Runner).where(Runner.id == runner_id))
    runner = result.scalar_one_or_none()

    if not runner:
        raise HTTPException(status_code=404, detail="Runner 不存在")

    update_data = runner_data.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(runner, key, value)

    await db.commit()
    await db.refresh(runner)

    return runner


@router.post(
    "/{runner_id}/approve",
    response_model=RunnerResponse,
    dependencies=[Depends(PermissionChecker("runner:approve"))],
)
async def approve_runner(
    runner_id: int,
    approve_data: RunnerApprove,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """审批 Runner"""
    result = await db.execute(select(Runner).where(Runner.id == runner_id))
    runner = result.scalar_one_or_none()

    if not runner:
        raise HTTPException(status_code=404, detail="Runner 不存在")

    if runner.status != RunnerStatus.PENDING:
        raise HTTPException(status_code=400, detail="该 Runner 不在待审批状态")

    if approve_data.approve:
        runner.status = RunnerStatus.APPROVED
        runner.approved_at = datetime.now(tz=timezone.utc)
        runner.approved_by = current_user.id
        if approve_data.user_id:
            runner.user_id = approve_data.user_id
            # 关联用户后自动上线（演示用）
            runner.status = RunnerStatus.ONLINE
            runner.last_heartbeat = datetime.now(tz=timezone.utc)
    else:
        runner.status = RunnerStatus.REJECTED
        runner.reject_reason = approve_data.reject_reason

    await db.commit()
    await db.refresh(runner)

    return runner


@router.post("/{runner_id}/heartbeat", response_model=MessageResponse)
async def runner_heartbeat(
    runner_id: int, heartbeat_data: RunnerHeartbeat, db: AsyncSession = Depends(get_db)
):
    """Runner 心跳（Runner 调用）"""
    result = await db.execute(select(Runner).where(Runner.id == runner_id))
    runner = result.scalar_one_or_none()

    if not runner:
        raise HTTPException(status_code=404, detail="Runner 不存在")

    if runner.status not in [
        RunnerStatus.APPROVED,
        RunnerStatus.ONLINE,
        RunnerStatus.OFFLINE,
    ]:
        raise HTTPException(status_code=400, detail="Runner 未被批准")

    runner.last_heartbeat = datetime.now(tz=timezone.utc)
    runner.status = RunnerStatus.ONLINE
    runner.current_task = heartbeat_data.current_task

    if heartbeat_data.capabilities:
        runner.capabilities = heartbeat_data.capabilities

    # Record connection event in the same transaction
    db.add(ConnectionEvent(runner_id=runner_id, event_type="online", detail="心跳上报"))
    await db.commit()

    return MessageResponse(message="心跳更新成功")


@router.post("/{runner_id}/offline", response_model=MessageResponse)
async def set_runner_offline(runner_id: int, db: AsyncSession = Depends(get_db)):
    """设置 Runner 离线"""
    result = await db.execute(select(Runner).where(Runner.id == runner_id))
    runner = result.scalar_one_or_none()

    if not runner:
        raise HTTPException(status_code=404, detail="Runner 不存在")

    runner.status = RunnerStatus.OFFLINE
    runner.last_heartbeat = datetime.now(tz=timezone.utc)

    # Record connection event in the same transaction
    db.add(
        ConnectionEvent(
            runner_id=runner_id, event_type="offline", detail="手动设置离线"
        )
    )
    await db.commit()

    return MessageResponse(message="Runner 已离线")


@router.post("/{runner_id}/bind-user", response_model=RunnerResponse)
async def bind_user_to_runner(
    runner_id: int,
    bind_data: BindUserRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """绑定用户到 Runner（需管理员权限）"""
    if not current_user.is_super_admin and not current_user.is_org_admin:
        raise HTTPException(status_code=403, detail="需要管理员权限")

    result = await db.execute(select(Runner).where(Runner.id == runner_id))
    runner = result.scalar_one_or_none()
    if not runner:
        raise HTTPException(status_code=404, detail="Runner 不存在")

    if runner.user_id is not None:
        raise HTTPException(status_code=400, detail="该 Runner 已绑定用户，请先解绑")

    # 验证目标用户存在
    user_result = await db.execute(select(User).where(User.id == bind_data.user_id))
    if not user_result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="用户不存在")

    runner.user_id = bind_data.user_id
    await db.commit()
    await db.refresh(runner)
    return runner


@router.delete("/{runner_id}/unbind-user", response_model=MessageResponse)
async def unbind_user_from_runner(
    runner_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """解绑 Runner 的用户（需管理员权限）"""
    if not current_user.is_super_admin and not current_user.is_org_admin:
        raise HTTPException(status_code=403, detail="需要管理员权限")

    result = await db.execute(select(Runner).where(Runner.id == runner_id))
    runner = result.scalar_one_or_none()
    if not runner:
        raise HTTPException(status_code=404, detail="Runner 不存在")

    runner.user_id = None
    await db.commit()
    return MessageResponse(message="解绑成功")


@router.post("/{runner_id}/rotate-token", response_model=RotateTokenResponse)
async def rotate_runner_token(
    runner_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """轮换 Runner Token（需管理员权限）"""
    if not current_user.is_super_admin and not current_user.is_org_admin:
        raise HTTPException(status_code=403, detail="需要管理员权限")

    result = await db.execute(select(Runner).where(Runner.id == runner_id))
    runner = result.scalar_one_or_none()
    if not runner:
        raise HTTPException(status_code=404, detail="Runner 不存在")

    if runner.status not in [RunnerStatus.APPROVED, RunnerStatus.ONLINE]:
        raise HTTPException(status_code=400, detail="Runner 未被批准，无法轮换 Token")

    # Generate new token
    new_token = secrets.token_urlsafe(32)
    now = datetime.now(tz=timezone.utc)

    # Encrypt and store
    runner.api_key = encrypt(new_token)
    runner.token_rotated_at = now
    runner.token_expires_at = now + timedelta(hours=24)

    await db.commit()
    await db.refresh(runner)

    return RotateTokenResponse(
        new_token=new_token,
        rotated_at=runner.token_rotated_at,
        old_token_expires_at=runner.token_expires_at,
    )


# ============================
# 诊断与日志 API
# ============================


@router.get("/{runner_id}/diagnostics", response_model=DiagnosticsResponse)
async def get_runner_diagnostics(
    runner_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """获取 Runner 诊断信息"""
    if not current_user.is_super_admin and not current_user.is_org_admin:
        raise HTTPException(status_code=403, detail="需要管理员权限")

    result = await db.execute(select(Runner).where(Runner.id == runner_id))
    runner = result.scalar_one_or_none()
    if not runner:
        raise HTTPException(status_code=404, detail="Runner 不存在")

    diag = runner.diagnostics or {}
    return DiagnosticsResponse(
        cpu_percent=diag.get("cpu_percent", 0),
        memory_percent=diag.get("memory_percent", 0),
        disk_percent=diag.get("disk_percent", 0),
        processes=diag.get("processes", []),
        updated_at=runner.last_heartbeat,
    )


@router.get("/{runner_id}/local-logs")
async def get_runner_local_logs(
    runner_id: int,
    category: str | None = None,
    start_time: str | None = None,
    end_time: str | None = None,
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """获取 Runner 本地日志（当前为模拟数据，v2 接入 Runner 端上报）"""
    if not current_user.is_super_admin and not current_user.is_org_admin:
        raise HTTPException(status_code=403, detail="需要管理员权限")

    result = await db.execute(select(Runner).where(Runner.id == runner_id))
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Runner 不存在")

    # v1: Return simulated data. Real implementation will fetch from Runner agent.
    all_logs = [
        {"category": "agent", "level": "INFO", "message": "Agent task started"},
        {"category": "agent", "level": "INFO", "message": "Tool call: read_file"},
        {"category": "task", "level": "INFO", "message": "Task #42 completed"},
        {"category": "system", "level": "WARN", "message": "High memory usage: 85%"},
        {"category": "error", "level": "ERROR", "message": "Connection timeout to LLM"},
    ]

    if category:
        all_logs = [log for log in all_logs if log["category"] == category]

    return all_logs[:limit]


@router.get("/{runner_id}/local-logs/categories")
async def get_log_categories(
    runner_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """获取日志分类列表"""
    if not current_user.is_super_admin and not current_user.is_org_admin:
        raise HTTPException(status_code=403, detail="需要管理员权限")

    result = await db.execute(select(Runner).where(Runner.id == runner_id))
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Runner 不存在")

    return ["agent", "task", "system", "error"]


@router.get(
    "/{runner_id}/connection-events", response_model=list[ConnectionEventResponse]
)
async def get_connection_events(
    runner_id: int,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """获取 Runner 连接事件历史"""
    if not current_user.is_super_admin and not current_user.is_org_admin:
        raise HTTPException(status_code=403, detail="需要管理员权限")

    result = await db.execute(select(Runner).where(Runner.id == runner_id))
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Runner 不存在")

    events_result = await db.execute(
        select(ConnectionEvent)
        .where(ConnectionEvent.runner_id == runner_id)
        .order_by(ConnectionEvent.created_at.desc())
        .limit(limit)
    )
    return events_result.scalars().all()


@router.delete(
    "/{runner_id}",
    response_model=MessageResponse,
    dependencies=[Depends(PermissionChecker("runner:delete"))],
)
async def delete_runner(
    runner_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """删除 Runner"""
    result = await db.execute(select(Runner).where(Runner.id == runner_id))
    runner = result.scalar_one_or_none()

    if not runner:
        raise HTTPException(status_code=404, detail="Runner 不存在")

    await db.delete(runner)
    await db.commit()

    return MessageResponse(message="Runner 已删除")


# ============================
# 补充端点
# ============================


@router.post("/{runner_id}/disconnect", response_model=MessageResponse)
async def disconnect_runner(
    runner_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """强制断开 Runner 连接（需管理员权限）"""
    if not current_user.is_super_admin and not current_user.is_org_admin:
        raise HTTPException(status_code=403, detail="需要管理员权限")

    result = await db.execute(select(Runner).where(Runner.id == runner_id))
    runner = result.scalar_one_or_none()
    if not runner:
        raise HTTPException(status_code=404, detail="Runner 不存在")

    runner.status = RunnerStatus.OFFLINE
    db.add(
        ConnectionEvent(
            runner_id=runner_id, event_type="disconnect", detail="管理员强制断开"
        )
    )
    await db.commit()

    return MessageResponse(message="Runner 已断开连接")


@router.delete("/{runner_id}/unbind-user/{user_id}", response_model=MessageResponse)
async def unbind_specific_user(
    runner_id: int,
    user_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """解绑指定用户（需管理员权限）"""
    if not current_user.is_super_admin and not current_user.is_org_admin:
        raise HTTPException(status_code=403, detail="需要管理员权限")

    result = await db.execute(select(Runner).where(Runner.id == runner_id))
    runner = result.scalar_one_or_none()
    if not runner:
        raise HTTPException(status_code=404, detail="Runner 不存在")

    if runner.user_id != user_id:
        raise HTTPException(status_code=400, detail="该用户未绑定到此 Runner")

    runner.user_id = None
    await db.commit()
    return MessageResponse(message="解绑成功")


# ============================================================
# Runner 指令队列（Center → Runner 通信）
# ============================================================

_instruction_queues: dict[int, list[dict]] = {}
_instruction_results: dict[str, dict] = {}


@router.post("/{runner_id}/instruction")
async def send_instruction(
    runner_id: int,
    action: str,
    path: str | None = None,
    content: str | None = None,
    command: str | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """向 Runner 下发指令"""
    result = await db.execute(select(Runner).where(Runner.id == runner_id))
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Runner 不存在")

    task_id = str(_uuid.uuid4())[:8]
    params = {}
    if path:
        params["path"] = path
    if content:
        params["content"] = content
    if command:
        params["command"] = command

    inst = {"task_id": task_id, "action": action, "params": params}
    _instruction_queues.setdefault(runner_id, []).append(inst)
    return {"task_id": task_id, "message": "指令已发送"}


@router.get("/{runner_id}/pending")
async def get_pending_instructions(runner_id: int, db: AsyncSession = Depends(get_db)):
    """Runner 轮询待执行指令"""
    if not (
        await db.execute(select(Runner).where(Runner.id == runner_id))
    ).scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Runner 不存在")
    queue = _instruction_queues.get(runner_id, [])
    instructions = queue.copy()
    _instruction_queues[runner_id] = []
    return {"instructions": instructions}


@router.post("/{runner_id}/result")
async def report_instruction_result(
    runner_id: int,
    task_id: str,
    success: bool,
    data: str | None = None,
    error: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    """Runner 上报执行结果"""
    if not (
        await db.execute(select(Runner).where(Runner.id == runner_id))
    ).scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Runner 不存在")
    _instruction_results[task_id] = {
        "runner_id": runner_id,
        "success": success,
        "data": data,
        "error": error,
    }
    return {"message": "结果已接收"}
