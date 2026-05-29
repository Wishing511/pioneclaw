"""Main public API — the single entry point for Agents to interact with memory."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Callable, List, Optional, Union

from .errors import (
    FileNotFoundInMemoryError,
    InvalidMemoryTypeError,
    MemorySystemError,
    MissingRequiredFieldError,
)
from .memory_extractor import DUPLICATE_SIMILARITY_THRESHOLD, MemoryExtractor, _text_overlap_ratio
from .memory_index import MemoryIndex
from .memory_ranker import MemoryRanker
from .memory_store import MemoryStore, generate_filename
from .models import (
    ConversationContext,
    ExtractionResult,
    ListOptions,
    MemoryAttachment,
    MemoryEntry,
    MemoryFailure,
    MemoryMetadata,
    MemoryResponse,
    MemoryResult,
    MemoryType,
    RecallOptions,
    SearchOptions,
)

logger = logging.getLogger(__name__)


class MemoryManage:
    """Facade for the AI Agent Memory System.

    The Agent interacts exclusively through this interface. All internal
    modules (MemoryStore, MemoryIndex, MemoryRanker, MemoryExtractor) are
    transparent to the caller.

    Usage:
        mgr = MemoryManage(
            memory_root="~/.agent/memory",
            llm_query_fn=my_llm_call,
            extract_agent_fn=my_agent_runner,
        )
        attachments = mgr.recall("help me write an API endpoint")
    """

    def __init__(
        self,
        memory_root: str,
        llm_query_fn: Optional[Callable[[str], str]] = None,
        extract_agent_fn: Optional[Callable[[str, str], str]] = None,
        turns_between_extraction: int = 5,
    ):
        self._llm_query = llm_query_fn
        self.store = MemoryStore(memory_root)
        self.index = MemoryIndex(memory_root)

        # 如果 MEMORY.md 不存在但目录下有 .md 文件，自动从游离文件重建索引
        if not os.path.exists(self.index.index_path):
            manifest = self.store.scan_files()
            if manifest:
                try:
                    self.index.rebuild_index(manifest)
                    logger.info(
                        "MEMORY.md not found, rebuilt from %d orphan .md files",
                        len(manifest),
                    )
                except Exception as e:
                    logger.warning("游离记忆文件索引重建失败: %s", e)

        self.ranker = MemoryRanker(self.store, llm_query_fn)
        self.extractor = MemoryExtractor(
            self.store,
            self.index,
            extract_agent_fn or (lambda _sys, _usr: ""),
            turns_between_extraction,
            save_callback=lambda content: self.save(content, "", None, True),
        )

    # ═══════════════════════════════════════════════════════════════
    # recall — auto-retrieve relevant memories
    # ═══════════════════════════════════════════════════════════════

    def recall(
        self, query: str, options: Optional[RecallOptions] = None
    ) -> List[MemoryAttachment]:
        """Retrieve memories relevant to the given query."""
        opts = options or RecallOptions()
        try:
            manifest = self.store.scan_files()
            if not manifest:
                return []

            ranked = self.ranker.rank(
                manifest,
                query,
                exclude_types=opts.exclude_types,
                include_stale=opts.include_stale,
            )

            ranked = [
                r
                for r in ranked
                if r.relevance_score >= opts.min_relevance
            ]
            ranked = ranked[: opts.max_results]

            attachments: List[MemoryAttachment] = []
            surfaced = set()
            for r in ranked:
                if r.filename in surfaced:
                    continue
                surfaced.add(r.filename)
                try:
                    entry = self.store.read_file(r.filename)
                    attachments.append(
                        MemoryAttachment(
                            entry=entry,
                            relevance_score=r.relevance_score,
                            surfaced_at=datetime.now(timezone.utc),
                        )
                    )
                except Exception as e:
                    logger.warning("读取记忆文件 %s 失败: %s", r.filename, e)
                    continue

            return attachments

        except Exception as e:
            logger.error("召回失败: %s", e)
            return []

    # ═══════════════════════════════════════════════════════════════
    # save — create a new memory entry
    # ═══════════════════════════════════════════════════════════════

    def save(
        self,
        content: str,
        mem_type: Union[MemoryType, str],
        metadata: Optional[MemoryMetadata] = None,
        upsert: bool = False,
    ) -> MemoryResponse:
        """Save a new memory entry.

        When upsert=True and a duplicate is detected, the existing entry is
        updated with the new content instead of returning the old entry.

        LLM-driven in one shot: _save_decision() classifies type, generates
        name/description, and picks the target file (new or existing).
        Falls back to cheap heuristics when LLM is unavailable.
        """
        try:
            if not content or not content.strip():
                return MemoryFailure(
                    False,
                    MissingRequiredFieldError("content", "(new)")
                )

            stripped = content.strip()

            # ── 1. 一次 LLM 完成分类 + 摘要 + 文件选择 ────────────────
            if self._llm_query:
                all_entries = self.store.scan_files()
                decision = self._save_decision(stripped, all_entries)
                if decision:
                    # 类型（用户未传时才用 LLM 结果）
                    if isinstance(mem_type, str) and not mem_type:
                        try:
                            mem_type = MemoryType(decision["type"])
                        except ValueError:
                            mem_type = MemoryType.USER
                    elif isinstance(mem_type, str):
                        try:
                            mem_type = MemoryType(mem_type)
                        except ValueError:
                            return MemoryFailure(False, InvalidMemoryTypeError(mem_type))

                    meta = metadata or MemoryMetadata(
                        name="", description="", type=mem_type
                    )

                    # name/description（用户未传时才用 LLM 结果）
                    if not meta.name:
                        meta.name = decision["name"] or stripped[:20]
                    if not meta.description:
                        meta.description = decision["description"] or stripped[:50]

                    logger.info(
                        "save() decision: type=%s name=%r desc=%r target=%s",
                        mem_type.value,
                        meta.name,
                        meta.description,
                        decision["target_filename"],
                    )

                    # 目标文件是已有文件 → 合并（需 upsert=True）
                    target = decision["target_filename"]
                    if target.upper() != "NEW":
                        try:
                            target_entry = self.store.read_file(target)
                            if not upsert:
                                # 调用方禁止更新，直接返回已有条目
                                return MemoryResult(True, target_entry)
                            merged = self._merge_into_existing(target_entry, stripped)
                            self.store.write_file(merged)
                            try:
                                self.index.update_entry(target, merged.description)
                            except Exception as e:
                                logger.warning("索引更新失败: %s", e)
                            return MemoryResult(True, merged)
                        except Exception as e:
                            logger.warning(
                                "LLM 决策合并失败 %s，fallback 到新建: %s", target, e
                            )

                    # 走到这里表示 target == "NEW" 或合并失败 → 继续新建流程
                    # 但跳过下面的 cheap 去重，因为 LLM 已经判断过
                    return self._create_new_entry(stripped, mem_type, meta)

            # ── 2. Fallback：无 LLM 时的廉价本地逻辑 ──────────────────
            if isinstance(mem_type, str) and not mem_type:
                mem_type = MemoryType.USER
            elif isinstance(mem_type, str):
                try:
                    mem_type = MemoryType(mem_type)
                except ValueError:
                    return MemoryFailure(False, InvalidMemoryTypeError(mem_type))

            meta = metadata or MemoryMetadata(
                name="", description="", type=mem_type
            )
            if not meta.name or not meta.description:
                llm_name, llm_desc = self._summarize_content(stripped, mem_type)
                if not meta.name:
                    meta.name = llm_name or stripped[:20]
                if not meta.description:
                    meta.description = llm_desc or stripped[:50]

            # cheap 去重（slug + description 匹配，无 LLM）
            existing_manifest = self.store.scan_files()
            dup = self._find_duplicate(meta, existing_manifest)
            if dup:
                if upsert:
                    return self.update(dup.filename, content, meta)
                return MemoryResult(True, dup)

            return self._create_new_entry(stripped, mem_type, meta)

        except MemorySystemError as e:
            return MemoryFailure(False, e)
        except Exception as e:
            logger.error("保存记忆失败: %s", e)
            return MemoryFailure(False, e)

    def _create_new_entry(
        self,
        content: str,
        mem_type: MemoryType,
        meta: MemoryMetadata,
    ) -> MemoryResponse:
        """新建记忆文件的公共逻辑。"""
        filename = generate_filename(mem_type, meta.name)
        now = datetime.now(timezone.utc)
        entry = MemoryEntry(
            id=filename,
            filename=filename,
            name=meta.name,
            description=meta.description,
            type=mem_type,
            content=content,
            created_at=now,
            updated_at=now,
            freshness="今天",
            is_stale=False,
            tags=meta.tags or [],
        )

        self.store.write_file(entry)

        try:
            self.index.add_entry(filename, meta.description)
        except Exception as e:
            logger.error("索引更新失败，回滚文件写入: %s", e)
            self.store.delete_file(filename)
            return MemoryFailure(False, e)

        return MemoryResult(True, entry)

    # ═══════════════════════════════════════════════════════════════
    # update — modify an existing memory entry
    # ═══════════════════════════════════════════════════════════════

    def update(
        self,
        path: str,
        content: str,
        metadata: Optional[MemoryMetadata] = None,
    ) -> MemoryResponse:
        """Update an existing memory entry."""
        try:
            existing = self.store.read_file(path)

            existing.content = content
            existing.updated_at = datetime.now(timezone.utc)

            # 内容变更时，用 LLM 重新生成摘要
            if metadata:
                if metadata.name:
                    existing.name = metadata.name
                if metadata.description:
                    existing.description = metadata.description
                if metadata.tags is not None:
                    existing.tags = metadata.tags

            # 未显式提供 name/description 时，用 LLM 生成
            if not metadata or not metadata.name or not metadata.description:
                llm_name, llm_desc = self._summarize_content(
                    content.strip(), existing.type
                )
                if not metadata or not metadata.name:
                    existing.name = llm_name or content.strip()[:20]
                if not metadata or not metadata.description:
                    existing.description = llm_desc or content.strip()[:50]

            self.store.write_file(existing)

            try:
                self.index.update_entry(path, existing.description)
            except Exception as e:
                logger.warning("索引更新失败: %s", e)

            return MemoryResult(True, existing)

        except FileNotFoundInMemoryError as e:
            return MemoryFailure(False, e)
        except MemorySystemError as e:
            return MemoryFailure(False, e)
        except Exception as e:
            logger.error("更新记忆失败: %s", e)
            return MemoryFailure(False, e)

    # ═══════════════════════════════════════════════════════════════
    # delete — remove a memory entry
    # ═══════════════════════════════════════════════════════════════

    def delete(self, path: str) -> MemoryResponse:
        """Delete a memory entry and its index reference."""
        try:
            self.store.read_file(path)
            self.store.delete_file(path)

            try:
                self.index.remove_entry(path)
            except Exception as e:
                logger.error("索引删除失败，重建索引: %s", e)
                self.index.rebuild_index(self.store.scan_files())

            return MemoryResult(True, True)

        except FileNotFoundInMemoryError as e:
            return MemoryFailure(False, e)
        except MemorySystemError as e:
            return MemoryFailure(False, e)
        except Exception as e:
            logger.error("删除记忆失败: %s", e)
            return MemoryFailure(False, e)

    # ═══════════════════════════════════════════════════════════════
    # list — enumerate memory entries
    # ═══════════════════════════════════════════════════════════════

    def list(
        self, options: Optional[ListOptions] = None
    ) -> MemoryResponse:
        """List all memory entries with optional filtering."""
        try:
            entries = self.store.get_all_files()
            opts = options or ListOptions()

            if opts.type:
                types = (
                    [opts.type]
                    if isinstance(opts.type, MemoryType)
                    else opts.type
                )
                entries = [e for e in entries if e.type in types]

            reverse = opts.order == "desc"
            if opts.sort_by == "name":
                entries.sort(key=lambda e: e.name, reverse=reverse)
            elif opts.sort_by == "createdAt":
                entries.sort(key=lambda e: e.created_at, reverse=reverse)
            else:
                entries.sort(key=lambda e: e.updated_at, reverse=reverse)

            if opts.offset > 0:
                entries = entries[opts.offset:]
            if opts.limit is not None:
                entries = entries[: opts.limit]

            return MemoryResult(True, entries)

        except Exception as e:
            logger.error("列出记忆失败: %s", e)
            return MemoryResult(True, [])

    # ═══════════════════════════════════════════════════════════════
    # search — full-text keyword search
    # ═══════════════════════════════════════════════════════════════

    def search(
        self, keyword: str, options: Optional[SearchOptions] = None
    ) -> MemoryResponse:
        """Full-text search across memory files."""
        opts = options or SearchOptions()
        try:
            matches = self.store.search_fulltext(
                keyword,
                case_sensitive=opts.case_sensitive,
            )

            entries: List[MemoryEntry] = []
            for fname in matches:
                try:
                    entry = self.store.read_file(fname)
                    if opts.type:
                        types = (
                            [opts.type]
                            if isinstance(opts.type, MemoryType)
                            else opts.type
                        )
                        if entry.type in types:
                            entries.append(entry)
                    else:
                        entries.append(entry)
                except Exception:
                    continue

            if opts.limit is not None:
                entries = entries[: opts.limit]

            return MemoryResult(True, entries)

        except Exception as e:
            logger.error("搜索记忆失败: %s", e)
            return MemoryResult(True, [])

    # ═══════════════════════════════════════════════════════════════
    # auto_extract — trigger background memory extraction
    # ═══════════════════════════════════════════════════════════════

    def auto_extract(self, context: ConversationContext) -> ExtractionResult:
        """Trigger background memory extraction after a conversation turn."""
        try:
            return self.extractor.extract(context)
        except Exception as e:
            logger.error("自动提取失败: %s", e)
            return ExtractionResult(
                extracted=0,
                skipped=True,
                error=str(e),
            )

    # ═══════════════════════════════════════════════════════════════
    # format_for_injection — format recalled memories for system prompt
    # ═══════════════════════════════════════════════════════════════

    def format_for_injection(
        self, attachments: List[MemoryAttachment]
    ) -> str:
        """Format recalled memories as a system-reminder injection text."""
        if not attachments:
            return ""

        lines = [
            "<system-reminder>",
            "以下是与当前对话可能相关的记忆:",
            "",
        ]

        for i, att in enumerate(attachments):
            entry = att.entry
            stale_warning = ""
            if entry.is_stale:
                stale_warning = (
                    " ⚠ 注意：此记忆已超过 30 天未更新，可能不再准确。"
                )

            lines.append(
                f"[记忆 {i + 1}] "
                f"(类型: {entry.type.value}, "
                f"更新于: {entry.freshness}, "
                f"相关度: {att.relevance_score:.2f})"
            )
            lines.append(f"文件: {entry.filename}")
            lines.append(f"描述: {entry.description}")
            lines.append(f"内容摘要: {entry.content[:300]}")
            if stale_warning:
                lines.append(stale_warning)
            lines.append("")

        lines.append("</system-reminder>")
        return "\n".join(lines)

    # ═══════════════════════════════════════════════════════════════
    # internal helpers
    # ═══════════════════════════════════════════════════════════════

    def _save_decision(
        self,
        content: str,
        existing_entries: list,
    ) -> Optional[dict]:
        """一次 LLM 调用完成分类、摘要、文件选择。

        Returns dict with keys:
            type (str): user|feedback|project|reference
            name (str): 提炼后的主题词
            description (str): ≤30 字的核心概括
            target_filename (str): 要合并的已有文件名，或 "NEW"
            reason (str): 决策理由
        Returns None on failure.
        """
        if not self._llm_query:
            return None

        # 构建已有记忆索引（只取 filename + name + description）
        index_lines = []
        for e in existing_entries:
            index_lines.append(f"- {e.filename}: {e.name} | {e.description}")
        index_text = "\n".join(index_lines) if index_lines else "(尚无记忆)"

        prompt = (
            "你是一个记忆归档助手。请分析以下新记忆内容，结合已有记忆列表，"
            "做出完整的归档决策。\n\n"
            f"新记忆内容:\n{content}\n\n"
            f"已有记忆文件列表（文件名: 名称 | 描述）:\n{index_text}\n\n"
            "请返回 JSON 格式（不要任何额外文字、markdown 标记、代码块）:\n"
            "{\n"
            '  "type": "user|feedback|project|reference",\n'
            '  "name": "提炼后的主题词（≤20字，不要带类型前缀）",\n'
            '  "description": "极度精炼的一句话概括（≤30字），只写核心主题",\n'
            '  "target_filename": "要合并的已有文件名，或 NEW",\n'
            '  "reason": "一句话说明决策理由"\n'
            "}\n\n"
            "规则:\n"
            "1. type: user=用户偏好/约定/身份; feedback=评价/建议/投诉; "
            "project=技术决策/架构; reference=参考资料/文档\n"
            "2. name: 只提炼核心主题词，不要复述原文，不要带类型前缀\n"
            "3. description: 极度精炼，≤30字，只写核心主题，不要复述细节\n"
            "4. target_filename: 如果新记忆与某条已有记忆是同一主题/同一事实，"
            "返回该文件名；如果是全新内容，返回 NEW\n"
            "5. 不要返回任何 JSON 之外的文字"
        )

        try:
            response = self._llm_query(prompt)
            if not response:
                return None

            # 清理可能的 markdown 代码块
            text = response.strip()
            if text.startswith("```"):
                text = text.strip("`").strip()
                if text.lower().startswith("json"):
                    text = text[4:].strip()

            decision = json.loads(text)
            if not isinstance(decision, dict):
                return None

            # 截断 + 清理
            decision["name"] = str(decision.get("name", "")).strip()[:20]
            decision["description"] = str(decision.get("description", "")).strip()[:50]
            decision["target_filename"] = str(decision.get("target_filename", "NEW")).strip().upper()
            decision["type"] = str(decision.get("type", "user")).strip().lower()

            logger.info(
                "_save_decision: type=%s name=%r target=%s reason=%s",
                decision["type"],
                decision["name"],
                decision["target_filename"],
                decision.get("reason", ""),
            )
            return decision
        except Exception as e:
            logger.warning("_save_decision failed: %s", e)
            return None

    def _merge_into_existing(
        self,
        entry: MemoryEntry,
        new_content: str,
    ) -> MemoryEntry:
        """Merge new content into an existing memory entry.

        One-shot LLM call: merges content and regenerates name/description.
        Falls back to local heuristics on failure.
        """
        existing = entry.content.strip()
        new = new_content.strip()

        # Cheap pre-checks before LLM
        if new == existing or new in existing:
            entry.updated_at = datetime.now(timezone.utc)
            return entry
        if existing in new:
            entry.content = new
            entry.updated_at = datetime.now(timezone.utc)
            return entry

        if not self._llm_query:
            entry.content = existing + "\n\n---\n\n" + new
            entry.updated_at = datetime.now(timezone.utc)
            return entry

        prompt = (
            "你是一个记忆整理助手。请将新内容合并到已有记忆中，并生成更新后的摘要。\n\n"
            "规则:\n"
            "1. 去重：如果新内容和已有内容重复，只保留一份\n"
            "2. 更新：如果新内容包含更新的信息，用新信息替换旧的\n"
            "3. 补充：如果新内容是全新信息，追加到已有内容后面\n"
            "4. 保持简洁，不要过度展开\n\n"
            f"已有记忆:\n"
            f"name: {entry.name}\n"
            f"description: {entry.description}\n"
            f"content: {existing}\n\n"
            f"新内容: {new}\n\n"
            '请返回 JSON 格式（不要任何额外文字、markdown 标记、代码块）:\n'
            '{\n'
            '  "merged_content": "合并后的完整内容",\n'
            '  "name": "更新后的主题词（≤20字，不要带类型前缀）",\n'
            '  "description": "更新后的一句话概括（≤30字，只写核心主题）"\n'
            '}\n'
        )

        try:
            response = self._llm_query(prompt)
            if not response:
                raise ValueError("empty response")

            # Clean markdown fences (same logic as _save_decision)
            text = response.strip()
            if text.startswith("```"):
                text = text.strip("`").strip()
                if text.lower().startswith("json"):
                    text = text[4:].strip()

            decision = json.loads(text)
            if not isinstance(decision, dict):
                raise ValueError("not a dict")

            merged = str(decision.get("merged_content", "")).strip()
            if merged:
                entry.content = merged
            else:
                # LLM 返回空内容视为失败，fallback 到本地拼接
                raise ValueError("merged_content is empty")

            name = str(decision.get("name", "")).strip()[:20]
            if name:
                entry.name = name

            desc = str(decision.get("description", "")).strip()[:50]
            if desc:
                entry.description = desc

        except Exception as e:
            logger.warning("_merge_into_existing LLM failed, fallback to append: %s", e)
            entry.content = existing + "\n\n---\n\n" + new

        entry.updated_at = datetime.now(timezone.utc)
        return entry

    def _summarize_content(
        self, content: str, mem_type: MemoryType
    ) -> tuple:
        """Use LLM to generate a concise name and description for the content.

        Returns (name, description) tuple. Falls back to (None, None) on failure.
        """
        if not self._llm_query:
            logger.warning("_summarize_content: _llm_query is None, skipping LLM")
            return (None, None)

        type_cn = {
            MemoryType.USER: "用户偏好",
            MemoryType.FEEDBACK: "用户反馈",
            MemoryType.PROJECT: "项目知识",
            MemoryType.REFERENCE: "参考资料",
        }.get(mem_type, "用户偏好")

        # 缩短内容长度，减少 LLM 复述倾向
        content_snippet = content[:500] if len(content) > 500 else content

        prompt = (
            "你是一个记忆摘要助手。你的任务是从以下内容中提取核心主题，生成极简摘要。\n"
            "严禁复述原文细节，只输出提炼后的关键词和一句话概括。\n\n"
            f"类型: {type_cn}\n"
            f"内容:\n{content_snippet}\n\n"
            "请只回复两行，严格格式如下（不要加任何额外文字、引号或 markdown）：\n"
            "name: <主题词，≤20字符>\n"
            "description: <核心主题概括，≤30字>"
        )

        try:
            response = self._llm_query(prompt)
            logger.debug("_summarize_content LLM response: %r", response)
            if not response:
                logger.warning("_summarize_content: LLM returned empty response")
                return (None, None)

            name = ""
            description = ""
            for line in response.strip().splitlines():
                line = line.strip()
                # 精确匹配 name: / description:，容错 markdown 加粗
                clean = line.strip("*").strip()
                lower_clean = clean.lower()
                if lower_clean.startswith("name:"):
                    val = clean.split(":", 1)[1].strip()
                    # 去掉可能的引号
                    val = val.strip('"').strip("'")
                    name = val[:20]
                elif lower_clean.startswith("description:"):
                    val = clean.split(":", 1)[1].strip()
                    val = val.strip('"').strip("'")
                    description = val[:50]

            logger.info(
                "_summarize_content parsed: name=%r description=%r",
                name, description,
            )
            return (name or None, description or None)
        except Exception as e:
            logger.warning("_summarize_content exception: %s", e)
            return (None, None)

    def _check_duplicate(
        self,
        meta: MemoryMetadata,
        manifest: list,
    ) -> Optional[MemoryEntry]:
        """Check if a memory with the same slug or highly similar description exists."""
        slug = MemoryStore.generate_slug(meta.name)
        for m in manifest:
            existing_slug = MemoryStore.generate_slug(m.name)
            if slug == existing_slug:
                try:
                    return self.store.read_file(m.filename)
                except Exception:
                    pass
                break

        if meta.description:
            for m in manifest:
                if meta.description.lower() == m.description.lower():
                    try:
                        return self.store.read_file(m.filename)
                    except Exception:
                        pass
                    break
                ratio = _text_overlap_ratio(meta.description, m.description)
                if ratio >= DUPLICATE_SIMILARITY_THRESHOLD:
                    try:
                        return self.store.read_file(m.filename)
                    except Exception:
                        pass
                    break
        return None

    def _find_duplicate(
        self,
        meta: MemoryMetadata,
        manifest: list,
    ) -> Optional[MemoryEntry]:
        """Find a duplicate memory using all available detection methods.

        Checks in order: slug match → exact description → CJK 2-gram overlap
        → LLM semantic match. Earlier checks are cheaper and run first.
        """
        dup = self._check_duplicate(meta, manifest)
        if dup:
            return dup

        if self.ranker.has_llm:
            dup = self._check_semantic_duplicate(meta, manifest)
            if dup:
                return dup

        return None

    def _check_semantic_duplicate(
        self,
        meta: MemoryMetadata,
        manifest: list,
    ) -> Optional[MemoryEntry]:
        """Use LLM to check if the new memory is semantically the same topic
        as any existing memory. Returns the matching entry or None.

        Feeds MEMORY.md index content (not per-file frontmatter) to the LLM —
        one file read instead of N.  The index is capped at 25KB / 200 lines,
        so the prompt size is bounded independently of the number of files.

        Only called when faster checks (slug, exact, CJK overlap) have failed.
        LLM call failures are silently degraded — they never block save().
        """
        if not meta.description:
            return None

        try:
            import os

            index_path = self.index.index_path
            if not os.path.exists(index_path):
                return None

            with open(index_path, "r", encoding="utf-8") as f:
                index_content = f.read()

            # Defensive truncation: even though write-path truncate() keeps
            # the index within limits, the read path should not trust it blindly.
            lines = index_content.splitlines()
            if len(lines) > 200:
                index_content = "\n".join(lines[:200])
            encoded = index_content.encode("utf-8")
            if len(encoded) > 25 * 1024:
                index_content = encoded[:25 * 1024].decode("utf-8", errors="ignore")

            if not index_content.strip():
                return None

            prompt = (
                "你是一个记忆去重助手。判断新记忆是否与已有记忆讨论同一主题/事实/偏好。\n"
                "\n"
                f"新记忆名称: {meta.name}\n"
                f"新记忆描述: {meta.description}\n"
                "\n"
                "已有记忆索引 (MEMORY.md):\n"
                f"{index_content}\n"
                "\n"
                "如果新记忆与某条已有记忆讨论的是同一件事（即使表述不同），返回该记忆的文件名。\n"
                "判断标准: 两条记忆是否在描述同一个用户偏好、同一项目约定、同一条用户反馈？\n"
                "如果新记忆是全新内容，返回 NONE。\n"
                "只返回文件名或 NONE，不要其他文字。\n"
            )

            response = self.ranker.query(prompt)
            if response is None:
                return None

            filename = self._extract_filename_from_llm(response)
            if filename is None:
                return None

            for m in manifest:
                if m.filename == filename or m.filename == f"{filename}.md":
                    try:
                        return self.store.read_file(m.filename)
                    except Exception:
                        return None

        except Exception:
            logger.debug("LLM semantic duplicate check failed, skipping", exc_info=True)

        return None

    @staticmethod
    def _extract_filename_from_llm(response: str) -> Optional[str]:
        """Parse an LLM response into a bare filename (without .md suffix).

        Handles common LLM output variations:
          - "user-称呼偏好.md"
          - '"user-称呼偏好.md"'
          - '`user-称呼偏好.md`'
          - "文件名是 user-称呼偏好.md"
          - "```\nuser-称呼偏好.md\n```"
        """
        import re

        text = response.strip()

        if not text or text.upper() == "NONE":
            return None

        # 1. Remove markdown code fences
        text = re.sub(r"^```\w*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)

        # 2. Remove wrapping quotes and backticks
        text = text.strip().strip("\"'`")

        # 3. Try to extract a valid memory filename via regex
        #    Matches: {type}-{slug}.md where slug is word chars + hyphens
        match = re.search(r"[\w\-]+\.md", text)
        if match:
            basename = match.group(0)
        else:
            # Fallback: use the whole cleaned string as-is
            basename = text.strip().strip(".")

        # 4. Strip .md suffix for matching (caller will compare both forms)
        if basename.endswith(".md"):
            basename = basename[:-3]

        if not basename:
            return None

        return basename
