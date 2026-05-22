"""
权限模型 - 树形结构
"""

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


def generate_uuid() -> str:
    return str(uuid.uuid4())


class Permission(Base):
    """权限模型 - 树形结构"""

    __tablename__ = "permissions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    code: Mapped[str] = mapped_column(
        String(100), unique=True, index=True
    )  # 例: task:create
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # 权限分类
    type: Mapped[str] = mapped_column(String(20), default="app")  # menu/system/app/api
    resource: Mapped[str] = mapped_column(String(50), default="")  # task/user/role
    action: Mapped[str] = mapped_column(
        String(20), default=""
    )  # create/read/update/delete/*

    # 树形结构
    parent_id: Mapped[str | None] = mapped_column(
        ForeignKey("permissions.id"), nullable=True, index=True
    )
    menu_id: Mapped[str | None] = mapped_column(String(36), nullable=True)  # 关联菜单ID

    # 状态
    is_system: Mapped[bool] = mapped_column(Boolean, default=False)  # 系统权限不可删除
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())

    # 关系
    parent: Mapped[Optional["Permission"]] = relationship(
        "Permission",
        back_populates="children",
        remote_side=[id],
        foreign_keys=[parent_id],
    )
    children: Mapped[list["Permission"]] = relationship(
        "Permission", back_populates="parent", foreign_keys=[parent_id]
    )

    @property
    def full_code(self) -> str:
        """完整权限码：resource:action"""
        if self.resource and self.action:
            return f"{self.resource}:{self.action}"
        return self.code

    def __repr__(self):
        return f"<Permission(id={self.id}, code={self.code}, name={self.name})>"


# 预置权限数据
DEFAULT_PERMISSIONS = [
    # 任务权限
    {
        "name": "任务管理",
        "code": "task:*",
        "resource": "task",
        "action": "*",
        "type": "app",
        "is_system": True,
    },
    {
        "name": "创建任务",
        "code": "task:create",
        "resource": "task",
        "action": "create",
        "type": "app",
        "is_system": True,
    },
    {
        "name": "查看任务",
        "code": "task:read",
        "resource": "task",
        "action": "read",
        "type": "app",
        "is_system": True,
    },
    {
        "name": "更新任务",
        "code": "task:update",
        "resource": "task",
        "action": "update",
        "type": "app",
        "is_system": True,
    },
    {
        "name": "删除任务",
        "code": "task:delete",
        "resource": "task",
        "action": "delete",
        "type": "app",
        "is_system": True,
    },
    {
        "name": "评论任务",
        "code": "task:comment",
        "resource": "task",
        "action": "comment",
        "type": "app",
        "is_system": True,
    },
    # 用户权限
    {
        "name": "用户管理",
        "code": "user:*",
        "resource": "user",
        "action": "*",
        "type": "app",
        "is_system": True,
    },
    {
        "name": "创建用户",
        "code": "user:create",
        "resource": "user",
        "action": "create",
        "type": "app",
        "is_system": True,
    },
    {
        "name": "查看用户",
        "code": "user:read",
        "resource": "user",
        "action": "read",
        "type": "app",
        "is_system": True,
    },
    {
        "name": "更新用户",
        "code": "user:update",
        "resource": "user",
        "action": "update",
        "type": "app",
        "is_system": True,
    },
    {
        "name": "删除用户",
        "code": "user:delete",
        "resource": "user",
        "action": "delete",
        "type": "app",
        "is_system": True,
    },
    # 角色权限
    {
        "name": "角色管理",
        "code": "role:*",
        "resource": "role",
        "action": "*",
        "type": "app",
        "is_system": True,
    },
    {
        "name": "创建角色",
        "code": "role:create",
        "resource": "role",
        "action": "create",
        "type": "app",
        "is_system": True,
    },
    {
        "name": "查看角色",
        "code": "role:read",
        "resource": "role",
        "action": "read",
        "type": "app",
        "is_system": True,
    },
    {
        "name": "更新角色",
        "code": "role:update",
        "resource": "role",
        "action": "update",
        "type": "app",
        "is_system": True,
    },
    {
        "name": "删除角色",
        "code": "role:delete",
        "resource": "role",
        "action": "delete",
        "type": "app",
        "is_system": True,
    },
    # 组织权限
    {
        "name": "组织管理",
        "code": "org:*",
        "resource": "org",
        "action": "*",
        "type": "app",
        "is_system": True,
    },
    {
        "name": "创建组织",
        "code": "org:create",
        "resource": "org",
        "action": "create",
        "type": "app",
        "is_system": True,
    },
    {
        "name": "查看组织",
        "code": "org:read",
        "resource": "org",
        "action": "read",
        "type": "app",
        "is_system": True,
    },
    {
        "name": "更新组织",
        "code": "org:update",
        "resource": "org",
        "action": "update",
        "type": "app",
        "is_system": True,
    },
    {
        "name": "删除组织",
        "code": "org:delete",
        "resource": "org",
        "action": "delete",
        "type": "app",
        "is_system": True,
    },
    # Wiki权限
    {
        "name": "Wiki管理",
        "code": "wiki:*",
        "resource": "wiki",
        "action": "*",
        "type": "app",
        "is_system": True,
    },
    {
        "name": "创建Wiki",
        "code": "wiki:create",
        "resource": "wiki",
        "action": "create",
        "type": "app",
        "is_system": True,
    },
    {
        "name": "查看Wiki",
        "code": "wiki:read",
        "resource": "wiki",
        "action": "read",
        "type": "app",
        "is_system": True,
    },
    {
        "name": "更新Wiki",
        "code": "wiki:update",
        "resource": "wiki",
        "action": "update",
        "type": "app",
        "is_system": True,
    },
    {
        "name": "删除Wiki",
        "code": "wiki:delete",
        "resource": "wiki",
        "action": "delete",
        "type": "app",
        "is_system": True,
    },
    # Agent权限
    {
        "name": "Agent管理",
        "code": "agent:*",
        "resource": "agent",
        "action": "*",
        "type": "app",
        "is_system": True,
    },
    {
        "name": "Skill管理",
        "code": "skill:*",
        "resource": "skill",
        "action": "*",
        "type": "app",
        "is_system": True,
    },
    {
        "name": "Runner管理",
        "code": "runner:*",
        "resource": "runner",
        "action": "*",
        "type": "app",
        "is_system": True,
    },
    # 系统权限
    {
        "name": "系统设置",
        "code": "system:settings",
        "resource": "system",
        "action": "settings",
        "type": "system",
        "is_system": True,
    },
    {
        "name": "系统监控",
        "code": "monitor:view",
        "resource": "monitor",
        "action": "view",
        "type": "system",
        "is_system": True,
    },
    # 超级权限
    {
        "name": "超级管理员",
        "code": "*",
        "resource": "*",
        "action": "*",
        "type": "system",
        "is_system": True,
    },
]
