"""
企业微信渠道适配器

使用企业微信 API 实现：
- 接收应用消息回调
- 发送文本/Markdown/卡片消息
- 群聊会话

文档：https://developer.work.weixin.qq.com/document/
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


class WeComChannel(BaseChannel):
    """企业微信渠道适配器"""

    API_BASE = "https://qyapi.weixin.qq.com/cgi-bin"

    def __init__(self, config: ChannelConfig):
        super().__init__(config)
        self.corp_id = config.extra.get("corp_id", config.app_id)
        self.corp_secret = config.app_secret
        self.agent_id = config.extra.get("agent_id", "")
        self._access_token: str | None = None
        self._token_expire: float = 0
        self._client: httpx.AsyncClient | None = None

    async def _get_access_token(self) -> str | None:
        """获取 access_token"""
        if self._access_token and time.time() < self._token_expire - 60:
            return self._access_token

        try:
            response = await self._client.get(
                f"{self.API_BASE}/gettoken",
                params={
                    "corpid": self.corp_id,
                    "corpsecret": self.corp_secret,
                },
            )
            data = response.json()

            if data.get("errcode") == 0:
                self._access_token = data.get("access_token")
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
        """连接到企业微信"""
        if not self.corp_id or not self.corp_secret:
            logger.error(f"[{self.channel_id}] Corp ID or Secret not configured")
            self.status = ChannelStatus.ERROR
            return False

        self.status = ChannelStatus.CONNECTING

        try:
            self._client = httpx.AsyncClient(timeout=30.0)

            token = await self._get_access_token()
            if not token:
                self.status = ChannelStatus.ERROR
                return False

            logger.info(f"[{self.channel_id}] Connected to WeCom")
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
        """发送应用消息"""
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
                "agentid": self.agent_id,
            }

            if msg_type == "text":
                body["text"] = {"content": content}
            elif msg_type == "markdown":
                body["markdown"] = {"content": content}
            elif msg_type == "textcard":
                body["textcard"] = {
                    "title": kwargs.get("title", "通知"),
                    "description": content[:512],
                    "url": kwargs.get("url", ""),
                }
            else:
                body["text"] = {"content": content}

            response = await self._client.post(
                f"{self.API_BASE}/message/send",
                params={"access_token": token},
                json=body,
            )

            data = response.json()
            if data.get("errcode") == 0:
                return True, str(data.get("response_code", ""))
            else:
                return False, data.get("errmsg", "Unknown error")

        except Exception as e:
            logger.error(f"[{self.channel_id}] Send message failed: {e}")
            return False, str(e)

    async def send_typing(self, chat_id: str) -> None:
        """企业微信不支持输入状态"""
        pass

    async def handle_callback(self, xml_data: str) -> ChannelMessage | None:
        """处理回调消息"""
        try:
            root = ElementTree.fromstring(xml_data)

            msg_type = root.findtext("MsgType", "text")
            from_user = root.findtext("FromUserName", "")
            content = ""

            if msg_type == "text":
                content = root.findtext("Content", "")
            elif msg_type == "event":
                event_type = root.findtext("Event", "")
                event_key = root.findtext("EventKey", "")
                content = f"[事件: {event_type}] {event_key}"

            message = ChannelMessage(
                message_id=root.findtext("MsgId", ""),
                channel_id=self.channel_id,
                channel_type=ChannelType.WECOM,
                sender_id=from_user,
                content=content,
                content_type="text",
                chat_id=from_user,
                chat_type="private",
                raw={"xml": xml_data, "msg_type": msg_type},
            )

            await self._on_message(message)
            return message

        except Exception as e:
            logger.error(f"[{self.channel_id}] Parse callback failed: {e}")
            return None


# 注册适配器
from .manager import register_channel  # noqa: E402

register_channel(ChannelType.WECOM, WeComChannel)
