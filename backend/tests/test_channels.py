"""
渠道适配器测试
"""

import pytest

from app.modules.channels.base import (
    BaseChannel,
    ChannelConfig,
    ChannelMessage,
    ChannelStatus,
    ChannelType,
)
from app.modules.channels.manager import (
    ChannelManager,
    _channel_registry,
    get_channel_class,
    register_channel,
)


class TestChannelType:
    """测试渠道类型"""

    def test_channel_types(self):
        """测试渠道类型枚举"""
        assert ChannelType.FEISHU.value == "feishu"
        assert ChannelType.DINGTALK.value == "dingtalk"
        assert ChannelType.WECOM.value == "wecom"
        assert ChannelType.QQ.value == "qq"
        assert ChannelType.WECHAT.value == "wechat"
        assert ChannelType.WEB.value == "web"


class TestChannelStatus:
    """测试渠道状态"""

    def test_channel_statuses(self):
        """测试渠道状态枚举"""
        assert ChannelStatus.DISCONNECTED.value == "disconnected"
        assert ChannelStatus.CONNECTING.value == "connecting"
        assert ChannelStatus.CONNECTED.value == "connected"
        assert ChannelStatus.RECONNECTING.value == "reconnecting"
        assert ChannelStatus.ERROR.value == "error"


class TestChannelConfig:
    """测试渠道配置"""

    def test_config_creation(self):
        """测试配置创建"""
        config = ChannelConfig(
            channel_id="test-channel",
            channel_type=ChannelType.DINGTALK,
            name="Test Channel",
        )
        assert config.channel_id == "test-channel"
        assert config.channel_type == ChannelType.DINGTALK
        assert config.enabled is True
        assert config.auto_reconnect is True

    def test_config_validate(self):
        """测试配置验证"""
        config = ChannelConfig(
            channel_id="test",
            channel_type=ChannelType.FEISHU,
            name="Test",
        )
        is_valid, error = config.validate()
        assert is_valid is True

    def test_config_validate_empty_id(self):
        """测试空 channel_id 验证失败"""
        config = ChannelConfig(
            channel_id="",
            channel_type=ChannelType.FEISHU,
            name="Test",
        )
        is_valid, error = config.validate()
        assert is_valid is False


class TestChannelMessage:
    """测试渠道消息"""

    def test_message_creation(self):
        """测试消息创建"""
        msg = ChannelMessage(
            message_id="msg-1",
            channel_id="test",
            channel_type=ChannelType.DINGTALK,
            sender_id="user-1",
            content="Hello",
        )
        assert msg.message_id == "msg-1"
        assert msg.content == "Hello"
        assert msg.content_type == "text"
        assert msg.chat_type == "private"

    def test_message_to_dict(self):
        """测试消息转字典"""
        msg = ChannelMessage(
            message_id="msg-1",
            channel_id="test",
            channel_type=ChannelType.QQ,
            sender_id="user-1",
            content="Test",
        )
        d = msg.to_dict()
        assert d["message_id"] == "msg-1"
        assert d["channel_type"] == "qq"


class TestChannelManager:
    """测试渠道管理器"""

    def test_manager_creation(self):
        """测试管理器创建"""
        manager = ChannelManager()
        assert len(manager.channels) == 0

    def test_set_message_handler(self):
        """测试设置消息处理器"""
        manager = ChannelManager()

        async def handler(msg):
            pass

        manager.set_message_handler(handler)
        assert manager._message_handler is not None

    @pytest.mark.asyncio
    async def test_register_unknown_type(self):
        """测试注册未知类型渠道"""
        manager = ChannelManager()
        config = ChannelConfig(
            channel_id="unknown",
            channel_type=ChannelType.WEB,
            name="Unknown",
        )
        success, msg = await manager.register(config)
        assert success is False

    @pytest.mark.asyncio
    async def test_get_nonexistent_channel(self):
        """测试获取不存在的渠道"""
        manager = ChannelManager()
        channel = await manager.get("nonexistent")
        assert channel is None

    @pytest.mark.asyncio
    async def test_unregister_nonexistent(self):
        """测试注销不存在的渠道"""
        manager = ChannelManager()
        result = await manager.unregister("nonexistent")
        assert result is False

    @pytest.mark.asyncio
    async def test_start_nonexistent_channel(self):
        """测试启动不存在的渠道"""
        manager = ChannelManager()
        success, msg = await manager.start_channel("nonexistent")
        assert success is False

    @pytest.mark.asyncio
    async def test_stop_nonexistent_channel(self):
        """测试停止不存在的渠道"""
        manager = ChannelManager()
        success, msg = await manager.stop_channel("nonexistent")
        assert success is False

    @pytest.mark.asyncio
    async def test_send_to_nonexistent_channel(self):
        """测试向不存在的渠道发送消息"""
        manager = ChannelManager()
        success, msg = await manager.send_message("nonexistent", "chat-1", "Hello")
        assert success is False

    def test_get_status_empty(self):
        """测试空管理器状态"""
        manager = ChannelManager()
        status = manager.get_status()
        assert len(status) == 0

    def test_get_channel_ids_empty(self):
        """测试空管理器渠道 ID 列表"""
        manager = ChannelManager()
        ids = manager.get_channel_ids()
        assert len(ids) == 0

    def test_get_connected_channels_empty(self):
        """测试空管理器已连接渠道"""
        manager = ChannelManager()
        connected = manager.get_connected_channels()
        assert len(connected) == 0


class TestChannelRegistry:
    """测试渠道注册表"""

    def test_register_and_get(self):
        """测试注册和获取"""
        # 先保存原始注册表
        original = dict(_channel_registry)

        class MockChannel(BaseChannel):
            async def connect(self):
                return True

            async def disconnect(self):
                pass

            async def send_message(self, chat_id, content, **kwargs):
                return True, "ok"

            async def send_typing(self, chat_id):
                pass

        register_channel(ChannelType.WEB, MockChannel)
        cls = get_channel_class(ChannelType.WEB)
        assert cls is MockChannel

        # 恢复原始注册表
        _channel_registry.clear()
        _channel_registry.update(original)

    def test_get_unknown_type(self):
        """测试获取未注册的类型"""
        cls = get_channel_class("nonexistent_type")
        assert cls is None


class TestAdapterRegistration:
    """测试适配器注册（导入即注册）"""

    def test_feishu_registered(self):
        """测试飞书适配器已注册"""
        cls = get_channel_class(ChannelType.FEISHU)
        assert cls is not None

    def test_dingtalk_registered(self):
        """测试钉钉适配器已注册"""
        cls = get_channel_class(ChannelType.DINGTALK)
        assert cls is not None

    def test_qq_registered(self):
        """测试 QQ 适配器已注册"""
        cls = get_channel_class(ChannelType.QQ)
        assert cls is not None

    def test_wechat_registered(self):
        """测试微信适配器已注册"""
        cls = get_channel_class(ChannelType.WECHAT)
        assert cls is not None

    def test_wecom_registered(self):
        """测试企微适配器已注册"""
        cls = get_channel_class(ChannelType.WECOM)
        assert cls is not None
