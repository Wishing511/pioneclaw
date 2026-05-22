"""
WebSocket API - 实时通信接口

提供工具调用状态、Agent 执行进度等实时推送
"""

import logging
import uuid

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    WebSocket,
    WebSocketDisconnect,
)
from sqlalchemy import select

from app.api.auth import get_current_active_user
from app.core.database import async_session_maker
from app.core.security import decode_access_token
from app.core.websocket import EventType, manager
from app.models import User

logger = logging.getLogger(__name__)

router = APIRouter(tags=["WebSocket"])


async def _resolve_user_id_from_token(token: str) -> int | None:
    """验证 WS token 并返回 user_id。无效 token 返回 None。"""
    payload = decode_access_token(token)
    if payload is None:
        return None
    user_id_str = payload.get("sub")
    if user_id_str is None:
        return None
    try:
        user_id = int(user_id_str)
    except (ValueError, TypeError):
        return None
    # 确认用户存在且活跃
    try:
        async with async_session_maker() as db:
            result = await db.execute(
                select(User).where(User.id == user_id, User.is_active)
            )
            if result.scalar_one_or_none() is None:
                return None
    except Exception:
        logger.warning("Failed to query user for WS auth", exc_info=True)
        return None
    return user_id


@router.websocket("/ws")
async def websocket_endpoint(
    websocket: WebSocket,
    token: str | None = Query(None),
    session_id: str | None = Query(None),
):
    """
    WebSocket 连接端点

    支持的事件：
    - tool_start: 工具调用开始
    - tool_progress: 工具执行进度
    - tool_complete: 工具执行完成
    - tool_error: 工具执行错误
    - agent_iteration: Agent 迭代
    - agent_complete: Agent 完成

    示例:
        ws://localhost:8000/api/ws?token=xxx
        ws://localhost:8000/api/ws?session_id=xxx (复用已有会话)
    """
    # 使用客户端提供的 session_id 或生成新的
    sid = session_id or str(uuid.uuid4())

    # 验证 token 获取 user_id（无效 token 拒绝连接）
    user_id = None
    if token:
        user_id = await _resolve_user_id_from_token(token)
        if user_id is not None:
            logger.info(f"WS auth: user_id={user_id}, session={sid}")
        else:
            logger.warning(f"WS auth rejected: invalid token for session={sid}")
            await websocket.close(code=4001, reason="Invalid or expired token")
            return

    connected = await manager.connect(
        websocket=websocket,
        session_id=sid,
        user_id=user_id,
    )

    if not connected:
        return

    try:
        # 发送连接成功消息
        await manager.send_to_session(
            sid,
            {
                "type": EventType.CONNECTED,
                "session_id": sid,
                "message": "WebSocket connected successfully",
            },
        )

        # 持续接收消息
        while True:
            try:
                data = await websocket.receive_json()
                msg_type = data.get("type")

                # 心跳
                if msg_type == "ping":
                    await manager.send_to_session(
                        sid,
                        {
                            "type": EventType.PONG,
                            "timestamp": data.get("timestamp"),
                        },
                    )

                # 取消执行
                elif msg_type == "cancel":
                    reason = data.get("reason", "User cancelled")
                    success = manager.cancel_session(sid, reason)
                    await manager.send_to_session(
                        sid,
                        {
                            "type": "cancel_ack",
                            "success": success,
                            "reason": reason,
                        },
                    )

                # 订阅频道
                elif msg_type == "subscribe":
                    channel = data.get("channel")
                    await manager.send_to_session(
                        sid,
                        {
                            "type": "subscribed",
                            "channel": channel,
                        },
                    )

            except Exception as e:
                logger.debug(f"WebSocket receive error: {e}")
                break

    except WebSocketDisconnect:
        logger.info(f"WebSocket disconnected: {sid}")
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
    finally:
        await manager.disconnect(sid)


@router.get("/ws/status")
async def websocket_status(
    current_user: User = Depends(get_current_active_user),
):
    """获取 WebSocket 连接状态（非超管只返回自己的会话）"""
    if current_user.is_super_admin:
        session_ids = manager.get_session_ids()
    else:
        session_ids = manager.get_session_ids_for_user(current_user.id)
    return {
        "active_connections": len(session_ids),
        "session_ids": session_ids,
    }


@router.post("/ws/cancel/{session_id}")
async def cancel_session(
    session_id: str,
    reason: str | None = None,
    current_user: User = Depends(get_current_active_user),
):
    """通过 HTTP 取消指定会话的执行（只能取消自己的会话）"""
    # 先检查会话是否存在
    if not manager.session_exists(session_id):
        raise HTTPException(status_code=404, detail="Session not found")
    # 非超管只能取消自己的会话
    if not current_user.is_super_admin:
        if not manager.is_session_owned_by(session_id, current_user.id):
            raise HTTPException(status_code=403, detail="无权取消其他用户的会话")
    manager.cancel_session(session_id, reason or "HTTP cancel")
    return {"success": True, "session_id": session_id, "reason": reason}
