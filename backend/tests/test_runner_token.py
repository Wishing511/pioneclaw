"""
Runner Token Rotation API 测试

TDD: 测试先于实现。首次运行应全部 FAIL（端点尚未实现）。
"""

from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import decrypt, encrypt
from app.models import Runner, RunnerStatus, User
from tests.conftest import auth_headers


def _naive_utcnow() -> datetime:
    """Return naive UTC datetime (compatible with SQLite round-trip)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


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
# POST /{runner_id}/rotate-token
# ============================


class TestRotateToken:
    """测试 Runner Token 轮换"""

    @pytest.mark.asyncio
    async def test_rotate_token_success(
        self, client: AsyncClient, db_session: AsyncSession, test_admin: User
    ):
        """管理员成功轮换 Runner Token，返回 new_token、rotated_at、old_token_expires_at"""
        runner = await create_runner(
            db_session, "rotate-success", status=RunnerStatus.APPROVED
        )

        response = await client.post(
            f"/api/runners/{runner.id}/rotate-token",
            headers=auth_headers(test_admin.id),
        )

        assert response.status_code == 200
        data = response.json()

        # 验证返回字段
        assert "new_token" in data
        assert "rotated_at" in data
        assert "old_token_expires_at" in data
        assert len(data["new_token"]) > 0

        # 验证过期时间约为 24 小时后（SQLite 返回 naive datetime）
        now_naive = _naive_utcnow()
        expires_at = datetime.fromisoformat(data["old_token_expires_at"])
        delta = expires_at - now_naive
        assert 23.5 * 3600 <= delta.total_seconds() <= 24.5 * 3600

        # 验证 rotated_at 是最近时间
        rotated_at = datetime.fromisoformat(data["rotated_at"])
        assert (now_naive - rotated_at).total_seconds() < 10

    @pytest.mark.asyncio
    async def test_rotate_token_unauthorized(self, client: AsyncClient):
        """未认证请求轮换 Token 应返回 401"""
        response = await client.post("/api/runners/1/rotate-token")
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_rotate_token_not_admin(
        self, client: AsyncClient, db_session: AsyncSession, test_user: User
    ):
        """普通用户尝试轮换 Token 应返回 403"""
        runner = await create_runner(
            db_session, "rotate-not-admin", status=RunnerStatus.APPROVED
        )

        response = await client.post(
            f"/api/runners/{runner.id}/rotate-token",
            headers=auth_headers(test_user.id),
        )

        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_rotate_token_runner_not_found(
        self, client: AsyncClient, test_admin: User
    ):
        """轮换不存在的 Runner Token 应返回 404"""
        response = await client.post(
            "/api/runners/99999/rotate-token",
            headers=auth_headers(test_admin.id),
        )

        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_rotate_token_runner_not_approved(
        self, client: AsyncClient, db_session: AsyncSession, test_admin: User
    ):
        """轮换未批准的 Runner Token 应返回 400"""
        runner = await create_runner(
            db_session, "rotate-pending", status=RunnerStatus.PENDING
        )

        response = await client.post(
            f"/api/runners/{runner.id}/rotate-token",
            headers=auth_headers(test_admin.id),
        )

        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_rotate_token_runner_rejected(
        self, client: AsyncClient, db_session: AsyncSession, test_admin: User
    ):
        """轮换已拒绝的 Runner Token 应返回 400"""
        runner = await create_runner(
            db_session, "rotate-rejected", status=RunnerStatus.REJECTED
        )

        response = await client.post(
            f"/api/runners/{runner.id}/rotate-token",
            headers=auth_headers(test_admin.id),
        )

        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_new_token_stored_encrypted_and_decryptable(
        self, client: AsyncClient, db_session: AsyncSession, test_admin: User
    ):
        """轮换后，数据库中存储的加密 api_key 可以解密得到 new_token"""
        runner = await create_runner(
            db_session, "rotate-decrypt", status=RunnerStatus.APPROVED
        )

        response = await client.post(
            f"/api/runners/{runner.id}/rotate-token",
            headers=auth_headers(test_admin.id),
        )

        assert response.status_code == 200
        new_token = response.json()["new_token"]

        # 刷新并验证数据库中的加密值可解密
        await db_session.refresh(runner)
        assert runner.api_key is not None
        decrypted = decrypt(runner.api_key)
        assert decrypted == new_token

    @pytest.mark.asyncio
    async def test_expired_model_state(self, db_session: AsyncSession):
        """设置 token_expires_at 为过去时间，验证模型反映过期状态"""
        # SQLite DateTime (naive) strips timezone on round-trip; use naive datetimes
        past_naive = _naive_utcnow() - timedelta(hours=1)
        runner = await create_runner(
            db_session,
            "rotate-expired-state",
            status=RunnerStatus.APPROVED,
            api_key=encrypt("some-token"),
            token_rotated_at=_naive_utcnow(),
            token_expires_at=past_naive,
        )

        await db_session.refresh(runner)
        assert runner.token_expires_at is not None
        # SQLite returns naive datetime, compare with naive
        assert runner.token_expires_at < _naive_utcnow()

    @pytest.mark.asyncio
    async def test_rotate_token_updates_fields(
        self, client: AsyncClient, db_session: AsyncSession, test_admin: User
    ):
        """轮换 Token 后，Runner 记录的 token_rotated_at 和 token_expires_at 正确更新"""
        runner = await create_runner(
            db_session, "rotate-update-fields", status=RunnerStatus.APPROVED
        )

        # 确认初始值为 None
        assert runner.token_rotated_at is None
        assert runner.token_expires_at is None

        response = await client.post(
            f"/api/runners/{runner.id}/rotate-token",
            headers=auth_headers(test_admin.id),
        )

        assert response.status_code == 200
        await db_session.refresh(runner)

        # 验证字段已更新（SQLite 返回 naive datetime）
        assert runner.token_rotated_at is not None
        assert runner.token_expires_at is not None

        now_naive = _naive_utcnow()
        # rotated_at 应该在最近几秒内
        assert (now_naive - runner.token_rotated_at).total_seconds() < 10
        # expires_at 应该在 24 小时后
        delta = (runner.token_expires_at - now_naive).total_seconds()
        assert 23.5 * 3600 <= delta <= 24.5 * 3600
