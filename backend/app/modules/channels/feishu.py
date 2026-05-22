"""
飞书渠道适配器

使用飞书开放平台 API 实现：
- WebSocket 长连接接收消息
- 发送文本/卡片消息
- 发送媒体文件

文档：https://open.feishu.cn/document/home/introduction-to-feishu-open-platform/
"""

import asyncio
import contextlib
import json
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


class FeishuChannel(BaseChannel):
    """飞书渠道适配器"""

    API_BASE = "https://open.feishu.cn/open-apis"

    def __init__(self, config: ChannelConfig):
        super().__init__(config)
        self.app_id = config.app_id
        self.app_secret = config.app_secret
        self._tenant_access_token: str | None = None
        self._token_expire: float = 0
        self._client: httpx.AsyncClient | None = None
        self._ws_task: asyncio.Task | None = None

    async def _get_tenant_access_token(self) -> str | None:
        """获取 tenant_access_token"""
        if self._tenant_access_token and time.time() < self._token_expire - 60:
            return self._tenant_access_token

        try:
            response = await self._client.post(
                f"{self.API_BASE}/auth/v3/tenant_access_token/internal",
                json={
                    "app_id": self.app_id,
                    "app_secret": self.app_secret,
                },
            )
            data = response.json()

            if data.get("code") == 0:
                self._tenant_access_token = data.get("tenant_access_token")
                self._token_expire = time.time() + data.get("expire", 7200)
                return self._tenant_access_token
            else:
                logger.error(f"[{self.channel_id}] Get token failed: {data.get('msg')}")
                return None

        except Exception as e:
            logger.error(f"[{self.channel_id}] Get token error: {e}")
            return None

    async def connect(self) -> bool:
        """连接到飞书"""
        if not self.app_id or not self.app_secret:
            logger.error(f"[{self.channel_id}] App ID or Secret not configured")
            self.status = ChannelStatus.ERROR
            return False

        self.status = ChannelStatus.CONNECTING

        try:
            self._client = httpx.AsyncClient(timeout=30.0)

            # 获取 access token
            token = await self._get_tenant_access_token()
            if not token:
                self.status = ChannelStatus.ERROR
                return False

            logger.info(f"[{self.channel_id}] Connected to Feishu")
            self.status = ChannelStatus.CONNECTED
            self.reset_reconnect_count()

            return True

        except Exception as e:
            logger.error(f"[{self.channel_id}] Connection failed: {e}")
            self.status = ChannelStatus.ERROR
            return False

    async def disconnect(self) -> None:
        """断开连接"""
        if self._ws_task:
            self._ws_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._ws_task
            self._ws_task = None

        if self._client:
            await self._client.aclose()
            self._client = None

        self._tenant_access_token = None
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

        token = await self._get_tenant_access_token()
        if not token:
            return False, "Failed to get access token"

        try:
            # 构建消息体
            message_body = {
                "receive_id": chat_id,
                "msg_type": kwargs.get("msg_type", "text"),
                "content": json.dumps({"text": content})
                if kwargs.get("msg_type", "text") == "text"
                else content,
            }

            response = await self._client.post(
                f"{self.API_BASE}/im/v1/messages",
                params={"receive_id_type": kwargs.get("receive_id_type", "chat_id")},
                headers={"Authorization": f"Bearer {token}"},
                json=message_body,
            )

            data = response.json()
            if data.get("code") == 0:
                message_id = data.get("data", {}).get("message_id", "")
                return True, message_id
            else:
                return False, data.get("msg", "Unknown error")

        except Exception as e:
            logger.error(f"[{self.channel_id}] Send message failed: {e}")
            return False, str(e)

    async def send_typing(self, chat_id: str) -> None:
        """飞书不支持输入状态"""
        pass

    async def _handle_webhook_event(self, event: dict) -> None:
        """处理 Webhook 事件（需要配合 API 端点使用）"""
        event_type = event.get("header", {}).get("event_type", "")

        # 处理消息事件
        if event_type == "im.message.receive_v1":
            message_data = event.get("event", {}).get("message", {})
            sender = event.get("event", {}).get("sender", {})

            message = ChannelMessage(
                message_id=message_data.get("message_id", ""),
                channel_id=self.channel_id,
                channel_type=ChannelType.FEISHU,
                sender_id=sender.get("sender_id", {}).get("union_id", ""),
                sender_name=sender.get("sender_id", {}).get("user_id", ""),
                content=json.loads(message_data.get("content", "{}")).get("text", ""),
                content_type=message_data.get("message_type", "text"),
                chat_id=message_data.get("chat_id", ""),
                chat_type="group"
                if "oc_" in message_data.get("chat_id", "")
                else "private",
                raw=event,
            )

            await self._on_message(message)


# 注册适配器
from .manager import register_channel  # noqa: E402

register_channel(ChannelType.FEISHU, FeishuChannel)
