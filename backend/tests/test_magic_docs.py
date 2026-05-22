"""
MagicDocUpdater 单元测试（VV.4）

覆盖：扫描、头部解析、单文档更新、全量更新、边界条件
"""

import tempfile
from pathlib import Path

from app.modules.agent.magic_docs import MagicDocUpdater

# ==================== 头部解析 ====================


class TestParsePurpose:
    """测试 parse_purpose"""

    def test_valid_header(self):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".md", delete=False, encoding="utf-8"
        ) as f:
            f.write(
                "# MAGIC DOC: 维护项目架构概览\n\n# Project Architecture\nContent here\n"
            )
            f.flush()
            path = Path(f.name)

        try:
            updater = MagicDocUpdater()
            purpose = updater.parse_purpose(path)
            assert purpose == "维护项目架构概览"
        finally:
            path.unlink()

    def test_header_without_purpose(self):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".md", delete=False, encoding="utf-8"
        ) as f:
            f.write("# MAGIC DOC:\n\n# Some Title\n")
            f.flush()
            path = Path(f.name)

        try:
            updater = MagicDocUpdater()
            purpose = updater.parse_purpose(path)
            assert purpose == "untitled"  # 空 purpose
        finally:
            path.unlink()

    def test_no_magic_header(self):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".md", delete=False, encoding="utf-8"
        ) as f:
            f.write("# Regular Document\n\nContent\n")
            f.flush()
            path = Path(f.name)

        try:
            updater = MagicDocUpdater()
            purpose = updater.parse_purpose(path)
            assert purpose is None
        finally:
            path.unlink()

    def test_empty_file(self):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".md", delete=False, encoding="utf-8"
        ) as f:
            f.write("")
            f.flush()
            path = Path(f.name)

        try:
            updater = MagicDocUpdater()
            purpose = updater.parse_purpose(path)
            assert purpose is None
        finally:
            path.unlink()

    def test_header_with_extra_spaces(self):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".md", delete=False, encoding="utf-8"
        ) as f:
            f.write("# MAGIC DOC:    多空格描述   \n\nContent\n")
            f.flush()
            path = Path(f.name)

        try:
            updater = MagicDocUpdater()
            purpose = updater.parse_purpose(path)
            assert purpose == "多空格描述"
        finally:
            path.unlink()

    def test_bom_handled(self):
        """测试 UTF-8 BOM 文件"""
        with tempfile.NamedTemporaryFile(mode="wb", suffix=".md", delete=False) as f:
            f.write(b"\xef\xbb\xbf# MAGIC DOC: BOM test\n\nContent\n")
            f.flush()
            path = Path(f.name)

        try:
            updater = MagicDocUpdater()
            purpose = updater.parse_purpose(path)
            assert purpose == "BOM test"
        finally:
            path.unlink()


# ==================== 扫描 ====================


class TestScanWorkspace:
    """测试 scan_workspace"""

    def test_scan_finds_magic_docs(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            # 创建 magic doc
            md1 = Path(tmpdir) / "architecture.md"
            md1.write_text(
                "# MAGIC DOC: 项目架构\n\n# Architecture\n", encoding="utf-8"
            )
            md2 = Path(tmpdir) / "roadmap.md"
            md2.write_text("# MAGIC DOC: 路线图\n\n# Roadmap\n", encoding="utf-8")
            # 创建普通文件
            normal = Path(tmpdir) / "notes.md"
            normal.write_text("# Regular Notes\n\nContent\n", encoding="utf-8")

            updater = MagicDocUpdater(workspace_path=tmpdir, enabled=True)
            found = updater.scan_workspace()

            assert len(found) == 2
            paths = {str(p) for p in found}
            assert str(md1) in paths
            assert str(md2) in paths
            assert str(normal) not in paths

    def test_scan_empty_workspace(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            updater = MagicDocUpdater(workspace_path=tmpdir, enabled=True)
            found = updater.scan_workspace()
            assert found == []

    def test_scan_no_magic_docs(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            normal = Path(tmpdir) / "readme.md"
            normal.write_text("# README\n\nHello\n", encoding="utf-8")

            updater = MagicDocUpdater(workspace_path=tmpdir, enabled=True)
            found = updater.scan_workspace()
            assert found == []

    def test_disabled_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            Path(tmpdir, "doc.md").write_text(
                "# MAGIC DOC: test\n\nContent\n", encoding="utf-8"
            )
            updater = MagicDocUpdater(workspace_path=tmpdir, enabled=False)
            found = updater.scan_workspace()
            assert found == []

    def test_skips_hidden_dirs(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            # 隐藏目录中的 magic doc
            hidden_dir = Path(tmpdir) / ".hidden"
            hidden_dir.mkdir()
            (hidden_dir / "secret.md").write_text(
                "# MAGIC DOC: hidden\n\nContent\n", encoding="utf-8"
            )

            updater = MagicDocUpdater(workspace_path=tmpdir, enabled=True)
            found = updater.scan_workspace()
            assert found == []

    def test_skips_node_modules(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            nm_dir = Path(tmpdir) / "node_modules" / "some-package"
            nm_dir.mkdir(parents=True)
            (nm_dir / "readme.md").write_text(
                "# MAGIC DOC: pkg\n\nContent\n", encoding="utf-8"
            )

            updater = MagicDocUpdater(workspace_path=tmpdir, enabled=True)
            found = updater.scan_workspace()
            assert found == []


# ==================== 更新 ====================


class TestUpdateDocument:
    """测试 update_document"""

    def test_update_single_doc(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            doc = Path(tmpdir) / "status.md"
            doc.write_text(
                "# MAGIC DOC: 项目状态\n\n# Status\n\n一切正常\n", encoding="utf-8"
            )

            updater = MagicDocUpdater(workspace_path=tmpdir, enabled=True)
            result = updater.update_document(doc)

            import asyncio

            success = asyncio.run(result)
            assert success is True

            # 验证时间戳被插入
            content = doc.read_text(encoding="utf-8")
            assert "# MAGIC DOC:" in content
            assert "Last updated by MagicDocs:" in content

    def test_disabled_returns_false(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            doc = Path(tmpdir) / "doc.md"
            doc.write_text("# MAGIC DOC: test\n\nContent\n", encoding="utf-8")

            updater = MagicDocUpdater(workspace_path=tmpdir, enabled=False)
            import asyncio

            success = asyncio.run(updater.update_document(doc))
            assert success is False

    def test_non_existent_file(self):
        updater = MagicDocUpdater(workspace_path=tempfile.gettempdir(), enabled=True)
        import asyncio

        success = asyncio.run(updater.update_document(Path("/nonexistent/doc.md")))
        assert success is False


class TestUpdateAll:
    """测试 update_all"""

    def test_update_all(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            Path(tmpdir, "a.md").write_text(
                "# MAGIC DOC: doc A\n\nA\n", encoding="utf-8"
            )
            Path(tmpdir, "b.md").write_text(
                "# MAGIC DOC: doc B\n\nB\n", encoding="utf-8"
            )
            Path(tmpdir, "c.md").write_text("# Regular\n\nC\n", encoding="utf-8")

            updater = MagicDocUpdater(workspace_path=tmpdir, enabled=True)
            import asyncio

            updated = asyncio.run(updater.update_all())

            assert len(updated) == 2

    def test_disabled_update_all_empty(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            Path(tmpdir, "doc.md").write_text(
                "# MAGIC DOC: test\n\nContent\n", encoding="utf-8"
            )
            updater = MagicDocUpdater(workspace_path=tmpdir, enabled=False)
            import asyncio

            updated = asyncio.run(updater.update_all())
            assert updated == []


# ==================== 辅助方法 ====================


class TestStats:
    def test_get_stats(self):
        updater = MagicDocUpdater(workspace_path="/test", enabled=True)
        stats = updater.get_stats()
        assert stats["enabled"] is True
        assert "workspace" in stats
        assert stats["magic_doc_count"] == 0
        assert stats["magic_docs"] == []
