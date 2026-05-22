"""
渠道模块 - 多渠道消息适配

借鉴 CountBot 的 ChannelManager 设计，实现：
- BaseChannel 抽象基类
- ChannelManager 多实例管理
- 自动重连（指数退避）
- 消息路由

支持的渠道：
- 飞书
- 钉钉
- 企业微信
- QQ
"""

# 导入所有渠道适配器以触发注册
from . import dingtalk, feishu, qq, wechat, wecom  # noqa: F401
from .base import BaseChannel, ChannelConfig, ChannelMessage, ChannelType
from .manager import ChannelManager, get_channel_manager

__all__ = [
    "BaseChannel",
    "ChannelConfig",
    "ChannelMessage",
    "ChannelType",
    "ChannelManager",
    "get_channel_manager",
]
