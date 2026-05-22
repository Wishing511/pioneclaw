"""
QQ 渠道适配器

使用 QQ 机器人开放平台 API 实现：
- WebSocket 长连接接收消息
- 发送文本/富文本消息
- 频道/私聊消息

文档：https://bot.q.qq.com/wiki/
"""

import logging
import time

import httpx

from .base import (
    BaseChannel,
    ChannelConfig,
    ChannelMessage,
    ChannelStatus,
    ChannelType,
)

logger = logging.getLogger(__name__)


class QQChannel(BaseChannel):
    """QQ 渠道适配器"""

    API_BASE = "https://api.sgroup.qq.com"

    def __init__(self, config: ChannelConfig):
        super().__init__(config)
        self.app_id = config.app_id
        self.app_secret = config.app_secret
        self._access_token: str | None = None
        self._token_expire: float = 0
        self._client: httpx.AsyncClient | None = None

    async def _get_access_token(self) -> str | None:
        """获取 access_token"""
        if self._access_token and time.time() < self._token_expire - 60:
            return self._access_token

        try:
            response = await self._client.post(
                f"{self.API_BASE}/app/getAppAccessToken",
                json={
                    "appId": self.app_id,
                    "clientSecret": self.app_secret,
                },
            )
            data = response.json()

            if data.get("access_token"):
                self._access_token = data["access_token"]
                self._token_expire = time.time() + data.get("expires_in", 7200)
                return self._access_token
            else:
                logger.error(f"[{self.channel_id}] Get token failed: {data}")
                return None

        except Exception as e:
            logger.error(f"[{self.channel_id}] Get token error: {e}")
            return None

    async def connect(self) -> bool:
        """连接到 QQ"""
        if not self.app_id or not self.app_secret:
            logger.error(f"[{self.channel_id}] App ID or Secret not configured")
            self.status = ChannelStatus.ERROR
            return False

        self.status = ChannelStatus.CONNECTING

        try:
            self._client = httpx.AsyncClient(timeout=30.0)

            token = await self._get_access_token()
            if not token:
                self.status = ChannelStatus.ERROR
                return False

            logger.info(f"[{self.channel_id}] Connected to QQ Bot")
            self.status = ChannelStatus.CONNECTED
            self.reset_reconnect_count()

            return True

        except Exception as e:
            logger.error(f"[{self.channel_id}] Connection failed: {e}")
            self.status = ChannelStatus.ERROR
            return False

    async def disconnect(self) -> None:
        """断开连接"""
        if self._client:
            await self._client.aclose()
            self._client = None

        self._access_token = None
        self.status = ChannelStatus.DISCONNECTED
        logger.info(f"[{self.channel_id}] Disconnected")

    async def send_message(
        self,
        chat_id: str,
        content: str,
        **kwargs,
    ) -> tuple[bool, str | None]:
        """发送消息"""
        if not self._client or self.status != ChannelStatus.CONNECTED:
            return False, "Channel not connected"

        token = await self._get_access_token()
        if not token:
            return False, "Failed to get access token"

        try:
            msg_type = kwargs.get("msg_type", "text")
            event_id = kwargs.get("event_id", "")

            if msg_type == "text":
                body = {
                    "content": content,
                    "msg_type": 0,
                    "event_id": event_id,
                }
            elif msg_type == "markdown":
                body = {
                    "markdown": {"content": content},
                    "msg_type": 2,
                    "event_id": event_id,
                }
            else:
                body = {
                    "content": content,
                    "msg_type": 0,
                    "event_id": event_id,
                }

            # 判断是频道消息还是私聊消息
            if chat_id.startswith("PRIVATE_"):
                # 私聊
                response = await self._client.post(
                    f"{self.API_BASE}/v2/users/{chat_id.replace('PRIVATE_', '')}/messages",
                    headers={"Authorization": f"QQBot {token}"},
                    json=body,
                )
            else:
                # 频道
                response = await self._client.post(
                    f"{self.API_BASE}/v2/channels/{chat_id}/messages",
                    headers={"Authorization": f"QQBot {token}"},
                    json=body,
                )

            data = response.json()
            message_id = data.get("id", "")
            if message_id:
                return True, message_id
            else:
                return False, str(data)

        except Exception as e:
            logger.error(f"[{self.channel_id}] Send message failed: {e}")
            return False, str(e)

    async def send_typing(self, chat_id: str) -> None:
        """QQ 不支持输入状态"""
        pass

    async def handle_callback(self, event: dict) -> None:
        """处理回调事件"""
        event_type = event.get("EventType", event.get("t", ""))

        if event_type in ("MESSAGE_CREATE", "C2C_MESSAGE_CREATE", "AT_MESSAGE_CREATE"):
            author = event.get("Author", event.get("author", {}))
            content = event.get("Content", event.get("content", ""))
            channel_id = event.get("ChannelId", event.get("channel_id", ""))

            chat_type = "private"
            if event_type in ("MESSAGE_CREATE", "AT_MESSAGE_CREATE"):
                chat_type = "group"

            message = ChannelMessage(
                message_id=event.get("Id", event.get("id", "")),
                channel_id=self.channel_id,
                channel_type=ChannelType.QQ,
                sender_id=str(author.get("UserOpenId", author.get("user_openid", ""))),
                sender_name=author.get("Nick", author.get("nick", "")),
                content=content,
                content_type="text",
                chat_id=channel_id,
                chat_type=chat_type,
                raw=event,
            )

            await self._on_message(message)


# 注册适配器
from .manager import register_channel  # noqa: E402

register_channel(ChannelType.QQ, QQChannel)
