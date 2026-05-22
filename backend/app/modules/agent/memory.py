"""
MemoryStore — 文件级记忆存储（三套记忆系统之一）

借鉴自 CountBot 的 memory.py，实现段落式记忆存储。

三套记忆系统各有定位：
- **MemoryStore (本文件)**: 文件级记忆，段落式读写 MEMORY.md，适合结构化日志、
  关键词搜索和手动编辑。对应 API: /api/memory
- **VectorStore (vector_store.py)**: 向量嵌入记忆，适合语义搜索和相似度检索。
  对应 API: /api/vector-store
- **LayeredMemory (layered_memory/)**: 三层记忆架构：
  L0 会话级 → L1 工作级 → L2 持久级，自动分层和晋升。对应 API: /api/layered-memory

存储格式：段落之间用空行分隔，每个段落的第一行格式为
  日期|来源|内容

示例：
  2026-02-15|web-chat|## 用户询问天气API
  推荐使用OpenWeatherMap，缓存建议Redis TTL=3600s

  2026-02-15|telegram|## 定时任务需求
  用户要求每天早上9点发日报，已创建cron任务

支持功能:
- 写入条目（追加一个段落）
- 关键词搜索（AND/OR，搜索整个段落内容）
- 按段落号读写
- 对话自动总结写入
- 记忆统计
"""

import logging
import re
import threading
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class MemorySource(Enum):
    """记忆来源"""

    WEB_CHAT = "web-chat"
    TELEGRAM = "telegram"
    DINGTALK = "dingtalk"
    FEISHU = "feishu"
    WECOM = "wecom"
    QQ = "qq"
    CRON = "cron"
    SYSTEM = "system"
    IMPORT = "import"


@dataclass
class MemoryEntry:
    """记忆条目（一个段落）"""

    line_number: int  # 段落号（1-based）
    date: str  # 日期
    source: str  # 来源
    content: str  # 内容（可多行）

    def to_paragraph(self) -> str:
        """转换为段落格式（首行带前缀，后续行纯内容）"""
        content_lines = self.content.split("\n")
        first_line = f"{self.date}|{self.source}|{content_lines[0]}"
        return "\n".join([first_line] + content_lines[1:])

    @classmethod
    def from_paragraph(
        cls, para_number: int, lines: list[str]
    ) -> Optional["MemoryEntry"]:
        """从段落行列表解析"""
        if not lines:
            return None
        first = lines[0]
        parts = first.split("|", 2)
        if len(parts) < 3:
            return None
        content_lines = [parts[2]] + lines[1:]
        return cls(
            line_number=para_number,
            date=parts[0],
            source=parts[1],
            content="\n".join(content_lines),
        )

    def to_dict(self) -> dict:
        return {
            "line_number": self.line_number,
            "date": self.date,
            "source": self.source,
            "content": self.content,
        }


@dataclass
class MemoryStats:
    """记忆统计"""

    total_entries: int
    sources: dict[str, int]
    date_range: str
    oldest_date: str | None
    newest_date: str | None
    total_chars: int


class MemoryStore:
    """
    记忆存储 - 基于文件的段落式记忆存储

    段落格式：
    - 段落之间用空行（\\n\\n）分隔
    - 每个段落第一行：日期|来源|内容首行
    - 段落后续行：纯内容（不带前缀）
    - 兼容旧的行式格式，首次读取时自动迁移
    """

    def __init__(self, memory_dir: Path, filename: str = "MEMORY.md"):
        self.memory_dir = Path(memory_dir)
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        self.memory_file = self.memory_dir / filename
        self._lock = threading.Lock()
        logger.debug(f"MemoryStore initialized: {self.memory_file}")

    # ==================== 文件读写 ====================

    def read_all(self) -> str:
        """读取全部记忆内容（原始文本）"""
        if not self.memory_file.exists():
            return ""
        return self.memory_file.read_text(encoding="utf-8")

    def write_all(self, content: str) -> None:
        """覆盖写入全部记忆"""
        with self._lock:
            self.memory_file.write_text(content, encoding="utf-8")

    def _is_legacy_format(self, content: str) -> bool:
        """检测是否为旧的行式格式（每行都有 date|source| 前缀）"""
        lines = [line for line in content.strip().split("\n") if line.strip()]
        if not lines:
            return False
        # 旧格式：所有行都以 YYYY-MM-DD|xxx| 开头
        legacy_pattern = re.compile(r"^\d{4}-\d{2}-\d{2}\|[^|]+\|")
        legacy_count = sum(1 for line in lines if legacy_pattern.match(line))
        # 如果大部分行匹配旧格式，则认为是旧格式
        return legacy_count > 0 and legacy_count / len(lines) > 0.5

    def _migrate_from_legacy(self, content: str) -> str:
        """
        将旧的行式格式迁移为段落格式。
        规则：以 # 开头的行视为新段落起点，其他行合并到当前段落。
        段落内首行保留 date|source| 前缀，后续行去除前缀仅保留内容。
        """
        lines = [line.strip() for line in content.strip().split("\n") if line.strip()]
        if not lines:
            return content

        def _extract_content(line: str) -> tuple:
            """Extract (date, source, content) or (None, None, line) if not prefixed"""
            parts = line.split("|", 2)
            if len(parts) >= 3:
                return parts[0], parts[1], parts[2]
            return None, None, line

        paragraphs: list[list[str]] = []
        current_para: list[str] = []

        for line in lines:
            date_str, source, content_part = _extract_content(line)

            # A heading line starts a new paragraph
            if content_part.strip().startswith("#") and current_para:
                paragraphs.append(current_para)
                current_para = [line]  # Keep full prefix on heading line
            elif current_para:
                # Continuation line: store only the content part (strip prefix)
                if date_str and source:
                    current_para.append(content_part)
                else:
                    current_para.append(line)
            else:
                current_para = [line]

        if current_para:
            paragraphs.append(current_para)

        if len(paragraphs) <= 1:
            # Single paragraph: keep as-is (no headings to split by)
            return content

        blocks = ["\n".join(p) for p in paragraphs]
        result = "\n\n".join(blocks) + "\n"
        logger.info(
            f"Migrated legacy format: {len(lines)} lines → {len(paragraphs)} paragraphs"
        )
        return result

    def _read_paragraphs(self) -> list[list[str]]:
        """读取所有段落（每个段落是一组行）"""
        if not self.memory_file.exists():
            return []
        try:
            content = self.memory_file.read_text(encoding="utf-8")
            if not content.strip():
                return []

            # 自动迁移旧格式
            if self._is_legacy_format(content):
                content = self._migrate_from_legacy(content)
                self.memory_file.write_text(content, encoding="utf-8")

            paragraphs = []
            for block in content.strip().split("\n\n"):
                lines = [line for line in block.strip().split("\n") if line.strip()]
                if lines:
                    paragraphs.append(lines)
            return paragraphs
        except Exception as e:
            logger.error(f"Failed to read memory file: {e}")
            return []

    def _write_paragraphs(self, paragraphs: list[list[str]]) -> None:
        """写入所有段落"""
        try:
            if not paragraphs:
                self.memory_file.write_text("", encoding="utf-8")
                return
            blocks = ["\n".join(p) for p in paragraphs]
            self.memory_file.write_text("\n\n".join(blocks) + "\n", encoding="utf-8")
        except Exception as e:
            logger.error(f"Failed to write memory file: {e}")

    def _parse_all_entries(self) -> list[MemoryEntry]:
        """解析所有段落为 MemoryEntry 列表"""
        paragraphs = self._read_paragraphs()
        entries = []
        for i, para in enumerate(paragraphs):
            entry = MemoryEntry.from_paragraph(i + 1, para)
            if entry:
                entries.append(entry)
        return entries

    def get_paragraph_count(self) -> int:
        """获取段落总数"""
        return len(self._read_paragraphs())

    # ==================== 写入 ====================

    def append_entry(self, source: str, content: str, date: str | None = None) -> int:
        """
        追加一条记忆段落

        Args:
            source: 来源
            content: 内容（可包含换行符，首行为段落标题）
            date: 日期（可选，默认今天）

        Returns:
            int: 写入后的段落号（1-based）
        """
        with self._lock:
            date_str = date or datetime.now().strftime("%Y-%m-%d")
            content_lines = content.strip().split("\n")
            # 第一行带前缀
            first_content = content_lines[0]
            para = [f"{date_str}|{source}|{first_content}"]
            # 后续行直接追加
            para.extend(content_lines[1:])

            paragraphs = self._read_paragraphs()
            paragraphs.append(para)
            self._write_paragraphs(paragraphs)

            para_num = len(paragraphs)
            logger.info(
                f"Memory appended at paragraph {para_num}: {first_content[:80]}..."
            )
            return para_num

    def append_entries(self, source: str, entries: list[str]) -> list[int]:
        """
        批量追加记忆段落

        Args:
            source: 来源
            entries: 记忆内容列表（每个元素是一个段落的内容，可含换行）

        Returns:
            List[int]: 写入后的段落号列表
        """
        with self._lock:
            paragraphs = self._read_paragraphs()
            para_nums = []
            date_str = datetime.now().strftime("%Y-%m-%d")

            for content in entries:
                content_lines = content.strip().split("\n")
                para = [f"{date_str}|{source}|{content_lines[0]}"]
                para.extend(content_lines[1:])
                paragraphs.append(para)
                para_nums.append(len(paragraphs))

            self._write_paragraphs(paragraphs)
            logger.info(f"Appended {len(entries)} memory paragraphs")
            return para_nums

    # ==================== 读取 ====================

    def read_paragraphs(self, start: int, end: int | None = None) -> str:
        """
        按段落号读取记忆

        Args:
            start: 起始段落号（1-based）
            end: 结束段落号（1-based，包含），None 表示只读一段

        Returns:
            str: 格式化的记忆内容
        """
        paragraphs = self._read_paragraphs()
        total = len(paragraphs)

        if total == 0:
            return "记忆为空"

        if end is None:
            end = start

        start = max(1, min(start, total))
        end = max(start, min(end, total))

        result = []
        for i in range(start - 1, end):
            result.append(f"[{i + 1}] {paragraphs[i][0]}")
            for extra in paragraphs[i][1:]:
                result.append(f"     {extra}")

        return "\n".join(result)

    def get_entry(self, para_number: int) -> MemoryEntry | None:
        """获取单条记忆段落"""
        paragraphs = self._read_paragraphs()
        if para_number < 1 or para_number > len(paragraphs):
            return None
        return MemoryEntry.from_paragraph(para_number, paragraphs[para_number - 1])

    def get_entries(self, start: int, end: int) -> list[MemoryEntry]:
        """获取多条记忆段落"""
        paragraphs = self._read_paragraphs()
        entries = []

        for i in range(max(0, start - 1), min(len(paragraphs), end)):
            entry = MemoryEntry.from_paragraph(i + 1, paragraphs[i])
            if entry:
                entries.append(entry)

        return entries

    def get_recent(self, count: int = 10) -> str:
        """获取最近 N 条记忆（格式化文本）"""
        paragraphs = self._read_paragraphs()
        if not paragraphs:
            return "记忆为空"

        start = max(0, len(paragraphs) - count)
        result = []
        for i in range(start, len(paragraphs)):
            for j, line in enumerate(paragraphs[i]):
                prefix = f"[{i + 1}] " if j == 0 else "     "
                result.append(f"{prefix}{line}")

        return "\n".join(result)

    def get_recent_entries(self, count: int = 10) -> list[MemoryEntry]:
        """获取最近 N 条 MemoryEntry"""
        paragraphs = self._read_paragraphs()
        entries = []

        start = max(0, len(paragraphs) - count)
        for i in range(start, len(paragraphs)):
            entry = MemoryEntry.from_paragraph(i + 1, paragraphs[i])
            if entry:
                entries.append(entry)

        return entries

    # ==================== 搜索 ====================

    def search(
        self, keywords: list[str], max_results: int = 15, match_mode: str = "or"
    ) -> str:
        """
        关键词搜索记忆（在整个段落内容中搜索）

        Args:
            keywords: 关键词列表
            max_results: 最大返回条数
            match_mode: "or" 或 "and"

        Returns:
            str: 格式化的搜索结果
        """
        paragraphs = self._read_paragraphs()
        if not paragraphs:
            return "记忆为空，无法搜索。"

        if not keywords:
            return "请提供搜索关键词。"

        keywords = [kw.strip().lower() for kw in keywords if kw.strip()]
        if not keywords:
            return "请提供有效的搜索关键词。"

        results = []
        for i, para in enumerate(paragraphs):
            full_text = "\n".join(para).lower()

            if match_mode == "and":
                if all(kw in full_text for kw in keywords):
                    results.append((i + 1, para))
            else:
                if any(kw in full_text for kw in keywords):
                    results.append((i + 1, para))

        if not results:
            mode_text = "任一" if match_mode == "or" else "全部"
            return f"未找到包含 {mode_text} 关键词 {', '.join(keywords)} 的记忆。"

        # 格式化输出
        formatted = []
        for para_num, para in results[:max_results]:
            for j, line in enumerate(para):
                prefix = f"[{para_num}] " if j == 0 else "     "
                formatted.append(f"{prefix}{line}")

        if len(results) > max_results:
            formatted.append(f"... 共 {len(results)} 条匹配，仅显示前 {max_results} 条")

        return "\n".join(formatted)

    def search_entries(
        self,
        keywords: list[str],
        max_results: int = 15,
        match_mode: str = "or",
    ) -> list[MemoryEntry]:
        """搜索并返回 MemoryEntry 列表"""
        paragraphs = self._read_paragraphs()
        keywords = [kw.strip().lower() for kw in keywords if kw.strip()]

        entries = []
        for i, para in enumerate(paragraphs):
            full_text = "\n".join(para).lower()

            if match_mode == "and":
                if not all(kw in full_text for kw in keywords):
                    continue
            else:
                if not any(kw in full_text for kw in keywords):
                    continue

            entry = MemoryEntry.from_paragraph(i + 1, para)
            if entry:
                entries.append(entry)
                if len(entries) >= max_results:
                    break

        return entries

    # ==================== 删除 ====================

    def delete_lines(self, para_numbers: list[int]) -> int:
        """
        删除指定段落号的记忆

        Args:
            para_numbers: 要删除的段落号列表（1-based）

        Returns:
            int: 实际删除的条数
        """
        with self._lock:
            paragraphs = self._read_paragraphs()
            if not paragraphs:
                return 0

            to_delete = set(para_numbers)
            new_paras = [
                para for i, para in enumerate(paragraphs) if (i + 1) not in to_delete
            ]

            deleted = len(paragraphs) - len(new_paras)
            if deleted > 0:
                self._write_paragraphs(new_paras)
                logger.info(f"Deleted {deleted} memory paragraphs: {para_numbers}")

            return deleted

    def delete_entry(self, para_number: int) -> bool:
        """删除单个记忆段落"""
        return self.delete_lines([para_number]) > 0

    def clear(self) -> int:
        """清空所有记忆，返回清除的段落数"""
        with self._lock:
            paragraphs = self._read_paragraphs()
            count = len(paragraphs)
            if count > 0:
                self._write_paragraphs([])
                logger.warning(f"Cleared all {count} memory paragraphs")
            return count

    # ==================== 统计 ====================

    def get_stats(self) -> MemoryStats:
        """获取记忆统计信息"""
        paragraphs = self._read_paragraphs()
        total = len(paragraphs)

        if total == 0:
            return MemoryStats(
                total_entries=0,
                sources={},
                date_range="",
                oldest_date=None,
                newest_date=None,
                total_chars=0,
            )

        sources: dict[str, int] = {}
        dates: list[str] = []
        total_chars = 0

        for para in paragraphs:
            full_text = "\n".join(para)
            total_chars += len(full_text)
            parts = para[0].split("|", 2)
            if len(parts) >= 2:
                dates.append(parts[0])
                src = parts[1]
                sources[src] = sources.get(src, 0) + 1

        date_range = ""
        oldest = dates[0] if dates else None
        newest = dates[-1] if dates else None

        if oldest and newest:
            date_range = f"{oldest} ~ {newest}"

        return MemoryStats(
            total_entries=total,
            sources=sources,
            date_range=date_range,
            oldest_date=oldest,
            newest_date=newest,
            total_chars=total_chars,
        )

    # ==================== 导入导出 ====================

    def export_to_text(self) -> str:
        """导出为文本"""
        return self.read_all()

    def import_from_text(self, content: str, source: str = "import") -> int:
        """
        从文本导入记忆。
        如果文本包含空行则视为段落格式，否则按旧的行格式处理。

        Returns:
            int: 导入的段落数
        """
        stripped = content.strip()
        if not stripped:
            return 0

        # 检测是否已经是段落格式（包含连续两个换行）
        if "\n\n" in stripped:
            # 段落格式：直接写入
            with self._lock:
                existing = self._read_paragraphs()
                for block in stripped.split("\n\n"):
                    lines = [line for line in block.strip().split("\n") if line.strip()]
                    if lines:
                        # 确保首行有前缀
                        if "|" not in lines[0] or lines[0].count("|") < 2:
                            date_str = datetime.now().strftime("%Y-%m-%d")
                            lines[0] = f"{date_str}|{source}|{lines[0]}"
                        existing.append(lines)
                self._write_paragraphs(existing)
                return len(existing)

        # 旧的行格式：按行处理
        lines = [line.strip() for line in stripped.split("\n") if line.strip()]
        count = 0
        for line in lines:
            if "|" in line and line.count("|") >= 2:
                parts = line.split("|", 2)
                if len(parts[0]) == 10 and parts[0].count("-") == 2:
                    self.append_entry(parts[1], parts[2], parts[0])
                else:
                    self.append_entry(source, line)
            else:
                self.append_entry(source, line)
            count += 1

        logger.info(f"Imported {count} memory entries")
        return count


# ==================== 全局实例 ====================

_global_memory_store: MemoryStore | None = None


def get_memory_store(memory_dir: Path | None = None) -> MemoryStore:
    """获取全局记忆存储实例"""
    global _global_memory_store

    if _global_memory_store is None:
        if memory_dir is None:
            memory_dir = Path.cwd() / "memory"
        _global_memory_store = MemoryStore(memory_dir)

    return _global_memory_store


def init_memory_store(memory_dir: Path) -> MemoryStore:
    """初始化全局记忆存储"""
    global _global_memory_store
    _global_memory_store = MemoryStore(memory_dir)
    return _global_memory_store
