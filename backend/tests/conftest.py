"""
PioneClaw 测试配置

每个测试使用独立的文件 SQLite 数据库，测试结束自动清理
"""

import os
import tempfile
import uuid
from collections.abc import AsyncGenerator

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.database import Base, get_db
from app.core.security import (
    create_access_token,
    create_refresh_token,
    get_password_hash,
)
from app.main import app
from app.models import Organization, Role, User, UserRole


@pytest_asyncio.fixture
async def db_engine():
    """每个测试创建独立的 SQLite 文件数据库"""
    db_file = os.path.join(
        tempfile.gettempdir(), f"pioneclaw_test_{uuid.uuid4().hex}.db"
    )
    db_url = f"sqlite+aiosqlite:///{db_file}"
    engine = create_async_engine(
        db_url, connect_args={"check_same_thread": False}, echo=False
    )

    import app.models  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield engine

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()
    if os.path.exists(db_file):
        os.remove(db_file)


@pytest_asyncio.fixture
async def db_session(db_engine) -> AsyncGenerator[AsyncSession, None]:
    """测试数据库会话"""
    session_maker = async_sessionmaker(
        db_engine, class_=AsyncSession, expire_on_commit=False
    )
    async with session_maker() as session:
        yield session


@pytest_asyncio.fixture
async def client(db_engine) -> AsyncGenerator[AsyncClient, None]:
    """HTTP 测试客户端，使用测试数据库"""
    session_maker = async_sessionmaker(
        db_engine, class_=AsyncSession, expire_on_commit=False
    )

    async def override_get_db():
        async with session_maker() as session:
            yield session

    # 测试环境禁用限流，避免跨测试累积触发 429
    from app.core.config import settings as app_settings

    app_settings.RATE_LIMIT_ENABLED = False

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()
    app_settings.RATE_LIMIT_ENABLED = True


@pytest_asyncio.fixture
async def test_org(db_session: AsyncSession) -> Organization:
    """创建测试组织"""
    org = Organization(
        name="测试组织",
        code="test_org",
        description="用于测试的组织",
        type="company",
        level=1,
        path=1,
    )
    db_session.add(org)
    await db_session.flush()
    org.path = org.id
    await db_session.commit()
    await db_session.refresh(org)
    return org


@pytest_asyncio.fixture
async def test_role(db_session: AsyncSession) -> Role:
    """创建测试角色"""
    role = Role(
        name="普通用户",
        code="user",
        description="系统默认用户角色",
        type="system",
        level=0,
        is_system=True,
        is_default=True,
        permissions={
            "codes": [
                "task:read",
                "task:create",
                "task:update",
                "agent:read",
                "agent:create",
                "memory:read",
                "memory:create",
                "skill:read",
                "knowledge:read",
                "user:read",
            ]
        },
    )
    db_session.add(role)
    await db_session.commit()
    await db_session.refresh(role)
    return role


@pytest_asyncio.fixture
async def test_user(
    db_session: AsyncSession, test_org: Organization, test_role: Role
) -> User:
    """创建测试普通用户"""
    user = User(
        username="testuser",
        email="test@example.com",
        display_name="测试用户",
        hashed_password=get_password_hash("test123456"),
        role=UserRole.USER,
        is_active=True,
        organization_id=test_org.id,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest_asyncio.fixture
async def test_admin(db_session: AsyncSession, test_org: Organization) -> User:
    """创建测试超级管理员"""
    admin = User(
        username="admin",
        email="admin@example.com",
        display_name="管理员",
        hashed_password=get_password_hash("admin123456"),
        role=UserRole.SUPER_ADMIN,
        is_active=True,
        is_super_admin=True,
        organization_id=test_org.id,
    )
    db_session.add(admin)
    await db_session.commit()
    await db_session.refresh(admin)
    return admin


@pytest_asyncio.fixture
async def test_org_admin(db_session: AsyncSession, test_org: Organization) -> User:
    """创建测试组织管理员"""
    org_admin = User(
        username="orgadmin",
        email="orgadmin@example.com",
        display_name="组织管理员",
        hashed_password=get_password_hash("orgadmin123456"),
        role=UserRole.ORG_ADMIN,
        is_active=True,
        is_org_admin=True,
        organization_id=test_org.id,
    )
    db_session.add(org_admin)
    await db_session.commit()
    await db_session.refresh(org_admin)
    return org_admin


def auth_headers(user_id: int) -> dict:
    """生成认证请求头"""
    token = create_access_token(data={"sub": str(user_id)})
    return {"Authorization": f"Bearer {token}"}


def refresh_token_headers(user_id: int) -> dict:
    """生成刷新令牌请求头"""
    token = create_refresh_token(data={"sub": str(user_id)})
    return {"Authorization": f"Bearer {token}"}
