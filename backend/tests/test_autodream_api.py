"""
AutoDream API 测试

测试 REST 端点：trigger、logs、config、status
使用测试数据库 + HTTP 客户端
"""

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.models import AIModelConfig
from app.models.autodream import AutoDreamConfig, AutoDreamLog

from .conftest import auth_headers


@pytest_asyncio.fixture
async def autodream_config(db_session):
    """创建默认 AutoDreamConfig"""
    config = AutoDreamConfig(
        enabled=True,
        cron_expression="0 2 * * *",
        batch_size=50,
    )
    db_session.add(config)
    await db_session.commit()
    await db_session.refresh(config)
    return config


@pytest_asyncio.fixture
async def ai_model_config(db_session):
    """创建默认 AI 模型配置（后台任务需要）"""
    model = AIModelConfig(
        name="test-model",
        display_name="Test Model",
        provider="openai",
        model_name="gpt-4o-mini",
        is_default=True,
        is_active=True,
    )
    db_session.add(model)
    await db_session.commit()
    await db_session.refresh(model)
    return model


# ═══════════════════════════════════════════════════════════════
# 配置管理
# ═══════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_get_config_creates_default(client, test_admin):
    """GET /config 不存在时自动创建默认配置"""
API_PREFIX = "/api"


@pytest.mark.asyncio
async def test_get_config_creates_default(client, test_admin):
    """GET /config 不存在时自动创建默认配置"""
    resp = await client.get(
        f"{API_PREFIX}/autodream/config",
        headers=auth_headers(test_admin.id),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["enabled"] is True
    assert data["batch_size"] == 50


@pytest.mark.asyncio
async def test_update_config(client, test_admin, autodream_config):
    """PUT /config 更新配置"""
    resp = await client.put(
        f"{API_PREFIX}/autodream/config",
        headers=auth_headers(test_admin.id),
        json={
            "enabled": False,
            "batch_size": 100,
            "cron_expression": "0 3 * * *",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["enabled"] is False
    assert data["batch_size"] == 100
    assert data["cron_expression"] == "0 3 * * *"


@pytest.mark.asyncio
async def test_update_config_partial(client, test_admin, autodream_config):
    """PUT /config 支持部分更新"""
    resp = await client.put(
        f"{API_PREFIX}/autodream/config",
        headers=auth_headers(test_admin.id),
        json={"batch_size": 20},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["batch_size"] == 20
    # 其他字段保持不变
    assert data["enabled"] is True


# ═══════════════════════════════════════════════════════════════
# 状态查询
# ═══════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_get_status_no_logs(client, test_admin, autodream_config):
    """GET /status 无日志时返回正确状态"""
    resp = await client.get(
        f"{API_PREFIX}/autodream/status",
        headers=auth_headers(test_admin.id),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["is_running"] is False
    assert data["last_run"] is None
    assert data["config"]["enabled"] is True


@pytest.mark.asyncio
async def test_get_status_with_success_log(client, test_admin, db_session):
    """GET /status 有成功日志时返回最近日志"""
    log = AutoDreamLog(
        triggered_by="manual",
        status="success",
        total_memories=10,
        duration_seconds=5.0,
    )
    db_session.add(log)
    await db_session.commit()

    resp = await client.get(
        f"{API_PREFIX}/autodream/status",
        headers=auth_headers(test_admin.id),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["is_running"] is False
    assert data["last_run"]["status"] == "success"
    assert data["last_run"]["total_memories"] == 10


# ═══════════════════════════════════════════════════════════════
# 手动触发
# ═══════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_trigger_accepted(client, test_admin, autodream_config, ai_model_config):
    """POST /trigger 首次触发返回 accepted"""
    resp = await client.post(
        f"{API_PREFIX}/autodream/trigger",
        headers=auth_headers(test_admin.id),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "accepted"
    assert "log_id" in data
    assert "已启动" in data["message"]


@pytest.mark.asyncio
async def test_trigger_skips_when_running(
    client, test_admin, db_session, autodream_config, ai_model_config
):
    """POST /trigger 当已有运行中任务时返回 skipped"""
    # 先创建一条 running 日志
    log = AutoDreamLog(
        triggered_by="manual",
        status="running",
    )
    db_session.add(log)
    await db_session.commit()
    await db_session.refresh(log)

    resp = await client.post(
        f"{API_PREFIX}/autodream/trigger",
        headers=auth_headers(test_admin.id),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "skipped"
    assert data["log_id"] == log.id


# ═══════════════════════════════════════════════════════════════
# 日志查询
# ═══════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_list_logs_pagination(client, test_admin, db_session):
    """GET /logs 分页查询"""
    for i in range(5):
        log = AutoDreamLog(
            triggered_by="manual",
            status="success",
            total_memories=i,
        )
        db_session.add(log)
    await db_session.commit()

    resp = await client.get(
        f"{API_PREFIX}/autodream/logs?limit=2&skip=1",
        headers=auth_headers(test_admin.id),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["items"]) == 2
    assert data["total"] == 5


@pytest.mark.asyncio
async def test_get_log_detail(client, test_admin, db_session):
    """GET /logs/{id} 查询单条日志"""
    log = AutoDreamLog(
        triggered_by="schedule",
        status="failed",
        error_message="测试错误",
        total_memories=3,
    )
    db_session.add(log)
    await db_session.commit()
    await db_session.refresh(log)

    resp = await client.get(
        f"{API_PREFIX}/autodream/logs/{log.id}",
        headers=auth_headers(test_admin.id),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "failed"
    assert data["error_message"] == "测试错误"
    assert data["total_memories"] == 3


@pytest.mark.asyncio
async def test_get_log_not_found(client, test_admin):
    """GET /logs/{id} 404"""
    resp = await client.get(
        f"{API_PREFIX}/autodream/logs/99999",
        headers=auth_headers(test_admin.id),
    )
    assert resp.status_code == 404


# ═══════════════════════════════════════════════════════════════
# 权限检查
# ═══════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_unauthorized_access(client):
    """未认证访问应返回 401"""
    resp = await client.get(f"{API_PREFIX}/autodream/config")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_normal_user_forbidden(client, test_user):
    """普通用户无 autodream:manage 权限应返回 403"""
    resp = await client.get(
        f"{API_PREFIX}/autodream/config",
        headers=auth_headers(test_user.id),
    )
    assert resp.status_code == 403
