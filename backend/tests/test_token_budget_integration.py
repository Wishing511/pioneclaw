"""TokenBudget 集成测试

测试内容：
1. estimate_tokens 函数
2. get_context_window_for_model 模型映射
3. TokenBudget 在 AgentLoop 中的初始化
"""

import pytest

from app.modules.agent.loop import AgentLoop
from app.modules.agent.token_budget import (
    TokenBudget,
    estimate_tokens,
    get_context_window_for_model,
)


class TestEstimateTokens:
    """测试 token 估算函数"""

    def test_empty_messages(self):
        assert estimate_tokens([]) == 1  # 最小返回 1

    def test_simple_text(self):
        messages = [{"role": "user", "content": "Hello world"}]
        # 11 chars // 4 = 2
        assert estimate_tokens(messages) == 2

    def test_long_text(self):
        text = "a" * 1000
        messages = [{"role": "user", "content": text}]
        assert estimate_tokens(messages) == 250

    def test_multiple_messages(self):
        messages = [
            {"role": "system", "content": "You are a helper."},
            {"role": "user", "content": "Hello!"},
            {"role": "assistant", "content": "Hi there!"},
        ]
        # 分别估算：17//4 + 6//4 + 9//4 = 4 + 1 + 2 = 7
        assert estimate_tokens(messages) == 7

    def test_with_tool_calls(self):
        messages = [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "tc-1",
                        "function": {"name": "read_file", "arguments": '{"path": "/tmp/test"}'},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "tc-1", "content": "file content here"},
        ]
        tokens = estimate_tokens(messages)
        assert tokens > 1

    def test_with_tools(self):
        messages = [{"role": "user", "content": "Hello"}]
        tools = [
            {
                "type": "function",
                "function": {"name": "test_tool", "description": "A test tool"},
            }
        ]
        tokens = estimate_tokens(messages, tools)
        assert tokens > estimate_tokens(messages)

    def test_multimodal_content(self):
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Hello world"},
                    {"type": "image_url", "image_url": {"url": "http://example.com/img.png"}},
                ],
            }
        ]
        assert estimate_tokens(messages) == 2


class TestGetContextWindowForModel:
    """测试模型上下文窗口映射"""

    def test_claude_models(self):
        assert get_context_window_for_model("claude-3-opus") == 200_000
        assert get_context_window_for_model("claude-3-sonnet") == 200_000
        assert get_context_window_for_model("claude-3-5-sonnet-20241022") == 200_000

    def test_gpt4o_models(self):
        assert get_context_window_for_model("gpt-4o") == 128_000
        assert get_context_window_for_model("gpt-4o-mini") == 128_000

    def test_deepseek_models(self):
        assert get_context_window_for_model("deepseek-chat") == 64_000
        assert get_context_window_for_model("deepseek-reasoner") == 64_000

    def test_unknown_model(self):
        assert get_context_window_for_model("unknown-model-v1") == 128_000

    def test_none_model(self):
        assert get_context_window_for_model(None) == 128_000

    def test_case_insensitive(self):
        assert get_context_window_for_model("GPT-4O") == 128_000
        assert get_context_window_for_model("Claude-3-Opus") == 200_000


class TestTokenBudgetIntegration:
    """测试 TokenBudget 集成到 AgentLoop"""

    @pytest.mark.asyncio
    async def test_agent_loop_initializes_token_budget(self):
        """AgentLoop 初始化时应创建 TokenBudget"""

        class FakeProvider:
            pass

        loop = AgentLoop(
            provider=FakeProvider(),
            model="gpt-4o",
            max_tokens=4096,
        )

        assert loop._token_budget is not None
        assert loop._token_budget.context_window == 128_000
        assert loop._token_budget.max_output_tokens == 4096

    @pytest.mark.asyncio
    async def test_agent_loop_claude_model_window(self):
        """Claude 模型应使用 200K 窗口"""

        class FakeProvider:
            pass

        loop = AgentLoop(
            provider=FakeProvider(),
            model="claude-3-sonnet",
            max_tokens=4096,
        )

        assert loop._token_budget.context_window == 200_000

    def test_token_budget_thresholds(self):
        """测试阈值计算"""
        budget = TokenBudget(context_window=128_000, max_output_tokens=4096)

        # effective_window = 128000 - 4096 = 123904
        assert budget.effective_window == 123_904

        # compact_threshold = 123904 - 13000 = 110904
        assert budget.compact_threshold == 110_904

        # warning_threshold = 110904 - 20000 = 90904
        assert budget.warning_threshold == 90_904

        # hard_block_threshold = 123904 - 3000 = 120904
        assert budget.hard_block_threshold == 120_904

    def test_token_budget_status_transitions(self):
        """测试状态转换边界"""
        # 使用大窗口配置，确保所有状态都可达
        budget = TokenBudget(context_window=200_000, max_output_tokens=10_000)

        # normal: < 70%
        assert budget.get_status(130_000) == "normal"

        # warning: 70% - 80%
        assert budget.get_status(140_000) == "warning"
        assert budget.get_status(150_000) == "warning"

        # caution: 80% - 90%
        assert budget.get_status(160_000) == "caution"
        assert budget.get_status(170_000) == "caution"

        # critical: >= 90% (but < hard_block)
        assert budget.get_status(180_000) == "critical"

        # block: >= hard_block
        assert budget.get_status(budget.hard_block_threshold) == "block"
        assert budget.get_status(budget.hard_block_threshold + 1) == "block"

    def test_small_window_auto_scaling(self):
        """小窗口自动缩放保护"""
        budget = TokenBudget(context_window=8_192, max_output_tokens=4096)

        # 8192 <= 4096 + 13000，触发自动缩放
        assert budget.max_output_tokens < 4096
        assert budget.safety_buffer == 0

    def test_token_budget_dict(self):
        """测试 to_dict 输出"""
        budget = TokenBudget(context_window=128_000, max_output_tokens=4096)
        info = budget.to_dict(80_000)

        assert info["input_tokens"] == 80_000
        assert info["context_window"] == 128_000
        assert info["status"] == "normal"  # 80K/128K = 62.5%
        assert info["usage_percent"] == 62.5
        assert "compact_threshold" in info
        assert "warning_threshold" in info
        assert "hard_block_threshold" in info
