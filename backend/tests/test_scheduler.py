"""
批处理调度器单元测试
"""

from __future__ import annotations

import asyncio

import pytest

from app.modules.tools.scheduler import (
    get_max_concurrency,
    partition_tool_calls,
    run_concurrent_batch,
    run_serial_batch,
)
from app.modules.tools.types import ToolUse


class FakeReadTool:
    id = "read_file"

    def is_read_only(self, input):
        return True

    def is_concurrency_safe(self, input):
        return True


class FakeWriteTool:
    id = "write_file"

    def is_read_only(self, input):
        return False

    def is_concurrency_safe(self, input):
        return False


class FakeMixedTool:
    id = "exec"

    def is_read_only(self, input):
        return True

    def is_concurrency_safe(self, input):
        return False


class FakeRegistry:
    def __init__(self, tools):
        self._tools = {t.id: t for t in tools}

    def get_tool(self, name):
        return self._tools.get(name)


# ------------------------------------------------------------------
# get_max_concurrency
# ------------------------------------------------------------------


class TestGetMaxConcurrency:
    def test_default(self, monkeypatch):
        monkeypatch.delenv("PIONECLAW_MAX_CONCURRENCY", raising=False)
        assert get_max_concurrency() == 10

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("PIONECLAW_MAX_CONCURRENCY", "5")
        assert get_max_concurrency() == 5


# ------------------------------------------------------------------
# partition_tool_calls
# ------------------------------------------------------------------


class TestPartitionToolCalls:
    def test_all_readonly_safe(self):
        registry = FakeRegistry([FakeReadTool()])
        uses = [
            ToolUse(id="1", tool_id="read_file", input={"path": "/a"}),
            ToolUse(id="2", tool_id="read_file", input={"path": "/b"}),
        ]
        batches = partition_tool_calls(uses, registry)
        assert len(batches) == 1
        assert batches[0].concurrent is True
        assert len(batches[0].tools) == 2

    def test_mixed_read_write(self):
        registry = FakeRegistry([FakeReadTool(), FakeWriteTool()])
        uses = [
            ToolUse(id="1", tool_id="read_file", input={"path": "/a"}),
            ToolUse(id="2", tool_id="write_file", input={"path": "/b"}),
        ]
        batches = partition_tool_calls(uses, registry)
        assert len(batches) == 2
        assert batches[0].concurrent is True
        assert batches[0].tools[0].tool_id == "read_file"
        assert batches[1].concurrent is False
        assert batches[1].tools[0].tool_id == "write_file"

    def test_write_between_reads(self):
        registry = FakeRegistry([FakeReadTool(), FakeWriteTool()])
        uses = [
            ToolUse(id="1", tool_id="read_file", input={"path": "/a"}),
            ToolUse(id="2", tool_id="write_file", input={"path": "/b"}),
            ToolUse(id="3", tool_id="read_file", input={"path": "/c"}),
        ]
        batches = partition_tool_calls(uses, registry)
        assert len(batches) == 3
        assert batches[0].concurrent is True  # read 1
        assert batches[1].concurrent is False  # write
        assert batches[2].concurrent is True  # read 3

    def test_not_readonly_but_safe(self):
        registry = FakeRegistry([FakeMixedTool()])
        uses = [
            ToolUse(id="1", tool_id="exec", input={"cmd": "ls"}),
        ]
        batches = partition_tool_calls(uses, registry)
        assert len(batches) == 1
        assert batches[0].concurrent is False

    def test_unknown_tool(self):
        registry = FakeRegistry([])
        uses = [
            ToolUse(id="1", tool_id="unknown", input={}),
        ]
        batches = partition_tool_calls(uses, registry)
        assert len(batches) == 1
        assert batches[0].concurrent is False

    def test_empty(self):
        registry = FakeRegistry([])
        batches = partition_tool_calls([], registry)
        assert batches == []


# ------------------------------------------------------------------
# run_concurrent_batch
# ------------------------------------------------------------------


class TestRunConcurrentBatch:
    @pytest.mark.asyncio
    async def test_basic(self):
        async def executor(x):
            await asyncio.sleep(0.01)
            return x * 2

        results = await run_concurrent_batch([1, 2, 3], executor)
        assert results == [2, 4, 6]

    @pytest.mark.asyncio
    async def test_order_preserved(self):
        async def executor(x):
            await asyncio.sleep(0.05 / x)
            return x

        results = await run_concurrent_batch([3, 2, 1], executor)
        assert results == [3, 2, 1]

    @pytest.mark.asyncio
    async def test_empty(self):
        results = await run_concurrent_batch([], lambda x: x)
        assert results == []

    @pytest.mark.asyncio
    async def test_exception_captured(self):
        async def executor(x):
            if x == 2:
                raise ValueError("boom")
            return x

        results = await run_concurrent_batch([1, 2, 3], executor)
        assert results[0] == 1
        assert results[1] == {
            "_scheduler_error": True,
            "type": "ValueError",
            "message": "boom",
        }
        assert results[2] == 3


# ------------------------------------------------------------------
# run_serial_batch
# ------------------------------------------------------------------


class TestRunSerialBatch:
    @pytest.mark.asyncio
    async def test_basic(self):
        async def executor(x):
            return x + 1

        results = await run_serial_batch([1, 2, 3], executor)
        assert results == [2, 3, 4]

    @pytest.mark.asyncio
    async def test_exception_captured(self):
        async def executor(x):
            if x == 2:
                raise RuntimeError("fail")
            return x

        results = await run_serial_batch([1, 2, 3], executor)
        assert results[0] == 1
        assert results[1] == {
            "_scheduler_error": True,
            "type": "RuntimeError",
            "message": "fail",
        }
        assert results[2] == 3
