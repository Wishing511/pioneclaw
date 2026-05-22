from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import get_current_active_user
from app.core.database import get_db
from app.models.models import Role, User, UserRole
from app.schemas.schemas import (
    MessageResponse,
    RoleCreate,
    RoleResponse,
    RoleUpdate,
    UserResponse,
)

router = APIRouter(prefix="/roles", tags=["角色管理"])


class PermissionsBody(BaseModel):
    permissions: list[str]


class RoleAssignBody(BaseModel):
    role_code: str


@router.get("", response_model=list[RoleResponse])
async def list_roles(
    skip: int = 0,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """获取角色列表"""
    result = await db.execute(
        select(Role).order_by(Role.created_at.desc()).offset(skip).limit(limit)
    )
    return result.scalars().all()


@router.post("", response_model=RoleResponse, status_code=status.HTTP_201_CREATED)
async def create_role(
    role_data: RoleCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """创建角色"""
    # 检查 code 是否已存在
    result = await db.execute(select(Role).where(Role.code == role_data.code))
    if result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="角色代码已存在")

    # 检查 name 是否已存在
    result = await db.execute(select(Role).where(Role.name == role_data.name))
    if result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="角色名称已存在")

    role = Role(
        name=role_data.name,
        code=role_data.code,
        description=role_data.description,
        permissions=role_data.permissions,
        is_active=role_data.is_active,
    )
    db.add(role)
    await db.commit()
    await db.refresh(role)

    return role


@router.get("/{role_id}", response_model=RoleResponse)
async def get_role(
    role_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """获取角色详情"""
    result = await db.execute(select(Role).where(Role.id == role_id))
    role = result.scalar_one_or_none()
    if not role:
        raise HTTPException(status_code=404, detail="角色不存在")

    return role


@router.put("/{role_id}/set-permissions", response_model=RoleResponse)
async def set_role_permissions(
    role_id: int,
    body: PermissionsBody,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """设置角色权限"""
    if not current_user.is_super_admin:
        raise HTTPException(status_code=403, detail="仅超级管理员可设置角色权限")
    result = await db.execute(select(Role).where(Role.id == role_id))
    role = result.scalar_one_or_none()
    if not role:
        raise HTTPException(status_code=404, detail="角色不存在")
    role.permissions = {"codes": body.permissions}
    await db.commit()
    await db.refresh(role)
    return role


@router.put("/user/{user_id}", response_model=UserResponse)
async def assign_role_to_user(
    user_id: int,
    body: RoleAssignBody,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """为用户分配角色"""
    if not current_user.is_super_admin and not current_user.is_org_admin:
        raise HTTPException(status_code=403, detail="需要管理员权限")
    role_code = body.role_code
    user_result = await db.execute(select(User).where(User.id == user_id))
    user = user_result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    role_result = await db.execute(select(Role).where(Role.code == role_code))
    if not role_result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="角色不存在")
    user.role = UserRole(role_code)
    if role_code == "super_admin":
        user.is_super_admin = True
    elif role_code == "org_admin":
        user.is_org_admin = True
    else:
        user.is_super_admin = False
        user.is_org_admin = False
    await db.commit()
    await db.refresh(user)
    return user


@router.put("/{role_id}", response_model=RoleResponse)
async def update_role(
    role_id: int,
    role_data: RoleUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """更新角色"""
    result = await db.execute(select(Role).where(Role.id == role_id))
    role = result.scalar_one_or_none()
    if not role:
        raise HTTPException(status_code=404, detail="角色不存在")

    # 只有超级管理员可以编辑系统角色
    if role.is_system and current_user.role != UserRole.SUPER_ADMIN:
        raise HTTPException(status_code=400, detail="系统角色不可修改")

    if role_data.name is not None:
        # 检查名称是否重复
        result = await db.execute(
            select(Role).where(Role.name == role_data.name, Role.id != role_id)
        )
        if result.scalar_one_or_none():
            raise HTTPException(status_code=400, detail="角色名称已存在")
        role.name = role_data.name

    if role_data.code is not None:
        # 检查代码是否重复
        result = await db.execute(
            select(Role).where(Role.code == role_data.code, Role.id != role_id)
        )
        if result.scalar_one_or_none():
            raise HTTPException(status_code=400, detail="角色代码已存在")
        role.code = role_data.code

    if role_data.description is not None:
        role.description = role_data.description
    if role_data.permissions is not None:
        role.permissions = role_data.permissions
    if role_data.is_active is not None:
        role.is_active = role_data.is_active

    await db.commit()
    await db.refresh(role)

    return role


@router.delete("/{role_id}", response_model=MessageResponse)
async def delete_role(
    role_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """删除角色"""
    result = await db.execute(select(Role).where(Role.id == role_id))
    role = result.scalar_one_or_none()
    if not role:
        raise HTTPException(status_code=404, detail="角色不存在")

    if role.is_system:
        raise HTTPException(status_code=400, detail="系统角色不可删除")

    await db.delete(role)
    await db.commit()
    return MessageResponse(message="角色已删除")
