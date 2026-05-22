"""
渠道基类 - 所有渠道适配器的抽象基类

设计原则：
1. 统一接口：所有渠道实现相同的 connect/disconnect/send_message 方法
2. 配置分离：通过 ChannelConfig 注入配置
3. 消息统一：所有渠道消息转换为 ChannelMessage
4. 错误处理：统一的异常处理和重连机制
"""

import asyncio
import logging
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

logger = logging.getLogger(__name__)


class ChannelType(str, Enum):
    """渠道类型"""

    FEISHU = "feishu"
    DINGTALK = "dingtalk"
    WECOM = "wecom"
    QQ = "qq"
    WECHAT = "wechat"
    WEB = "web"  # Web 端（内部使用）


class ChannelStatus(str, Enum):
    """渠道状态"""

    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"
    ERROR = "error"


@dataclass
class ChannelConfig:
    """渠道配置"""

    channel_id: str  # 渠道实例 ID
    channel_type: ChannelType
    name: str  # 显示名称

    # 认证信息
    app_id: str | None = None
    app_secret: str | None = None
    bot_token: str | None = None
    webhook_url: str | None = None

    # 连接配置
    enabled: bool = True
    auto_reconnect: bool = True
    max_retries: int = 5
    reconnect_delay: float = 1.0  # 初始重连延迟（秒）
    reconnect_max_delay: float = 60.0  # 最大重连延迟

    # 扩展配置
    extra: dict = field(default_factory=dict)

    def validate(self) -> tuple[bool, str]:
        """验证配置是否有效"""
        if not self.channel_id:
            return False, "channel_id is required"
        if not self.channel_type:
            return False, "channel_type is required"
        return True, ""


@dataclass
class ChannelMessage:
    """
    统一消息格式

    所有渠道的消息都转换为此格式，方便内部处理
    """

    message_id: str  # 原始消息 ID
    channel_id: str  # 渠道 ID
    channel_type: ChannelType

    # 发送者信息
    sender_id: str
    sender_name: str = ""
    sender_avatar: str | None = None

    # 消息内容
    content: str = ""  # 文本内容
    content_type: str = "text"  # text/image/audio/video/file

    # 媒体文件
    media_url: str | None = None
    media_path: str | None = None

    # 会话信息
    chat_id: str = ""  # 会话 ID（群组/私聊）
    chat_type: str = "private"  # private/group/channel

    # 回复
    reply_to: str | None = None  # 回复的消息 ID

    # 元数据
    timestamp: datetime = field(default_factory=datetime.now)
    raw: dict = field(default_factory=dict)  # 原始消息数据

    def to_dict(self) -> dict:
        return {
            "message_id": self.message_id,
            "channel_id": self.channel_id,
            "channel_type": self.channel_type.value,
            "sender_id": self.sender_id,
            "sender_name": self.sender_name,
            "content": self.content,
            "content_type": self.content_type,
            "chat_id": self.chat_id,
            "chat_type": self.chat_type,
            "timestamp": self.timestamp.isoformat(),
        }


class BaseChannel(ABC):
    """
    渠道适配器基类

    所有渠道适配器必须继承此类并实现抽象方法
    """

    def __init__(self, config: ChannelConfig):
        self.config = config
        self.status = ChannelStatus.DISCONNECTED
        self._reconnect_count = 0
        self._message_handler: Callable[[ChannelMessage], None] | None = None
        self._running = False
        self._tasks: list[asyncio.Task] = []

    @property
    def channel_id(self) -> str:
        return self.config.channel_id

    @property
    def channel_type(self) -> ChannelType:
        return self.config.channel_type

    @abstractmethod
    async def connect(self) -> bool:
        """
        连接到渠道

        Returns:
            bool: 连接是否成功
        """
        pass

    @abstractmethod
    async def disconnect(self) -> None:
        """断开连接"""
        pass

    @abstractmethod
    async def send_message(
        self,
        chat_id: str,
        content: str,
        **kwargs,
    ) -> tuple[bool, str | None]:
        """
        发送消息

        Args:
            chat_id: 目标会话 ID
            content: 消息内容
            **kwargs: 扩展参数（如 reply_to, media_url 等）

        Returns:
            tuple[bool, Optional[str]]: (是否成功, 消息 ID 或错误信息)
        """
        pass

    @abstractmethod
    async def send_typing(self, chat_id: str) -> None:
        """发送正在输入状态"""
        pass

    def set_message_handler(self, handler: Callable[[ChannelMessage], None]) -> None:
        """设置消息处理器"""
        self._message_handler = handler

    async def _on_message(self, message: ChannelMessage) -> None:
        """内部消息处理"""
        if self._message_handler:
            try:
                await self._message_handler(message)
            except Exception as e:
                logger.error(f"Message handler error: {e}")

    async def reconnect_with_backoff(self) -> bool:
        """指数退避重连"""
        if not self.config.auto_reconnect:
            return False

        self.status = ChannelStatus.RECONNECTING

        while self._reconnect_count < self.config.max_retries:
            self._reconnect_count += 1

            # 计算延迟（指数退避）
            delay = min(
                self.config.reconnect_delay * (2 ** (self._reconnect_count - 1)),
                self.config.reconnect_max_delay,
            )

            logger.info(
                f"[{self.channel_id}] Reconnecting in {delay:.1f}s "
                f"(attempt {self._reconnect_count}/{self.config.max_retries})"
            )

            await asyncio.sleep(delay)

            try:
                if await self.connect():
                    self._reconnect_count = 0
                    self.status = ChannelStatus.CONNECTED
                    logger.info(f"[{self.channel_id}] Reconnected successfully")
                    return True
            except Exception as e:
                logger.error(f"[{self.channel_id}] Reconnect failed: {e}")

        self.status = ChannelStatus.ERROR
        logger.error(f"[{self.channel_id}] Max reconnection attempts reached")
        return False

    def reset_reconnect_count(self) -> None:
        """重置重连计数"""
        self._reconnect_count = 0

    async def start(self) -> bool:
        """启动渠道"""
        if self._running:
            return True

        self._running = True
        return await self.connect()

    async def stop(self) -> None:
        """停止渠道"""
        self._running = False
        await self.disconnect()

    def get_info(self) -> dict:
        """获取渠道信息"""
        return {
            "channel_id": self.channel_id,
            "channel_type": self.channel_type.value,
            "name": self.config.name,
            "status": self.status.value,
            "enabled": self.config.enabled,
            "reconnect_count": self._reconnect_count,
        }
