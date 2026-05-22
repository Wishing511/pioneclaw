"""
角色管理 API 单元测试

覆盖：CRUD、系统角色保护、权限更新
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Role, User
from tests.conftest import auth_headers


class TestListRoles:
    @pytest.mark.asyncio
    async def test_list_roles(
        self, client: AsyncClient, test_user: User, test_role: Role
    ):
        resp = await client.get("/api/roles", headers=auth_headers(test_user.id))
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert any(r["code"] == "user" for r in data)

    @pytest.mark.asyncio
    async def test_list_roles_no_auth(self, client: AsyncClient):
        resp = await client.get("/api/roles")
        assert resp.status_code == 401


class TestCreateRole:
    @pytest.mark.asyncio
    async def test_create_custom_role(self, client: AsyncClient, test_admin: User):
        resp = await client.post(
            "/api/roles",
            json={
                "name": "审计员",
                "code": "auditor",
                "description": "审计角色",
                "permissions": {"codes": ["task:read", "agent:read"]},
                "is_active": True,
            },
            headers=auth_headers(test_admin.id),
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["code"] == "auditor"
        assert data["name"] == "审计员"

    @pytest.mark.asyncio
    async def test_create_duplicate_code(
        self, client: AsyncClient, test_admin: User, test_role: Role
    ):
        resp = await client.post(
            "/api/roles",
            json={
                "name": "另一个用户",
                "code": "user",
                "is_active": True,
            },
            headers=auth_headers(test_admin.id),
        )
        assert resp.status_code == 400
        assert "角色代码已存在" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_create_duplicate_name(
        self, client: AsyncClient, test_admin: User, test_role: Role
    ):
        resp = await client.post(
            "/api/roles",
            json={
                "name": "普通用户",
                "code": "another_user",
                "is_active": True,
            },
            headers=auth_headers(test_admin.id),
        )
        assert resp.status_code == 400
        assert "角色名称已存在" in resp.json()["detail"]


class TestGetRole:
    @pytest.mark.asyncio
    async def test_get_existing_role(
        self, client: AsyncClient, test_user: User, test_role: Role
    ):
        resp = await client.get(
            f"/api/roles/{test_role.id}", headers=auth_headers(test_user.id)
        )
        assert resp.status_code == 200
        assert resp.json()["code"] == "user"

    @pytest.mark.asyncio
    async def test_get_nonexistent_role(self, client: AsyncClient, test_user: User):
        resp = await client.get("/api/roles/99999", headers=auth_headers(test_user.id))
        assert resp.status_code == 404


class TestUpdateRole:
    @pytest.mark.asyncio
    async def test_update_permissions(
        self, client: AsyncClient, test_admin: User, test_role: Role
    ):
        resp = await client.put(
            f"/api/roles/{test_role.id}",
            json={
                "permissions": {
                    "codes": ["task:read", "task:create", "task:update", "task:delete"]
                },
            },
            headers=auth_headers(test_admin.id),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "task:delete" in data["permissions"]["codes"]

    @pytest.mark.asyncio
    async def test_update_name(
        self, client: AsyncClient, test_admin: User, test_role: Role
    ):
        resp = await client.put(
            f"/api/roles/{test_role.id}",
            json={
                "name": "普通用户(改)",
            },
            headers=auth_headers(test_admin.id),
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "普通用户(改)"

    @pytest.mark.asyncio
    async def test_update_nonexistent_role(self, client: AsyncClient, test_admin: User):
        resp = await client.put(
            "/api/roles/99999",
            json={
                "name": "不存在",
            },
            headers=auth_headers(test_admin.id),
        )
        assert resp.status_code == 404


class TestDeleteRole:
    @pytest.mark.asyncio
    async def test_delete_custom_role(
        self, client: AsyncClient, test_admin: User, db_session: AsyncSession
    ):
        # 创建一个可删除的自定义角色
        role = Role(
            name="待删除角色",
            code="to_delete",
            type="custom",
            is_system=False,
            is_active=True,
        )
        db_session.add(role)
        await db_session.commit()
        await db_session.refresh(role)

        resp = await client.delete(
            f"/api/roles/{role.id}", headers=auth_headers(test_admin.id)
        )
        assert resp.status_code == 200
        assert "角色已删除" in resp.json()["message"]

    @pytest.mark.asyncio
    async def test_delete_system_role_forbidden(
        self, client: AsyncClient, test_admin: User, test_role: Role
    ):
        # test_role 是 is_system=True
        resp = await client.delete(
            f"/api/roles/{test_role.id}", headers=auth_headers(test_admin.id)
        )
        assert resp.status_code == 400
        assert "系统角色不可删除" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_delete_nonexistent_role(self, client: AsyncClient, test_admin: User):
        resp = await client.delete(
            "/api/roles/99999", headers=auth_headers(test_admin.id)
        )
        assert resp.status_code == 404
