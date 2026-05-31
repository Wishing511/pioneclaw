"""
AutoDream 集成测试

使用真实临时文件系统 + MockLLMProvider，验证端到端文件变更。
所有测试使用单一类型（PROJECT）记忆，确保只有一个 batch，简化响应顺序。
"""

import json
import os
from datetime import datetime, timezone

import pytest
import pytest_asyncio

from app.models.autodream import AutoDreamConfig, AutoDreamLog
from app.modules.llm import MockLLMProvider
from app.modules.memory.autodream import AutoDreamEngine
from app.modules.memory.manager import MemoryManage
from app.modules.memory.models import MemoryEntry, MemoryMetadata, MemoryType


@pytest_asyncio.fixture
async def temp_memory_manager(tmp_path):
    """在临时目录中创建真实的 MemoryManage 实例"""
    root = str(tmp_path / "memory")
    os.makedirs(root, exist_ok=True)

    # 传入一个 dummy llm_query_fn，避免自动查询数据库
    mm = MemoryManage(
        memory_root=root,
        llm_query_fn=lambda prompt: "",
        extract_agent_fn=lambda sys, usr: "",
    )
    return mm


def _make_log():
    """创建并初始化 AutoDreamLog 数值字段"""
    log = AutoDreamLog(status="running")
    log.llm_calls = 0
    log.llm_tokens_in = 0
    log.llm_tokens_out = 0
    log.consolidated = 0
    log.duplicates_found = 0
    log.merged = 0
    log.archived = 0
    log.deleted = 0
    return log


def _scripted_provider(*contents: str) -> MockLLMProvider:
    """按顺序返回指定 JSON 文本的 MockLLMProvider"""
    mock = MockLLMProvider()
    for c in contents:
        mock.add_response([{"content": c, "finish_reason": "stop"}])
    return mock


@pytest.mark.asyncio
async def test_end_to_end_dedup_and_consolidate(temp_memory_manager):
    """端到端：去重 + 升层 + 归档，验证文件系统变更"""
    mm = temp_memory_manager
    root = mm.store.memory_root

    # 创建 4 条 PROJECT 记忆（同类型确保只有一个 batch）
    mm.save(
        content="用户偏好使用中文进行交流",
        mem_type=MemoryType.PROJECT,
        metadata=MemoryMetadata(
            name="用户偏好",
            description="用户喜欢用中文",
            type=MemoryType.PROJECT,
        ),
    )
    mm.save(
        content="用户喜欢使用中文沟通",
        mem_type=MemoryType.PROJECT,
        metadata=MemoryMetadata(
            name="中文偏好",
            description="偏好使用中文",
            type=MemoryType.PROJECT,
        ),
    )
    for i in range(2):
        mm.save(
            content=f"修复了内存泄漏问题 #{i}",
            mem_type=MemoryType.PROJECT,
            metadata=MemoryMetadata(
                name=f"内存泄漏修复 {i}",
                description=f"修复了第 {i} 个内存泄漏",
                type=MemoryType.PROJECT,
            ),
        )

    # 去重、升层、时效 各需一个响应
    dedup_json = json.dumps(
        {
            "duplicates": [
                {
                    "keep": "project-用户偏好.md",
                    "merge_from": ["project-中文偏好.md"],
                    "delete": ["project-中文偏好.md"],
                }
            ],
            "reasoning": "两条都是中文偏好",
        },
        ensure_ascii=False,
    )
    consolidate_json = json.dumps(
        {
            "consolidated_memories": [
                {
                    "name": "近期内存泄漏修复汇总",
                    "description": "集中修复了多个内存泄漏",
                    "content": "详细归纳了最近修复的内存泄漏问题",
                    "source_filenames": [
                        "project-内存泄漏修复-0.md",
                        "project-内存泄漏修复-1.md",
                    ],
                }
            ]
        },
        ensure_ascii=False,
    )
    freshness_json = json.dumps(
        {"decisions": []},
        ensure_ascii=False,
    )

    provider = _scripted_provider(dedup_json, consolidate_json, freshness_json)

    config = AutoDreamConfig(
        enabled=True,
        batch_size=50,
        max_consolidated_per_run=5,
        archive_after_days=30,
        enable_dedup=True,
        enable_consolidation=True,
        enable_archival=True,
    )

    engine = AutoDreamEngine(
        llm_provider=provider,
        memory_manager=mm,
        config=config,
    )

    log = _make_log()
    await engine.run(log)

    assert log.status == "success"
    assert log.total_memories == 4
    assert log.duplicates_found == 1
    assert log.merged == 1
    assert log.consolidated == 1
    assert log.archived == 0

    # 验证文件系统：project-中文偏好.md 应被删除
    files_after = {e.filename for e in mm.store.get_all_files()}
    assert "project-中文偏好.md" not in files_after


@pytest.mark.asyncio
async def test_dedup_merge_content(temp_memory_manager):
    """验证去重合并后目标文件包含了源文件内容"""
    mm = temp_memory_manager

    mm.save(
        content="用户偏好使用中文进行交流",
        mem_type=MemoryType.PROJECT,
        metadata=MemoryMetadata(
            name="用户偏好",
            description="用户喜欢用中文",
            type=MemoryType.PROJECT,
        ),
    )
    mm.save(
        content="用户喜欢使用中文沟通",
        mem_type=MemoryType.PROJECT,
        metadata=MemoryMetadata(
            name="中文偏好",
            description="偏好使用中文",
            type=MemoryType.PROJECT,
        ),
    )

    dedup_json = json.dumps(
        {
            "duplicates": [
                {
                    "keep": "project-用户偏好.md",
                    "merge_from": ["project-中文偏好.md"],
                    "delete": ["project-中文偏好.md"],
                }
            ]
        },
        ensure_ascii=False,
    )
    # 升层跳过（<3）、时效空
    empty_cons = json.dumps({"consolidated_memories": []}, ensure_ascii=False)
    empty_fresh = json.dumps({"decisions": []}, ensure_ascii=False)

    provider = _scripted_provider(dedup_json, empty_cons, empty_fresh)

    config = AutoDreamConfig(
        enabled=True,
        batch_size=50,
        enable_dedup=True,
        enable_consolidation=True,
        enable_archival=True,
    )

    engine = AutoDreamEngine(
        llm_provider=provider,
        memory_manager=mm,
        config=config,
    )

    log = _make_log()
    await engine.run(log)

    assert log.status == "success"
    assert log.merged == 1

    # 验证合并后目标文件包含源内容
    merged_entry = mm.store.read_file("project-用户偏好.md")
    assert "中文偏好" in merged_entry.content or "用户喜欢使用中文沟通" in merged_entry.content


@pytest.mark.asyncio
async def test_freshness_rule_fallback(temp_memory_manager):
    """LLM 失败时，规则兜底自动归档超期记忆"""
    mm = temp_memory_manager

    # 创建 3 条旧记忆（created_at 设为 100 天前）
    for i in range(3):
        entry = MemoryEntry(
            id=f"old_task_{i}.md",
            filename=f"old_task_{i}.md",
            name=f"旧任务 {i}",
            description="过期的临时任务",
            type=MemoryType.PROJECT,
            content=f"很早以前的临时任务记录 {i}",
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        mm.store.write_file(entry)

    # 3 次调用：去重、升层、时效（USER batch 没有，所以只有 1 个 batch）
    provider = MockLLMProvider()
    provider.inject_error(1, RuntimeError("API 超时"))
    provider.inject_error(2, RuntimeError("API 超时"))
    provider.inject_error(3, RuntimeError("API 超时"))

    config = AutoDreamConfig(
        enabled=True,
        batch_size=50,
        max_consolidated_per_run=5,
        archive_after_days=0,  # 任何正数天龄都归档
        enable_dedup=True,
        enable_consolidation=True,
        enable_archival=True,
    )

    engine = AutoDreamEngine(
        llm_provider=provider,
        memory_manager=mm,
        config=config,
    )

    log = _make_log()
    await engine.run(log)

    assert log.status == "success"
    # 所有记忆都应被规则兜底归档（因为 archive_after_days=0）
    assert log.archived > 0


@pytest.mark.asyncio
async def test_consolidate_respects_max_limit(temp_memory_manager):
    """升层数量受 max_consolidated_per_run 限制"""
    mm = temp_memory_manager

    # 创建 3 条 PROJECT 记忆（单一 batch）
    for i in range(3):
        mm.save(
            content=f"修复了内存泄漏问题 #{i}",
            mem_type=MemoryType.PROJECT,
            metadata=MemoryMetadata(
                name=f"内存泄漏修复 {i}",
                description=f"修复了第 {i} 个内存泄漏",
                type=MemoryType.PROJECT,
            ),
        )

    consolidate_json = json.dumps(
        {
            "consolidated_memories": [
                {"name": "归纳 A", "content": "...", "source_filenames": []},
                {"name": "归纳 B", "content": "...", "source_filenames": []},
            ]
        },
        ensure_ascii=False,
    )
    empty_dedup = json.dumps({"duplicates": []}, ensure_ascii=False)
    empty_fresh = json.dumps({"decisions": []}, ensure_ascii=False)

    provider = _scripted_provider(empty_dedup, consolidate_json, empty_fresh)

    config = AutoDreamConfig(
        enabled=True,
        batch_size=50,
        max_consolidated_per_run=1,
        enable_dedup=True,
        enable_consolidation=True,
        enable_archival=True,
    )

    engine = AutoDreamEngine(
        llm_provider=provider,
        memory_manager=mm,
        config=config,
    )

    log = _make_log()
    await engine.run(log)

    assert log.status == "success"
    assert log.consolidated == 1  # 被限制为 1
