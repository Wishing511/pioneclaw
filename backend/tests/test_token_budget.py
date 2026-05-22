"""
Test TokenBudget — 统一 token 预算计算

验证：
1. 各阈值计算正确（200K/128K/16K/8K/4K）
2. 小上下文窗口自动缩放
3. 状态判定（normal/warning/caution/critical/block）
4. usage_percent 计算正确
"""

from app.modules.agent.token_budget import TokenBudget, TokenUsage


class TestTokenBudgetThresholds:
    def test_200k_model(self):
        """200K 模型：阈值 = 200000 - 20000 - 13000 = 167000"""
        budget = TokenBudget(context_window=200_000)
        assert budget.effective_window == 180_000
        assert budget.compact_threshold == 167_000
        assert budget.warning_threshold == 147_000
        assert budget.hard_block_threshold == 177_000

    def test_128k_model(self):
        """128K 模型"""
        budget = TokenBudget(context_window=128_000)
        assert budget.effective_window == 108_000
        assert budget.compact_threshold == 95_000

    def test_256k_model(self):
        """256K 模型"""
        budget = TokenBudget(context_window=256_000)
        assert budget.compact_threshold == 223_000

    def test_16k_model(self):
        """16K 模型：触发自动缩放"""
        budget = TokenBudget(context_window=16_000)
        # 16K <= 33K，触发自动缩放：max_output_tokens = 1600, safety_buffer = 0
        assert budget.max_output_tokens == 1_600
        assert budget.compact_threshold == 14_400  # 16000 - 1600

    def test_8k_model_auto_scaling(self):
        """8K 模型：buffer 自动缩放到 10%"""
        budget = TokenBudget(context_window=8_000)
        # 8K <= 20K + 13K = 33K，触发自动缩放
        # max_output_tokens 缩到 800，safety_buffer 缩到 0
        assert budget.max_output_tokens == 800
        assert budget.safety_buffer == 0
        assert budget.effective_window == 7_200
        assert budget.compact_threshold == 7_200
        assert budget.compact_threshold > 0  # 不为负

    def test_4k_model_auto_scaling(self):
        """4K 模型：阈值仍为正"""
        budget = TokenBudget(context_window=4_000)
        assert budget.compact_threshold > 0
        assert budget.compact_threshold == 3_600  # 4000 - 400


class TestTokenBudgetStatus:
    def test_status_normal(self):
        budget = TokenBudget(context_window=200_000)
        assert budget.get_status(100_000) == "normal"  # 50%

    def test_status_warning(self):
        budget = TokenBudget(context_window=200_000)
        assert budget.get_status(150_000) == "warning"  # 75%

    def test_status_caution(self):
        budget = TokenBudget(context_window=200_000)
        assert budget.get_status(170_000) == "caution"  # 85%

    def test_status_critical(self):
        # 使用 256K 窗口测试 critical：hard_block=233K(91%)，90%=230.4K
        budget = TokenBudget(context_window=256_000)
        assert budget.get_status(232_000) == "critical"  # 90.6%，>=90% 但 < hard_block
        assert budget.get_status(234_000) == "block"  # >= hard_block

    def test_status_block(self):
        budget = TokenBudget(context_window=200_000)
        assert budget.get_status(180_000) == "block"  # >= hard_block

    def test_should_compact(self):
        budget = TokenBudget(context_window=200_000)
        assert budget.should_compact(170_000) is True
        assert budget.should_compact(160_000) is False


class TestTokenBudgetUsagePercent:
    def test_usage_percent(self):
        budget = TokenBudget(context_window=200_000)
        assert budget.usage_percent(100_000) == 50.0
        assert budget.usage_percent(50_000) == 25.0

    def test_usage_percent_zero_window(self):
        budget = TokenBudget(context_window=0)
        assert budget.usage_percent(100) == 0.0


class TestTokenUsage:
    def test_token_usage_api(self):
        usage = TokenUsage(input_tokens=1000, output_tokens=200, source="api")
        assert usage.source == "api"

    def test_token_usage_estimated(self):
        usage = TokenUsage(input_tokens=1000, output_tokens=0, source="estimated")
        assert usage.source == "estimated"
