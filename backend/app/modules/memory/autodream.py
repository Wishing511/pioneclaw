"""
AutoDream Engine - 记忆自动整理核心

定时自动调用 LLM 对记忆库进行批量整理：去重、升层、归档。
"""

import asyncio
import json
import logging
import os
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.models.autodream import AutoDreamConfig, AutoDreamLog
from app.modules.memory.autodream_prompts import (
    CONSOLIDATION_PROMPT,
    CONSOLIDATION_SYSTEM_PROMPT,
    DEDUPLICATION_PROMPT,
    DEDUPLICATION_SYSTEM_PROMPT,
    FRESHNESS_PROMPT,
    FRESHNESS_SYSTEM_PROMPT,
)
from app.modules.memory.models import ManifestEntry, MemoryEntry, MemoryMetadata, MemoryType

logger = logging.getLogger(__name__)


@dataclass
class ChangeSet:
    """整理操作集合"""

    merges: list[dict] = field(default_factory=list)
    consolidations: list[dict] = field(default_factory=list)
    archives: list[str] = field(default_factory=list)
    deletes: list[str] = field(default_factory=list)


class AutoDreamEngine:
    """记忆自动整理引擎

    用法:
        engine = AutoDreamEngine(llm_provider, memory_manager, config)
        await engine.run(log)  # log 由调用方创建并管理 DB 事务
    """

    _lock = asyncio.Lock()

    def __init__(
        self,
        llm_provider,
        memory_manager,
        config: AutoDreamConfig,
    ):
        self.llm_provider = llm_provider
        self.memory_manager = memory_manager
        self.config = config

    # ═══════════════════════════════════════════════════════════════
    # 主入口
    # ═══════════════════════════════════════════════════════════════

    async def run(self, log: AutoDreamLog) -> None:
        """执行一次完整的记忆整理流程

        Args:
            log: 已创建的运行日志对象（状态应为 running），
                 方法内会更新该对象字段，调用方负责 commit。
        """
        if not self.config.enabled:
            logger.info("[AutoDream] 配置 disabled，跳过本次整理")
            log.status = "skipped"
            log.duration_seconds = 0.0
            log.details = '{"reason": "config.enabled=false"}'
            return

        async with AutoDreamEngine._lock:
            start_time = datetime.now(timezone.utc)
            details_actions: list[dict] = []

            try:
                # 1. 收集记忆
                memories = await self._gather_memories()
                log.total_memories = len(memories)
                logger.info(f"[AutoDream] 收集到 {len(memories)} 条记忆")

                # 空记忆库直接返回
                if not memories:
                    log.status = "success"
                    log.duration_seconds = (
                        datetime.now(timezone.utc) - start_time
                    ).total_seconds()
                    log.details = '{"actions": [], "reason": "empty memory"}'
                    logger.info("[AutoDream] 记忆库为空，跳过整理")
                    return

                # 2. 批次分组
                batches = self._group_into_batches(memories)
                logger.info(f"[AutoDream] 分成 {len(batches)} 个批次")

                changes = ChangeSet()

                # 3. 去重
                if self.config.enable_dedup:
                    for batch in batches:
                        dup_result = await self._deduplicate_batch(batch, log)
                        changes.merges.extend(dup_result)
                        log.duplicates_found += len(dup_result)
                        for d in dup_result:
                            details_actions.append(
                                {
                                    "action": "merge",
                                    "keep": d.get("keep"),
                                    "from": d.get("merge_from", []),
                                }
                            )

                # 4. 升层
                if self.config.enable_consolidation:
                    for batch in batches:
                        if len(batch) >= 3:
                            cons_result = await self._consolidate_batch(batch, log)
                            changes.consolidations.extend(cons_result)
                            log.consolidated += len(cons_result)
                            for c in cons_result:
                                details_actions.append(
                                    {
                                        "action": "consolidate",
                                        "name": c.get("name"),
                                        "sources": c.get("source_filenames", []),
                                    }
                                )

                # 5. 时效评估
                if self.config.enable_archival:
                    fresh_result = await self._evaluate_freshness(memories, log)
                    changes.archives.extend(fresh_result["archives"])
                    changes.deletes.extend(fresh_result["deletes"])
                    log.archived = len(changes.archives)
                    log.deleted = len(changes.deletes)
                    for a in changes.archives:
                        details_actions.append({"action": "archive", "filename": a})
                    for d in changes.deletes:
                        details_actions.append({"action": "delete", "filename": d})

                # 6. 执行物理变更
                await self._apply_changes(changes, log)

                # 7. 重建索引
                await self._rebuild_index()

                log.status = "success"
                log.duration_seconds = (
                    datetime.now(timezone.utc) - start_time
                ).total_seconds()
                log.details = json.dumps(
                    {"actions": details_actions}, ensure_ascii=False
                )
                logger.info(
                    f"[AutoDream] 整理完成: {log.total_memories} 记忆, "
                    f"{log.duplicates_found} 重复, {log.merged} 合并, "
                    f"{log.consolidated} 升层, {log.archived} 归档, {log.deleted} 删除, "
                    f"耗时 {log.duration_seconds:.1f}s"
                )

            except Exception as e:
                logger.error(f"[AutoDream] 整理失败: {e}", exc_info=True)
                log.status = "failed"
                log.error_message = str(e)
                log.duration_seconds = (
                    datetime.now(timezone.utc) - start_time
                ).total_seconds()
                log.details = json.dumps(
                    {"actions": details_actions, "error": str(e)}, ensure_ascii=False
                )

    # ═══════════════════════════════════════════════════════════════
    # 记忆收集与分组
    # ═══════════════════════════════════════════════════════════════

    async def _gather_memories(self) -> list[MemoryEntry]:
        """读取全部记忆文件"""
        return await asyncio.to_thread(self.memory_manager.store.get_all_files)

    def _group_into_batches(self, memories: list[MemoryEntry]) -> list[list[MemoryEntry]]:
        """按 type 分组，每组按 updated_at 倒序，每批最多 batch_size"""
        by_type: dict[MemoryType, list[MemoryEntry]] = {}
        for m in memories:
            by_type.setdefault(m.type, []).append(m)

        batches: list[list[MemoryEntry]] = []
        for entries in by_type.values():
            entries.sort(key=lambda e: e.updated_at, reverse=True)
            for i in range(0, len(entries), self.config.batch_size):
                batches.append(entries[i : i + self.config.batch_size])
        return batches

    # ═══════════════════════════════════════════════════════════════
    # LLM 调用封装
    # ═══════════════════════════════════════════════════════════════

    async def _call_llm(
        self, prompt: str, system_prompt: str | None = None
    ) -> dict[str, Any]:
        """统一 LLM 调用

        Returns:
            {"content": str, "error": str|None, "llm_calls": int,
             "tokens_in": int, "tokens_out": int}
        """
        messages: list[dict] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        content = ""
        error: str | None = None
        llm_calls = 0
        tokens_in = 0
        tokens_out = 0

        try:
            async for chunk in self.llm_provider.chat_stream(messages):
                if "error" in chunk:
                    error = chunk["error"]
                    break
                if "content" in chunk:
                    content += chunk["content"]

            llm_calls = 1
            tokens_in = getattr(self.llm_provider, "last_input_tokens", 0)
            tokens_out = getattr(self.llm_provider, "last_output_tokens", 0)
        except Exception as e:
            error = str(e)
            logger.error(f"[AutoDream] LLM 调用异常: {e}")

        return {
            "content": content,
            "error": error,
            "llm_calls": llm_calls,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
        }

    # ═══════════════════════════════════════════════════════════════
    # 去重 Pipeline
    # ═══════════════════════════════════════════════════════════════

    async def _deduplicate_batch(
        self, batch: list[MemoryEntry], log: AutoDreamLog
    ) -> list[dict]:
        """对单批次记忆进行语义去重

        Returns:
            [{"keep": str, "merge_from": [str], "delete": [str]}]
        """
        if len(batch) < 2:
            return []

        entries_json = json.dumps(
            [
                {
                    "filename": e.filename,
                    "name": e.name,
                    "description": e.description,
                    "type": e.type.value,
                    "content": e.content[:500],
                    "created_at": e.created_at.isoformat() if e.created_at else "",
                }
                for e in batch
            ],
            ensure_ascii=False,
            indent=2,
        )

        prompt = DEDUPLICATION_PROMPT.format(entries=entries_json)
        result = await self._call_llm(prompt, DEDUPLICATION_SYSTEM_PROMPT)

        log.llm_calls += result["llm_calls"]
        log.llm_tokens_in += result["tokens_in"]
        log.llm_tokens_out += result["tokens_out"]

        if result["error"]:
            logger.warning(f"[AutoDream] 去重 LLM 失败: {result['error']}")
            return []

        try:
            data = json.loads(result["content"])
            duplicates = data.get("duplicates", [])
            # 验证 filename 合法性
            valid: list[dict] = []
            filenames = {e.filename for e in batch}
            for dup in duplicates:
                keep = dup.get("keep")
                if keep in filenames:
                    valid.append(dup)
            return valid
        except json.JSONDecodeError as e:
            logger.warning(f"[AutoDream] 去重 JSON 解析失败: {e}")
            return []

    # ═══════════════════════════════════════════════════════════════
    # 升层 Pipeline
    # ═══════════════════════════════════════════════════════════════

    async def _consolidate_batch(
        self, batch: list[MemoryEntry], log: AutoDreamLog
    ) -> list[dict]:
        """对单批次记忆进行升层归纳

        Returns:
            [{"name": str, "description": str, "content": str, "source_filenames": [str]}]
        """
        if len(batch) < 3:
            return []

        # 已达上限则跳过
        if log.consolidated >= self.config.max_consolidated_per_run:
            return []

        entries_json = json.dumps(
            [
                {
                    "filename": e.filename,
                    "name": e.name,
                    "description": e.description,
                    "type": e.type.value,
                    "content": e.content[:300],
                }
                for e in batch
            ],
            ensure_ascii=False,
            indent=2,
        )

        prompt = CONSOLIDATION_PROMPT.format(entries=entries_json)
        result = await self._call_llm(prompt, CONSOLIDATION_SYSTEM_PROMPT)

        log.llm_calls += result["llm_calls"]
        log.llm_tokens_in += result["tokens_in"]
        log.llm_tokens_out += result["tokens_out"]

        if result["error"]:
            logger.warning(f"[AutoDream] 升层 LLM 失败: {result['error']}")
            return []

        try:
            data = json.loads(result["content"])
            memories = data.get("consolidated_memories", [])
            # 限制数量
            remaining = self.config.max_consolidated_per_run - log.consolidated
            return memories[:remaining]
        except json.JSONDecodeError as e:
            logger.warning(f"[AutoDream] 升层 JSON 解析失败: {e}")
            return []

    # ═══════════════════════════════════════════════════════════════
    # 时效评估 Pipeline
    # ═══════════════════════════════════════════════════════════════

    async def _evaluate_freshness(
        self, entries: list[MemoryEntry], log: AutoDreamLog
    ) -> dict[str, list[str]]:
        """评估记忆时效价值

        consolidated 记忆（name 以 consolidated_ 开头）不参与评估，
        避免删除归纳成果。

        Returns:
            {"archives": [filename], "deletes": [filename]}
        """
        # 过滤掉 consolidated 记忆
        eligible = [e for e in entries if not e.name.startswith("consolidated_")]
        if not eligible:
            return {"archives": [], "deletes": []}

        entries_json = json.dumps(
            [
                {
                    "filename": e.filename,
                    "name": e.name,
                    "type": e.type.value,
                    "created_at": e.created_at.isoformat() if e.created_at else "",
                }
                for e in eligible
            ],
            ensure_ascii=False,
            indent=2,
        )

        prompt = FRESHNESS_PROMPT.format(
            entries=entries_json,
            archive_after_days=self.config.archive_after_days,
        )
        result = await self._call_llm(prompt, FRESHNESS_SYSTEM_PROMPT)

        log.llm_calls += result["llm_calls"]
        log.llm_tokens_in += result["tokens_in"]
        log.llm_tokens_out += result["tokens_out"]

        archives: list[str] = []
        deletes: list[str] = []

        if result["error"]:
            logger.warning(f"[AutoDream] 时效评估 LLM 失败: {result['error']}")
            # 降级：基于规则的兜底
            now = datetime.now(timezone.utc)
            for e in eligible:
                created = e.created_at
                if created and created.tzinfo is None:
                    created = created.replace(tzinfo=timezone.utc)
                age_days = (now - created).days if created else 0
                if age_days > self.config.archive_after_days:
                    archives.append(e.filename)
            return {"archives": archives, "deletes": []}

        try:
            data = json.loads(result["content"])
            filenames = {e.filename for e in eligible}
            for decision in data.get("decisions", []):
                filename = decision.get("filename")
                action = decision.get("action")
                if filename not in filenames:
                    continue
                if action == "archive":
                    archives.append(filename)
                elif action == "delete":
                    # 如果 delete_after_days 为 None，降级为 archive
                    if self.config.delete_after_days is None:
                        archives.append(filename)
                    else:
                        deletes.append(filename)
        except json.JSONDecodeError as e:
            logger.warning(f"[AutoDream] 时效 JSON 解析失败: {e}")

        return {"archives": archives, "deletes": deletes}

    # ═══════════════════════════════════════════════════════════════
    # 变更执行
    # ═══════════════════════════════════════════════════════════════

    async def _apply_changes(self, changes: ChangeSet, log: AutoDreamLog) -> None:
        """将所有决策转化为物理文件操作"""
        # 合并
        for merge in changes.merges:
            keep = merge.get("keep")
            merge_from = merge.get("merge_from", [])
            for src in merge_from:
                await self._merge_memory(src, keep)
            log.merged += len(merge_from)

        # 归档
        for filename in changes.archives:
            await self._archive_memory(filename)

        # 删除
        for filename in changes.deletes:
            await self._delete_memory(filename)

        # 升层（保存新记忆）
        for cons in changes.consolidations:
            await self._save_consolidated(cons)

    async def _merge_memory(self, src_filename: str, dst_filename: str) -> None:
        """将 src 的内容合并到 dst，然后删除 src"""
        try:
            src_entry = await asyncio.to_thread(
                self.memory_manager.store.read_file, src_filename
            )
            dst_entry = await asyncio.to_thread(
                self.memory_manager.store.read_file, dst_filename
            )
            new_content = (
                dst_entry.content
                + "\n\n---\n\n【合并来源: "
                + src_entry.name
                + "】\n"
                + src_entry.content
            )
            await asyncio.to_thread(
                self.memory_manager.update,
                dst_filename,
                new_content,
                MemoryMetadata(
                    name=dst_entry.name,
                    description=dst_entry.description,
                    type=dst_entry.type,
                ),
            )
            await asyncio.to_thread(self.memory_manager.delete, src_filename)
            logger.info(f"[AutoDream] 合并: {src_filename} -> {dst_filename}")
        except Exception as e:
            logger.warning(f"[AutoDream] 合并失败 {src_filename} -> {dst_filename}: {e}")

    async def _archive_memory(self, filename: str) -> None:
        """将记忆移动到 archive/YYYY-MM/ 目录"""
        try:
            memory_root = self.memory_manager.store.memory_root
            src = os.path.join(memory_root, filename)
            if not os.path.exists(src):
                return

            now = datetime.now(timezone.utc)
            archive_dir = os.path.join(memory_root, "archive", now.strftime("%Y-%m"))
            os.makedirs(archive_dir, exist_ok=True)

            dst = os.path.join(archive_dir, filename)
            await asyncio.to_thread(shutil.move, src, dst)
            logger.info(f"[AutoDream] 归档: {filename}")
        except Exception as e:
            logger.warning(f"[AutoDream] 归档失败 {filename}: {e}")

    async def _delete_memory(self, filename: str) -> None:
        """删除记忆文件"""
        try:
            await asyncio.to_thread(self.memory_manager.delete, filename)
            logger.info(f"[AutoDream] 删除: {filename}")
        except Exception as e:
            logger.warning(f"[AutoDream] 删除失败 {filename}: {e}")

    async def _save_consolidated(self, cons: dict) -> None:
        """保存升层生成的新记忆"""
        try:
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            name = cons.get("name", "归纳记忆")

            mem_type_str = cons.get("type", "project")
            try:
                mem_type = MemoryType(mem_type_str)
            except ValueError:
                mem_type = MemoryType.PROJECT

            content = cons.get("content", "")
            source_filenames = cons.get("source_filenames", [])
            if source_filenames:
                content += "\n\n---\n\n**来源记忆**: " + ", ".join(source_filenames)

            metadata = MemoryMetadata(
                name=name,
                description=cons.get("description", ""),
                type=mem_type,
            )

            result = await asyncio.to_thread(
                self.memory_manager.save,
                content,
                mem_type,
                metadata,
            )

            # save() 返回 MemoryResponse，检查是否成功
            if hasattr(result, "success") and result.success:
                logger.info(f"[AutoDream] 升层保存: {name}")
            else:
                logger.warning(f"[AutoDream] 升层保存失败: {result}")
        except Exception as e:
            logger.warning(f"[AutoDream] 升层保存异常: {e}")

    # ═══════════════════════════════════════════════════════════════
    # 索引重建
    # ═══════════════════════════════════════════════════════════════

    async def _rebuild_index(self) -> None:
        """重建 MEMORY.md"""
        try:
            entries = await asyncio.to_thread(self.memory_manager.store.get_all_files)
            manifest = [
                ManifestEntry(
                    filename=e.filename,
                    name=e.name,
                    description=e.description,
                    type=e.type,
                )
                for e in entries
            ]
            await asyncio.to_thread(
                self.memory_manager.index.rebuild_index, manifest
            )
            logger.info(f"[AutoDream] 索引重建完成: {len(manifest)} 条目")
        except Exception as e:
            logger.warning(f"[AutoDream] 索引重建失败: {e}")
