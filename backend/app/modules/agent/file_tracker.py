"""
FileTracker — 压缩后关键文件恢复

面向编程任务，压缩后恢复最近访问/编辑过的文件内容。
"""

import hashlib
import logging
from dataclasses import dataclass
from datetime import datetime

logger = logging.getLogger(__name__)


@dataclass
class FileAccessRecord:
    """文件访问记录"""

    path: str
    content_hash: str
    estimated_tokens: int
    last_accessed_at: datetime
    was_edited: bool
    source_tool_call_id: str = ""


class FileTracker:
    """跟踪文件访问，支持压缩后恢复关键文件内容。

    设计原则：
    1. 轻量级：只记录元信息，不缓存完整内容
    2. 去重：同一文件多次访问只保留最新记录
    3. 优先级：编辑过的文件 > 最近读取的 > 其他
    """

    def __init__(
        self,
        max_files: int = 5,
        max_tokens: int = 50_000,
    ):
        self.max_files = max_files
        self.max_tokens = max_tokens
        # path -> FileAccessRecord
        self._records: dict[str, FileAccessRecord] = {}

    def record_access(
        self,
        path: str,
        content: str,
        was_edited: bool = False,
        tool_call_id: str = "",
    ) -> None:
        """记录一次文件访问。

        Args:
            path: 文件路径
            content: 文件内容（用于计算 hash 和 token 估算）
            was_edited: 是否被编辑过
            tool_call_id: 来源工具调用 ID
        """
        if not path or not content:
            return

        # 估算 token 数（复用 context_pruner 的估算逻辑）
        from app.modules.agent.context_pruner import _rough_char_tokens

        estimated_tokens = _rough_char_tokens(content)

        # 计算内容 hash（用于后续比对）
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]

        # 更新记录（去重：同一文件保留最新）
        existing = self._records.get(path)
        if existing:
            # 如果之前标记为编辑过，保持编辑状态
            was_edited = was_edited or existing.was_edited

        self._records[path] = FileAccessRecord(
            path=path,
            content_hash=content_hash,
            estimated_tokens=estimated_tokens,
            last_accessed_at=datetime.now(),
            was_edited=was_edited,
            source_tool_call_id=tool_call_id,
        )

        logger.debug(
            f"FileTracker: recorded {path} "
            f"(edited={was_edited}, tokens={estimated_tokens})"
        )

    def get_recent(
        self,
        max_tokens: int | None = None,
        max_files: int | None = None,
    ) -> list[FileAccessRecord]:
        """在预算内返回最近访问的文件记录。

        优先级：编辑过的文件 > 最近读取的 > 其他

        Args:
            max_tokens: 最大 token 预算（默认 self.max_tokens）
            max_files: 最大文件数（默认 self.max_files）

        Returns:
            List[FileAccessRecord]: 按优先级排序的文件记录
        """
        max_tokens = max_tokens or self.max_tokens
        max_files = max_files or self.max_files

        if not self._records:
            return []

        # 按优先级排序：编辑过的优先，然后按时间倒序
        records = sorted(
            self._records.values(),
            key=lambda r: (-int(r.was_edited), r.last_accessed_at),
            reverse=False,
        )
        # 修正排序：was_edited 为 True 的排在前面，然后按时间倒序
        records = sorted(
            self._records.values(),
            key=lambda r: (not r.was_edited, -r.last_accessed_at.timestamp()),
        )

        result = []
        total_tokens = 0
        for record in records:
            if len(result) >= max_files:
                break
            if total_tokens + record.estimated_tokens > max_tokens:
                # 跳过当前过大的文件，继续尝试后续可能更小、更优先的文件
                continue
            result.append(record)
            total_tokens += record.estimated_tokens

        return result

    def clear(self) -> None:
        """清空所有记录"""
        self._records.clear()

    @property
    def record_count(self) -> int:
        return len(self._records)


def create_file_tracker(
    max_files: int = 5,
    max_tokens: int = 50_000,
) -> FileTracker:
    """创建 FileTracker 实例"""
    return FileTracker(max_files=max_files, max_tokens=max_tokens)
