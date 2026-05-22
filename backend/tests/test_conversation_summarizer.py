"""
ConversationSummarizer 测试 — LLM 对话摘要写入 MEMORY.md (Track 1)
"""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.modules.agent.conversation_summarizer import (
    ConversationSummarizer,
    SummarizerConfig,
)
from app.modules.agent.memory import MemoryStore


@pytest.fixture
def memory_store():
    with tempfile.TemporaryDirectory() as tmpdir:
        store = MemoryStore(Path(tmpdir))
        yield store


@pytest.fixture
def mock_llm():
    """创建带 chat_stream 的 mock LLM provider"""
    llm = MagicMock()

    def _set_stream(chunks):
        """设置 chat_stream 返回的 chunk 列表"""

        async def _gen(*args, **kwargs):
            for c in chunks:
                yield c

        llm.chat_stream = _gen

    llm._set_stream = _set_stream
    return llm


@pytest.fixture
def summarizer(memory_store, mock_llm):
    return ConversationSummarizer(
        llm_provider=mock_llm,
        memory_store=memory_store,
        model="test-model",
        user_id=1,
        session_id="test-session",
        config=SummarizerConfig(),
    )


# ============================================================
# SummarizerConfig 测试
# ============================================================


class TestSummarizerConfig:
    def test_defaults(self):
        cfg = SummarizerConfig()
        assert cfg.token_threshold == 4000
        assert cfg.tool_call_threshold == 5
        assert cfg.cooldown_seconds == 300.0
        assert cfg.max_memory_chars == 4000
        assert cfg.source_tag == "agent-summary"

    def test_custom(self):
        cfg = SummarizerConfig(
            token_threshold=2000,
            cooldown_seconds=60.0,
        )
        assert cfg.token_threshold == 2000
        assert cfg.cooldown_seconds == 60.0


# ============================================================
# should_summarize 测试
# ============================================================


class TestShouldSummarize:
    def test_token_threshold_triggered(self, summarizer):
        """达到 token 阈值应触发"""
        assert summarizer.should_summarize(token_count=4000, tool_call_count=0) is True
        assert summarizer.should_summarize(token_count=5000, tool_call_count=0) is True

    def test_tool_call_threshold_triggered(self, summarizer):
        """达到工具调用阈值应触发"""
        assert summarizer.should_summarize(token_count=0, tool_call_count=5) is True
        assert summarizer.should_summarize(token_count=0, tool_call_count=10) is True

    def test_below_threshold_not_triggered(self, summarizer):
        """低于阈值不触发"""
        assert summarizer.should_summarize(token_count=1000, tool_call_count=2) is False

    def test_cooldown_blocks(self, summarizer):
        """冷却期内不触发"""
        summarizer._last_summarized_at = __import__("time").time()  # 刚摘要过
        assert (
            summarizer.should_summarize(token_count=10000, tool_call_count=10) is False
        )

    def test_custom_config_thresholds(self, memory_store, mock_llm):
        """自定义配置的阈值"""
        cfg = SummarizerConfig(token_threshold=100, tool_call_threshold=2)
        s = ConversationSummarizer(
            llm_provider=mock_llm,
            memory_store=memory_store,
            config=cfg,
        )
        assert s.should_summarize(token_count=100, tool_call_count=0) is True
        assert s.should_summarize(token_count=50, tool_call_count=1) is False


# ============================================================
# summarize_conversation 测试
# ============================================================


class TestSummarizeConversation:
    @pytest.fixture
    def sample_messages(self):
        return [
            {"role": "user", "content": "帮我写一个 Python 脚本处理 CSV"},
            {"role": "assistant", "content": "好的，这是一个 CSV 处理脚本..."},
            {"role": "user", "content": "再加一个数据可视化的功能"},
            {"role": "assistant", "content": "我添加了 matplotlib 图表..."},
        ]

    @pytest.mark.asyncio
    async def test_summarize_success(self, summarizer, mock_llm, sample_messages):
        """LLM 返回摘要后写入 MEMORY.md"""
        mock_llm._set_stream([{"content": "用户请求了 CSV 处理和可视化功能"}])
        result = await summarizer.summarize_conversation(sample_messages)
        assert result is not None
        assert "CSV" in result
        # 检查写入 MEMORY.md
        assert summarizer.memory_store.get_paragraph_count() == 1
        entry = summarizer.memory_store.get_entry(1)
        assert entry.source == "agent-summary"

    @pytest.mark.asyncio
    async def test_summarize_no_messages(self, summarizer):
        """空消息列表返回 None"""
        result = await summarizer.summarize_conversation([])
        assert result is None

    @pytest.mark.asyncio
    async def test_summarize_no_store(self, mock_llm):
        """无 memory_store 返回 None"""
        s = ConversationSummarizer(llm_provider=mock_llm, memory_store=None)
        result = await s.summarize_conversation([{"role": "user", "content": "hello"}])
        assert result is None

    @pytest.mark.asyncio
    async def test_summarize_nothing_to_record(self, summarizer, mock_llm):
        """LLM 判断无需记录时返回 None"""
        mock_llm._set_stream([{"content": "无需记录"}])
        result = await summarizer.summarize_conversation(
            [{"role": "user", "content": "hello"}]
        )
        assert result is None
        assert summarizer.memory_store.get_paragraph_count() == 0

    @pytest.mark.asyncio
    async def test_summarize_llm_unavailable_fallback(self, summarizer):
        """LLM 不可用时降级截断写入"""
        summarizer.llm_provider = None  # No LLM
        messages = [{"role": "user", "content": "hello world" * 100}]
        result = await summarizer.summarize_conversation(messages)
        assert result is not None
        assert summarizer.memory_store.get_paragraph_count() == 1

    @pytest.mark.asyncio
    async def test_summarize_truncates_to_max_chars(
        self, summarizer, mock_llm, sample_messages
    ):
        """摘要超过 max_memory_chars 时截断"""
        summarizer.config.max_memory_chars = 10
        mock_llm._set_stream([{"content": "这是一个非常非常非常长的摘要文本"}])
        result = await summarizer.summarize_conversation(sample_messages)
        assert result is not None
        # 返回的是原始摘要，存储时才会截断
        entry = summarizer.memory_store.get_entry(1)
        assert len(entry.content) <= 10

    @pytest.mark.asyncio
    async def test_summarize_updates_cooldown(
        self, summarizer, mock_llm, sample_messages
    ):
        """摘要后更新冷却时间戳"""
        mock_llm._set_stream([{"content": "摘要内容"}])
        await summarizer.summarize_conversation(sample_messages)
        assert summarizer._last_summarized_at > 0
        # 冷却期内不再触发
        assert summarizer.should_summarize(token_count=10000) is False


# ============================================================
# _format_messages 测试
# ============================================================


class TestFormatMessages:
    def test_simple_messages(self, summarizer):
        result = summarizer._format_messages(
            [
                {"role": "user", "content": "Hello"},
                {"role": "assistant", "content": "Hi there"},
            ]
        )
        assert "[user]" in result
        assert "[assistant]" in result
        assert "Hello" in result
        assert "Hi there" in result

    def test_content_list_type(self, summarizer):
        """支持 content 为 list 格式（多模态消息）"""
        result = summarizer._format_messages(
            [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "What is this image?"},
                        {"type": "image_url", "image_url": {"url": "http://..."}},
                    ],
                },
            ]
        )
        assert "What is this image?" in result
        assert "image_url" not in result  # 非文本部分被过滤

    def test_empty_content_skipped(self, summarizer):
        """空内容消息被跳过"""
        result = summarizer._format_messages(
            [
                {"role": "user", "content": ""},
                {"role": "user", "content": "Real message"},
            ]
        )
        assert "Real message" in result

    def test_truncation_on_max_chars(self, summarizer):
        """超过 max_chars 时截断"""
        long_msg = "A" * 9000
        result = summarizer._format_messages(
            [
                {"role": "user", "content": long_msg},
            ]
        )
        assert "截断" in result or "truncat" in result.lower() or len(result) <= 8500


# ============================================================
# summarize_overflow 测试
# ============================================================


class TestSummarizeOverflow:
    @pytest.mark.asyncio
    async def test_overflow_with_llm(self, summarizer, mock_llm):
        mock_llm._set_stream([{"content": "旧对话摘要"}])
        result = await summarizer.summarize_overflow(
            [{"role": "user", "content": "old message"}]
        )
        assert result is not None
        entry = summarizer.memory_store.get_entry(1)
        assert entry.source == "auto-overflow"

    @pytest.mark.asyncio
    async def test_overflow_no_llm_fallback(self, summarizer):
        summarizer.llm_provider = None
        result = await summarizer.summarize_overflow(
            [{"role": "user", "content": "old " * 200}]
        )
        assert result is not None
        assert summarizer.memory_store.get_paragraph_count() == 1

    @pytest.mark.asyncio
    async def test_overflow_empty_messages(self, summarizer):
        result = await summarizer.summarize_overflow([])
        assert result is None
