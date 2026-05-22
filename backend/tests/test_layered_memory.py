"""
分层记忆 API 单元测试

覆盖：存储、检索、获取、更新、删除、列表、统计、提升、清理
"""

import pytest
from httpx import AsyncClient

from app.models import User
from tests.conftest import auth_headers


class TestStoreMemory:
    @pytest.mark.asyncio
    async def test_store_success(self, client: AsyncClient, test_user: User):
        resp = await client.post(
            "/api/layered-memory/store",
            json={
                "content": "这是一个测试记忆内容，用于验证 L0/L1/L2 三级记忆体系的功能。",
                "name": "测试记忆",
                "context_type": "memory",
                "importance": 3,
                "source": "test",
            },
            headers=auth_headers(test_user.id),
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "测试记忆"
        assert data["layer"] == 2
        assert data["context_type"] == "memory"
        assert data["uri"].startswith("viking://user/")

    @pytest.mark.asyncio
    async def test_store_with_custom_uri(self, client: AsyncClient, test_user: User):
        resp = await client.post(
            "/api/layered-memory/store",
            json={
                "content": "自定义 URI 记忆",
                "name": "自定义URI",
                "uri": "viking://user/1/custom_uri_test",
            },
            headers=auth_headers(test_user.id),
        )
        assert resp.status_code == 201
        assert resp.json()["uri"] == "viking://user/1/custom_uri_test"

    @pytest.mark.asyncio
    async def test_store_no_auth(self, client: AsyncClient):
        resp = await client.post(
            "/api/layered-memory/store",
            json={
                "content": "无认证",
                "name": "测试",
            },
        )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_store_empty_content(self, client: AsyncClient, test_user: User):
        resp = await client.post(
            "/api/layered-memory/store",
            json={
                "content": "",
                "name": "空内容",
            },
            headers=auth_headers(test_user.id),
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_store_with_tags(self, client: AsyncClient, test_user: User):
        resp = await client.post(
            "/api/layered-memory/store",
            json={
                "content": "带标签的记忆",
                "name": "标签测试",
                "tags": ["test", "memory"],
            },
            headers=auth_headers(test_user.id),
        )
        assert resp.status_code == 201


class TestRecallMemory:
    @pytest.mark.asyncio
    async def test_recall_after_store(self, client: AsyncClient, test_user: User):
        # 先存储
        await client.post(
            "/api/layered-memory/store",
            json={
                "content": "Python 是一种解释型编程语言，支持面向对象和函数式编程。",
                "name": "Python知识",
                "context_type": "memory",
            },
            headers=auth_headers(test_user.id),
        )

        # 检索
        resp = await client.post(
            "/api/layered-memory/recall",
            json={
                "query": "编程语言",
                "top_k": 5,
            },
            headers=auth_headers(test_user.id),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "results" in data
        assert "total" in data

    @pytest.mark.asyncio
    async def test_recall_no_results(self, client: AsyncClient, test_user: User):
        resp = await client.post(
            "/api/layered-memory/recall",
            json={
                "query": "完全不存在的查询xyz",
                "top_k": 5,
            },
            headers=auth_headers(test_user.id),
        )
        assert resp.status_code == 200
        assert resp.json()["total"] == 0

    @pytest.mark.asyncio
    async def test_recall_no_auth(self, client: AsyncClient):
        resp = await client.post(
            "/api/layered-memory/recall",
            json={
                "query": "测试",
            },
        )
        assert resp.status_code == 401


class TestGetMemory:
    @pytest.mark.asyncio
    async def test_get_existing(self, client: AsyncClient, test_user: User):
        store_resp = await client.post(
            "/api/layered-memory/store",
            json={
                "content": "获取测试内容",
                "name": "获取测试",
            },
            headers=auth_headers(test_user.id),
        )
        uri = store_resp.json()["uri"]

        resp = await client.get(
            f"/api/layered-memory/{uri}", headers=auth_headers(test_user.id)
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["uri"] == uri
        assert data["name"] == "获取测试"

    @pytest.mark.asyncio
    async def test_get_with_layer(self, client: AsyncClient, test_user: User):
        store_resp = await client.post(
            "/api/layered-memory/store",
            json={
                "content": "层级获取测试内容",
                "name": "层级获取测试",
            },
            headers=auth_headers(test_user.id),
        )
        uri = store_resp.json()["uri"]

        # 获取 L0
        resp = await client.get(
            f"/api/layered-memory/{uri}/.level_0?layer=0",
            headers=auth_headers(test_user.id),
        )
        assert resp.status_code == 200
        assert resp.json()["layer"] == 0

    @pytest.mark.asyncio
    async def test_get_nonexistent(self, client: AsyncClient, test_user: User):
        resp = await client.get(
            "/api/layered-memory/viking://nonexistent",
            headers=auth_headers(test_user.id),
        )
        assert resp.status_code == 404


class TestUpdateMemory:
    @pytest.mark.asyncio
    async def test_update_name(self, client: AsyncClient, test_user: User):
        store_resp = await client.post(
            "/api/layered-memory/store",
            json={
                "content": "更新测试内容",
                "name": "原名",
            },
            headers=auth_headers(test_user.id),
        )
        uri = store_resp.json()["uri"]

        resp = await client.put(
            f"/api/layered-memory/{uri}",
            json={
                "name": "新名",
            },
            headers=auth_headers(test_user.id),
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "新名"

    @pytest.mark.asyncio
    async def test_update_content_with_tier_regeneration(
        self, client: AsyncClient, test_user: User
    ):
        store_resp = await client.post(
            "/api/layered-memory/store",
            json={
                "content": "原始内容",
                "name": "内容更新测试",
            },
            headers=auth_headers(test_user.id),
        )
        uri = store_resp.json()["uri"]

        resp = await client.put(
            f"/api/layered-memory/{uri}",
            json={
                "content": "这是更新后的内容，应该重新生成 L0 和 L1。",
                "regenerate_tiers": True,
            },
            headers=auth_headers(test_user.id),
        )
        assert resp.status_code == 200
        assert resp.json()["content"] == "这是更新后的内容，应该重新生成 L0 和 L1。"

    @pytest.mark.asyncio
    async def test_update_nonexistent(self, client: AsyncClient, test_user: User):
        resp = await client.put(
            "/api/layered-memory/viking://nonexistent",
            json={
                "name": "不存在",
            },
            headers=auth_headers(test_user.id),
        )
        assert resp.status_code == 404


class TestDeleteMemory:
    @pytest.mark.asyncio
    async def test_delete_existing(self, client: AsyncClient, test_user: User):
        store_resp = await client.post(
            "/api/layered-memory/store",
            json={
                "content": "待删除内容",
                "name": "待删除",
            },
            headers=auth_headers(test_user.id),
        )
        uri = store_resp.json()["uri"]

        resp = await client.delete(
            f"/api/layered-memory/{uri}", headers=auth_headers(test_user.id)
        )
        assert resp.status_code == 204

        # 确认已删除
        get_resp = await client.get(
            f"/api/layered-memory/{uri}", headers=auth_headers(test_user.id)
        )
        assert get_resp.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_nonexistent(self, client: AsyncClient, test_user: User):
        resp = await client.delete(
            "/api/layered-memory/viking://nonexistent",
            headers=auth_headers(test_user.id),
        )
        assert resp.status_code == 404


class TestListMemories:
    @pytest.mark.asyncio
    async def test_list_after_store(self, client: AsyncClient, test_user: User):
        await client.post(
            "/api/layered-memory/store",
            json={
                "content": "列表测试内容",
                "name": "列表测试",
            },
            headers=auth_headers(test_user.id),
        )

        resp = await client.get(
            "/api/layered-memory", headers=auth_headers(test_user.id)
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data
        assert "total" in data
        assert data["total"] >= 1

    @pytest.mark.asyncio
    async def test_list_with_layer_filter(self, client: AsyncClient, test_user: User):
        resp = await client.get(
            "/api/layered-memory?layer=2", headers=auth_headers(test_user.id)
        )
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_list_no_auth(self, client: AsyncClient):
        resp = await client.get("/api/layered-memory")
        assert resp.status_code == 401


class TestStats:
    @pytest.mark.asyncio
    async def test_stats(self, client: AsyncClient, test_user: User):
        await client.post(
            "/api/layered-memory/store",
            json={
                "content": "统计测试内容",
                "name": "统计测试",
            },
            headers=auth_headers(test_user.id),
        )

        resp = await client.get(
            "/api/layered-memory/stats/overview", headers=auth_headers(test_user.id)
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "total" in data
        assert "l0_count" in data
        assert "l1_count" in data
        assert "l2_count" in data
        assert data["l2_count"] >= 1
        assert data["l0_count"] >= 1
        assert data["l1_count"] >= 1


class TestPromote:
    @pytest.mark.asyncio
    async def test_promote_l1_to_l2(self, client: AsyncClient, test_user: User):
        store_resp = await client.post(
            "/api/layered-memory/store",
            json={
                "content": "提升测试内容",
                "name": "提升测试",
            },
            headers=auth_headers(test_user.id),
        )
        l2_uri = store_resp.json()["uri"]
        l1_uri = f"{l2_uri}/.level_1"

        resp = await client.post(
            "/api/layered-memory/promote",
            json={
                "uri": l1_uri,
            },
            headers=auth_headers(test_user.id),
        )
        # 提升可能返回 200（更新已有 L2）或 201
        assert resp.status_code in [200, 201]

    @pytest.mark.asyncio
    async def test_promote_nonexistent(self, client: AsyncClient, test_user: User):
        resp = await client.post(
            "/api/layered-memory/promote",
            json={
                "uri": "viking://nonexistent/.level_1",
            },
            headers=auth_headers(test_user.id),
        )
        assert resp.status_code == 404


class TestEvict:
    @pytest.mark.asyncio
    async def test_evict_session(self, client: AsyncClient, test_user: User):
        # 存储带 session_id 的记忆
        await client.post(
            "/api/layered-memory/store",
            json={
                "content": "会话记忆内容",
                "name": "会话记忆",
                "session_id": "test-session-123",
            },
            headers=auth_headers(test_user.id),
        )

        resp = await client.post(
            "/api/layered-memory/evict",
            json={
                "session_id": "test-session-123",
            },
            headers=auth_headers(test_user.id),
        )
        assert resp.status_code == 200
        assert "count" in resp.json()

    @pytest.mark.asyncio
    async def test_evict_empty_session(self, client: AsyncClient, test_user: User):
        resp = await client.post(
            "/api/layered-memory/evict",
            json={
                "session_id": "nonexistent-session",
            },
            headers=auth_headers(test_user.id),
        )
        assert resp.status_code == 200
        assert resp.json()["count"] == 0
