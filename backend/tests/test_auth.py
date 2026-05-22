"""
认证系统单元测试

覆盖：注册、登录、令牌刷新、账户锁定、密码修改、密码重置、资料更新
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_access_token,
    decode_refresh_token,
    get_password_hash,
    verify_password,
)
from app.models import User, UserRole
from tests.conftest import auth_headers

# ────────────────────────────────────────────
# 密码哈希与 JWT 工具函数
# ────────────────────────────────────────────


class TestPasswordHash:
    def test_hash_and_verify_success(self):
        pw = "MySecret123"
        hashed = get_password_hash(pw)
        assert verify_password(pw, hashed) is True

    def test_hash_and_verify_fail(self):
        hashed = get_password_hash("correct")
        assert verify_password("wrong", hashed) is False

    def test_different_hashes_for_same_password(self):
        pw = "SamePassword"
        h1 = get_password_hash(pw)
        h2 = get_password_hash(pw)
        assert h1 != h2  # bcrypt salt 不同
        assert verify_password(pw, h1)
        assert verify_password(pw, h2)


class TestJWT:
    def test_create_and_decode_access_token(self):
        token = create_access_token(data={"sub": "42"})
        payload = decode_access_token(token)
        assert payload is not None
        assert payload["sub"] == "42"
        assert payload["type"] == "access"

    def test_create_and_decode_refresh_token(self):
        token = create_refresh_token(data={"sub": "42"})
        payload = decode_refresh_token(token)
        assert payload is not None
        assert payload["sub"] == "42"
        assert payload["type"] == "refresh"

    def test_access_token_rejected_by_refresh_decoder(self):
        token = create_access_token(data={"sub": "42"})
        assert decode_refresh_token(token) is None

    def test_refresh_token_rejected_by_access_decoder(self):
        token = create_refresh_token(data={"sub": "42"})
        assert decode_access_token(token) is None

    def test_invalid_token_returns_none(self):
        assert decode_access_token("invalid.token.here") is None
        assert decode_refresh_token("invalid.token.here") is None

    def test_expired_access_token(self):
        from datetime import timedelta

        token = create_access_token(
            data={"sub": "42"}, expires_delta=timedelta(seconds=-1)
        )
        assert decode_access_token(token) is None


# ────────────────────────────────────────────
# 注册 API
# ────────────────────────────────────────────


class TestRegister:
    @pytest.mark.asyncio
    async def test_register_success(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        resp = await client.post(
            "/api/auth/register",
            json={
                "username": "newuser",
                "email": "new@example.com",
                "password": "NewPass@123",
                "display_name": "新用户",
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["username"] == "newuser"
        assert data["email"] == "new@example.com"
        assert data["role"] == "org_admin"  # 注册用户默认组织管理员
        assert "hashed_password" not in data

    @pytest.mark.asyncio
    async def test_register_duplicate_username(
        self, client: AsyncClient, test_user: User
    ):
        resp = await client.post(
            "/api/auth/register",
            json={
                "username": "testuser",
                "email": "another@example.com",
                "password": "Pass@123456",
                "display_name": "重复用户名",
            },
        )
        assert resp.status_code == 400
        assert "用户名已存在" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_register_duplicate_email(self, client: AsyncClient, test_user: User):
        resp = await client.post(
            "/api/auth/register",
            json={
                "username": "anotheruser",
                "email": "test@example.com",
                "password": "Pass@123456",
                "display_name": "重复邮箱",
            },
        )
        assert resp.status_code == 400
        assert "邮箱已注册" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_register_creates_organization(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        from sqlalchemy import select

        resp = await client.post(
            "/api/auth/register",
            json={
                "username": "orgcreator",
                "email": "orgcreator@example.com",
                "password": "Pass@123456",
                "display_name": "组织创建者",
            },
        )
        assert resp.status_code == 201
        # 验证组织已创建
        from app.models import Organization

        result = await db_session.execute(
            select(Organization).where(Organization.code == "orgcreator")
        )
        org = result.scalar_one_or_none()
        assert org is not None


# ────────────────────────────────────────────
# 登录 API
# ────────────────────────────────────────────


class TestLogin:
    @pytest.mark.asyncio
    async def test_login_success(self, client: AsyncClient, test_user: User):
        resp = await client.post(
            "/api/auth/login",
            json={
                "username": "testuser",
                "password": "test123456",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "access_token" in data
        assert data["token_type"] == "bearer"
        # refresh_token 在 HttpOnly cookie 中
        assert "refresh_token" in resp.cookies

    @pytest.mark.asyncio
    async def test_login_wrong_password(self, client: AsyncClient, test_user: User):
        resp = await client.post(
            "/api/auth/login",
            json={
                "username": "testuser",
                "password": "wrongpassword",
            },
        )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_login_nonexistent_user(self, client: AsyncClient):
        resp = await client.post(
            "/api/auth/login",
            json={
                "username": "ghost",
                "password": "doesntmatter",
            },
        )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_login_missing_fields(self, client: AsyncClient):
        resp = await client.post("/api/auth/login", json={})
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_login_disabled_user(
        self, client: AsyncClient, db_session: AsyncSession, test_user: User
    ):
        test_user.is_active = False
        await db_session.commit()
        resp = await client.post(
            "/api/auth/login",
            json={
                "username": "testuser",
                "password": "test123456",
            },
        )
        assert resp.status_code == 400


# ────────────────────────────────────────────
# 账户锁定
# ────────────────────────────────────────────


class TestAccountLockout:
    @pytest.mark.asyncio
    async def test_lockout_after_max_attempts(
        self, client: AsyncClient, test_user: User
    ):
        from app.core.config import settings

        for _ in range(settings.MAX_LOGIN_ATTEMPTS):
            await client.post(
                "/api/auth/login",
                json={
                    "username": "testuser",
                    "password": "wrongpass",
                },
            )
        # 第 N+1 次应该被锁定
        resp = await client.post(
            "/api/auth/login",
            json={
                "username": "testuser",
                "password": "test123456",
            },
        )
        assert resp.status_code == 423

    @pytest.mark.asyncio
    async def test_successful_login_resets_attempts(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        # 创建专用用户
        from app.core.security import get_password_hash

        user = User(
            username="locktest",
            email="locktest@example.com",
            display_name="锁定测试",
            hashed_password=get_password_hash("lockpass123"),
            role=UserRole.USER,
            is_active=True,
        )
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)

        # 失败几次（不超过上限）
        for _ in range(3):
            await client.post(
                "/api/auth/login",
                json={
                    "username": "locktest",
                    "password": "wrongpass",
                },
            )

        # 正确登录
        resp = await client.post(
            "/api/auth/login",
            json={
                "username": "locktest",
                "password": "lockpass123",
            },
        )
        assert resp.status_code == 200

        # 再次验证失败计数已重置 — 下一次错误不应锁定
        resp = await client.post(
            "/api/auth/login",
            json={
                "username": "locktest",
                "password": "wrongpass",
            },
        )
        assert resp.status_code == 401  # 正常 401，不是 423


# ────────────────────────────────────────────
# 令牌刷新
# ────────────────────────────────────────────


class TestRefreshToken:
    @pytest.mark.asyncio
    async def test_refresh_success(self, client: AsyncClient, test_user: User):
        login_resp = await client.post(
            "/api/auth/login",
            json={
                "username": "testuser",
                "password": "test123456",
            },
        )
        refresh_token = login_resp.cookies["refresh_token"]

        resp = await client.post(
            "/api/auth/refresh-token",
            json={
                "refresh_token": refresh_token,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "access_token" in data
        # 新的 refresh_token 在 cookie 中
        assert "refresh_token" in resp.cookies

    @pytest.mark.asyncio
    async def test_refresh_with_invalid_token(self, client: AsyncClient):
        resp = await client.post(
            "/api/auth/refresh-token",
            json={
                "refresh_token": "invalid.token.here",
            },
        )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_refresh_missing_token(self, client: AsyncClient):
        # 不传 body（不是空 JSON），refresh_token 字段必填，空 JSON 会被 Pydantic 拦截为 422
        resp = await client.post("/api/auth/refresh-token")
        assert resp.status_code == 401  # 无 body 也无 cookie


# ────────────────────────────────────────────
# 获取当前用户 / 令牌验证
# ────────────────────────────────────────────


class TestGetCurrentUser:
    @pytest.mark.asyncio
    async def test_get_me(self, client: AsyncClient, test_user: User):
        resp = await client.get("/api/auth/me", headers=auth_headers(test_user.id))
        assert resp.status_code == 200
        data = resp.json()
        assert data["username"] == "testuser"
        assert data["email"] == "test@example.com"

    @pytest.mark.asyncio
    async def test_get_me_no_token(self, client: AsyncClient):
        resp = await client.get("/api/auth/me")
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_validate_token(self, client: AsyncClient, test_user: User):
        resp = await client.get(
            "/api/auth/validate-token", headers=auth_headers(test_user.id)
        )
        assert resp.status_code == 200
        assert resp.json()["valid"] is True


# ────────────────────────────────────────────
# 修改密码
# ────────────────────────────────────────────


class TestChangePassword:
    @pytest.mark.asyncio
    async def test_change_password_success(self, client: AsyncClient, test_user: User):
        resp = await client.post(
            "/api/auth/change-password",
            json={
                "old_password": "test123456",
                "new_password": "NewPass@654",
            },
            headers=auth_headers(test_user.id),
        )
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_change_password_wrong_old(
        self, client: AsyncClient, test_user: User
    ):
        resp = await client.post(
            "/api/auth/change-password",
            json={
                "old_password": "wrongold",
                "new_password": "NewPass@654",
            },
            headers=auth_headers(test_user.id),
        )
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_change_password_too_short(
        self, client: AsyncClient, test_user: User
    ):
        resp = await client.post(
            "/api/auth/change-password",
            json={
                "old_password": "test123456",
                "new_password": "123",
            },
            headers=auth_headers(test_user.id),
        )
        assert resp.status_code == 400


# ────────────────────────────────────────────
# 密码重置
# ────────────────────────────────────────────


class TestPasswordReset:
    @pytest.mark.asyncio
    async def test_request_reset_existing_email(
        self, client: AsyncClient, test_user: User
    ):
        resp = await client.post(
            "/api/auth/password-reset/request",
            json={
                "email": "test@example.com",
            },
        )
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_request_reset_nonexistent_email(self, client: AsyncClient):
        # 不泄露用户是否存在
        resp = await client.post(
            "/api/auth/password-reset/request",
            json={
                "email": "nobody@example.com",
            },
        )
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_request_reset_missing_email(self, client: AsyncClient):
        resp = await client.post("/api/auth/password-reset/request", json={})
        assert resp.status_code == 422


# ────────────────────────────────────────────
# 更新资料
# ────────────────────────────────────────────


class TestUpdateProfile:
    @pytest.mark.asyncio
    async def test_update_display_name(self, client: AsyncClient, test_user: User):
        resp = await client.put(
            "/api/auth/profile",
            json={
                "display_name": "新名字",
            },
            headers=auth_headers(test_user.id),
        )
        assert resp.status_code == 200
        assert resp.json()["display_name"] == "新名字"

    @pytest.mark.asyncio
    async def test_update_avatar(self, client: AsyncClient, test_user: User):
        resp = await client.put(
            "/api/auth/profile",
            json={
                "avatar": "https://example.com/avatar.png",
            },
            headers=auth_headers(test_user.id),
        )
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_update_profile_no_token(self, client: AsyncClient):
        resp = await client.put(
            "/api/auth/profile",
            json={
                "display_name": "匿名",
            },
        )
        assert resp.status_code == 401


# ────────────────────────────────────────────
# 登出
# ────────────────────────────────────────────


class TestLogout:
    @pytest.mark.asyncio
    async def test_logout_success(self, client: AsyncClient, test_user: User):
        resp = await client.post("/api/auth/logout", headers=auth_headers(test_user.id))
        assert resp.status_code == 200
        assert "登出成功" in resp.json()["message"]
