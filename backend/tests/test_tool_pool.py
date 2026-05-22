"""
工具池过滤测试：ToolPoolMode, TOOL_POOL_PRESETS, get_pool_tool_policy
"""

from unittest.mock import patch

import pytest

from app.core.sandbox_policy import ToolPolicy
from app.modules.tools.pool_filter import (
    TOOL_POOL_PRESETS,
    ToolPoolMode,
    get_pool_mode_description,
    get_pool_tool_policy,
    list_pool_modes,
)

# ============================================================
# ToolPoolMode 枚举测试
# ============================================================


class TestToolPoolMode:
    """测试 ToolPoolMode 枚举"""

    def test_all_modes_exist(self):
        assert ToolPoolMode.DEFAULT.value == "default"
        assert ToolPoolMode.PLAN.value == "plan"
        assert ToolPoolMode.CODE_REVIEW.value == "code_review"
        assert ToolPoolMode.BARE.value == "bare"

    def test_string_to_enum(self):
        assert ToolPoolMode("default") == ToolPoolMode.DEFAULT
        assert ToolPoolMode("plan") == ToolPoolMode.PLAN

    def test_invalid_string_raises(self):
        with pytest.raises(ValueError):
            ToolPoolMode("nonexistent")

    def test_is_str_enum(self):
        """ToolPoolMode 是 str 枚举，可以直接当字符串用"""
        assert isinstance(ToolPoolMode.PLAN, str)
        assert ToolPoolMode.PLAN == "plan"


# ============================================================
# TOOL_POOL_PRESETS 测试
# ============================================================


class TestPoolPresets:
    """测试预设定义"""

    def test_all_modes_have_presets(self):
        for mode in ToolPoolMode:
            assert mode in TOOL_POOL_PRESETS

    def test_preset_has_required_keys(self):
        for _mode, preset in TOOL_POOL_PRESETS.items():
            assert "allow" in preset
            assert "deny" in preset
            assert "description" in preset
            assert isinstance(preset["allow"], list)
            assert isinstance(preset["deny"], list)

    def test_default_allows_all(self):
        """DEFAULT 预设: allow 和 deny 都为空 = 无限制"""
        preset = TOOL_POOL_PRESETS[ToolPoolMode.DEFAULT]
        assert preset["allow"] == []
        assert preset["deny"] == []

    def test_plan_has_readonly_tools(self):
        """PLAN 预设: 包含只读工具，不含写入工具"""
        preset = TOOL_POOL_PRESETS[ToolPoolMode.PLAN]
        assert "read_file" in preset["allow"]
        assert "grep" in preset["allow"]
        assert "web_search" in preset["allow"]
        # 不应包含写入工具
        assert "write_file" not in preset["allow"]
        assert "edit_file" not in preset["allow"]
        assert "exec" not in preset["allow"]

    def test_code_review_has_code_tools(self):
        """CODE_REVIEW 预设: 包含代码读取工具"""
        preset = TOOL_POOL_PRESETS[ToolPoolMode.CODE_REVIEW]
        assert "read_file" in preset["allow"]
        assert "grep" in preset["allow"]
        # 不应包含写入工具
        assert "write_file" not in preset["allow"]

    def test_bare_denies_all(self):
        """BARE 预设: deny 列表为 ['*']"""
        preset = TOOL_POOL_PRESETS[ToolPoolMode.BARE]
        assert "*" in preset["deny"]

    def test_plan_includes_plan_mode_tools(self):
        """PLAN 预设: 包含 enter/exit_plan_mode"""
        preset = TOOL_POOL_PRESETS[ToolPoolMode.PLAN]
        assert "enter_plan_mode" in preset["allow"]
        assert "exit_plan_mode" in preset["allow"]


# ============================================================
# get_pool_tool_policy 测试
# ============================================================


class TestGetPoolToolPolicy:
    """测试 get_pool_tool_policy 函数"""

    def test_returns_tool_policy(self):
        policy = get_pool_tool_policy(ToolPoolMode.DEFAULT)
        assert isinstance(policy, ToolPolicy)

    def test_default_allows_all(self):
        """DEFAULT 模式: 所有工具都允许"""
        policy = get_pool_tool_policy(ToolPoolMode.DEFAULT)
        allowed, _ = policy.is_allowed("write_file")
        assert allowed is True
        allowed, _ = policy.is_allowed("exec")
        assert allowed is True

    def test_plan_restricts_write_tools(self):
        """PLAN 模式: 写入工具被拒绝（因为不在 allow 列表中）"""
        policy = get_pool_tool_policy(ToolPoolMode.PLAN)
        # 只读工具允许
        allowed, _ = policy.is_allowed("read_file")
        assert allowed is True
        allowed, _ = policy.is_allowed("grep")
        assert allowed is True
        # 写入工具拒绝
        allowed, reason = policy.is_allowed("write_file")
        assert allowed is False
        allowed, reason = policy.is_allowed("exec")
        assert allowed is False

    def test_code_review_restricts_write_tools(self):
        """CODE_REVIEW 模式: 写入工具被拒绝"""
        policy = get_pool_tool_policy(ToolPoolMode.CODE_REVIEW)
        allowed, _ = policy.is_allowed("read_file")
        assert allowed is True
        allowed, _ = policy.is_allowed("write_file")
        assert allowed is False

    def test_bare_denies_all(self):
        """BARE 模式: 拒绝所有已知工具"""
        with patch("app.modules.tools.registry.get_tool_registry") as mock_reg:
            mock_reg.return_value.list_tools.return_value = [
                "read_file",
                "write_file",
                "exec",
                "grep",
            ]
            policy = get_pool_tool_policy(ToolPoolMode.BARE)

        allowed, _ = policy.is_allowed("read_file")
        assert allowed is False
        allowed, _ = policy.is_allowed("write_file")
        assert allowed is False

    def test_string_mode(self):
        """字符串模式名也能工作"""
        policy = get_pool_tool_policy("plan")
        assert isinstance(policy, ToolPolicy)
        allowed, _ = policy.is_allowed("read_file")
        assert allowed is True

    def test_invalid_string_falls_back_to_default(self):
        """无效模式名回退到 DEFAULT"""
        policy = get_pool_tool_policy("invalid_mode")
        assert isinstance(policy, ToolPolicy)
        # DEFAULT 允许所有
        allowed, _ = policy.is_allowed("write_file")
        assert allowed is True

    def test_get_allowed_tools(self):
        """get_allowed_tools 过滤工具列表"""
        policy = get_pool_tool_policy(ToolPoolMode.PLAN)
        all_tools = ["read_file", "write_file", "grep", "exec", "web_search"]
        allowed = policy.get_allowed_tools(all_tools)
        assert "read_file" in allowed
        assert "grep" in allowed
        assert "write_file" not in allowed
        assert "exec" not in allowed


# ============================================================
# 辅助函数测试
# ============================================================


class TestHelperFunctions:
    """测试辅助函数"""

    def test_get_pool_mode_description(self):
        desc = get_pool_mode_description(ToolPoolMode.PLAN)
        assert "只读" in desc or "计划" in desc

    def test_get_pool_mode_description_invalid(self):
        desc = get_pool_mode_description("invalid")
        assert "未知" in desc

    def test_list_pool_modes(self):
        modes = list_pool_modes()
        assert "default" in modes
        assert "plan" in modes
        assert "code_review" in modes
        assert "bare" in modes
        assert len(modes) == 4


# ============================================================
# Plan Mode 集成测试
# ============================================================


class TestPlanModeIntegration:
    """测试 plan mode 与 pool preset 的集成"""

    def test_plan_mode_allowed_tools_from_pool(self):
        """plan_mode.py 的 get_plan_mode_allowed_tools() 来源于 pool preset"""
        # 当工具未注册时，回退到硬编码列表
        from app.modules.tools.plan_mode import get_plan_mode_allowed_tools

        allowed = get_plan_mode_allowed_tools()
        # 硬编码回退列表包含这些只读工具
        assert "read_file" in allowed
        assert "grep" in allowed
        assert "web_search" in allowed
        # 不应包含写入工具
        assert "write_file" not in allowed
        assert "exec" not in allowed

    def test_pool_plan_preset_matches_hardcoded(self):
        """pool PLAN 预设与硬编码回退列表一致"""
        from app.modules.tools.pool_filter import TOOL_POOL_PRESETS, ToolPoolMode

        plan_allow = set(TOOL_POOL_PRESETS[ToolPoolMode.PLAN]["allow"])
        # 回退列表（plan_mode 模块级）
        hardcoded = {
            "read_file",
            "grep",
            "file_search",
            "list_dir",
            "web_search",
            "web_fetch",
            "current_time",
            "calculator",
            "ask_user_question",
            "exit_plan_mode",
            "image",
            "knowledge_search",
            "vector_memory_recall",
            "file_memory_read",
        }
        # pool preset 包含所有回退列表的工具
        assert hardcoded.issubset(plan_allow)
