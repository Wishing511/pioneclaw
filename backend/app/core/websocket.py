"""
WebSocket 连接管理器（企业级）

借鉴 CountBot 的设计，实现：
- 4 状态工具通知：start / progress / complete / error
- ToolNotificationHandler 类封装
- execute_tool_with_notifications 一站式包装
- BatchToolNotificationHandler 批量通知
- CancellationToken 会话取消令牌
"""

import asyncio
import logging
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from fastapi import WebSocket

logger = logging.getLogger(__name__)


# ==================== 取消令牌 ====================


@dataclass
class CancellationToken:
    """会话取消令牌"""

    is_cancelled: bool = False
    cancelled_at: datetime | None = None
    reason: str | None = None

    def cancel(self, reason: str | None = None) -> None:
        """触发取消"""
        if not self.is_cancelled:
            self.is_cancelled = True
            self.cancelled_at = datetime.now()
            self.reason = reason

    def reset(self) -> None:
        """重置令牌"""
        self.is_cancelled = False
        self.cancelled_at = None
        self.reason = None


# ==================== 连接管理 ====================


@dataclass
class WebSocketConnection:
    """WebSocket 连接"""

    websocket: WebSocket
    session_id: str
    user_id: int | None = None
    connected_at: datetime = field(default_factory=datetime.now)


class ConnectionManager:
    """
    WebSocket 连接管理器

    管理所有活跃的 WebSocket 连接，支持：
    - 按 session_id 和 user_id 分组
    - 会话级取消令牌
    - 连接计数（同会话多 tab）
    """

    def __init__(self):
        # session_id -> WebSocketConnection
        self._connections: dict[str, WebSocketConnection] = {}
        # user_id -> set of session_ids
        self._user_sessions: dict[int, set[str]] = {}
        # session_id -> CancellationToken
        self._cancel_tokens: dict[str, CancellationToken] = {}
        # 锁
        self._lock = asyncio.Lock()

    async def connect(
        self,
        websocket: WebSocket,
        session_id: str,
        user_id: int | None = None,
    ) -> bool:
        """接受新的 WebSocket 连接"""
        try:
            await websocket.accept()

            async with self._lock:
                conn = WebSocketConnection(
                    websocket=websocket,
                    session_id=session_id,
                    user_id=user_id,
                )
                self._connections[session_id] = conn

                if user_id:
                    if user_id not in self._user_sessions:
                        self._user_sessions[user_id] = set()
                    self._user_sessions[user_id].add(session_id)

            logger.info(f"WebSocket connected: session={session_id}, user={user_id}")
            return True

        except Exception as e:
            logger.error(f"WebSocket connect failed: {e}")
            return False

    async def disconnect(self, session_id: str) -> None:
        """断开 WebSocket 连接"""
        async with self._lock:
            conn = self._connections.pop(session_id, None)
            if conn and conn.user_id:
                user_sessions = self._user_sessions.get(conn.user_id)
                if user_sessions:
                    user_sessions.discard(session_id)
                    if not user_sessions:
                        del self._user_sessions[conn.user_id]

        logger.info(f"WebSocket disconnected: session={session_id}")

    async def send_to_session(self, session_id: str, message: dict) -> bool:
        """发送消息到指定会话"""
        conn = self._connections.get(session_id)
        if not conn:
            return False

        try:
            await conn.websocket.send_json(message)
            return True
        except Exception as e:
            logger.warning(f"Send to session {session_id} failed: {e}")
            await self.disconnect(session_id)
            return False

    async def send_to_user(self, user_id: int, message: dict) -> int:
        """发送消息到用户的所有会话"""
        session_ids = list(self._user_sessions.get(user_id, set()))
        success_count = 0
        for session_id in session_ids:
            if await self.send_to_session(session_id, message):
                success_count += 1
        return success_count

    async def broadcast(self, message: dict) -> int:
        """广播消息到所有连接"""
        success_count = 0
        for session_id in list(self._connections.keys()):
            if await self.send_to_session(session_id, message):
                success_count += 1
        return success_count

    def get_connection_count(self) -> int:
        return len(self._connections)

    def get_session_ids(self) -> list:
        return list(self._connections.keys())

    def get_session_ids_for_user(self, user_id: int) -> list:
        """获取属于指定用户的 session_id 列表"""
        return list(self._user_sessions.get(user_id, set()))

    def session_exists(self, session_id: str) -> bool:
        """检查 session 是否存在"""
        return session_id in self._connections

    def is_session_owned_by(self, session_id: str, user_id: int) -> bool:
        """检查 session 是否属于指定用户"""
        conn = self._connections.get(session_id)
        return conn is not None and conn.user_id == user_id

    # ===== 取消令牌管理 =====

    def get_cancel_token(self, session_id: str) -> CancellationToken:
        """获取或创建会话的取消令牌"""
        if session_id in self._cancel_tokens:
            token = self._cancel_tokens[session_id]
            # 如果已取消，重置
            if token.is_cancelled:
                token.reset()
            return token

        token = CancellationToken()
        self._cancel_tokens[session_id] = token
        return token

    def cancel_session(self, session_id: str, reason: str | None = None) -> bool:
        """取消指定会话的执行"""
        token = self._cancel_tokens.get(session_id)
        if token:
            token.cancel(reason)
            logger.info(f"Session {session_id} cancelled: {reason}")
            return True
        return False

    def cleanup_token(self, session_id: str) -> None:
        """清理会话令牌"""
        self._cancel_tokens.pop(session_id, None)


# 全局连接管理器
manager = ConnectionManager()


# ==================== 事件类型 ====================


class EventType:
    """WebSocket 事件类型"""

    # 工具调用 4 状态（CountBot 风格）
    TOOL_START = "tool_start"
    TOOL_PROGRESS = "tool_progress"
    TOOL_COMPLETE = "tool_complete"
    TOOL_ERROR = "tool_error"

    # 批量
    BATCH_TOOLS_COMPLETE = "batch_tools_complete"

    # Agent 执行
    AGENT_ITERATION = "agent_iteration"
    AGENT_COMPLETE = "agent_complete"
    AGENT_ERROR = "agent_error"

    # 流式响应
    STREAM_CHUNK = "stream_chunk"
    STREAM_END = "stream_end"

    # 任务
    TASK_STARTED = "task_started"
    TASK_PROGRESS = "task_progress"
    TASK_COMPLETED = "task_completed"
    TASK_FAILED = "task_failed"
    TASK_CANCELLED = "task_cancelled"

    # 系统
    CONNECTED = "connected"
    PING = "ping"
    PONG = "pong"
    ERROR = "error"


# ==================== 工具通知 Handler ====================


class ToolNotificationHandler:
    """
    工具通知处理器

    管理单个工具调用的全生命周期通知：
    - start: 开始执行
    - progress: 执行进度（0-100）
    - complete: 完成
    - error: 错误
    """

    def __init__(
        self, session_id: str, tool_name: str, tool_call_id: str | None = None
    ):
        """
        Args:
            session_id: WebSocket 会话 ID
            tool_name: 工具名称
            tool_call_id: 工具调用 ID（可选，多个工具调用时区分）
        """
        self.session_id = session_id
        self.tool_name = tool_name
        self.tool_call_id = tool_call_id or str(uuid.uuid4())
        self.start_time = time.time()
        self.progress = 0

    async def notify_start(self, arguments: dict[str, Any]) -> None:
        """通知工具开始执行"""
        logger.info(f"Tool start: {self.tool_name} (id={self.tool_call_id})")

        await manager.send_to_session(
            self.session_id,
            {
                "type": EventType.TOOL_START,
                "tool_call_id": self.tool_call_id,
                "tool": self.tool_name,
                "arguments": arguments,
                "timestamp": self.start_time,
            },
        )

    async def notify_progress(
        self,
        progress: int,
        message: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        """通知工具执行进度"""
        self.progress = max(0, min(100, progress))

        logger.debug(f"Tool progress: {self.tool_name} - {self.progress}%")

        await manager.send_to_session(
            self.session_id,
            {
                "type": EventType.TOOL_PROGRESS,
                "tool_call_id": self.tool_call_id,
                "tool": self.tool_name,
                "progress": self.progress,
                "message": message,
                "details": details,
                "timestamp": time.time(),
            },
        )

    async def notify_complete(self, result: str) -> None:
        """通知工具执行完成"""
        duration_ms = (time.time() - self.start_time) * 1000

        logger.info(f"Tool complete: {self.tool_name} ({duration_ms:.2f}ms)")

        # 限制结果长度，避免 WebSocket 消息过大
        result_truncated = result[:5000] if len(result) > 5000 else result

        await manager.send_to_session(
            self.session_id,
            {
                "type": EventType.TOOL_COMPLETE,
                "tool_call_id": self.tool_call_id,
                "tool": self.tool_name,
                "result": result_truncated,
                "duration_ms": duration_ms,
                "timestamp": time.time(),
            },
        )

    async def notify_error(self, error: str) -> None:
        """通知工具执行错误"""
        duration_ms = (time.time() - self.start_time) * 1000

        logger.error(f"Tool error: {self.tool_name} - {error} ({duration_ms:.2f}ms)")

        await manager.send_to_session(
            self.session_id,
            {
                "type": EventType.TOOL_ERROR,
                "tool_call_id": self.tool_call_id,
                "tool": self.tool_name,
                "error": error,
                "duration_ms": duration_ms,
                "timestamp": time.time(),
            },
        )

    def get_duration_ms(self) -> float:
        return (time.time() - self.start_time) * 1000


# ==================== 批量工具通知 ====================


class BatchToolNotificationHandler:
    """批量工具通知处理器（管理多个并发工具）"""

    def __init__(self, session_id: str):
        self.session_id = session_id
        self.handlers: dict[str, ToolNotificationHandler] = {}

    def create_handler(
        self,
        tool_name: str,
        tool_call_id: str | None = None,
    ) -> ToolNotificationHandler:
        """创建工具通知处理器"""
        handler = ToolNotificationHandler(self.session_id, tool_name, tool_call_id)
        self.handlers[handler.tool_call_id] = handler
        return handler

    def get_handler(self, tool_call_id: str) -> ToolNotificationHandler | None:
        return self.handlers.get(tool_call_id)

    def get_all_handlers(self) -> list[ToolNotificationHandler]:
        return list(self.handlers.values())

    async def notify_batch_complete(self) -> None:
        """通知批量工具执行完成"""
        await manager.send_to_session(
            self.session_id,
            {
                "type": EventType.BATCH_TOOLS_COMPLETE,
                "total": len(self.handlers),
                "timestamp": time.time(),
            },
        )


# ==================== 一站式工具执行 ====================


async def execute_tool_with_notifications(
    session_id: str,
    tool_name: str,
    arguments: dict[str, Any],
    executor: Callable,
    tool_call_id: str | None = None,
) -> str:
    """
    执行工具并自动发送通知（4 状态全覆盖）

    Args:
        session_id: 会话 ID
        tool_name: 工具名称
        arguments: 工具参数
        executor: 异步执行函数 async (tool_name, arguments) -> str
        tool_call_id: 工具调用 ID（可选）

    Returns:
        str: 执行结果

    Raises:
        Exception: 执行失败时抛出
    """
    handler = ToolNotificationHandler(session_id, tool_name, tool_call_id)

    try:
        await handler.notify_start(arguments)
        result = await executor(tool_name, arguments)
        await handler.notify_complete(result)
        return result
    except Exception as e:
        await handler.notify_error(str(e))
        raise


# ==================== 便捷函数（向后兼容） ====================


async def emit_tool_call_start(
    session_id: str,
    tool_name: str,
    arguments: dict,
    tool_call_id: str | None = None,
    agent_id: str | None = None,
) -> None:
    """发送工具调用开始事件（向后兼容旧接口）"""
    await manager.send_to_session(
        session_id,
        {
            "type": EventType.TOOL_START,
            "tool_call_id": tool_call_id or str(uuid.uuid4()),
            "tool": tool_name,
            "arguments": arguments,
            "agent_id": agent_id,
            "timestamp": time.time(),
        },
    )


async def emit_tool_call_result(
    session_id: str,
    tool_name: str,
    result: str,
    latency_ms: int,
    tool_call_id: str | None = None,
    agent_id: str | None = None,
) -> None:
    """发送工具调用结果事件（向后兼容旧接口）"""
    result_truncated = result[:5000] if len(result) > 5000 else result
    await manager.send_to_session(
        session_id,
        {
            "type": EventType.TOOL_COMPLETE,
            "tool_call_id": tool_call_id or str(uuid.uuid4()),
            "tool": tool_name,
            "result": result_truncated,
            "duration_ms": latency_ms,
            "agent_id": agent_id,
            "timestamp": time.time(),
        },
    )


async def emit_tool_call_error(
    session_id: str,
    tool_name: str,
    error: str,
    latency_ms: int,
    tool_call_id: str | None = None,
) -> None:
    """发送工具调用错误事件"""
    await manager.send_to_session(
        session_id,
        {
            "type": EventType.TOOL_ERROR,
            "tool_call_id": tool_call_id or str(uuid.uuid4()),
            "tool": tool_name,
            "error": error,
            "duration_ms": latency_ms,
            "timestamp": time.time(),
        },
    )


async def emit_tool_progress(
    session_id: str,
    tool_name: str,
    progress: int,
    message: str | None = None,
    details: dict[str, Any] | None = None,
    tool_call_id: str | None = None,
) -> None:
    """发送工具执行进度事件"""
    await manager.send_to_session(
        session_id,
        {
            "type": EventType.TOOL_PROGRESS,
            "tool_call_id": tool_call_id or str(uuid.uuid4()),
            "tool": tool_name,
            "progress": max(0, min(100, progress)),
            "message": message,
            "details": details,
            "timestamp": time.time(),
        },
    )


async def emit_agent_iteration(
    session_id: str,
    iteration: int,
    content: str,
    tool_calls: list,
) -> None:
    """发送 Agent 迭代事件"""
    await manager.send_to_session(
        session_id,
        {
            "type": EventType.AGENT_ITERATION,
            "iteration": iteration,
            "content": content[:500] if content else "",
            "tool_calls": tool_calls,
            "timestamp": time.time(),
        },
    )


async def emit_agent_complete(
    session_id: str,
    content: str,
    total_iterations: int,
    total_tool_calls: int,
    latency_ms: int,
) -> None:
    """发送 Agent 完成事件"""
    await manager.send_to_session(
        session_id,
        {
            "type": EventType.AGENT_COMPLETE,
            "content": content[:1000] if content else "",
            "total_iterations": total_iterations,
            "total_tool_calls": total_tool_calls,
            "latency_ms": latency_ms,
            "timestamp": time.time(),
        },
    )
