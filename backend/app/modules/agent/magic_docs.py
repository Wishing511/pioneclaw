"""
MagicDocUpdater - Magic Docs 自动维护文档（VV.4，P2可选）

借鉴 claude-code-sourcemap magicDocs：
扫描 workspace 中带有 # MAGIC DOC: 头标记的 .md 文件，
通过后台子 Agent 自动更新其内容。

默认关闭，通过 enabled=True 或配置项 VV_MAGIC_DOCS_ENABLED 启用。
"""

import logging
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class MagicDocUpdater:
    """
    扫描 # MAGIC DOC: 头标记，后台更新文档

    Header 格式: # MAGIC DOC: <purpose description>

    示例:
        # MAGIC DOC: 维护项目当前架构概览
        # Project Architecture
        ...

    用法:
        updater = MagicDocUpdater(workspace_path="/workspace")
        updater.scan_workspace()
        await updater.update_all()
    """

    MAGIC_HEADER = "# MAGIC DOC:"

    def __init__(
        self,
        workspace_path: str = "",
        enabled: bool = False,  # P2 默认关闭
    ):
        self.workspace_path = Path(workspace_path) if workspace_path else Path.home()
        self.enabled = enabled

        # 缓存扫描结果
        self._magic_docs: dict[str, str] = {}  # file_path -> purpose

    # ==================== 扫描 ====================

    def scan_workspace(self) -> list[Path]:
        """
        扫描 workspace 中所有包含 # MAGIC DOC: 头部的 .md 文件

        Returns:
            找到的 magic doc 文件路径列表
        """
        if not self.enabled:
            return []

        self._magic_docs = {}
        found = []

        try:
            if not self.workspace_path.exists():
                return []

            for md_file in self.workspace_path.rglob("*.md"):
                # 跳过隐藏目录和 node_modules
                if any(part.startswith(".") for part in md_file.parts):
                    continue
                if "node_modules" in md_file.parts:
                    continue

                try:
                    purpose = self.parse_purpose(md_file)
                    if purpose is not None:
                        self._magic_docs[str(md_file)] = purpose
                        found.append(md_file)
                except Exception as e:
                    logger.debug(f"MagicDocUpdater: skip {md_file}: {e}")

            logger.info(f"MagicDocUpdater: found {len(found)} magic docs")
            return found

        except Exception as e:
            logger.error(f"MagicDocUpdater: scan failed: {e}")
            return []

    def parse_purpose(self, file_path: Path) -> str | None:
        """
        解析文件的 MAGIC DOC 头标记

        Args:
            file_path: .md 文件路径

        Returns:
            purpose 描述文本，非 magic doc 返回 None
        """
        try:
            with open(file_path, encoding="utf-8") as f:
                first_line = f.readline().strip()
                # 也检查是否有 BOM
                if first_line.startswith("\ufeff"):
                    first_line = first_line[1:].strip()

                if first_line.startswith(self.MAGIC_HEADER):
                    purpose = first_line[len(self.MAGIC_HEADER) :].strip()
                    return purpose or "untitled"
        except (OSError, UnicodeDecodeError):
            pass

        return None

    # ==================== 更新 ====================

    async def update_document(self, file_path: Path) -> bool:
        """
        更新单个 magic doc

        当前实现：更新时间戳标记，保留现有内容。
        完整实现需要 LLM provider 来根据 purpose 生成更新内容。

        Args:
            file_path: 文档路径

        Returns:
            是否成功更新
        """
        if not self.enabled:
            return False

        try:
            if not file_path.exists():
                return False

            purpose = self.parse_purpose(file_path)
            if purpose is None:
                return False

            # 读取当前内容
            content = file_path.read_text(encoding="utf-8")

            # 更新时间戳
            now = datetime.now().isoformat()
            if "<!-- Last updated by MagicDocs:" in content:
                # 更新已有时间戳
                import re

                content = re.sub(
                    r"<!-- Last updated by MagicDocs:.*?-->",
                    f"<!-- Last updated by MagicDocs: {now} -->",
                    content,
                )
            else:
                # 在 MAGIC DOC 头部后插入时间戳
                header_end = content.find("\n")
                if header_end > 0:
                    content = (
                        content[: header_end + 1]
                        + f"<!-- Last updated by MagicDocs: {now} -->\n"
                        + content[header_end + 1 :]
                    )

            file_path.write_text(content, encoding="utf-8")
            logger.info(f"MagicDocUpdater: updated {file_path.name} ({purpose})")
            return True

        except Exception as e:
            logger.error(f"MagicDocUpdater: failed to update {file_path}: {e}")
            return False

    async def update_all(self) -> list[str]:
        """
        扫描并更新所有 magic docs

        Returns:
            成功更新的文件路径列表
        """
        if not self.enabled:
            return []

        updated = []
        files = self.scan_workspace()

        for file_path in files:
            success = await self.update_document(file_path)
            if success:
                updated.append(str(file_path))

        if updated:
            logger.info(f"MagicDocUpdater: updated {len(updated)}/{len(files)} docs")

        return updated

    # ==================== 辅助方法 ====================

    def get_stats(self) -> dict[str, Any]:
        """获取当前统计"""
        return {
            "enabled": self.enabled,
            "workspace": str(self.workspace_path),
            "magic_doc_count": len(self._magic_docs),
            "magic_docs": [
                {"path": path, "purpose": purpose}
                for path, purpose in self._magic_docs.items()
            ],
        }
