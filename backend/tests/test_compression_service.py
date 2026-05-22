"""
Test ContextCompressionService fixes from PR #3 review

验证：
1. Compactor._call_llm 支持 chat_stream 聚合
2. Compactor.compact(force=True) 绕过 should_compact
3. estimate_or_read_usage 取 max(API, estimated)
4. FileTracker get_recent 跳过超大文件继续尝试后续
5. CompressionService _rebuild_after_compact 恢复 FileTracker 记录
"""

import pytest

from app.modules.agent.compactor import CompactionConfig, CompactionResult, Compactor
from app.modules.agent.compression_service import (
    ContextCompressionService,
)
from app.modules.agent.context_pruner import ContextPruner
from app.modules.agent.file_tracker import FileTracker
from app.modules.agent.token_budget import TokenBudget

# --- Compactor._call_llm chat_stream 支持 ---


class FakeChatStreamProvider:
    """模拟 SimpleLLMProvider（只有 chat_stream，无 chat/complete）"""

    def __init__(self, content="summary text"):
        self._content = content

    async def chat_stream(self, messages):
        yield {"content": self._content[:5]}
        yield {"content": self._content[5:]}


@pytest.mark.asyncio
async def test_compactor_call_llm_with_chat_stream():
    """P1: Compactor 能聚合 chat_stream 的响应生成摘要"""
    provider = FakeChatStreamProvider(content="Hello world summary")
    compactor = Compactor(
        config=CompactionConfig(
            context_window=10, message_threshold=1, keep_recent_messages=0
        ),
        llm_client=provider,
    )
    result = await compactor.compact(
        [{"role": "user", "content": "x"}],
        force=True,
    )
    assert result.summary == "Hello world summary"
    assert result.removed_messages == 1


class FakeChatStreamErrorProvider:
    """模拟流中返回 error 的 provider"""

    async def chat_stream(self, messages):
        yield {"content": "part1"}
        yield {"error": "something wrong"}


@pytest.mark.asyncio
async def test_compactor_call_llm_chat_stream_error_graceful():
    """chat_stream 返回 error chunk 时应返回空，不崩溃"""
    provider = FakeChatStreamErrorProvider()
    compactor = Compactor(
        config=CompactionConfig(context_window=10, message_threshold=1),
        llm_client=provider,
    )
    result = await compactor.compact(
        [{"role": "user", "content": "x"}],
        force=True,
    )
    # error 后内容为空，触发空摘要保护
    assert result.summary == ""
    assert result.removed_messages == 0


# --- Compactor compact(force=True) ---


@pytest.mark.asyncio
async def test_compactor_force_bypasses_should_compact():
    """P1: force=True 时即使未达阈值也执行压缩"""
    provider = FakeChatStreamProvider(content="forced summary")
    compactor = Compactor(
        config=CompactionConfig(
            context_window=200_000, message_threshold=200, keep_recent_messages=0
        ),
        llm_client=provider,
    )
    # 只有 2 条消息，远未达阈值
    messages = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
    ]

    # force=False 不压缩
    result_normal = await compactor.compact(messages, force=False)
    assert result_normal.removed_messages == 0

    # force=True 强制压缩
    result_forced = await compactor.compact(messages, force=True)
    assert result_forced.removed_messages == 2  # keep_recent=0，全部总结
    assert result_forced.summary == "forced summary"


# --- estimate_or_read_usage 取 max ---


class FakeProviderWithStaleTokens:
    """模拟 provider.last_input_tokens 滞后的情况"""

    last_input_tokens = 100
    last_output_tokens = 20


class FakeProviderNoTokens:
    """模拟 provider 无 token 记录"""

    last_input_tokens = 0
    last_output_tokens = 0


def test_estimate_or_read_usage_prefers_max():
    """P1: 当字符估算 > API 真实值时，取估算值"""
    budget = TokenBudget(context_window=200_000)
    service = ContextCompressionService(budget=budget)

    provider = FakeProviderWithStaleTokens()
    # 大消息列表，字符估算会超过 100
    messages = [{"role": "user", "content": "x" * 10_000}]

    usage = service.estimate_or_read_usage(messages, provider)
    # 字符估算 10_000 * 0.25 = 2500 > 100，应取估算值
    assert usage.input_tokens > 100
    assert usage.source == "estimated"


def test_estimate_or_read_usage_uses_api_when_larger():
    """当 API 真实值 > 字符估算时，取 API 值"""
    budget = TokenBudget(context_window=200_000)
    service = ContextCompressionService(budget=budget)

    provider = FakeProviderWithStaleTokens()
    # 小消息列表
    messages = [{"role": "user", "content": "hi"}]

    usage = service.estimate_or_read_usage(messages, provider)
    assert usage.input_tokens == 100
    assert usage.source == "api"


def test_estimate_or_read_usage_fallback_when_no_provider():
    """无 provider 时回退到字符估算"""
    budget = TokenBudget(context_window=200_000)
    service = ContextCompressionService(budget=budget)

    messages = [{"role": "user", "content": "hello world"}]
    usage = service.estimate_or_read_usage(messages, None)
    assert usage.input_tokens > 0
    assert usage.source == "estimated"


# --- FileTracker get_recent skip oversized ---


def test_file_tracker_skips_oversized_continues():
    """P2: 跳过超大文件，继续尝试后续小文件"""
    tracker = FileTracker(max_files=5, max_tokens=50)
    # 第一个文件 60 tokens（单独就超过 max_tokens，无法放入）
    tracker.record_access(path="/app/big.py", content="x" * 240, was_edited=False)
    # 第二个文件 10 tokens（可以放入）
    tracker.record_access(path="/app/small.py", content="x" * 40, was_edited=False)

    records = tracker.get_recent(max_tokens=50, max_files=5)
    # 应跳过 big.py（60 > 50），保留 small.py（10 <= 50）
    assert len(records) == 1
    assert records[0].path == "/app/small.py"


# --- CompressionService rebuild with FileTracker ---


@pytest.mark.asyncio
async def test_rebuild_after_compact_restores_files():
    """P2: service 主路径压缩后包含 restored files"""
    budget = TokenBudget(context_window=200_000)
    tracker = FileTracker(max_files=5, max_tokens=50_000)
    tracker.record_access(
        path="/app/main.py", content="def main(): pass", was_edited=True
    )

    service = ContextCompressionService(
        budget=budget,
        compactor=None,
        context_pruner=None,
        file_tracker=tracker,
    )

    result = CompactionResult(
        summary="test summary",
        removed_messages=5,
        kept_messages=3,
        saved_tokens=1000,
    )
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
    ]
    rebuilt = service._rebuild_after_compact(messages, result)

    # 应包含 system + summary + recent + restored files system msg
    roles = [m["role"] for m in rebuilt]
    assert "system" in roles
    assert "user" in roles

    # 找到 restored files 消息
    restored_msgs = [
        m
        for m in rebuilt
        if m.get("role") == "system" and "Restored files" in m.get("content", "")
    ]
    assert len(restored_msgs) == 1
    assert "/app/main.py" in restored_msgs[0]["content"]


@pytest.mark.asyncio
async def test_rebuild_without_file_tracker_no_crash():
    """未注入 FileTracker 时不崩溃，不插入 restored files"""
    budget = TokenBudget(context_window=200_000)
    service = ContextCompressionService(
        budget=budget,
        compactor=None,
        context_pruner=None,
        file_tracker=None,
    )

    result = CompactionResult(
        summary="test summary",
        removed_messages=5,
        kept_messages=3,
        saved_tokens=1000,
    )
    messages = [{"role": "user", "content": "hi"}]
    rebuilt = service._rebuild_after_compact(messages, result)

    # 不应包含 restored files
    restored_msgs = [m for m in rebuilt if "Restored files" in m.get("content", "")]
    assert len(restored_msgs) == 0


# --- manual_compact uses force=True ---


@pytest.mark.asyncio
async def test_manual_compact_uses_force():
    """P1: manual_compact 调用 compact(..., force=True)"""
    provider = FakeChatStreamProvider(content="manual summary")
    budget = TokenBudget(context_window=200_000)
    compactor = Compactor(
        config=CompactionConfig(
            context_window=200_000, message_threshold=200, keep_recent_messages=0
        ),
        llm_client=provider,
    )
    service = ContextCompressionService(
        budget=budget,
        compactor=compactor,
        context_pruner=ContextPruner(),
    )

    messages = [{"role": "user", "content": "hi"}]
    report, compacted = await service.manual_compact(
        messages, instruction="keep API design"
    )

    assert report.summary == "manual summary"
    assert report.removed_messages == 1  # keep_recent=0，全部总结
    assert len(compacted) > 0
    assert any(
        "[Previous conversation summary]" in str(m.get("content", ""))
        for m in compacted
    )
