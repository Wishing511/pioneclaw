"""
Test ContextPruner: MicroCompacter + Snip

验证：
1. MicroCompacter 清除旧工具结果内容，保留最近 N 个
2. MicroCompacter 截断超过 max_chars 的工具结果
3. Snip 移除空 system 消息
4. Snip 移除空 assistant 消息（无 tool_calls）
5. Snip 截断超长 reasoning_content
6. ContextPruner 统一入口组合 Snip + MicroCompacter
"""

from app.modules.agent.context_pruner import (
    ContextPruner,
    MicroCompacter,
    Snip,
    estimate_tokens,
)

# --- MicroCompacter tests ---


class TestMicroCompacter:
    def _tool_result(self, name, content, idx=0):
        return {
            "role": "tool",
            "tool_name": name,
            "tool_call_id": f"call-{idx}",
            "content": content,
        }

    def test_keeps_recent_n_results(self):
        compacter = MicroCompacter(keep_recent=2, max_chars=9999)
        original = [
            self._tool_result("read_file", f"File content {i}" * 50, idx=i)
            for i in range(5)
        ]
        result, saved = compacter.prune(original)

        # 返回新列表，不修改原列表
        assert result is not original
        assert original[0]["content"] == "File content 0" * 50  # unchanged

        # 最早 3 个被清除
        for i in range(3):
            assert result[i]["content"] == "[tool_result: read_file, content cleared]"
        # 最近 2 个保留
        for i in range(3, 5):
            assert result[i]["content"] == f"File content {i}" * 50

        assert saved > 0

    def test_clears_old_results_by_count(self):
        compacter = MicroCompacter(keep_recent=8, max_chars=9999)
        # 使用足够长的内容，确保占位符比内容短
        original = [
            self._tool_result("grep", f"result {i}" + "x" * 100, idx=i)
            for i in range(15)
        ]
        result, saved = compacter.prune(original)

        # 长内容使用结构化占位符
        cleared = sum(
            1 for m in result if m["content"] == "[tool_result: grep, content cleared]"
        )
        assert cleared == 7  # 15 - 8 = 7
        assert saved > 0

    def test_truncates_oversized_results(self):
        compacter = MicroCompacter(keep_recent=10, max_chars=50)
        long_content = "X" * 200
        messages = [self._tool_result("read_file", long_content, idx=0)]
        result, _ = compacter.prune(messages)

        assert result[0]["content"] != long_content
        assert "truncated" in result[0]["content"].lower()
        assert len(result[0]["content"]) < len(long_content)
        assert messages[0]["content"] == long_content  # original unchanged

    def test_does_not_touch_non_compactable_tools(self):
        compacter = MicroCompacter(keep_recent=0, max_chars=9999)
        original = [
            {"role": "tool", "tool_name": "custom_tool", "content": "should stay"},
            {
                "role": "tool",
                "tool_name": "read_file",
                "content": "will be cleared" + "x" * 100,
            },
        ]
        result, _ = compacter.prune(original)

        assert result[0]["content"] == "should stay"
        # 长内容使用结构化占位符
        assert result[1]["content"] == "[tool_result: read_file, content cleared]"
        assert (
            original[1]["content"] == "will be cleared" + "x" * 100
        )  # original unchanged

    def test_does_not_touch_non_tool_roles(self):
        compacter = MicroCompacter(keep_recent=0, max_chars=9999)
        messages = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "let me check"},
            {"role": "system", "content": "be helpful"},
        ]
        compacter.prune(messages)

        assert messages[0]["content"] == "hello"
        assert messages[1]["content"] == "let me check"
        assert messages[2]["content"] == "be helpful"

    def test_empty_messages_returns_zero_saved(self):
        compacter = MicroCompacter()
        result, saved = compacter.prune([])
        assert saved == 0

    def test_no_tool_results_returns_zero_saved(self):
        compacter = MicroCompacter()
        messages = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello!"},
        ]
        _, saved = compacter.prune(messages)
        assert saved == 0


# --- Snip tests ---


class TestSnip:
    def test_removes_empty_system_messages(self):
        snip = Snip()
        messages = [
            {"role": "system", "content": ""},
            {"role": "system", "content": "   "},
            {"role": "system", "content": "be helpful"},
            {"role": "user", "content": "hello"},
        ]
        result, saved = snip.prune(messages)

        assert len(result) == 2  # removed 2 empty system
        assert result[0]["content"] == "be helpful"
        assert saved > 0

    def test_removes_empty_assistant_without_tool_calls(self):
        snip = Snip()
        messages = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": ""},
            {"role": "assistant", "content": "  \n  "},
            {"role": "assistant", "content": "answer"},
        ]
        result, saved = snip.prune(messages)

        assert len(result) == 2  # user + "answer"
        assert saved > 0

    def test_keeps_assistant_with_tool_calls(self):
        snip = Snip()
        messages = [
            {"role": "user", "content": "hi"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": "1", "function": {"name": "read"}}],
            },
        ]
        result, saved = snip.prune(messages)

        assert len(result) == 2  # keeps assistant with tool_calls
        assert saved == 0

    def test_truncates_reasoning_content(self):
        snip = Snip(max_reasoning_chars=50)
        messages = [
            {
                "role": "assistant",
                "content": "answer",
                "reasoning_content": "X" * 200,
            },
        ]
        result, saved = snip.prune(messages)

        assert len(result[0]["reasoning_content"]) < 200
        assert "truncated" in result[0]["reasoning_content"].lower()
        assert saved > 0

    def test_does_not_truncate_short_reasoning(self):
        snip = Snip(max_reasoning_chars=2000)
        messages = [
            {
                "role": "assistant",
                "content": "answer",
                "reasoning_content": "short reasoning",
            },
        ]
        result, saved = snip.prune(messages)

        assert result[0]["reasoning_content"] == "short reasoning"

    def test_returns_new_list(self):
        snip = Snip()
        original = [
            {"role": "system", "content": ""},
            {"role": "user", "content": "hi"},
        ]
        result, _ = snip.prune(original)

        assert result is not original
        # original should be unchanged
        assert len(original) == 2


# --- ContextPruner tests ---


class TestContextPruner:
    def test_combined_pruning(self):
        pruner = ContextPruner(
            keep_recent=2, max_tool_result_chars=9999, max_reasoning_chars=50
        )

        messages = [
            {"role": "system", "content": ""},  # should be removed by snip
            {"role": "system", "content": "be helpful"},
            {"role": "user", "content": "read files"},
            # 5 tool results — only keep 2 most recent
            {
                "role": "tool",
                "tool_name": "read_file",
                "tool_call_id": "1",
                "content": "A" * 100,
            },
            {
                "role": "tool",
                "tool_name": "read_file",
                "tool_call_id": "2",
                "content": "B" * 100,
            },
            {
                "role": "tool",
                "tool_name": "read_file",
                "tool_call_id": "3",
                "content": "C" * 100,
            },
            {
                "role": "tool",
                "tool_name": "read_file",
                "tool_call_id": "4",
                "content": "D" * 100,
            },
            {
                "role": "tool",
                "tool_name": "read_file",
                "tool_call_id": "5",
                "content": "E" * 100,
            },
            {"role": "assistant", "content": "done", "reasoning_content": "X" * 200},
        ]

        # Snip first
        messages, snip_saved = pruner.snip_prune(messages)
        assert snip_saved > 0  # empty system removed + reasoning truncated
        assert (
            len(
                [
                    m
                    for m in messages
                    if m.get("role") == "system" and m.get("content") == ""
                ]
            )
            == 0
        )

        # Micro compact
        messages, micro_saved = pruner.micro_compact(messages)
        assert micro_saved > 0

        # Verify old tool results cleared
        tool_msgs = [m for m in messages if m.get("role") == "tool"]
        cleared_count = sum(1 for m in tool_msgs if "content cleared" in m["content"])
        assert cleared_count >= 3  # at least 3 cleared (5 total - 2 kept)

    def test_pruner_with_no_prunable_content(self):
        pruner = ContextPruner()
        messages = [
            {"role": "system", "content": "be helpful"},
            {"role": "user", "content": "hello"},
        ]
        result, saved = pruner.snip_prune(messages)
        assert saved == 0
        assert len(result) == 2


# --- estimate_tokens tests ---


class TestEstimateTokens:
    def test_empty_list(self):
        assert estimate_tokens([]) == 0

    def test_english_text(self):
        tokens = estimate_tokens([{"role": "user", "content": "hello world"}])
        assert 1 <= tokens <= 5

    def test_chinese_text(self):
        tokens = estimate_tokens([{"role": "user", "content": "你好世界"}])
        # Chinese: 1.5 tokens per char
        assert 4 <= tokens <= 10

    def test_tool_calls_add_tokens(self):
        msg = {
            "role": "assistant",
            "content": "let me check",
            "tool_calls": [
                {
                    "function": {
                        "name": "read_file",
                        "arguments": '{"path": "file.txt"}',
                    }
                }
            ],
        }
        tokens = estimate_tokens([msg])
        assert tokens > 0
