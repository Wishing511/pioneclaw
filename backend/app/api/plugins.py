"""
插件系统 API
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.api.auth import get_current_active_user
from app.models import User
from app.modules.plugins import EventBus, PluginManager, PluginState

# 全局单例（单 worker 模式安全；多 worker 需 Redis/DB 协调状态）
_event_bus = EventBus()
_plugin_manager: PluginManager | None = None


def get_plugin_manager() -> PluginManager:
    """获取全局插件管理器"""
    global _plugin_manager
    if _plugin_manager is None:
        import os

        # 默认插件目录: 项目根目录下的 plugins/
        # __file__ -> backend/app/api/plugins.py -> 向上4级到项目根目录
        default_plugin_dir = os.path.join(
            os.path.dirname(
                os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
            ),
            "plugins",
        )
        _plugin_manager = PluginManager(
            event_bus=_event_bus,
            plugin_dir=default_plugin_dir,
        )
    return _plugin_manager


def get_event_bus() -> EventBus:
    """获取全局事件总线"""
    return _event_bus


router = APIRouter(prefix="/plugins", tags=["plugins"])


# ---- Schema ----


class PluginLoadRequest(BaseModel):
    plugin_id: str
    config: dict[str, Any] | None = None


class PluginResponse(BaseModel):
    plugin_id: str
    name: str
    version: str
    description: str
    state: str
    error: str | None = None
    dependencies: list[str] = []
    subscriptions: list[str] = []


class PluginStatsResponse(BaseModel):
    total: int
    by_state: dict[str, int]
    plugin_dir: str | None = None


class EventPublishRequest(BaseModel):
    topic: str
    data: dict[str, Any] = {}


class SubscriptionResponse(BaseModel):
    sub_id: str
    topic: str
    handler: str
    priority: int
    wildcard: bool


# ---- 端点 ----


@router.get("/discover", response_model=list[str])
async def discover_plugins(
    current_user: User = Depends(get_current_active_user),
):
    """发现可用插件"""
    manager = get_plugin_manager()
    return manager.discover_plugins()


@router.post("/load", response_model=PluginResponse)
async def load_plugin(
    req: PluginLoadRequest,
    current_user: User = Depends(get_current_active_user),
):
    """加载插件"""
    manager = get_plugin_manager()
    info = await manager.load_plugin_async(req.plugin_id, config=req.config)
    if info.state == PluginState.ERROR:
        raise HTTPException(status_code=400, detail=info.error)
    return PluginResponse(
        plugin_id=info.plugin_id,
        name=info.name,
        version=info.version,
        description=info.description,
        state=info.state.value,
        error=info.error,
        dependencies=info.dependencies,
        subscriptions=info.subscriptions,
    )


@router.post("/unload/{plugin_id}")
async def unload_plugin(
    plugin_id: str,
    current_user: User = Depends(get_current_active_user),
):
    """卸载插件"""
    manager = get_plugin_manager()
    success = manager.unload_plugin(plugin_id)
    if not success:
        raise HTTPException(
            status_code=400, detail=f"Failed to unload plugin '{plugin_id}'"
        )
    return {"success": True, "message": f"Plugin '{plugin_id}' unloaded"}


@router.post("/reload/{plugin_id}", response_model=PluginResponse)
async def reload_plugin(
    plugin_id: str,
    current_user: User = Depends(get_current_active_user),
):
    """热重载插件"""
    manager = get_plugin_manager()
    info = await manager.reload_plugin_async(plugin_id)
    if info.state == PluginState.ERROR:
        raise HTTPException(status_code=400, detail=info.error)
    return PluginResponse(
        plugin_id=info.plugin_id,
        name=info.name,
        version=info.version,
        description=info.description,
        state=info.state.value,
        error=info.error,
        dependencies=info.dependencies,
        subscriptions=info.subscriptions,
    )


@router.get("/list", response_model=list[PluginResponse])
async def list_plugins(
    state: str | None = None,
    current_user: User = Depends(get_current_active_user),
):
    """列出已加载插件"""
    manager = get_plugin_manager()
    filter_state = PluginState(state) if state else None
    plugins = manager.list_plugins(state=filter_state)
    return [
        PluginResponse(
            plugin_id=p.plugin_id,
            name=p.name,
            version=p.version,
            description=p.description,
            state=p.state.value,
            error=p.error,
            dependencies=p.dependencies,
            subscriptions=p.subscriptions,
        )
        for p in plugins
    ]


@router.get("/stats", response_model=PluginStatsResponse)
async def plugin_stats(
    current_user: User = Depends(get_current_active_user),
):
    """插件统计"""
    manager = get_plugin_manager()
    return manager.get_stats()


@router.get("/{plugin_id}", response_model=PluginResponse)
async def get_plugin(
    plugin_id: str,
    current_user: User = Depends(get_current_active_user),
):
    """获取插件详情"""
    manager = get_plugin_manager()
    info = manager.get_plugin(plugin_id)
    if not info:
        raise HTTPException(status_code=404, detail=f"Plugin '{plugin_id}' not found")
    return PluginResponse(
        plugin_id=info.plugin_id,
        name=info.name,
        version=info.version,
        description=info.description,
        state=info.state.value,
        error=info.error,
        dependencies=info.dependencies,
        subscriptions=info.subscriptions,
    )


# ---- 生命周期端点 (Stage PP) ----


class LifecycleResponse(BaseModel):
    plugin_id: str
    name: str
    state: str
    health_status: bool | None = None
    last_health_check: str | None = None
    retry_count: int | None = None
    max_retries: int | None = None
    error_history: list[dict[str, Any]] = []
    paused_at: str | None = None
    stopped_at: str | None = None
    last_transition: dict[str, Any] | None = None


@router.post("/{plugin_id}/pause")
async def pause_plugin(
    plugin_id: str,
    current_user: User = Depends(get_current_active_user),
):
    """暂停插件"""
    manager = get_plugin_manager()
    success = manager.pause_plugin(plugin_id)
    if not success:
        raise HTTPException(
            status_code=400, detail=f"Cannot pause plugin '{plugin_id}'"
        )
    return {"success": True, "message": f"Plugin '{plugin_id}' paused"}


@router.post("/{plugin_id}/resume")
async def resume_plugin(
    plugin_id: str,
    current_user: User = Depends(get_current_active_user),
):
    """恢复插件"""
    manager = get_plugin_manager()
    success = manager.resume_plugin(plugin_id)
    if not success:
        raise HTTPException(
            status_code=400, detail=f"Cannot resume plugin '{plugin_id}'"
        )
    return {"success": True, "message": f"Plugin '{plugin_id}' resumed"}


@router.post("/{plugin_id}/stop")
async def stop_plugin(
    plugin_id: str,
    current_user: User = Depends(get_current_active_user),
):
    """停止插件"""
    manager = get_plugin_manager()
    success = manager.stop_plugin(plugin_id)
    if not success:
        raise HTTPException(status_code=400, detail=f"Cannot stop plugin '{plugin_id}'")
    return {"success": True, "message": f"Plugin '{plugin_id}' stopped"}


@router.post("/{plugin_id}/restart")
async def restart_plugin(
    plugin_id: str,
    current_user: User = Depends(get_current_active_user),
):
    """重启插件"""
    manager = get_plugin_manager()
    info = manager.restart_plugin(plugin_id)
    if info is None:
        raise HTTPException(
            status_code=400, detail=f"Cannot restart plugin '{plugin_id}'"
        )
    return {
        "success": True,
        "message": f"Plugin '{plugin_id}' restarted",
        "state": info.state.value,
    }


@router.post("/{plugin_id}/enable")
async def enable_plugin(
    plugin_id: str,
    current_user: User = Depends(get_current_active_user),
):
    """启用插件（从 DISABLED 恢复为 UNLOADED）"""
    manager = get_plugin_manager()
    success = manager.enable_plugin(plugin_id)
    if not success:
        raise HTTPException(
            status_code=400, detail=f"Cannot enable plugin '{plugin_id}'"
        )
    return {"success": True, "message": f"Plugin '{plugin_id}' enabled"}


@router.post("/{plugin_id}/disable")
async def disable_plugin(
    plugin_id: str,
    current_user: User = Depends(get_current_active_user),
):
    """禁用插件"""
    manager = get_plugin_manager()
    success = manager.disable_plugin(plugin_id)
    if not success:
        raise HTTPException(
            status_code=400, detail=f"Cannot disable plugin '{plugin_id}'"
        )
    return {"success": True, "message": f"Plugin '{plugin_id}' disabled"}


@router.get("/{plugin_id}/health", response_model=LifecycleResponse)
async def get_plugin_health(
    plugin_id: str,
    current_user: User = Depends(get_current_active_user),
):
    """获取单个插件健康状态"""
    manager = get_plugin_manager()
    result = manager.health_check(plugin_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Plugin '{plugin_id}' not found")
    return result


@router.get("/health", response_model=list[LifecycleResponse])
async def get_all_plugins_health(
    current_user: User = Depends(get_current_active_user),
):
    """批量获取所有插件健康状态"""
    manager = get_plugin_manager()
    return await manager.health_check_all()


# ---- 事件总线端点 ----


@router.post("/events/publish")
async def publish_event(
    req: EventPublishRequest,
    current_user: User = Depends(get_current_active_user),
):
    """发布事件"""
    bus = get_event_bus()
    fired = await bus.publish(req.topic, req.data)
    return {"success": True, "handlers_fired": fired}


@router.get("/events/subscriptions", response_model=list[SubscriptionResponse])
async def list_subscriptions(
    topic: str | None = None,
    current_user: User = Depends(get_current_active_user),
):
    """列出事件订阅"""
    bus = get_event_bus()
    return bus.get_subscriptions(topic=topic)
