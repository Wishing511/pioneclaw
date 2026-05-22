"""
Runner User Binding API 测试

TDD: 测试先于实现。首次运行应全部 FAIL（端点尚未实现）。
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Runner, RunnerStatus, User
from tests.conftest import auth_headers

# ============================
# 辅助函数
# ============================


async def create_runner(db_session: AsyncSession, name: str, **kwargs) -> Runner:
    """在测试数据库中创建一个 Runner"""
    runner = Runner(
        name=name,
        display_name=kwargs.pop("display_name", name),
        status=kwargs.pop("status", RunnerStatus.ONLINE),
        **kwargs,
    )
    db_session.add(runner)
    await db_session.commit()
    await db_session.refresh(runner)
    return runner


# ============================
# 1. POST /{runner_id}/bind-user
# ============================


class TestBindUser:
    """测试绑定用户到 Runner"""

    @pytest.mark.asyncio
    async def test_bind_user_success(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        test_admin: User,
        test_user: User,
    ):
        """管理员成功绑定用户到 Runner"""
        runner = await create_runner(db_session, "bind-success-1")

        response = await client.post(
            f"/api/runners/{runner.id}/bind-user",
            json={"user_id": test_user.id},
            headers=auth_headers(test_admin.id),
        )

        assert response.status_code == 200
        data = response.json()
        assert data["user_id"] == test_user.id

        # 验证数据库已更新
        await db_session.refresh(runner)
        assert runner.user_id == test_user.id

    @pytest.mark.asyncio
    async def test_bind_user_already_bound(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        test_admin: User,
        test_user: User,
    ):
        """绑定已绑定用户的 Runner 应返回 400"""
        runner = await create_runner(
            db_session, "bind-already-bound", user_id=test_user.id
        )

        response = await client.post(
            f"/api/runners/{runner.id}/bind-user",
            json={"user_id": test_admin.id},
            headers=auth_headers(test_admin.id),
        )

        assert response.status_code == 400
        assert "已绑定" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_bind_user_unauthorized(
        self, client: AsyncClient, db_session: AsyncSession, test_user: User
    ):
        """未认证请求绑定 Runner 应返回 401"""
        runner = await create_runner(db_session, "bind-noauth")

        response = await client.post(
            f"/api/runners/{runner.id}/bind-user",
            json={"user_id": test_user.id},
        )

        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_bind_user_not_admin(
        self, client: AsyncClient, db_session: AsyncSession, test_user: User
    ):
        """普通用户尝试绑定 Runner 应返回 403"""
        runner = await create_runner(db_session, "bind-not-admin")

        response = await client.post(
            f"/api/runners/{runner.id}/bind-user",
            json={"user_id": test_user.id},
            headers=auth_headers(test_user.id),
        )

        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_bind_user_nonexistent_user(
        self, client: AsyncClient, db_session: AsyncSession, test_admin: User
    ):
        """绑定到不存在的用户应返回 404"""
        runner = await create_runner(db_session, "bind-no-user")

        response = await client.post(
            f"/api/runners/{runner.id}/bind-user",
            json={"user_id": 99999},
            headers=auth_headers(test_admin.id),
        )

        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_bind_user_nonexistent_runner(
        self, client: AsyncClient, test_admin: User, test_user: User
    ):
        """绑定不存在的 Runner 应返回 404"""
        response = await client.post(
            "/api/runners/99999/bind-user",
            json={"user_id": test_user.id},
            headers=auth_headers(test_admin.id),
        )

        assert response.status_code == 404


# ============================
# 2. DELETE /{runner_id}/unbind-user
# ============================


class TestUnbindUser:
    """测试解绑 Runner 用户"""

    @pytest.mark.asyncio
    async def test_unbind_user_success(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        test_admin: User,
        test_user: User,
    ):
        """管理员成功解绑 Runner 用户"""
        runner = await create_runner(db_session, "unbind-success", user_id=test_user.id)

        response = await client.delete(
            f"/api/runners/{runner.id}/unbind-user",
            headers=auth_headers(test_admin.id),
        )

        assert response.status_code == 200
        assert response.json()["message"] == "解绑成功"

        # 验证数据库已更新
        await db_session.refresh(runner)
        assert runner.user_id is None

    @pytest.mark.asyncio
    async def test_unbind_user_not_bound(
        self, client: AsyncClient, db_session: AsyncSession, test_admin: User
    ):
        """解绑未绑定用户的 Runner —— 幂等成功"""
        runner = await create_runner(db_session, "unbind-none")

        response = await client.delete(
            f"/api/runners/{runner.id}/unbind-user",
            headers=auth_headers(test_admin.id),
        )

        assert response.status_code == 200
        assert response.json()["message"] == "解绑成功"

    @pytest.mark.asyncio
    async def test_unbind_user_unauthorized(
        self, client: AsyncClient, db_session: AsyncSession, test_user: User
    ):
        """未认证解绑 Runner 应返回 401"""
        runner = await create_runner(db_session, "unbind-noauth", user_id=test_user.id)

        response = await client.delete(
            f"/api/runners/{runner.id}/unbind-user",
        )

        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_unbind_user_not_admin(
        self, client: AsyncClient, db_session: AsyncSession, test_user: User
    ):
        """普通用户尝试解绑 Runner 应返回 403"""
        runner = await create_runner(
            db_session, "unbind-not-admin", user_id=test_user.id
        )

        response = await client.delete(
            f"/api/runners/{runner.id}/unbind-user",
            headers=auth_headers(test_user.id),
        )

        assert response.status_code == 403


# ============================
# 3. GET /my-bindings
# ============================


class TestMyBindings:
    """测试获取当前用户绑定的 Runner 列表"""

    @pytest.mark.asyncio
    async def test_my_bindings_returns_user_runners(
        self, client: AsyncClient, db_session: AsyncSession, test_user: User
    ):
        """返回当前用户的 Runner"""
        r1 = await create_runner(db_session, "mybind-1", user_id=test_user.id)
        r2 = await create_runner(db_session, "mybind-2", user_id=test_user.id)

        response = await client.get(
            "/api/runners/my-bindings",
            headers=auth_headers(test_user.id),
        )

        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        ids = {r["id"] for r in data}
        assert r1.id in ids
        assert r2.id in ids

    @pytest.mark.asyncio
    async def test_my_bindings_excludes_other_users(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        test_user: User,
        test_admin: User,
    ):
        """不返回其他用户的 Runner"""
        await create_runner(db_session, "mybind-mine", user_id=test_user.id)
        await create_runner(db_session, "mybind-other", user_id=test_admin.id)

        response = await client.get(
            "/api/runners/my-bindings",
            headers=auth_headers(test_user.id),
        )

        assert response.status_code == 200
        data = response.json()
        {r["id"] for r in data}
        names = {r["name"] for r in data}
        assert "mybind-mine" in names
        assert "mybind-other" not in names

    @pytest.mark.asyncio
    async def test_my_bindings_empty(
        self, client: AsyncClient, db_session: AsyncSession, test_user: User
    ):
        """没有绑定 Runner 的用户返回空列表"""
        response = await client.get(
            "/api/runners/my-bindings",
            headers=auth_headers(test_user.id),
        )

        assert response.status_code == 200
        data = response.json()
        assert data == []

    @pytest.mark.asyncio
    async def test_my_bindings_unauthorized(self, client: AsyncClient):
        """未认证请求 my-bindings 应返回 401"""
        response = await client.get("/api/runners/my-bindings")
        assert response.status_code == 401


# ============================
# 4. PUT /my-default
# ============================


class TestSetDefaultRunner:
    """测试设置用户默认 Runner"""

    @pytest.mark.asyncio
    async def test_set_default_runner_success(
        self, client: AsyncClient, db_session: AsyncSession, test_user: User
    ):
        """成功设置默认 Runner"""
        runner = await create_runner(db_session, "default-ok", user_id=test_user.id)

        response = await client.put(
            "/api/runners/my-default",
            json={"runner_id": runner.id},
            headers=auth_headers(test_user.id),
        )

        assert response.status_code == 200
        assert response.json()["message"] == "默认 Runner 设置成功"

        # 验证 User.default_runner_id 已更新
        await db_session.refresh(test_user)
        assert test_user.default_runner_id == runner.id

    @pytest.mark.asyncio
    async def test_set_default_runner_not_found(
        self, client: AsyncClient, test_user: User
    ):
        """设置不存在的默认 Runner 应返回 404"""
        response = await client.put(
            "/api/runners/my-default",
            json={"runner_id": 99999},
            headers=auth_headers(test_user.id),
        )

        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_set_default_runner_other_user(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        test_user: User,
        test_admin: User,
    ):
        """设置其他用户的 Runner 为默认应返回 403"""
        runner = await create_runner(db_session, "default-other", user_id=test_admin.id)

        response = await client.put(
            "/api/runners/my-default",
            json={"runner_id": runner.id},
            headers=auth_headers(test_user.id),
        )

        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_set_default_runner_unauthorized(self, client: AsyncClient):
        """未认证设置默认 Runner 应返回 401"""
        response = await client.put(
            "/api/runners/my-default",
            json={"runner_id": 1},
        )

        assert response.status_code == 401
