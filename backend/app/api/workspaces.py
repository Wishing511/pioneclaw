"""
Workspace 管理 API
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import get_current_active_user
from app.core.database import get_db
from app.models import User, Workspace
from app.schemas import MessageResponse
from app.schemas.workspace import (
    WorkspaceBrief,
    WorkspaceCreate,
    WorkspaceResponse,
    WorkspaceSettingsUpdate,
    WorkspaceUpdate,
)

router = APIRouter(prefix="/workspaces", tags=["工作空间"])


@router.get("", response_model=list[WorkspaceResponse])
async def list_workspaces(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """获取当前用户的 workspace 列表"""
    result = await db.execute(
        select(Workspace)
        .where(Workspace.owner_id == current_user.id, Workspace.is_active)
        .order_by(Workspace.is_default.desc(), Workspace.updated_at.desc())
    )
    workspaces = result.scalars().all()
    return workspaces


@router.post("", response_model=WorkspaceResponse, status_code=status.HTTP_201_CREATED)
async def create_workspace(
    data: WorkspaceCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """创建 workspace"""
    workspace = Workspace(
        name=data.name,
        path=data.path,
        description=data.description,
        owner_id=current_user.id,
        organization_id=current_user.organization_id,
        settings=data.settings,
        is_default=False,
    )
    db.add(workspace)
    await db.commit()
    await db.refresh(workspace)
    return workspace


@router.get("/brief", response_model=list[WorkspaceBrief])
async def list_workspaces_brief(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """获取简要 workspace 列表（下拉选择用）"""
    result = await db.execute(
        select(Workspace)
        .where(Workspace.owner_id == current_user.id, Workspace.is_active)
        .order_by(Workspace.is_default.desc())
    )
    return result.scalars().all()


@router.get("/{workspace_id}", response_model=WorkspaceResponse)
async def get_workspace(
    workspace_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """获取 workspace 详情"""
    result = await db.execute(
        select(Workspace).where(
            Workspace.id == workspace_id,
            Workspace.owner_id == current_user.id,
        )
    )
    workspace = result.scalar_one_or_none()
    if not workspace:
        raise HTTPException(status_code=404, detail="工作空间不存在")
    return workspace


@router.put("/{workspace_id}", response_model=WorkspaceResponse)
async def update_workspace(
    workspace_id: int,
    data: WorkspaceUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """更新 workspace"""
    result = await db.execute(
        select(Workspace).where(
            Workspace.id == workspace_id,
            Workspace.owner_id == current_user.id,
        )
    )
    workspace = result.scalar_one_or_none()
    if not workspace:
        raise HTTPException(status_code=404, detail="工作空间不存在")

    update_data = data.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(workspace, key, value)

    await db.commit()
    await db.refresh(workspace)
    return workspace


@router.put("/{workspace_id}/settings", response_model=WorkspaceResponse)
async def update_workspace_settings(
    workspace_id: int,
    data: WorkspaceSettingsUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """更新 workspace 设置（人设、语言等）"""
    result = await db.execute(
        select(Workspace).where(
            Workspace.id == workspace_id,
            Workspace.owner_id == current_user.id,
        )
    )
    workspace = result.scalar_one_or_none()
    if not workspace:
        raise HTTPException(status_code=404, detail="工作空间不存在")

    # 合并设置
    current_settings = workspace.settings or {}
    update_data = data.model_dump(exclude_unset=True)
    current_settings.update(update_data)
    workspace.settings = current_settings

    await db.commit()
    await db.refresh(workspace)
    return workspace


@router.post("/{workspace_id}/activate", response_model=MessageResponse)
async def activate_workspace(
    workspace_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """设为当前活跃 workspace"""
    result = await db.execute(
        select(Workspace).where(
            Workspace.id == workspace_id,
            Workspace.owner_id == current_user.id,
        )
    )
    workspace = result.scalar_one_or_none()
    if not workspace:
        raise HTTPException(status_code=404, detail="工作空间不存在")

    # 取消其他默认
    result = await db.execute(
        select(Workspace).where(
            Workspace.owner_id == current_user.id,
            Workspace.is_default,
        )
    )
    for ws in result.scalars().all():
        ws.is_default = False

    workspace.is_default = True
    workspace.last_active_at = datetime.now(tz=timezone.utc)
    current_user.default_workspace_id = workspace.id

    await db.commit()
    return MessageResponse(message=f"工作空间 '{workspace.name}' 已设为默认")


@router.delete("/{workspace_id}", response_model=MessageResponse)
async def delete_workspace(
    workspace_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """删除 workspace"""
    result = await db.execute(
        select(Workspace).where(
            Workspace.id == workspace_id,
            Workspace.owner_id == current_user.id,
        )
    )
    workspace = result.scalar_one_or_none()
    if not workspace:
        raise HTTPException(status_code=404, detail="工作空间不存在")

    # 检查是否是唯一 workspace
    count_result = await db.execute(
        select(func.count())
        .select_from(Workspace)
        .where(
            Workspace.owner_id == current_user.id,
            Workspace.is_active,
        )
    )
    count = count_result.scalar() or 0
    if count <= 1:
        raise HTTPException(status_code=400, detail="不能删除唯一的工作空间")

    workspace.is_active = False
    await db.commit()
    return MessageResponse(message=f"工作空间 '{workspace.name}' 已删除")
