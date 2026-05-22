"""
Test FileTracker — 压缩后关键文件恢复

验证：
1. 记录文件访问
2. 去重：同一文件保留最新
3. 优先级：编辑过的 > 最近读取的
4. Token 预算控制
"""

from app.modules.agent.file_tracker import FileTracker


class TestFileTracker:
    def test_record_read_file(self):
        tracker = FileTracker(max_files=5, max_tokens=50_000)
        tracker.record_access(
            path="/app/main.py",
            content="def main():\n    pass\n",
            was_edited=False,
            tool_call_id="call-1",
        )
        assert tracker.record_count == 1

    def test_record_write_file_marks_edited(self):
        tracker = FileTracker(max_files=5, max_tokens=50_000)
        tracker.record_access(
            path="/app/main.py",
            content="def main():\n    pass\n",
            was_edited=True,
            tool_call_id="call-1",
        )
        records = tracker.get_recent()
        assert len(records) == 1
        assert records[0].was_edited is True

    def test_deduplication_keeps_latest(self):
        tracker = FileTracker(max_files=5, max_tokens=50_000)
        tracker.record_access(
            path="/app/main.py",
            content="version 1",
            was_edited=False,
            tool_call_id="call-1",
        )
        tracker.record_access(
            path="/app/main.py",
            content="version 2",
            was_edited=True,
            tool_call_id="call-2",
        )
        assert tracker.record_count == 1
        records = tracker.get_recent()
        assert records[0].was_edited is True  # 合并编辑状态
        assert records[0].content_hash != ""  # hash 已更新

    def test_priority_edited_over_read(self):
        tracker = FileTracker(max_files=5, max_tokens=50_000)
        # 先读取
        tracker.record_access(path="/app/read.py", content="x" * 1000, was_edited=False)
        # 再编辑
        tracker.record_access(path="/app/edit.py", content="x" * 1000, was_edited=True)

        records = tracker.get_recent()
        # 编辑过的排在前面
        assert records[0].path == "/app/edit.py"
        assert records[1].path == "/app/read.py"

    def test_token_budget_limits(self):
        tracker = FileTracker(max_files=5, max_tokens=1000)
        tracker.record_access(path="/app/a.py", content="x" * 100, was_edited=False)
        tracker.record_access(path="/app/b.py", content="x" * 1000, was_edited=False)

        # 第一个文件约 25 tokens，第二个约 250 tokens
        # max_tokens=50 时只能返回第一个
        records = tracker.get_recent(max_tokens=50, max_files=5)
        assert len(records) == 1
        assert records[0].path == "/app/a.py"

    def test_max_files_limits(self):
        tracker = FileTracker(max_files=2, max_tokens=50_000)
        for i in range(5):
            tracker.record_access(
                path=f"/app/f{i}.py", content="x" * 100, was_edited=False
            )

        records = tracker.get_recent()
        assert len(records) == 2

    def test_clear(self):
        tracker = FileTracker()
        tracker.record_access(path="/app/main.py", content="hello", was_edited=False)
        assert tracker.record_count == 1
        tracker.clear()
        assert tracker.record_count == 0
        assert tracker.get_recent() == []

    def test_empty_content_skipped(self):
        tracker = FileTracker()
        tracker.record_access(path="/app/main.py", content="", was_edited=False)
        tracker.record_access(path="", content="hello", was_edited=False)
        assert tracker.record_count == 0
