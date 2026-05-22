from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import get_current_active_user
from app.core import get_db, get_password_hash
from app.core.permissions import PermissionChecker
from app.models import User, UserRole
from app.schemas import MessageResponse, UserResponse

router = APIRouter(prefix="/users", tags=["用户管理"])


class UserCreate(BaseModel):
    username: str = Field(
        min_length=3,
        max_length=50,
        pattern=r"^[a-zA-Z0-9_一-鿿]+$",
    )
    email: EmailStr
    display_name: str = ""
    password: str = Field(min_length=8)
    role: str = "user"
    is_active: bool = True
    organization_id: str = None


class UserUpdate(BaseModel):
    email: str = None
    display_name: str = None
    role: str = None
    is_active: bool = None
    organization_id: str = None


class PasswordUpdate(BaseModel):
    password: str


@router.get(
    "",
    response_model=list[UserResponse],
    dependencies=[Depends(PermissionChecker("user:read"))],
)
async def list_users(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """获取用户列表（非超管只返回本组织用户）"""
    query = select(User).order_by(User.created_at.desc())
    if current_user.organization_id and not current_user.is_super_admin:
        query = query.where(User.organization_id == current_user.organization_id)
    result = await db.execute(query)
    users = result.scalars().all()
    return users


@router.get(
    "/{user_id}",
    response_model=UserResponse,
    dependencies=[Depends(PermissionChecker("user:read"))],
)
async def get_user(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """获取用户详情"""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")

    # 非超管只能查看本组织用户
    if (
        not current_user.is_super_admin
        and current_user.organization_id
        and user.organization_id != current_user.organization_id
    ):
        raise HTTPException(status_code=403, detail="无权查看其他组织用户")

    return user


@router.post(
    "",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(PermissionChecker("user:create"))],
)
async def create_user(
    user_data: UserCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """创建用户"""
    # 检查用户名是否已存在
    result = await db.execute(select(User).where(User.username == user_data.username))
    if result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="用户名已存在")

    # 检查邮箱是否已存在
    result = await db.execute(select(User).where(User.email == user_data.email))
    if result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="邮箱已被使用")

    # 转换角色
    role_map = {
        "user": UserRole.USER,
        "org_admin": UserRole.ORG_ADMIN,
        "super_admin": UserRole.SUPER_ADMIN,
    }
    role = role_map.get(user_data.role, UserRole.USER)

    # 非超管不能创建超管
    if role == UserRole.SUPER_ADMIN and not current_user.is_super_admin:
        raise HTTPException(status_code=403, detail="无权创建超级管理员")

    # 非超管创建的 org_admin 必须和自己同组织
    if role == UserRole.ORG_ADMIN and not current_user.is_super_admin:
        if not current_user.is_org_admin:
            raise HTTPException(status_code=403, detail="无权创建组织管理员")
        user_data.organization_id = current_user.organization_id

    # 非超管创建普通用户归入自己组织
    if not current_user.is_super_admin and not user_data.organization_id:
        user_data.organization_id = current_user.organization_id

    # 超级管理员不需要组织，其他角色可选
    org_id = user_data.organization_id
    if role == UserRole.SUPER_ADMIN:
        org_id = None  # 超管不绑定组织

    user = User(
        username=user_data.username,
        email=user_data.email,
        display_name=user_data.display_name or user_data.username,
        hashed_password=get_password_hash(user_data.password),
        role=role,
        is_active=user_data.is_active,
        organization_id=org_id,
        is_super_admin=(role == UserRole.SUPER_ADMIN),
        is_org_admin=(role == UserRole.ORG_ADMIN),
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)

    return user


@router.put(
    "/{user_id}",
    response_model=UserResponse,
    dependencies=[Depends(PermissionChecker("user:update"))],
)
async def update_user(
    user_id: int,
    user_data: UserUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """更新用户"""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")

    # 非超管只能修改本组织用户
    if (
        not current_user.is_super_admin
        and current_user.organization_id
        and user.organization_id != current_user.organization_id
    ):
        raise HTTPException(status_code=403, detail="无权修改其他组织用户")

    # 非超管不能提升用户为超管
    if user_data.role == "super_admin" and not current_user.is_super_admin:
        raise HTTPException(status_code=403, detail="无权设置超级管理员角色")

    if user_data.email is not None:
        user.email = user_data.email
    if user_data.display_name is not None:
        user.display_name = user_data.display_name
    if user_data.role is not None:
        role_map = {
            "user": UserRole.USER,
            "org_admin": UserRole.ORG_ADMIN,
            "super_admin": UserRole.SUPER_ADMIN,
        }
        user.role = role_map.get(user_data.role, UserRole.USER)
        if user_data.role == "super_admin":
            user.is_super_admin = True
            user.is_org_admin = False
        elif user_data.role == "org_admin":
            user.is_super_admin = False
            user.is_org_admin = True
        else:
            user.is_super_admin = False
            user.is_org_admin = False
    if user_data.is_active is not None:
        user.is_active = user_data.is_active
    if user_data.organization_id is not None:
        # 非超管不能修改组织归属
        if not current_user.is_super_admin:
            raise HTTPException(status_code=403, detail="无权修改用户组织归属")
        user.organization_id = user_data.organization_id

    await db.commit()
    await db.refresh(user)

    return user


@router.put(
    "/{user_id}/password",
    response_model=MessageResponse,
    dependencies=[Depends(PermissionChecker("user:update"))],
)
async def update_password(
    user_id: int,
    password_data: PasswordUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """重置用户密码"""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")

    # 非超管只能重置本组织用户密码
    if (
        not current_user.is_super_admin
        and current_user.organization_id
        and user.organization_id != current_user.organization_id
    ):
        raise HTTPException(status_code=403, detail="无权重置其他组织用户密码")

    user.hashed_password = get_password_hash(password_data.password)
    await db.commit()

    return MessageResponse(message="密码已重置")


@router.delete(
    "/{user_id}",
    response_model=MessageResponse,
    dependencies=[Depends(PermissionChecker("user:delete"))],
)
async def delete_user(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """删除用户"""
    if user_id == 1:
        raise HTTPException(status_code=400, detail="不能删除超级管理员")

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")

    # 不允许删除自己
    if user.id == current_user.id:
        raise HTTPException(status_code=400, detail="不能删除自己")

    # 非超管只能删除本组织用户
    if (
        not current_user.is_super_admin
        and current_user.organization_id
        and user.organization_id != current_user.organization_id
    ):
        raise HTTPException(status_code=403, detail="无权删除其他组织用户")

    # 非超管不能删除组织管理员
    if user.is_org_admin and not current_user.is_super_admin:
        raise HTTPException(status_code=403, detail="无权删除组织管理员")

    await db.delete(user)
    await db.commit()

    return MessageResponse(message="用户已删除")
