"""
数据库初始化脚本
创建默认管理员用户、权限、角色
"""

import asyncio

from sqlalchemy import select

from app.core.database import async_session_maker
from app.core.security import get_password_hash
from app.models import (
    DEFAULT_PERMISSIONS,
    Agent,
    Organization,
    Permission,
    Role,
    Skill,
    User,
    UserRole,
    Workspace,
)


async def init_default_data():
    """初始化基础数据"""
    async with async_session_maker() as session:
        # 检查是否已有用户
        result = await session.execute(select(User).limit(1))
        if result.scalar_one_or_none():
            print("数据库已有数据，跳过初始化")
            return

        # 1. 创建默认组织（先创建，因为 User 需要引用）
        org = Organization(
            name="PioneClaw",
            code="pioneclaw",
            description="PioneClaw 默认组织",
            type="company",
            level=1,
        )
        session.add(org)
        await session.flush()  # 获取 org.id
        org.path = str(org.id)  # 设置路径

        # 2. 创建默认管理员（先不设置 workspace，因为 workspace 需要 owner_id）
        admin = User(
            username="admin",
            email="admin@example.com",
            display_name="管理员",
            hashed_password=get_password_hash("admin123"),
            role=UserRole.SUPER_ADMIN,
            is_super_admin=True,
            is_org_admin=False,
            is_active=True,
            organization_id=org.id,
        )
        session.add(admin)
        await session.flush()  # 获取 admin.id

        # 更新组织管理者
        org.manager_id = admin.id

        # 3. 创建默认工作空间（需要 owner_id）
        workspace = Workspace(
            name="默认工作空间",
            path="",
            description="管理员默认工作空间",
            owner_id=admin.id,
            organization_id=org.id,
            is_default=True,
            is_active=True,
        )
        session.add(workspace)
        await session.flush()  # 获取 workspace.id

        # 更新管理员的默认工作空间
        admin.default_workspace_id = workspace.id

        # 4. 创建默认权限
        for perm_data in DEFAULT_PERMISSIONS:
            perm = Permission(**perm_data)
            session.add(perm)

        # 5. 创建默认角色（三级：超级管理员 / 组织管理员 / 普通用户）
        roles = [
            Role(
                name="超级管理员",
                code="super_admin",
                description="系统最高权限管理员",
                permissions={"codes": ["*"]},
                type="system",
                level=2,
                is_default=False,
                is_system=True,
                is_active=True,
            ),
            Role(
                name="组织管理员",
                code="org_admin",
                description="组织级管理员，可管理本组织用户",
                permissions={
                    "codes": [
                        "dashboard:view",
                        "chat:*",
                        "agent:*",
                        "skill:*",
                        "memory:*",
                        "knowledge:*",
                        "runner:read",
                        "wiki:*",
                        "task:*",
                        "user:read",
                        "user:create",
                        "user:update",
                        "user:delete",
                        "role:read",
                        "org:*",
                    ]
                },
                type="system",
                level=1,
                is_default=False,
                is_system=True,
                is_active=True,
            ),
            Role(
                name="普通用户",
                code="user",
                description="普通用户，基础功能权限",
                permissions={
                    "codes": [
                        "dashboard:view",
                        "chat:view",
                        "chat:create",
                        "agent:read",
                        "agent:execute",
                        "skill:read",
                        "memory:read",
                        "memory:create",
                        "knowledge:read",
                        "wiki:read",
                        "task:read",
                        "task:create",
                    ]
                },
                type="system",
                level=0,
                is_default=True,
                is_system=True,
                is_active=True,
            ),
        ]
        for role in roles:
            session.add(role)

        # 6. 创建默认 Agent（需要 creator_id 和 workspace_id）
        agents = [
            Agent(
                name="general-purpose",
                display_name="通用执行",
                description="通用型 Agent，适合大多数任务",
                model="gpt-4o",
                max_turns=20,
                creator_id=admin.id,
                workspace_id=workspace.id,
            ),
            Agent(
                name="coding",
                display_name="编程",
                description="专注于代码编写和调试",
                model="gpt-4o",
                max_turns=100,
                creator_id=admin.id,
                workspace_id=workspace.id,
            ),
            Agent(
                name="plan",
                display_name="规划分析",
                description="偏规划拆解和方案设计的 Agent",
                model="gpt-4o",
                max_turns=10,
                creator_id=admin.id,
                workspace_id=workspace.id,
            ),
        ]
        for agent in agents:
            session.add(agent)

        # 7. 创建默认 Skill（需要 creator_id）
        skills = [
            Skill(
                name="file-operations",
                display_name="文件操作",
                description="文件读写、创建、删除等操作",
                category="system",
                scope="system",
                creator_id=admin.id,
                always_activate=False,
                skill_format="inline",
            ),
            Skill(
                name="web-search",
                display_name="网络搜索",
                description="搜索引擎搜索和信息获取",
                category="system",
                scope="system",
                creator_id=admin.id,
                always_activate=False,
                skill_format="inline",
            ),
        ]
        for skill in skills:
            session.add(skill)

        await session.commit()
        import logging

        _logger = logging.getLogger(__name__)
        _logger.info("PioneClaw 初始化完成！")
        _logger.info("   默认管理员: admin / <请查看文档获取初始密码>")
        _logger.info(f"   默认组织: {org.name} ({org.code})")
        _logger.info(f"   默认工作空间: {workspace.name}")


if __name__ == "__main__":
    asyncio.run(init_default_data())
