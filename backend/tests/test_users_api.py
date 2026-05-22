"""
用户管理 API 单元测试

覆盖：用户列表、创建、更新、删除、密码重置、组织分配
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User, UserRole
from tests.conftest import auth_headers


class TestListUsers:
    @pytest.mark.asyncio
    async def test_list_users(self, client: AsyncClient, test_user: User):
        resp = await client.get("/api/users", headers=auth_headers(test_user.id))
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert any(u["username"] == "testuser" for u in data)

    @pytest.mark.asyncio
    async def test_list_users_no_auth(self, client: AsyncClient):
        resp = await client.get("/api/users")
        assert resp.status_code == 401


class TestCreateUser:
    @pytest.mark.asyncio
    async def test_create_user_success(self, client: AsyncClient, test_admin: User):
        resp = await client.post(
            "/api/users",
            json={
                "username": "newuser",
                "email": "new@example.com",
                "display_name": "新建用户",
                "password": "pass123456",
                "role": "user",
            },
            headers=auth_headers(test_admin.id),
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["username"] == "newuser"
        assert data["role"] == "user"
        assert "hashed_password" not in data

    @pytest.mark.asyncio
    async def test_create_user_with_org(
        self, client: AsyncClient, test_admin: User, test_org
    ):
        resp = await client.post(
            "/api/users",
            json={
                "username": "orguser",
                "email": "orguser@example.com",
                "display_name": "组织用户",
                "password": "pass123456",
                "role": "user",
                "organization_id": test_org.id,
            },
            headers=auth_headers(test_admin.id),
        )
        assert resp.status_code == 201
        assert resp.json()["organization_id"] == test_org.id

    @pytest.mark.asyncio
    async def test_create_duplicate_username(
        self, client: AsyncClient, test_admin: User, test_user: User
    ):
        resp = await client.post(
            "/api/users",
            json={
                "username": "testuser",
                "email": "unique@example.com",
                "display_name": "重复",
                "password": "pass123456",
            },
            headers=auth_headers(test_admin.id),
        )
        assert resp.status_code == 400
        assert "用户名已存在" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_create_duplicate_email(
        self, client: AsyncClient, test_admin: User, test_user: User
    ):
        resp = await client.post(
            "/api/users",
            json={
                "username": "uniqueuser",
                "email": "test@example.com",
                "display_name": "重复邮箱",
                "password": "pass123456",
            },
            headers=auth_headers(test_admin.id),
        )
        assert resp.status_code == 400
        assert "邮箱已被使用" in resp.json()["detail"]


class TestGetUser:
    @pytest.mark.asyncio
    async def test_get_existing_user(self, client: AsyncClient, test_user: User):
        resp = await client.get(
            f"/api/users/{test_user.id}", headers=auth_headers(test_user.id)
        )
        assert resp.status_code == 200
        assert resp.json()["username"] == "testuser"

    @pytest.mark.asyncio
    async def test_get_nonexistent_user(self, client: AsyncClient, test_user: User):
        resp = await client.get("/api/users/99999", headers=auth_headers(test_user.id))
        assert resp.status_code == 404


class TestUpdateUser:
    @pytest.mark.asyncio
    async def test_update_display_name(
        self, client: AsyncClient, test_admin: User, test_user: User
    ):
        resp = await client.put(
            f"/api/users/{test_user.id}",
            json={
                "display_name": "改名了",
            },
            headers=auth_headers(test_admin.id),
        )
        assert resp.status_code == 200
        assert resp.json()["display_name"] == "改名了"

    @pytest.mark.asyncio
    async def test_update_role(
        self, client: AsyncClient, test_admin: User, test_user: User
    ):
        resp = await client.put(
            f"/api/users/{test_user.id}",
            json={
                "role": "org_admin",
            },
            headers=auth_headers(test_admin.id),
        )
        assert resp.status_code == 200
        assert resp.json()["role"] == "org_admin"

    @pytest.mark.asyncio
    async def test_update_organization(
        self, client: AsyncClient, test_admin: User, test_user: User, test_org
    ):
        resp = await client.put(
            f"/api/users/{test_user.id}",
            json={
                "organization_id": test_org.id,
            },
            headers=auth_headers(test_admin.id),
        )
        assert resp.status_code == 200
        assert resp.json()["organization_id"] == test_org.id

    @pytest.mark.asyncio
    async def test_update_nonexistent_user(self, client: AsyncClient, test_admin: User):
        resp = await client.put(
            "/api/users/99999",
            json={
                "display_name": "不存在",
            },
            headers=auth_headers(test_admin.id),
        )
        assert resp.status_code == 404


class TestResetPassword:
    @pytest.mark.asyncio
    async def test_reset_password(
        self, client: AsyncClient, test_admin: User, test_user: User
    ):
        resp = await client.put(
            f"/api/users/{test_user.id}/password",
            json={
                "password": "newpassword123",
            },
            headers=auth_headers(test_admin.id),
        )
        assert resp.status_code == 200
        assert "密码已重置" in resp.json()["message"]

    @pytest.mark.asyncio
    async def test_reset_nonexistent_user(self, client: AsyncClient, test_admin: User):
        resp = await client.put(
            "/api/users/99999/password",
            json={
                "password": "newpass123",
            },
            headers=auth_headers(test_admin.id),
        )
        assert resp.status_code == 404


class TestDeleteUser:
    @pytest.mark.asyncio
    async def test_delete_user(
        self, client: AsyncClient, test_admin: User, db_session: AsyncSession
    ):
        from app.core.security import get_password_hash

        user = User(
            username="todelete",
            email="todelete@example.com",
            display_name="待删除",
            hashed_password=get_password_hash("pass123456"),
            role=UserRole.USER,
            is_active=True,
        )
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)

        resp = await client.delete(
            f"/api/users/{user.id}", headers=auth_headers(test_admin.id)
        )
        assert resp.status_code == 200
        assert "用户已删除" in resp.json()["message"]

    @pytest.mark.asyncio
    async def test_delete_super_admin_blocked(
        self, client: AsyncClient, test_admin: User
    ):
        # id=1 的用户不可删除
        resp = await client.delete("/api/users/1", headers=auth_headers(test_admin.id))
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_delete_nonexistent_user(self, client: AsyncClient, test_admin: User):
        resp = await client.delete(
            "/api/users/99999", headers=auth_headers(test_admin.id)
        )
        assert resp.status_code == 404
