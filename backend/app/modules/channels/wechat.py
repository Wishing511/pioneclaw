"""
微信渠道适配器

使用微信公众号/客服消息 API 实现：
- Webhook 接收消息
- 发送文本/图文消息
- 模板消息

文档：https://developers.weixin.qq.com/doc/offiaccount/
"""

import logging
import time
from xml.etree import ElementTree

import httpx

from .base import (
    BaseChannel,
    ChannelConfig,
    ChannelMessage,
    ChannelStatus,
    ChannelType,
)

logger = logging.getLogger(__name__)


class WeChatChannel(BaseChannel):
    """微信渠道适配器"""

    API_BASE = "https://api.weixin.qq.com/cgi-bin"

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
            response = await self._client.get(
                f"{self.API_BASE}/token",
                params={
                    "grant_type": "client_credential",
                    "appid": self.app_id,
                    "secret": self.app_secret,
                },
            )
            data = response.json()

            if data.get("access_token"):
                self._access_token = data["access_token"]
                self._token_expire = time.time() + data.get("expires_in", 7200)
                return self._access_token
            else:
                logger.error(
                    f"[{self.channel_id}] Get token failed: {data.get('errmsg')}"
                )
                return None

        except Exception as e:
            logger.error(f"[{self.channel_id}] Get token error: {e}")
            return None

    async def connect(self) -> bool:
        """连接到微信"""
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

            logger.info(f"[{self.channel_id}] Connected to WeChat")
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
        """发送客服消息"""
        if not self._client or self.status != ChannelStatus.CONNECTED:
            return False, "Channel not connected"

        token = await self._get_access_token()
        if not token:
            return False, "Failed to get access token"

        try:
            msg_type = kwargs.get("msg_type", "text")

            body = {
                "touser": chat_id,
                "msgtype": msg_type,
            }

            if msg_type == "text":
                body["text"] = {"content": content}
            elif msg_type == "news":
                body["news"] = {
                    "articles": [
                        {
                            "title": kwargs.get("title", ""),
                            "description": content[:120],
                            "url": kwargs.get("url", ""),
                        }
                    ]
                }
            else:
                body["text"] = {"content": content}

            response = await self._client.post(
                f"{self.API_BASE}/message/custom/send",
                params={"access_token": token},
                json=body,
            )

            data = response.json()
            if data.get("errcode") == 0:
                return True, chat_id
            else:
                return False, data.get("errmsg", "Unknown error")

        except Exception as e:
            logger.error(f"[{self.channel_id}] Send message failed: {e}")
            return False, str(e)

    async def send_typing(self, chat_id: str) -> None:
        """微信不支持输入状态"""
        pass

    def parse_xml_message(self, xml_data: str) -> ChannelMessage | None:
        """解析微信 XML 消息格式"""
        try:
            root = ElementTree.fromstring(xml_data)

            msg_type = root.findtext("MsgType", "text")
            from_user = root.findtext("FromUserName", "")
            root.findtext("ToUserName", "")
            msg_id = root.findtext("MsgId", "")
            root.findtext("CreateTime", "0")

            content = ""
            content_type = "text"

            if msg_type == "text":
                content = root.findtext("Content", "")
            elif msg_type == "image":
                content = "[图片]"
                content_type = "image"
            elif msg_type == "voice":
                content = "[语音]"
                content_type = "audio"
            elif msg_type == "video":
                content = "[视频]"
                content_type = "video"
            elif msg_type == "event":
                event_type = root.findtext("Event", "")
                event_key = root.findtext("EventKey", "")
                content = f"[事件: {event_type}] {event_key}"

            return ChannelMessage(
                message_id=msg_id,
                channel_id=self.channel_id,
                channel_type=ChannelType.WECHAT,
                sender_id=from_user,
                content=content,
                content_type=content_type,
                chat_id=from_user,
                chat_type="private",
                raw={"xml": xml_data, "msg_type": msg_type},
            )

        except Exception as e:
            logger.error(f"[{self.channel_id}] Parse XML failed: {e}")
            return None

    async def handle_webhook(self, xml_data: str) -> str | None:
        """处理 Webhook 消息，返回自动回复（如有）"""
        message = self.parse_xml_message(xml_data)
        if message:
            await self._on_message(message)
        return None


# 注册适配器
from .manager import register_channel  # noqa: E402

register_channel(ChannelType.WECHAT, WeChatChannel)
