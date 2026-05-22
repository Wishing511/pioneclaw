"""
权限管理 API
"""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import get_current_active_user
from app.core.database import get_db
from app.core.permissions import PermissionChecker
from app.models import DEFAULT_PERMISSIONS, Permission, User
from app.schemas import MessageResponse
from app.schemas.permission import (
    PermissionCreate,
    PermissionInDB,
    PermissionListResponse,
    PermissionTree,
    PermissionUpdate,
    UserPermissionsResponse,
)

router = APIRouter(prefix="/permissions", tags=["权限管理"])


def build_permission_tree(
    permissions: list[Permission], parent_id: str = None
) -> list[PermissionTree]:
    """构建权限树"""
    tree = []
    children = [p for p in permissions if p.parent_id == parent_id]
    for perm in children:
        node = PermissionTree(
            id=perm.id,
            name=perm.name,
            code=perm.code,
            description=perm.description,
            type=perm.type,
            resource=perm.resource,
            action=perm.action,
            parent_id=perm.parent_id,
            menu_id=perm.menu_id,
            is_system=perm.is_system,
            is_active=perm.is_active,
            sort_order=perm.sort_order,
            created_at=perm.created_at,
        )
        node.children = build_permission_tree(permissions, perm.id)
        tree.append(node)
    return tree


@router.get("/", response_model=PermissionListResponse)
async def list_permissions(
    type: str | None = None,
    resource: str | None = None,
    is_active: bool | None = None,
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """获取权限列表"""
    query = select(Permission)
    count_query = select(func.count()).select_from(Permission)

    if type:
        query = query.where(Permission.type == type)
        count_query = count_query.where(Permission.type == type)
    if resource:
        query = query.where(Permission.resource == resource)
        count_query = count_query.where(Permission.resource == resource)
    if is_active is not None:
        query = query.where(Permission.is_active == is_active)
        count_query = count_query.where(Permission.is_active == is_active)

    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    query = query.order_by(Permission.sort_order).offset(skip).limit(limit)
    result = await db.execute(query)
    permissions = result.scalars().all()

    return PermissionListResponse(
        items=[PermissionInDB.model_validate(p) for p in permissions],
        total=total,
    )


@router.get("/tree", response_model=list[PermissionTree])
async def get_permission_tree(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """获取权限树"""
    result = await db.execute(select(Permission).order_by(Permission.sort_order))
    permissions = result.scalars().all()
    return build_permission_tree(list(permissions))


@router.get("/resources", response_model=list[str])
async def list_resources(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """获取所有资源类型"""
    result = await db.execute(
        select(Permission.resource).distinct().where(Permission.resource != "")
    )
    return [r for r in result.scalars().all() if r]


@router.post("/", response_model=PermissionInDB, status_code=status.HTTP_201_CREATED)
async def create_permission(
    data: PermissionCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(PermissionChecker("permission:create")),
):
    """创建权限"""
    # 检查 code 唯一性
    result = await db.execute(select(Permission).where(Permission.code == data.code))
    if result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="权限代码已存在")

    # 检查父权限
    if data.parent_id:
        result = await db.execute(
            select(Permission).where(Permission.id == data.parent_id)
        )
        if not result.scalar_one_or_none():
            raise HTTPException(status_code=400, detail="父权限不存在")

    perm = Permission(**data.model_dump())
    db.add(perm)
    await db.commit()
    await db.refresh(perm)
    return perm


@router.get("/{permission_id}", response_model=PermissionInDB)
async def get_permission(
    permission_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """获取权限详情"""
    result = await db.execute(select(Permission).where(Permission.id == permission_id))
    perm = result.scalar_one_or_none()
    if not perm:
        raise HTTPException(status_code=404, detail="权限不存在")
    return perm


@router.put("/{permission_id}", response_model=PermissionInDB)
async def update_permission(
    permission_id: str,
    data: PermissionUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(PermissionChecker("permission:update")),
):
    """更新权限"""
    result = await db.execute(select(Permission).where(Permission.id == permission_id))
    perm = result.scalar_one_or_none()
    if not perm:
        raise HTTPException(status_code=404, detail="权限不存在")

    if perm.is_system:
        raise HTTPException(status_code=400, detail="系统权限不可修改")

    update_data = data.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(perm, key, value)

    await db.commit()
    await db.refresh(perm)
    return perm


@router.delete("/{permission_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_permission(
    permission_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(PermissionChecker("permission:delete")),
):
    """删除权限"""
    result = await db.execute(select(Permission).where(Permission.id == permission_id))
    perm = result.scalar_one_or_none()
    if not perm:
        raise HTTPException(status_code=404, detail="权限不存在")

    if perm.is_system:
        raise HTTPException(status_code=400, detail="系统权限不可删除")

    # 检查是否有子权限
    result = await db.execute(
        select(Permission).where(Permission.parent_id == permission_id).limit(1)
    )
    if result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="存在子权限，不可删除")

    await db.delete(perm)
    await db.commit()


@router.post("/init-defaults", response_model=MessageResponse)
async def init_default_permissions(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(PermissionChecker("*")),
):
    """初始化默认权限（仅超级管理员）"""
    created = 0
    for perm_data in DEFAULT_PERMISSIONS:
        result = await db.execute(
            select(Permission).where(Permission.code == perm_data["code"])
        )
        if not result.scalar_one_or_none():
            perm = Permission(**perm_data)
            db.add(perm)
            created += 1

    await db.commit()
    return MessageResponse(message=f"初始化完成，创建 {created} 个权限")


@router.get("/user/{user_id}/permissions", response_model=UserPermissionsResponse)
async def get_user_permissions(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """获取用户权限列表"""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")

    from app.core.permissions import get_user_permission_codes

    permission_codes = await get_user_permission_codes(user, db)

    return UserPermissionsResponse(
        user_id=user.id,
        permissions=permission_codes,
        is_super_admin=user.is_super_admin,
        is_org_admin=user.is_org_admin,
    )
