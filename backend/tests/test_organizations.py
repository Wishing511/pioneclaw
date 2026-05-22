"""
组织管理 API 单元测试

覆盖：组织列表、树、创建、更新、删除、组织用户
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Organization, User
from tests.conftest import auth_headers


class TestListOrganizations:
    @pytest.mark.asyncio
    async def test_list_orgs(
        self, client: AsyncClient, test_user: User, test_org: Organization
    ):
        resp = await client.get(
            "/api/organizations/", headers=auth_headers(test_user.id)
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 1
        assert any(o["code"] == "test_org" for o in data["items"])

    @pytest.mark.asyncio
    async def test_list_orgs_no_auth(self, client: AsyncClient):
        resp = await client.get("/api/organizations/")
        assert resp.status_code == 401


class TestOrganizationTree:
    @pytest.mark.asyncio
    async def test_org_tree(
        self, client: AsyncClient, test_user: User, test_org: Organization
    ):
        resp = await client.get(
            "/api/organizations/tree", headers=auth_headers(test_user.id)
        )
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert any(o["name"] == "测试组织" for o in data)


class TestSimpleOrganizations:
    @pytest.mark.asyncio
    async def test_simple_list(
        self, client: AsyncClient, test_user: User, test_org: Organization
    ):
        resp = await client.get(
            "/api/organizations/simple", headers=auth_headers(test_user.id)
        )
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert any(o["id"] == test_org.id for o in data)


class TestCreateOrganization:
    @pytest.mark.asyncio
    async def test_create_root_org(self, client: AsyncClient, test_admin: User):
        resp = await client.post(
            "/api/organizations/",
            json={
                "name": "新组织",
                "code": "new_org",
                "type": "company",
            },
            headers=auth_headers(test_admin.id),
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["code"] == "new_org"
        assert data["level"] == 1

    @pytest.mark.asyncio
    async def test_create_duplicate_code(
        self, client: AsyncClient, test_admin: User, test_org: Organization
    ):
        resp = await client.post(
            "/api/organizations/",
            json={
                "name": "重复组织",
                "code": "test_org",
            },
            headers=auth_headers(test_admin.id),
        )
        assert resp.status_code == 400
        assert "组织代码已存在" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_create_child_org(
        self, client: AsyncClient, test_admin: User, test_org: Organization
    ):
        resp = await client.post(
            "/api/organizations/",
            json={
                "name": "子部门",
                "code": "child_dept",
                "parent_id": test_org.id,
                "type": "department",
            },
            headers=auth_headers(test_admin.id),
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["level"] == 2
        assert data["parent_id"] == test_org.id


class TestGetOrganization:
    @pytest.mark.asyncio
    async def test_get_existing(
        self, client: AsyncClient, test_user: User, test_org: Organization
    ):
        resp = await client.get(
            f"/api/organizations/{test_org.id}", headers=auth_headers(test_user.id)
        )
        assert resp.status_code == 200
        assert resp.json()["code"] == "test_org"

    @pytest.mark.asyncio
    async def test_get_nonexistent(self, client: AsyncClient, test_user: User):
        resp = await client.get(
            "/api/organizations/nonexistent-id", headers=auth_headers(test_user.id)
        )
        assert resp.status_code == 404


class TestUpdateOrganization:
    @pytest.mark.asyncio
    async def test_update_name(
        self, client: AsyncClient, test_admin: User, test_org: Organization
    ):
        resp = await client.put(
            f"/api/organizations/{test_org.id}",
            json={
                "name": "更新组织名",
            },
            headers=auth_headers(test_admin.id),
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "更新组织名"

    @pytest.mark.asyncio
    async def test_update_nonexistent(self, client: AsyncClient, test_admin: User):
        resp = await client.put(
            "/api/organizations/nonexistent-id",
            json={
                "name": "不存在",
            },
            headers=auth_headers(test_admin.id),
        )
        assert resp.status_code == 404


class TestDeleteOrganization:
    @pytest.mark.asyncio
    async def test_delete_empty_org(
        self, client: AsyncClient, test_admin: User, db_session: AsyncSession
    ):
        org = Organization(
            name="待删组织", code="to_delete_org", type="team", level=1, path="temp"
        )
        db_session.add(org)
        await db_session.commit()
        await db_session.refresh(org)
        org.path = org.id
        await db_session.commit()

        resp = await client.delete(
            f"/api/organizations/{org.id}", headers=auth_headers(test_admin.id)
        )
        assert resp.status_code == 204

    @pytest.mark.asyncio
    async def test_delete_org_with_users(
        self, client: AsyncClient, test_admin: User, test_org: Organization
    ):
        resp = await client.delete(
            f"/api/organizations/{test_org.id}", headers=auth_headers(test_admin.id)
        )
        assert resp.status_code == 400
        assert "用户" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_delete_nonexistent(self, client: AsyncClient, test_admin: User):
        resp = await client.delete(
            "/api/organizations/nonexistent-id", headers=auth_headers(test_admin.id)
        )
        assert resp.status_code == 404


class TestOrganizationUsers:
    @pytest.mark.asyncio
    async def test_get_org_users(
        self, client: AsyncClient, test_user: User, test_org: Organization
    ):
        resp = await client.get(
            f"/api/organizations/{test_org.id}/users",
            headers=auth_headers(test_user.id),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 1
        assert any(u["id"] == test_user.id for u in data["items"])
