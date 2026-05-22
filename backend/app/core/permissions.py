"""
PioneClaw 权限系统

实现 RBAC 三级权限 + 资源级访问控制 + 通配符匹配

三级架构：
- 超级管理员 (level 2): 全系统权限，管理组织、模型配置、系统级 Skill
- 组织管理员 (level 1): 本组织管理权限，审批共享请求、管理组织用户
- 普通用户 (level 0): 私有资源完全控制，公共资源只读

资源作用域：
- system: 系统级资源，超管 CRUD，组织管理员/用户只读
- org: 组织级资源，组织管理员 CRUD，用户只读
- user: 用户级资源，创建者完全控制
"""

from fastapi import Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import get_current_active_user
from app.core.database import get_db
from app.models import Role, User


class PermissionChecker:
    """
    权限检查器 - 用作 FastAPI 依赖

    用法:
        @router.post("/", dependencies=[Depends(PermissionChecker("task:create"))])

        # 多个权限（满足任一）
        @router.get("/", dependencies=[Depends(PermissionChecker(["task:read", "task:*"]))])
    """

    def __init__(self, permissions: str | list[str]):
        if isinstance(permissions, str):
            self.required_permissions = [permissions]
        else:
            self.required_permissions = permissions

    async def __call__(
        self,
        current_user: User = Depends(get_current_active_user),
        db: AsyncSession = Depends(get_db),
    ):
        # 超级管理员直接通过
        if current_user.is_super_admin:
            return current_user

        # 获取用户所有权限代码
        user_permission_codes = await get_user_permission_codes(current_user, db)

        # 检查是否满足任一权限
        for required in self.required_permissions:
            if check_permission(required, user_permission_codes):
                return current_user

        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"权限不足，需要: {', '.join(self.required_permissions)}",
        )


class PermissionCheckerAll:
    """
    权限检查器（需要全部满足）

    用法:
        @router.post("/", dependencies=[Depends(PermissionCheckerAll(["task:create", "task:update"]))])
    """

    def __init__(self, permissions: list[str]):
        self.required_permissions = permissions

    async def __call__(
        self,
        current_user: User = Depends(get_current_active_user),
        db: AsyncSession = Depends(get_db),
    ):
        if current_user.is_super_admin:
            return current_user

        user_permission_codes = await get_user_permission_codes(current_user, db)

        for required in self.required_permissions:
            if not check_permission(required, user_permission_codes):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"权限不足，缺少: {required}",
                )

        return current_user


# ------------------------------------------------------------------
# 资源级权限检查
# ------------------------------------------------------------------


def can_access_resource(
    user: User,
    resource_scope: str,
    resource_creator_id: int | None = None,
    resource_org_id: str | None = None,
    action: str = "read",
) -> bool:
    """
    检查用户对特定作用域资源的访问权限

    Args:
        user: 当前用户
        resource_scope: 资源作用域 (system / org / user)
        resource_creator_id: 资源创建者 ID（scope=user 时必填）
        resource_org_id: 资源所属组织 ID（scope=org 时必填）
        action: 操作类型 (read / create / update / delete)

    Returns:
        bool: 是否有权限
    """
    # 超管全权
    if user.is_super_admin:
        return True

    if resource_scope == "system":
        # 系统级资源：超管可写，其他只读
        return action == "read"

    if resource_scope == "org":
        # 组织级资源：组织管理员可写，其他只读
        if action == "read":
            return True
        return bool(user.is_org_admin and user.organization_id == resource_org_id)

    if resource_scope == "user":
        # 用户级资源：创建者完全控制，组织管理员可读
        if resource_creator_id == user.id:
            return True
        return bool(
            action == "read"
            and user.is_org_admin
            and user.organization_id == resource_org_id
        )

    return False


def can_manage_approval(
    user: User, target_scope: str, target_org_id: str | None = None
) -> bool:
    """
    检查用户是否能审批指定级别的请求

    Args:
        user: 当前用户
        target_scope: 目标作用域 (org / system)
        target_org_id: 目标组织 ID（scope=org 时需要）
    """
    if user.is_super_admin:
        return True

    if target_scope == "org" and user.is_org_admin:
        return user.organization_id == target_org_id

    return False


# ------------------------------------------------------------------
# 权限代码相关
# ------------------------------------------------------------------


async def get_user_permission_codes(user: User, db: AsyncSession) -> list[str]:
    """获取用户所有权限代码列表"""
    # 超级管理员拥有所有权限
    if user.is_super_admin:
        return ["*"]

    permission_codes = set()

    # 从角色权限中获取
    result = await db.execute(
        select(Role).where(
            Role.code == user.role.value if hasattr(user.role, "value") else user.role
        )
    )
    role = result.scalar_one_or_none()

    if role and role.permissions:
        codes = role.permissions.get("codes", [])
        permission_codes.update(codes)

    # 组织管理员额外权限
    if user.is_org_admin:
        permission_codes.add("org:*")
        permission_codes.add("user:*")
        permission_codes.add("role:read")
        permission_codes.add("approval:org")

    # 所有已认证用户的基础权限（仅保留最基本的非敏感权限）
    permission_codes.add("dashboard:view")
    permission_codes.add("chat:view")
    permission_codes.add("chat:create")
    permission_codes.add("skill:user")
    permission_codes.add("approval:submit")

    return list(permission_codes)


def check_permission(required: str, user_permissions: list[str]) -> bool:
    """
    检查用户是否拥有指定权限

    支持通配符匹配:
    - "*" 匹配所有权限
    - "task:*" 匹配 "task:create", "task:read" 等
    - "task:create" 精确匹配
    """
    if "*" in user_permissions:
        return True

    if required in user_permissions:
        return True

    # 通配符匹配: task:* 匹配 task:create
    parts = required.split(":")
    if len(parts) >= 2:
        wildcard = f"{parts[0]}:*"
        if wildcard in user_permissions:
            return True

    return False


def has_any_permission(user_permissions: list[str], required: list[str]) -> bool:
    """检查用户是否拥有任一权限"""
    return any(check_permission(p, user_permissions) for p in required)


def has_all_permissions(user_permissions: list[str], required: list[str]) -> bool:
    """检查用户是否拥有全部权限"""
    return all(check_permission(p, user_permissions) for p in required)
