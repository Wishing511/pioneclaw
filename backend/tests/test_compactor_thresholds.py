"""
Test Compactor threshold strategy based on context_window

验证：
1. token_threshold = context_window - buffer_tokens
2. should_compact 在超过阈值时触发
3. 200K 模型默认阈值 167K（对标 Claude Code）
4. 小上下文模型也能正确计算
5. message_threshold 作为兜底保护
"""

from app.modules.agent.compactor import (
    CompactionConfig,
    Compactor,
)


class TestCompactionConfigThresholds:
    def test_default_token_threshold(self):
        """默认 200K 窗口，阈值 = 200000 - 33000 = 167000"""
        cfg = CompactionConfig()
        assert cfg.context_window == 200_000
        assert cfg.buffer_tokens == 33_000
        assert cfg.token_threshold == 167_000

    def test_custom_context_window(self):
        """自定义上下文窗口"""
        cfg = CompactionConfig(context_window=128_000)
        assert cfg.token_threshold == 128_000 - 33_000  # 95000

    def test_small_context_window(self):
        """小上下文模型（如 8K）"""
        cfg = CompactionConfig(context_window=8_000, buffer_tokens=2_000)
        assert cfg.token_threshold == 6_000

    def test_message_threshold_default(self):
        """消息数兜底阈值"""
        cfg = CompactionConfig()
        assert cfg.message_threshold == 200

    def test_context_window_smaller_than_buffer_auto_scaling(self):
        """context_window < buffer_tokens 时自动缩小 buffer，避免负阈值"""
        cfg = CompactionConfig(context_window=8_000, buffer_tokens=33_000)
        # buffer 自动缩到 context_window // 10 = 800
        # threshold = 8000 - 800 = 7200（正数）
        assert cfg.token_threshold == 7_200
        assert cfg.token_threshold > 0  # 永远不为负

    def test_very_small_context_window(self):
        """极小上下文（如 4K）阈值仍为正"""
        cfg = CompactionConfig(context_window=4_000, buffer_tokens=33_000)
        assert cfg.token_threshold > 0


class TestShouldCompact:
    def _messages(self, count, content_len=100):
        return [{"role": "user", "content": "X" * content_len} for _ in range(count)]

    def test_token_threshold_triggered(self):
        """Token 超过阈值应触发（用显式 token_count 隔离 token 路径）"""
        cfg = CompactionConfig(context_window=10_000, buffer_tokens=3_000)
        c = Compactor(config=cfg)
        # token_threshold = 7000
        # 传空消息 + 显式 token_count，避免命中 message 阈值
        assert c.should_compact([], token_count=8000) is True
        assert c.should_compact([], token_count=7000) is False

    def test_below_threshold_no_trigger(self):
        """低于阈值不应触发"""
        cfg = CompactionConfig(context_window=200_000, buffer_tokens=33_000)
        c = Compactor(config=cfg)
        # token_threshold = 167000
        # 10 条消息远低于阈值
        messages = self._messages(10, content_len=100)
        assert c.should_compact(messages) is False

    def test_message_count_fallback(self):
        """消息数超过兜底阈值应触发（即使 token 很低）"""
        cfg = CompactionConfig(
            context_window=200_000,  # token 阈值很高，不会触发
            message_threshold=10,
        )
        c = Compactor(config=cfg)
        messages = self._messages(15, content_len=1)
        assert c.should_compact(messages) is True

    def test_explicit_token_count(self):
        """传入显式 token_count 跳过估算"""
        cfg = CompactionConfig(context_window=10_000, buffer_tokens=3_000)
        c = Compactor(config=cfg)
        # token_threshold = 7000
        assert c.should_compact([], token_count=8000) is True
        assert c.should_compact([], token_count=6000) is False
