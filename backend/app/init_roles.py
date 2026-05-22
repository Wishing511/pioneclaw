"""
初始化角色数据
"""

import asyncio

from sqlalchemy import select

from app.core import async_session_maker
from app.models import Role


async def init_roles():
    """初始化角色数据"""
    async with async_session_maker() as session:
        # 检查是否已有角色
        result = await session.execute(select(Role).limit(1))
        if result.scalar_one_or_none():
            print("角色数据已存在，跳过初始化")
            return

        # 创建默认角色
        roles = [
            Role(
                name="超级管理员",
                code="super_admin",
                description="系统最高权限管理员",
                permissions={
                    "dashboard": ["view"],
                    "chat": ["view", "create", "delete"],
                    "agent": ["view", "create", "edit", "delete", "execute"],
                    "skill": ["view", "create", "edit", "delete"],
                    "memory": ["view", "create", "edit", "delete"],
                    "knowledge": ["view", "create", "edit", "delete", "upload"],
                    "runner": ["view", "approve", "delete"],
                    "system": ["ai_config", "role", "user", "settings"],
                },
                is_system=True,
                is_active=True,
            ),
            Role(
                name="组织管理员",
                code="org_admin",
                description="组织级管理员，可管理本组织用户",
                permissions={
                    "dashboard": ["view"],
                    "chat": ["view", "create", "delete"],
                    "agent": ["view", "create", "edit", "execute"],
                    "skill": ["view", "create", "edit"],
                    "memory": ["view", "create", "edit"],
                    "knowledge": ["view", "create", "edit", "upload"],
                    "runner": ["view"],
                    "system": ["user"],
                },
                is_system=True,
                is_active=True,
            ),
            Role(
                name="普通用户",
                code="user",
                description="普通用户，基础功能权限",
                permissions={
                    "dashboard": ["view"],
                    "chat": ["view", "create"],
                    "agent": ["view", "execute"],
                    "skill": ["view"],
                    "memory": ["view", "create"],
                    "knowledge": ["view"],
                },
                is_system=True,
                is_active=True,
            ),
        ]
        for role in roles:
            session.add(role)

        await session.commit()
        print("✅ 角色初始化完成！")


if __name__ == "__main__":
    asyncio.run(init_roles())
