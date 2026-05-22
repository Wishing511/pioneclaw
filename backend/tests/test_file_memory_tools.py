"""
File Memory Tools 测试: FileMemoryWriteTool, FileMemorySearchTool, FileMemoryReadTool
Track 1 — MEMORY.md 纯文本记忆存储
"""

import json
import tempfile
from pathlib import Path

import pytest

from app.modules.agent.memory import MemoryStore
from app.modules.tools.builtin import (
    FileMemoryReadTool,
    FileMemorySearchTool,
    FileMemoryWriteTool,
)


@pytest.fixture
def memory_store():
    """创建使用临时目录的 MemoryStore"""
    with tempfile.TemporaryDirectory() as tmpdir:
        store = MemoryStore(Path(tmpdir))
        yield store


@pytest.fixture
def populated_store(memory_store):
    """预填充一些条目的 MemoryStore"""
    memory_store.append_entry("web-chat", "用户询问天气API接口，推荐使用OpenWeatherMap")
    memory_store.append_entry("telegram", "用户要求每天早上9点发日报，已创建cron任务")
    memory_store.append_entry("web-chat", "讨论了Python异步编程的最佳实践")
    memory_store.append_entry("cron", "每天自动备份数据库到远程服务器")
    return memory_store


# ============================================================
# FileMemoryWriteTool 测试
# ============================================================


class TestFileMemoryWriteTool:
    @pytest.mark.asyncio
    async def test_write_success(self, memory_store, monkeypatch):
        """写入一条记忆，返回 line_number"""
        monkeypatch.setattr(
            "app.modules.agent.memory.get_memory_store",
            lambda: memory_store,
        )
        tool = FileMemoryWriteTool()
        result_str = await tool.execute(content="测试记忆内容", source="test")
        result = json.loads(result_str)
        assert result["success"] is True
        assert result["line_number"] == 1
        assert result["track"] == "file"

    @pytest.mark.asyncio
    async def test_write_default_source(self, memory_store, monkeypatch):
        """默认来源应为 agent"""
        monkeypatch.setattr(
            "app.modules.agent.memory.get_memory_store",
            lambda: memory_store,
        )
        tool = FileMemoryWriteTool()
        result_str = await tool.execute(content="测试内容")
        result = json.loads(result_str)
        assert result["success"] is True
        entry = memory_store.get_entry(1)
        assert entry is not None
        assert entry.source == "agent"

    @pytest.mark.asyncio
    async def test_write_multiple(self, memory_store, monkeypatch):
        """连续写入多条"""
        monkeypatch.setattr(
            "app.modules.agent.memory.get_memory_store",
            lambda: memory_store,
        )
        tool = FileMemoryWriteTool()
        for i in range(3):
            result_str = await tool.execute(content=f"记忆条目{i + 1}", source="test")
            result = json.loads(result_str)
            assert result["success"] is True
            assert result["line_number"] == i + 1

    @pytest.mark.asyncio
    async def test_write_empty_content(self, memory_store, monkeypatch):
        """写入空内容仍应成功（后端不验证）"""
        monkeypatch.setattr(
            "app.modules.agent.memory.get_memory_store",
            lambda: memory_store,
        )
        tool = FileMemoryWriteTool()
        result_str = await tool.execute(content="", source="test")
        result = json.loads(result_str)
        assert result["success"] is True


# ============================================================
# FileMemorySearchTool 测试
# ============================================================


class TestFileMemorySearchTool:
    @pytest.mark.asyncio
    async def test_search_or_mode(self, populated_store, monkeypatch):
        """OR 模式：任一关键词匹配"""
        monkeypatch.setattr(
            "app.modules.agent.memory.get_memory_store",
            lambda: populated_store,
        )
        tool = FileMemorySearchTool()
        result = await tool.execute(keywords="天气 数据库")
        assert "天气" in result or "OpenWeatherMap" in result
        assert "数据库" in result

    @pytest.mark.asyncio
    async def test_search_and_mode(self, populated_store, monkeypatch):
        """AND 模式：所有关键词必须匹配"""
        monkeypatch.setattr(
            "app.modules.agent.memory.get_memory_store",
            lambda: populated_store,
        )
        tool = FileMemorySearchTool()
        result = await tool.execute(keywords="Python 异步", match_mode="and")
        assert "Python" in result
        assert "异步" in result

    @pytest.mark.asyncio
    async def test_search_no_match(self, populated_store, monkeypatch):
        """无匹配时返回未找到提示"""
        monkeypatch.setattr(
            "app.modules.agent.memory.get_memory_store",
            lambda: populated_store,
        )
        tool = FileMemorySearchTool()
        result = await tool.execute(keywords="zzzznotfound", match_mode="or")
        assert "zzzznotfound" in result

    @pytest.mark.asyncio
    async def test_search_empty_store(self, memory_store, monkeypatch):
        """空存储搜索返回提示"""
        monkeypatch.setattr(
            "app.modules.agent.memory.get_memory_store",
            lambda: memory_store,
        )
        tool = FileMemorySearchTool()
        result = await tool.execute(keywords="test")
        assert "记忆为空" in result

    @pytest.mark.asyncio
    async def test_search_max_results(self, populated_store, monkeypatch):
        """max_results 限制返回条数"""
        monkeypatch.setattr(
            "app.modules.agent.memory.get_memory_store",
            lambda: populated_store,
        )
        tool = FileMemorySearchTool()
        result = await tool.execute(keywords="的", max_results=2)
        lines = [line for line in result.split("\n") if line.startswith("[")]
        assert len(lines) <= 3


# ============================================================
# FileMemoryReadTool 测试
# ============================================================


class TestFileMemoryReadTool:
    @pytest.mark.asyncio
    async def test_read_recent(self, populated_store, monkeypatch):
        """读取最近 N 条"""
        monkeypatch.setattr(
            "app.modules.agent.memory.get_memory_store",
            lambda: populated_store,
        )
        tool = FileMemoryReadTool()
        result = await tool.execute(recent=2)
        lines = [line for line in result.split("\n") if line.startswith("[")]
        assert len(lines) == 2

    @pytest.mark.asyncio
    async def test_read_empty_store(self, memory_store, monkeypatch):
        """空存储返回提示"""
        monkeypatch.setattr(
            "app.modules.agent.memory.get_memory_store",
            lambda: memory_store,
        )
        tool = FileMemoryReadTool()
        result = await tool.execute(recent=5)
        assert "记忆为空" in result

    @pytest.mark.asyncio
    async def test_read_single_line(self, populated_store, monkeypatch):
        """读取单行"""
        monkeypatch.setattr(
            "app.modules.agent.memory.get_memory_store",
            lambda: populated_store,
        )
        tool = FileMemoryReadTool()
        result = await tool.execute(start=1, end=0)
        assert "[1]" in result

    @pytest.mark.asyncio
    async def test_read_range(self, populated_store, monkeypatch):
        """读取行范围"""
        monkeypatch.setattr(
            "app.modules.agent.memory.get_memory_store",
            lambda: populated_store,
        )
        tool = FileMemoryReadTool()
        result = await tool.execute(start=1, end=2)
        lines = [line for line in result.split("\n") if line.startswith("[")]
        assert len(lines) == 2
        assert "[1]" in lines[0]
        assert "[2]" in lines[1]

    @pytest.mark.asyncio
    async def test_read_out_of_range(self, populated_store, monkeypatch):
        """超出范围的行号被 clamped 到有效范围"""
        monkeypatch.setattr(
            "app.modules.agent.memory.get_memory_store",
            lambda: populated_store,
        )
        tool = FileMemoryReadTool()
        result = await tool.execute(start=100, end=200)
        # start 被 clamp 到 max line (4), 返回第4条
        assert "[4]" in result


# ============================================================
# MemoryStore 底层单元测试
# ============================================================


class TestMemoryStore:
    def test_append_and_read(self, memory_store):
        """追加后能正确读取"""
        ln = memory_store.append_entry("test", "Hello World")
        entry = memory_store.get_entry(ln)
        assert entry is not None
        assert entry.content == "Hello World"
        assert entry.source == "test"

    def test_delete_entry(self, memory_store):
        """删除条目"""
        ln = memory_store.append_entry("test", "to delete")
        assert memory_store.delete_entry(ln) is True
        assert memory_store.get_entry(ln) is None

    def test_delete_lines_reindex(self, memory_store):
        """删除后行号重新编排"""
        memory_store.append_entry("s1", "A")
        memory_store.append_entry("s2", "B")
        memory_store.append_entry("s3", "C")
        memory_store.delete_lines([2])
        assert memory_store.get_paragraph_count() == 2
        entry = memory_store.get_entry(2)
        assert entry is not None
        assert entry.content == "C"

    def test_clear(self, memory_store):
        """清空所有条目"""
        memory_store.append_entry("s1", "A")
        memory_store.append_entry("s2", "B")
        count = memory_store.clear()
        assert count == 2
        assert memory_store.get_paragraph_count() == 0

    def test_get_stats(self, memory_store):
        """统计信息正确"""
        memory_store.append_entry("web-chat", "A")
        memory_store.append_entry("web-chat", "B")
        memory_store.append_entry("telegram", "C")
        stats = memory_store.get_stats()
        assert stats.total_entries == 3
        assert stats.sources["web-chat"] == 2
        assert stats.sources["telegram"] == 1

    def test_export_import(self, memory_store):
        """导出再导入保持内容一致"""
        memory_store.append_entry("s1", "Content 1")
        memory_store.append_entry("s2", "Content 2")
        exported = memory_store.export_to_text()
        memory_store.clear()
        assert memory_store.get_paragraph_count() == 0
        count = memory_store.import_from_text(exported, source="import")
        assert count == 2
        assert memory_store.get_paragraph_count() == 2

    def test_search_entries(self, memory_store):
        """search_entries 返回正确条目"""
        memory_store.append_entry("web-chat", "Python is great for data science")
        memory_store.append_entry("cron", "Daily Python backup job")
        memory_store.append_entry("system", "System maintenance log")
        results = memory_store.search_entries(["Python"], match_mode="or")
        assert len(results) == 2
        results_and = memory_store.search_entries(
            ["Python", "backup"], match_mode="and"
        )
        assert len(results_and) == 1

    def test_write_all(self, memory_store):
        """全量写入替换全部内容"""
        memory_store.append_entry("old", "Legacy data")
        new_content = "2026-05-01|manual|Fresh start"
        memory_store.write_all(new_content)
        assert memory_store.read_all() == new_content
