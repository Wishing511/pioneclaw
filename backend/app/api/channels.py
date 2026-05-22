"""
渠道管理 API

提供渠道的 CRUD 和操作接口
"""

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.api.auth import get_current_active_user
from app.models import User
from app.modules.channels import (
    ChannelConfig,
    ChannelType,
    get_channel_manager,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/channels", tags=["渠道管理"])


# ==================== 请求模型 ====================


class ChannelCreateRequest(BaseModel):
    """创建渠道请求"""

    channel_type: str
    name: str
    app_id: str | None = None
    app_secret: str | None = None
    bot_token: str | None = None
    webhook_url: str | None = None
    enabled: bool = True
    auto_reconnect: bool = True
    extra: dict = {}


class ChannelUpdateRequest(BaseModel):
    """更新渠道请求"""

    name: str | None = None
    app_id: str | None = None
    app_secret: str | None = None
    bot_token: str | None = None
    webhook_url: str | None = None
    enabled: bool | None = None
    auto_reconnect: bool | None = None
    extra: dict | None = None


class SendMessageRequest(BaseModel):
    """发送消息请求"""

    chat_id: str
    content: str
    reply_to: str | None = None


# ==================== API 端点 ====================


@router.get("")
async def list_channels(
    current_user: User = Depends(get_current_active_user),
):
    """获取所有渠道状态"""
    manager = get_channel_manager()
    return {
        "channels": manager.get_status(),
        "total": len(manager.channels),
    }


@router.post("")
async def create_channel(
    request: ChannelCreateRequest,
    current_user: User = Depends(get_current_active_user),
):
    """创建渠道"""
    manager = get_channel_manager()

    # 解析渠道类型
    try:
        channel_type = ChannelType(request.channel_type)
    except ValueError:
        raise HTTPException(
            status_code=400, detail=f"Unknown channel type: {request.channel_type}"
        )

    # 生成渠道 ID
    import uuid

    channel_id = f"{channel_type.value}_{uuid.uuid4().hex[:8]}"

    # 创建配置
    config = ChannelConfig(
        channel_id=channel_id,
        channel_type=channel_type,
        name=request.name,
        app_id=request.app_id,
        app_secret=request.app_secret,
        bot_token=request.bot_token,
        webhook_url=request.webhook_url,
        enabled=request.enabled,
        auto_reconnect=request.auto_reconnect,
        extra=request.extra,
    )

    # 注册渠道
    success, message = await manager.register(config)
    if not success:
        raise HTTPException(status_code=400, detail=message)

    return {
        "success": True,
        "channel_id": channel_id,
        "message": message,
    }


@router.get("/{channel_id}")
async def get_channel(
    channel_id: str,
    current_user: User = Depends(get_current_active_user),
):
    """获取渠道详情"""
    manager = get_channel_manager()
    channel = await manager.get(channel_id)

    if not channel:
        raise HTTPException(status_code=404, detail="Channel not found")

    return channel.get_info()


@router.put("/{channel_id}")
async def update_channel(
    channel_id: str,
    request: ChannelUpdateRequest,
    current_user: User = Depends(get_current_active_user),
):
    """更新渠道配置"""
    manager = get_channel_manager()
    channel = await manager.get(channel_id)

    if not channel:
        raise HTTPException(status_code=404, detail="Channel not found")

    # 更新配置
    if request.name:
        channel.config.name = request.name
    if request.app_id:
        channel.config.app_id = request.app_id
    if request.app_secret:
        channel.config.app_secret = request.app_secret
    if request.bot_token:
        channel.config.bot_token = request.bot_token
    if request.enabled is not None:
        channel.config.enabled = request.enabled
    if request.auto_reconnect is not None:
        channel.config.auto_reconnect = request.auto_reconnect
    if request.extra:
        channel.config.extra.update(request.extra)

    return {"success": True, "message": "Channel updated"}


@router.delete("/{channel_id}")
async def delete_channel(
    channel_id: str,
    current_user: User = Depends(get_current_active_user),
):
    """删除渠道"""
    manager = get_channel_manager()
    success = await manager.unregister(channel_id)

    if not success:
        raise HTTPException(status_code=404, detail="Channel not found")

    return {"success": True, "message": "Channel deleted"}


@router.post("/{channel_id}/start")
async def start_channel(
    channel_id: str,
    current_user: User = Depends(get_current_active_user),
):
    """启动渠道"""
    manager = get_channel_manager()
    success, message = await manager.start_channel(channel_id)

    if not success:
        raise HTTPException(status_code=400, detail=message)

    return {"success": True, "message": message}


@router.post("/{channel_id}/stop")
async def stop_channel(
    channel_id: str,
    current_user: User = Depends(get_current_active_user),
):
    """停止渠道"""
    manager = get_channel_manager()
    success, message = await manager.stop_channel(channel_id)

    if not success:
        raise HTTPException(status_code=400, detail=message)

    return {"success": True, "message": message}


@router.post("/{channel_id}/send")
async def send_message(
    channel_id: str,
    request: SendMessageRequest,
    current_user: User = Depends(get_current_active_user),
):
    """通过渠道发送消息"""
    manager = get_channel_manager()
    success, result = await manager.send_message(
        channel_id=channel_id,
        chat_id=request.chat_id,
        content=request.content,
        reply_to=request.reply_to,
    )

    if not success:
        raise HTTPException(status_code=400, detail=result)

    return {"success": True, "message_id": result}


@router.post("/broadcast")
async def broadcast_message(
    content: str,
    channel_ids: list[str] | None = None,
    chat_id: str | None = None,
    current_user: User = Depends(get_current_active_user),
):
    """广播消息到多个渠道"""
    manager = get_channel_manager()
    results = await manager.broadcast(content, channel_ids, chat_id)

    return {
        "success": True,
        "results": results,
    }


@router.get("/types/available")
async def get_available_channel_types(
    current_user: User = Depends(get_current_active_user),
):
    """获取可用的渠道类型"""
    return {
        "types": [
            {"value": "feishu", "label": "飞书", "icon": "Feishu"},
            {"value": "dingtalk", "label": "钉钉", "icon": "DingTalk"},
            {"value": "wecom", "label": "企业微信", "icon": "WeCom"},
            {"value": "qq", "label": "QQ", "icon": "QQ"},
        ]
    }
