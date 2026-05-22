"""
渠道管理器 - 管理多个渠道实例

功能：
1. 多渠道注册和管理
2. 消息路由（根据 channel_id 分发）
3. 统一启动/停止
4. 状态监控
"""

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from .base import (
    BaseChannel,
    ChannelConfig,
    ChannelMessage,
    ChannelStatus,
    ChannelType,
)

logger = logging.getLogger(__name__)

# 渠道适配器注册表
_channel_registry: dict[ChannelType, type[BaseChannel]] = {}


def register_channel(
    channel_type: ChannelType, channel_class: type[BaseChannel]
) -> None:
    """注册渠道适配器"""
    _channel_registry[channel_type] = channel_class
    logger.info(f"Registered channel adapter: {channel_type.value}")


def get_channel_class(channel_type: ChannelType) -> type[BaseChannel] | None:
    """获取渠道适配器类"""
    return _channel_registry.get(channel_type)


class ChannelManager:
    """
    渠道管理器

    管理所有渠道实例，提供统一的接口
    """

    def __init__(self):
        self._channels: dict[str, BaseChannel] = {}
        self._message_handler: Callable[[ChannelMessage], Any] | None = None
        self._started = False
        self._lock = asyncio.Lock()

    @property
    def channels(self) -> dict[str, BaseChannel]:
        return self._channels

    def set_message_handler(self, handler: Callable[[ChannelMessage], Any]) -> None:
        """
        设置全局消息处理器

        所有渠道收到的消息都会调用此处理器
        """
        self._message_handler = handler
        # 更新所有已注册渠道的消息处理器
        for channel in self._channels.values():
            channel.set_message_handler(handler)

    async def register(self, config: ChannelConfig) -> tuple[bool, str]:
        """
        注册渠道

        Args:
            config: 渠道配置

        Returns:
            tuple[bool, str]: (是否成功, 消息或错误信息)
        """
        # 验证配置
        is_valid, error = config.validate()
        if not is_valid:
            return False, error

        # 检查是否已注册
        if config.channel_id in self._channels:
            return False, f"Channel {config.channel_id} already registered"

        # 获取渠道适配器类
        channel_class = get_channel_class(config.channel_type)
        if not channel_class:
            return False, f"Unknown channel type: {config.channel_type.value}"

        # 创建渠道实例
        try:
            channel = channel_class(config)

            # 设置消息处理器
            if self._message_handler:
                channel.set_message_handler(self._message_handler)

            self._channels[config.channel_id] = channel
            logger.info(
                f"Registered channel: {config.channel_id} ({config.channel_type.value})"
            )
            return True, f"Channel {config.channel_id} registered"

        except Exception as e:
            logger.error(f"Failed to create channel {config.channel_id}: {e}")
            return False, str(e)

    async def unregister(self, channel_id: str) -> bool:
        """注销渠道"""
        channel = self._channels.pop(channel_id, None)
        if channel:
            await channel.stop()
            logger.info(f"Unregistered channel: {channel_id}")
            return True
        return False

    async def get(self, channel_id: str) -> BaseChannel | None:
        """获取渠道实例"""
        return self._channels.get(channel_id)

    async def start_channel(self, channel_id: str) -> tuple[bool, str]:
        """启动指定渠道"""
        channel = self._channels.get(channel_id)
        if not channel:
            return False, f"Channel {channel_id} not found"

        if channel.status == ChannelStatus.CONNECTED:
            return True, f"Channel {channel_id} already connected"

        try:
            success = await channel.start()
            if success:
                return True, f"Channel {channel_id} started"
            else:
                return False, f"Channel {channel_id} failed to start"
        except Exception as e:
            logger.error(f"Failed to start channel {channel_id}: {e}")
            return False, str(e)

    async def stop_channel(self, channel_id: str) -> tuple[bool, str]:
        """停止指定渠道"""
        channel = self._channels.get(channel_id)
        if not channel:
            return False, f"Channel {channel_id} not found"

        try:
            await channel.stop()
            return True, f"Channel {channel_id} stopped"
        except Exception as e:
            logger.error(f"Failed to stop channel {channel_id}: {e}")
            return False, str(e)

    async def start_all(self) -> dict[str, tuple[bool, str]]:
        """启动所有渠道"""
        results = {}
        for channel_id in list(self._channels.keys()):
            results[channel_id] = await self.start_channel(channel_id)
        self._started = True
        return results

    async def stop_all(self) -> dict[str, tuple[bool, str]]:
        """停止所有渠道"""
        results = {}
        for channel_id in list(self._channels.keys()):
            results[channel_id] = await self.stop_channel(channel_id)
        self._started = False
        return results

    async def send_message(
        self,
        channel_id: str,
        chat_id: str,
        content: str,
        **kwargs,
    ) -> tuple[bool, str | None]:
        """
        通过指定渠道发送消息

        Args:
            channel_id: 渠道 ID
            chat_id: 目标会话 ID
            content: 消息内容
            **kwargs: 扩展参数

        Returns:
            tuple[bool, Optional[str]]: (是否成功, 消息 ID 或错误信息)
        """
        channel = self._channels.get(channel_id)
        if not channel:
            return False, f"Channel {channel_id} not found"

        if channel.status != ChannelStatus.CONNECTED:
            return False, f"Channel {channel_id} not connected"

        return await channel.send_message(chat_id, content, **kwargs)

    async def broadcast(
        self,
        content: str,
        channel_ids: list[str] | None = None,
        chat_id: str | None = None,
    ) -> dict[str, tuple[bool, str | None]]:
        """
        广播消息到多个渠道

        Args:
            content: 消息内容
            channel_ids: 目标渠道列表（None 表示所有）
            chat_id: 目标会话 ID（如果渠道支持）

        Returns:
            Dict[str, tuple]: 每个渠道的发送结果
        """
        results = {}
        target_channels = channel_ids or list(self._channels.keys())

        for channel_id in target_channels:
            channel = self._channels.get(channel_id)
            if not channel:
                results[channel_id] = (False, "Channel not found")
                continue

            if channel.status != ChannelStatus.CONNECTED:
                results[channel_id] = (False, "Channel not connected")
                continue

            # 如果指定了 chat_id，使用它；否则尝试渠道默认
            target_chat = chat_id or channel.config.extra.get("default_chat_id")
            if not target_chat:
                results[channel_id] = (False, "No target chat_id")
                continue

            results[channel_id] = await channel.send_message(target_chat, content)

        return results

    def get_status(self) -> dict[str, dict]:
        """获取所有渠道状态"""
        return {
            channel_id: channel.get_info()
            for channel_id, channel in self._channels.items()
        }

    def get_channel_ids(self) -> list[str]:
        """获取所有渠道 ID"""
        return list(self._channels.keys())

    def get_connected_channels(self) -> list[str]:
        """获取已连接的渠道 ID"""
        return [
            channel_id
            for channel_id, channel in self._channels.items()
            if channel.status == ChannelStatus.CONNECTED
        ]

    @property
    def is_started(self) -> bool:
        return self._started


# 全局单例
_channel_manager: ChannelManager | None = None


def get_channel_manager() -> ChannelManager:
    """获取全局渠道管理器"""
    global _channel_manager
    if _channel_manager is None:
        _channel_manager = ChannelManager()
    return _channel_manager
