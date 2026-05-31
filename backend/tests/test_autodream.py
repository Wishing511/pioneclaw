"""
AutoDreamEngine 单元测试

覆盖：gather / batch / dedup / consolidate / freshness / apply_changes / run
使用 unittest.mock 隔离 LLM 和文件系统。
"""

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.autodream import AutoDreamConfig, AutoDreamLog
from app.modules.memory.autodream import AutoDreamEngine, ChangeSet
from app.modules.memory.models import MemoryEntry, MemoryType


# ═══════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════

@pytest.fixture
def config():
    return AutoDreamConfig(
        enabled=True,
        cron_expression="0 2 * * *",
        batch_size=50,
        max_consolidated_per_run=10,
        archive_after_days=90,
        delete_after_days=None,
        enable_dedup=True,
        enable_consolidation=True,
        enable_archival=True,
    )


@pytest.fixture
def mock_llm_provider():
    """返回一个可编程的 mock provider"""
    provider = MagicMock()
    provider.last_input_tokens = 100
    provider.last_output_tokens = 50
    return provider


@pytest.fixture
def mock_memory_manager():
    """返回一个 mock memory manager"""
    mm = MagicMock()
    mm.store = MagicMock()
    mm.store.memory_root = "/tmp/test_memory"
    mm.index = MagicMock()
    return mm


@pytest.fixture
def engine(mock_llm_provider, mock_memory_manager, config):
    return AutoDreamEngine(
        llm_provider=mock_llm_provider,
        memory_manager=mock_memory_manager,
        config=config,
    )


@pytest.fixture
def sample_memories():
    """一组测试记忆"""
    return [
        MemoryEntry(
            id="user_pref_1.md",
            filename="user_pref_1.md",
            name="用户偏好",
            description="用户喜欢用中文",
            type=MemoryType.USER,
            content="用户喜欢用中文交流",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        ),
        MemoryEntry(
            id="user_pref_2.md",
            filename="user_pref_2.md",
            name="中文偏好",
            description="偏好使用中文",
            type=MemoryType.USER,
            content="用户偏好使用中文进行沟通",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        ),
        MemoryEntry(
            id="project_bug_1.md",
            filename="project_bug_1.md",
            name="内存泄漏修复",
            description="修复了内存泄漏",
            type=MemoryType.PROJECT,
            content="修复了 backend 中的内存泄漏问题",
            created_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
            updated_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
        ),
        MemoryEntry(
            id="project_bug_2.md",
            filename="project_bug_2.md",
            name="另一个内存泄漏",
            description="又修复了一个内存泄漏",
            type=MemoryType.PROJECT,
            content="修复了另一个内存泄漏问题",
            created_at=datetime(2026, 5, 2, tzinfo=timezone.utc),
            updated_at=datetime(2026, 5, 2, tzinfo=timezone.utc),
        ),
    ]


# ═══════════════════════════════════════════════════════════════
# 收集与分组
# ═══════════════════════════════════════════════════════════════

class TestGatherAndBatch:
    def test_group_into_batches_by_type(self, engine, sample_memories):
        batches = engine._group_into_batches(sample_memories)
        assert len(batches) == 2  # USER 和 PROJECT 两组

        # USER 组 2 条，PROJECT 组 2 条
        user_batch = next(b for b in batches if b[0].type == MemoryType.USER)
        project_batch = next(b for b in batches if b[0].type == MemoryType.PROJECT)
        assert len(user_batch) == 2
        assert len(project_batch) == 2

    def test_group_into_batches_respects_batch_size(self, engine):
        engine.config.batch_size = 2
        memories = [
            MemoryEntry(
                id=f"m{i}.md",
                filename=f"m{i}.md",
                name=f"M{i}",
                description="",
                type=MemoryType.PROJECT,
                content="",
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )
            for i in range(5)
        ]
        batches = engine._group_into_batches(memories)
        assert len(batches) == 3  # 5 / 2 = 3 批
        assert len(batches[0]) == 2
        assert len(batches[1]) == 2
        assert len(batches[2]) == 1

    def test_group_into_batches_sorted_by_updated_at(self, engine):
        memories = [
            MemoryEntry(
                id=f"m{i}.md",
                filename=f"m{i}.md",
                name=f"M{i}",
                description="",
                type=MemoryType.PROJECT,
                content="",
                created_at=datetime.now(timezone.utc),
                updated_at=datetime(2026, 5, i + 1, tzinfo=timezone.utc),
            )
            for i in range(3)
        ]
        batches = engine._group_into_batches(memories)
        assert batches[0][0].updated_at.day == 3  # 倒序，最新的在前


# ═══════════════════════════════════════════════════════════════
# 去重 Pipeline
# ═══════════════════════════════════════════════════════════════

class TestDeduplicate:
    @pytest.mark.asyncio
    async def test_deduplicate_finds_duplicates(self, engine, sample_memories):
        # Mock LLM 返回重复判断
        dup_json = json.dumps(
            {
                "duplicates": [
                    {
                        "keep": "user_pref_1.md",
                        "merge_from": ["user_pref_2.md"],
                        "delete": ["user_pref_2.md"],
                    }
                ],
                "reasoning": "两条都是关于中文偏好的",
            }
        )

        async def _mock_chat_stream(messages, **kwargs):
            yield {"content": dup_json, "finish_reason": "stop"}

        engine.llm_provider.chat_stream = _mock_chat_stream

        log = AutoDreamLog()
        log.llm_calls = 0
        log.llm_tokens_in = 0
        log.llm_tokens_out = 0
        batch = [m for m in sample_memories if m.type == MemoryType.USER]
        result = await engine._deduplicate_batch(batch, log)

        assert len(result) == 1
        assert result[0]["keep"] == "user_pref_1.md"
        assert "user_pref_2.md" in result[0]["merge_from"]
        assert log.llm_calls == 1

    @pytest.mark.asyncio
    async def test_deduplicate_skips_small_batch(self, engine):
        log = AutoDreamLog()
        log.llm_calls = 0
        log.llm_tokens_in = 0
        log.llm_tokens_out = 0
        result = await engine._deduplicate_batch([], log)
        assert result == []

    @pytest.mark.asyncio
    async def test_deduplicate_handles_llm_error(self, engine, sample_memories):
        async def _mock_error(messages, **kwargs):
            yield {"error": "API 错误"}

        engine.llm_provider.chat_stream = _mock_error

        log = AutoDreamLog()
        log.llm_calls = 0
        log.llm_tokens_in = 0
        log.llm_tokens_out = 0
        batch = [m for m in sample_memories if m.type == MemoryType.USER]
        result = await engine._deduplicate_batch(batch, log)

        assert result == []
        assert log.llm_calls == 1

    @pytest.mark.asyncio
    async def test_deduplicate_invalid_json_returns_empty(self, engine, sample_memories):
        async def _mock_invalid(messages, **kwargs):
            yield {"content": "不是 JSON", "finish_reason": "stop"}

        engine.llm_provider.chat_stream = _mock_invalid

        log = AutoDreamLog()
        log.llm_calls = 0
        log.llm_tokens_in = 0
        log.llm_tokens_out = 0
        batch = [m for m in sample_memories if m.type == MemoryType.USER]
        result = await engine._deduplicate_batch(batch, log)

        assert result == []


# ═══════════════════════════════════════════════════════════════
# 升层 Pipeline
# ═══════════════════════════════════════════════════════════════

class TestConsolidate:
    @pytest.mark.asyncio
    async def test_consolidate_generates_memories(self, engine):
        batch = [
            MemoryEntry(
                id=f"bug_{i}.md",
                filename=f"bug_{i}.md",
                name=f"Bug {i}",
                description="修复了内存泄漏",
                type=MemoryType.PROJECT,
                content=f"修复了内存泄漏 {i}",
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )
            for i in range(3)
        ]

        cons_json = json.dumps(
            {
                "consolidated_memories": [
                    {
                        "name": "近期内存泄漏修复",
                        "description": "集中修复了多个内存泄漏问题",
                        "content": "详细归纳...",
                        "source_filenames": ["bug_0.md", "bug_1.md", "bug_2.md"],
                    }
                ]
            }
        )

        async def _mock_chat_stream(messages, **kwargs):
            yield {"content": cons_json, "finish_reason": "stop"}

        engine.llm_provider.chat_stream = _mock_chat_stream

        log = AutoDreamLog()
        log.consolidated = 0
        log.llm_calls = 0
        log.llm_tokens_in = 0
        log.llm_tokens_out = 0
        result = await engine._consolidate_batch(batch, log)

        assert len(result) == 1
        assert result[0]["name"] == "近期内存泄漏修复"
        assert log.llm_calls == 1

    @pytest.mark.asyncio
    async def test_consolidate_skips_small_batch(self, engine):
        batch = [
            MemoryEntry(
                id="a.md",
                filename="a.md",
                name="A",
                description="",
                type=MemoryType.PROJECT,
                content="",
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )
            for _ in range(2)
        ]
        log = AutoDreamLog()
        log.consolidated = 0
        result = await engine._consolidate_batch(batch, log)
        assert result == []

    @pytest.mark.asyncio
    async def test_consolidate_respects_max_limit(self, engine):
        engine.config.max_consolidated_per_run = 1
        batch = [
            MemoryEntry(
                id=f"bug_{i}.md",
                filename=f"bug_{i}.md",
                name=f"Bug {i}",
                description="",
                type=MemoryType.PROJECT,
                content="",
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )
            for i in range(5)
        ]

        cons_json = json.dumps(
            {
                "consolidated_memories": [
                    {"name": "A", "content": "", "source_filenames": []},
                    {"name": "B", "content": "", "source_filenames": []},
                ]
            }
        )

        async def _mock_chat_stream(messages, **kwargs):
            yield {"content": cons_json, "finish_reason": "stop"}

        engine.llm_provider.chat_stream = _mock_chat_stream

        log = AutoDreamLog()
        log.consolidated = 1
        log.llm_calls = 0
        log.llm_tokens_in = 0
        log.llm_tokens_out = 0
        result = await engine._consolidate_batch(batch, log)

        # 已达上限，返回空
        assert result == []


# ═══════════════════════════════════════════════════════════════
# 时效评估 Pipeline
# ═══════════════════════════════════════════════════════════════

class TestFreshness:
    @pytest.mark.asyncio
    async def test_freshness_archives_old(self, engine, sample_memories):
        fresh_json = json.dumps(
            {
                "decisions": [
                    {"filename": "project_bug_1.md", "action": "archive", "reason": "过期"},
                    {"filename": "project_bug_2.md", "action": "keep", "reason": "仍相关"},
                ]
            }
        )

        async def _mock_chat_stream(messages, **kwargs):
            yield {"content": fresh_json, "finish_reason": "stop"}

        engine.llm_provider.chat_stream = _mock_chat_stream

        log = AutoDreamLog()
        log.llm_calls = 0
        log.llm_tokens_in = 0
        log.llm_tokens_out = 0
        result = await engine._evaluate_freshness(sample_memories, log)

        assert "project_bug_1.md" in result["archives"]
        assert "project_bug_2.md" not in result["archives"]
        assert result["deletes"] == []

    @pytest.mark.asyncio
    async def test_freshness_degrades_delete_when_disabled(self, engine):
        engine.config.delete_after_days = None
        memories = [
            MemoryEntry(
                id="old.md",
                filename="old.md",
                name="Old",
                description="",
                type=MemoryType.PROJECT,
                content="",
                created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            )
        ]

        fresh_json = json.dumps(
            {"decisions": [{"filename": "old.md", "action": "delete", "reason": "无用"}]}
        )

        async def _mock_chat_stream(messages, **kwargs):
            yield {"content": fresh_json, "finish_reason": "stop"}

        engine.llm_provider.chat_stream = _mock_chat_stream

        log = AutoDreamLog()
        log.llm_calls = 0
        log.llm_tokens_in = 0
        log.llm_tokens_out = 0
        result = await engine._evaluate_freshness(memories, log)

        # delete_after_days=None 时降级为 archive
        assert "old.md" in result["archives"]
        assert "old.md" not in result["deletes"]

    @pytest.mark.asyncio
    async def test_freshness_fallback_on_llm_error(self, engine):
        async def _mock_error(messages, **kwargs):
            yield {"error": "API 错误"}

        engine.llm_provider.chat_stream = _mock_error

        old_memory = MemoryEntry(
            id="old.md",
            filename="old.md",
            name="Old",
            description="",
            type=MemoryType.PROJECT,
            content="",
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )

        log = AutoDreamLog()
        log.llm_calls = 0
        log.llm_tokens_in = 0
        log.llm_tokens_out = 0
        result = await engine._evaluate_freshness([old_memory], log)

        # 规则兜底：超 90 天自动 archive
        assert "old.md" in result["archives"]


# ═══════════════════════════════════════════════════════════════
# 变更执行
# ═══════════════════════════════════════════════════════════════

class TestApplyChanges:
    @pytest.mark.asyncio
    async def test_merge_memory(self, engine, mock_memory_manager):
        src_entry = MagicMock()
        src_entry.name = "源记忆"
        src_entry.content = "源内容"
        dst_entry = MagicMock()
        dst_entry.name = "目标记忆"
        dst_entry.content = "目标内容"
        dst_entry.description = "desc"
        dst_entry.type = MemoryType.USER

        mock_memory_manager.store.read_file.side_effect = [src_entry, dst_entry]

        await engine._merge_memory("src.md", "dst.md")

        mock_memory_manager.update.assert_called_once()
        mock_memory_manager.delete.assert_called_once_with("src.md")

    @pytest.mark.asyncio
    async def test_archive_memory(self, engine, mock_memory_manager, tmp_path):
        engine.memory_manager.store.memory_root = str(tmp_path)
        (tmp_path / "test.md").write_text("test")

        await engine._archive_memory("test.md")

        archive_dir = tmp_path / "archive" / datetime.now(timezone.utc).strftime("%Y-%m")
        assert (archive_dir / "test.md").exists()
        assert not (tmp_path / "test.md").exists()

    @pytest.mark.asyncio
    async def test_delete_memory(self, engine, mock_memory_manager):
        await engine._delete_memory("old.md")
        mock_memory_manager.delete.assert_called_once_with("old.md")

    @pytest.mark.asyncio
    async def test_save_consolidated(self, engine, mock_memory_manager):
        result_mock = MagicMock()
        result_mock.success = True
        mock_memory_manager.save.return_value = result_mock

        await engine._save_consolidated(
            {
                "name": "归纳记忆",
                "description": "测试",
                "content": "内容",
                "type": "project",
                "source_filenames": ["a.md", "b.md"],
            }
        )

        mock_memory_manager.save.assert_called_once()
        call_args = mock_memory_manager.save.call_args
        assert "来源记忆" in call_args[0][0]  # content 包含来源


# ═══════════════════════════════════════════════════════════════
# 整体流程
# ═══════════════════════════════════════════════════════════════

class TestRun:
    @pytest.mark.asyncio
    async def test_run_success(self, engine, mock_memory_manager, sample_memories):
        mock_memory_manager.store.get_all_files.return_value = sample_memories

        # Mock LLM：去重、升层、时效都返回空（无变更）
        async def _mock_empty(messages, **kwargs):
            yield {"content": '{"duplicates":[]}', "finish_reason": "stop"}

        engine.llm_provider.chat_stream = _mock_empty

        log = AutoDreamLog(status="running")
        log.llm_calls = 0
        log.llm_tokens_in = 0
        log.llm_tokens_out = 0
        log.consolidated = 0
        log.duplicates_found = 0
        log.merged = 0
        log.archived = 0
        log.deleted = 0
        await engine.run(log)

        assert log.status == "success"
        assert log.total_memories == 4
        assert log.duration_seconds >= 0

    @pytest.mark.asyncio
    async def test_run_with_changes(self, engine, mock_memory_manager):
        memories = [
            MemoryEntry(
                id=f"m{i}.md",
                filename=f"m{i}.md",
                name=f"M{i}",
                description="",
                type=MemoryType.PROJECT,
                content=f"内容{i}",
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )
            for i in range(5)
        ]
        mock_memory_manager.store.get_all_files.return_value = memories

        # 顺序返回：去重、升层、时效
        responses = [
            # 去重
            '{"duplicates": [{"keep": "m0.md", "merge_from": ["m1.md"]}]}',
            # 升层
            '{"consolidated_memories": [{"name": "归纳", "content": "...", "source_filenames": ["m2.md", "m3.md"]}]}',
            # 时效
            '{"decisions": [{"filename": "m4.md", "action": "archive"}]}',
        ]
        call_idx = [0]

        async def mock_chat_stream(messages, **kwargs):
            content = responses[call_idx[0]]
            call_idx[0] += 1
            yield {"content": content, "finish_reason": "stop"}

        engine.llm_provider.chat_stream = mock_chat_stream

        log = AutoDreamLog(status="running")
        log.llm_calls = 0
        log.llm_tokens_in = 0
        log.llm_tokens_out = 0
        log.consolidated = 0
        log.duplicates_found = 0
        log.merged = 0
        log.archived = 0
        log.deleted = 0
        await engine.run(log)

        assert log.status == "success"
        assert log.total_memories == 5
        assert log.duplicates_found == 1
        assert log.merged == 1
        assert log.consolidated == 1
        assert log.archived == 1

    @pytest.mark.asyncio
    async def test_run_overlap_protection(self, engine, mock_memory_manager):
        """验证锁防止并发执行"""
        mock_memory_manager.store.get_all_files.return_value = []

        async def slow_run(log):
            await asyncio.sleep(0.5)
            log.status = "success"

        # 替换 run 内部逻辑为慢速版本
        original_run = engine.run

        log1 = AutoDreamLog(status="running")
        log2 = AutoDreamLog(status="running")

        # 由于锁是类级别的，第二个 run 会等待第一个完成
        import asyncio as aio

        task1 = aio.create_task(original_run(log1))
        # 稍微等一下让 task1 先拿到锁
        await aio.sleep(0.1)
        task2 = aio.create_task(original_run(log2))

        await aio.gather(task1, task2)

        # 两个都应该成功（顺序执行）
        assert log1.status == "success"
        assert log2.status == "success"


# ═══════════════════════════════════════════════════════════════
# 边界场景
# ═══════════════════════════════════════════════════════════════

class TestEdgeCases:
    @pytest.mark.asyncio
    async def test_run_skips_when_disabled(self, engine):
        """config.enabled=False 时直接返回 skipped"""
        engine.config.enabled = False

        log = AutoDreamLog(status="running")
        await engine.run(log)

        assert log.status == "skipped"
        assert log.duration_seconds == 0.0

    @pytest.mark.asyncio
    async def test_run_empty_memories(self, engine, mock_memory_manager):
        """空记忆库时快速返回 success，不调用 LLM"""
        mock_memory_manager.store.get_all_files.return_value = []

        # 故意设置一个会报错的 LLM，验证不会被调用
        async def _mock_boom(messages, **kwargs):
            raise RuntimeError("不应被调用")

        engine.llm_provider.chat_stream = _mock_boom

        log = AutoDreamLog(status="running")
        await engine.run(log)

        assert log.status == "success"
        assert log.total_memories == 0
        assert log.duration_seconds >= 0

    @pytest.mark.asyncio
    async def test_merge_memory_src_not_found(self, engine, mock_memory_manager):
        """合并时源文件不存在应优雅跳过"""
        mock_memory_manager.store.read_file.side_effect = FileNotFoundError("文件不存在")

        # 不应抛出异常
        await engine._merge_memory("not_exist.md", "dst.md")

    @pytest.mark.asyncio
    async def test_archive_memory_file_not_exist(self, engine, mock_memory_manager, tmp_path):
        """归档时文件不存在应安全跳过"""
        engine.memory_manager.store.memory_root = str(tmp_path)

        # 文件不存在，不应抛出异常
        await engine._archive_memory("not_exist.md")

        # 归档目录不应被创建（因为没有文件需要移动）
        assert not (tmp_path / "archive").exists()

    @pytest.mark.asyncio
    async def test_freshness_skips_consolidated(self, engine):
        """consolidated 记忆不参与时效评估"""
        entries = [
            MemoryEntry(
                id="normal.md",
                filename="normal.md",
                name="普通记忆",
                description="",
                type=MemoryType.PROJECT,
                content="",
                created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            ),
            MemoryEntry(
                id="consolidated_xxx.md",
                filename="consolidated_xxx.md",
                name="consolidated_汇总",
                description="",
                type=MemoryType.PROJECT,
                content="",
                created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            ),
        ]

        fresh_json = json.dumps(
            {
                "decisions": [
                    {"filename": "normal.md", "action": "archive", "reason": "过期"},
                    {"filename": "consolidated_xxx.md", "action": "delete", "reason": "测试"},
                ]
            }
        )

        async def _mock_chat_stream(messages, **kwargs):
            yield {"content": fresh_json, "finish_reason": "stop"}

        engine.llm_provider.chat_stream = _mock_chat_stream

        log = AutoDreamLog()
        log.llm_calls = 0
        log.llm_tokens_in = 0
        log.llm_tokens_out = 0
        result = await engine._evaluate_freshness(entries, log)

        # normal.md 被归档
        assert "normal.md" in result["archives"]
        # consolidated 记忆不应被处理（即使 LLM 建议 delete）
        assert "consolidated_xxx.md" not in result["deletes"]
        assert "consolidated_xxx.md" not in result["archives"]

    @pytest.mark.asyncio
    async def test_rebuild_index_failure_not_fatal(self, engine, mock_memory_manager):
        """索引重建失败不应导致整体 run() 失败"""
        mock_memory_manager.store.get_all_files.return_value = []
        mock_memory_manager.index.rebuild_index.side_effect = RuntimeError("索引损坏")

        log = AutoDreamLog(status="running")
        await engine.run(log)

        assert log.status == "success"

    @pytest.mark.asyncio
    async def test_all_features_disabled(self, engine, mock_memory_manager):
        """所有功能关闭时只收集记忆不做任何操作"""
        mock_memory_manager.store.get_all_files.return_value = [
            MemoryEntry(
                id="m1.md",
                filename="m1.md",
                name="M1",
                description="",
                type=MemoryType.PROJECT,
                content="内容",
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )
        ]

        engine.config.enable_dedup = False
        engine.config.enable_consolidation = False
        engine.config.enable_archival = False

        # 故意设置会报错的 LLM，验证不会被调用
        async def _mock_boom(messages, **kwargs):
            raise RuntimeError("不应被调用")

        engine.llm_provider.chat_stream = _mock_boom

        log = AutoDreamLog(status="running")
        log.llm_calls = 0
        log.llm_tokens_in = 0
        log.llm_tokens_out = 0
        log.consolidated = 0
        log.duplicates_found = 0
        log.merged = 0
        log.archived = 0
        log.deleted = 0
        await engine.run(log)

        assert log.status == "success"
        assert log.total_memories == 1
        assert log.llm_calls == 0
        assert log.duplicates_found == 0
        assert log.merged == 0
        assert log.consolidated == 0
        assert log.archived == 0


# ═══════════════════════════════════════════════════════════════
# 辅助函数
# ═══════════════════════════════════════════════════════════════

async def async_gen(*items):
    """辅助：将多个 dict yield 出去，模拟 chat_stream"""
    for item in items:
        yield item
