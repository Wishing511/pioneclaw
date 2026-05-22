"""
TT 工具测试：TT.1 + TT.2 新工具的单测

覆盖：
- AskUserQuestionTool (TT.1)
- ConfigTool (TT.1)
- TeamCreateTool / TeamDeleteTool (TT.1)
- SendMessageTool (TT.1)
- EnterPlanModeTool / ExitPlanModeTool (TT.1)
- BuiltinAgentType 枚举 (TT.2)
- SpawnTool verification/guide 类型 (TT.2)
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.modules.agent.subagent import BuiltinAgentType
from app.modules.tools.builtin import AskUserQuestionTool, SpawnTool
from app.modules.tools.config import ConfigTool
from app.modules.tools.plan_mode import EnterPlanModeTool, ExitPlanModeTool
from app.modules.tools.send_message import SendMessageTool
from app.modules.tools.team import TeamCreateTool, TeamDeleteTool

# ── 辅助函数 ──────────────────────────────────────────────────


def _make_async_db_session_mock(scalars_list=None, scalar_one=None):
    """构造完整的异步 DB session mock 链

    Args:
        scalars_list: result.scalars().all() 返回值列表
        scalar_one: result.scalar_one_or_none() 返回值
    """
    if scalars_list is None:
        scalars_list = []
    mock_scalars = MagicMock()
    mock_scalars.all = MagicMock(return_value=scalars_list)

    mock_result = MagicMock()
    mock_result.scalars = MagicMock(return_value=mock_scalars)
    mock_result.scalar_one_or_none = MagicMock(return_value=scalar_one)

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_session.add = MagicMock()
    mock_session.delete = AsyncMock()
    mock_session.commit = AsyncMock()
    mock_session.refresh = AsyncMock()
    mock_session.rollback = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=None)

    return MagicMock(return_value=mock_session)


# ============================================================
# AskUserQuestionTool 测试
# ============================================================


class TestAskUserQuestionTool:
    """测试 AskUserQuestionTool"""

    @pytest.fixture
    def tool(self):
        return AskUserQuestionTool()

    def test_questions_parameter_exists(self, tool):
        assert "questions" in tool.parameters

    def test_questions_is_required(self, tool):
        assert "questions" in tool.required

    @pytest.mark.asyncio
    async def test_invalid_json_questions(self, tool):
        """非法 JSON 返回错误"""
        result = await tool.execute(questions="not valid json")
        data = json.loads(result)
        assert data["success"] is False
        assert "JSON" in data["error"]

    @pytest.mark.asyncio
    async def test_empty_array_questions(self, tool):
        """空数组返回错误"""
        result = await tool.execute(questions="[]")
        data = json.loads(result)
        assert data["success"] is False
        assert "非空" in data["error"]

    @pytest.mark.asyncio
    async def test_missing_question_field(self, tool):
        """缺少 question 字段"""
        result = await tool.execute(
            questions='[{"header": "test", "options": [{"label": "A", "description": "desc"}]}]'
        )
        data = json.loads(result)
        assert data["success"] is False
        assert "question" in data["error"]

    @pytest.mark.asyncio
    async def test_missing_options_field(self, tool):
        """缺少 options 字段"""
        result = await tool.execute(questions='[{"question": "What to do?"}]')
        data = json.loads(result)
        assert data["success"] is False
        assert "options" in data["error"]

    @pytest.mark.asyncio
    async def test_not_a_dict_item(self, tool):
        """数组中元素不是对象"""
        result = await tool.execute(questions='["string instead of object"]')
        data = json.loads(result)
        assert data["success"] is False
        assert "有效的对象" in data["error"]

    def test_parameter_type_is_string(self, tool):
        assert tool.parameters["questions"].type == "string"


# ============================================================
# ConfigTool 测试
# ============================================================


class TestConfigTool:
    """测试 ConfigTool"""

    @pytest.fixture
    def tool(self):
        return ConfigTool()

    def test_action_parameter_enum(self, tool):
        assert tool.parameters["action"].enum == ["list", "get", "set"]

    def test_action_is_required(self, tool):
        assert "action" in tool.required

    @pytest.mark.asyncio
    async def test_list_returns_settings(self, tool):
        """模拟 DB 返回设置列表"""
        from app.models.models import SystemSetting

        mock_setting = MagicMock(spec=SystemSetting)
        mock_setting.key = "max_turns"
        mock_setting.value = "20"
        mock_setting.category = "execution"
        mock_setting.description = "Max conversation turns"

        db_mock = _make_async_db_session_mock(scalars_list=[mock_setting])

        with patch("app.core.database.async_session_maker", db_mock):
            result = await tool.execute(action="list")
            data = json.loads(result)
            assert data["success"] is True
            assert len(data["settings"]) == 1
            assert data["settings"][0]["key"] == "max_turns"

    @pytest.mark.asyncio
    async def test_get_missing_key(self, tool):
        """get 操作缺少 key"""
        result = await tool.execute(action="get")
        data = json.loads(result)
        assert data["success"] is False
        assert "key" in data["error"]

    @pytest.mark.asyncio
    async def test_get_nonexistent_setting(self, tool):
        """get 不存在的配置项"""
        db_mock = _make_async_db_session_mock(scalar_one=None)

        with patch("app.core.database.async_session_maker", db_mock):
            result = await tool.execute(action="get", key="nonexistent")
            data = json.loads(result)
            assert data["success"] is False
            assert "不存在" in data["error"]

    @pytest.mark.asyncio
    async def test_set_blacklisted_key_fails(self, tool):
        """写入禁止的 key 返回错误"""
        result = await tool.execute(action="set", key="token_expiry", value="3600")
        data = json.loads(result)
        assert data["success"] is False
        assert "不允许" in data["error"]

    @pytest.mark.asyncio
    async def test_set_missing_key(self, tool):
        """set 操作缺少 key"""
        result = await tool.execute(action="set")
        data = json.loads(result)
        assert data["success"] is False
        assert "key" in data["error"]

    @pytest.mark.asyncio
    async def test_get_blacklisted_read_key(self, tool):
        """get 黑名单中的 key（当前 READ_BLACKLIST 为空，验证逻辑存在）"""
        # _READ_BLACKLIST 当前为空，这是设计预期的——SystemSetting 无极度敏感项
        # 如果未来添加了读黑名单，此测试结构提供了覆盖
        result = await tool.execute(action="get", key="token_expiry")
        data = json.loads(result)
        # token_expiry 不在 _READ_BLACKLIST 中，所以应该去查 DB
        assert data["success"] is False  # DB 中没有这个配置项

    @pytest.mark.asyncio
    async def test_unknown_action(self, tool):
        result = await tool.execute(action="unknown")
        data = json.loads(result)
        assert data["success"] is False
        assert "未知操作" in data["error"]


# ============================================================
# TeamCreateTool / TeamDeleteTool 测试
# ============================================================


class TestTeamTools:
    """测试 TeamCreateTool 和 TeamDeleteTool"""

    @pytest.fixture(autouse=True)
    def _clean_teams_registry(self):
        """每个测试前后清理运行时团队注册表"""
        import app.modules.tools.team as team_module

        team_module._teams.clear()
        yield
        team_module._teams.clear()

    @pytest.fixture
    def create_tool(self):
        return TeamCreateTool()

    @pytest.fixture
    def delete_tool(self):
        return TeamDeleteTool()

    def test_team_create_name_required(self, create_tool):
        assert "name" in create_tool.required

    def test_team_delete_team_id_required(self, delete_tool):
        assert "team_id" in delete_tool.required

    @pytest.mark.asyncio
    async def test_team_create_success(self, create_tool):
        """模拟 DB 创建成功"""

        from app.models.organization import Organization

        MagicMock(spec=Organization)
        db_mock = _make_async_db_session_mock()

        with patch("app.core.database.async_session_maker", db_mock):
            result = await create_tool.execute(
                name="test-team",
                description="Test team",
                member_agent_ids='["agent1", "agent2"]',
            )
            data = json.loads(result)
            assert data["success"] is True
            assert data["name"] == "test-team"
            assert data["member_count"] == 2
            assert "team_id" in data

    @pytest.mark.asyncio
    async def test_team_create_invalid_member_json(self, create_tool):
        """member_agent_ids 非法 JSON"""
        db_mock = _make_async_db_session_mock()

        with patch("app.core.database.async_session_maker", db_mock):
            result = await create_tool.execute(
                name="bad-team",
                member_agent_ids="not-json",
            )
            data = json.loads(result)
            assert data["success"] is False
            assert "JSON" in data["error"]

    @pytest.mark.asyncio
    async def test_team_create_members_not_array(self, create_tool):
        """member_agent_ids 不是数组"""
        db_mock = _make_async_db_session_mock()

        with patch("app.core.database.async_session_maker", db_mock):
            result = await create_tool.execute(
                name="bad-team",
                member_agent_ids='"string"',
            )
            data = json.loads(result)
            assert data["success"] is False
            assert "数组" in data["error"]

    @pytest.mark.asyncio
    async def test_team_delete_nonexistent(self, delete_tool):
        """删除不存在的 team"""
        db_mock = _make_async_db_session_mock(scalar_one=None)

        with patch("app.core.database.async_session_maker", db_mock):
            result = await delete_tool.execute(team_id="nonexistent-id")
            data = json.loads(result)
            assert data["success"] is False
            assert "不存在" in data["error"]

    @pytest.mark.asyncio
    async def test_team_delete_success(self, delete_tool):
        """删除成功（DB 中有记录）"""
        mock_org = MagicMock()
        db_mock = _make_async_db_session_mock(scalar_one=mock_org)

        with patch("app.core.database.async_session_maker", db_mock):
            result = await delete_tool.execute(team_id="existing-id")
            data = json.loads(result)
            assert data["success"] is True
            assert data["db_deleted"] is True

    @pytest.mark.asyncio
    async def test_team_create_no_description(self, create_tool):
        """无 description 的创建"""
        db_mock = _make_async_db_session_mock()

        with patch("app.core.database.async_session_maker", db_mock):
            result = await create_tool.execute(name="minimal-team")
            data = json.loads(result)
            assert data["success"] is True
            assert data["member_count"] == 0


# ============================================================
# SendMessageTool 测试
# ============================================================


class TestSendMessageTool:
    """测试 SendMessageTool"""

    @pytest.fixture(autouse=True)
    def _clean_agent_state(self):
        """每个测试前后清理 Agent 注册表"""
        import app.modules.tools.send_message as sm

        sm._agent_inboxes.clear()
        sm._agent_metadata.clear()
        yield
        sm._agent_inboxes.clear()
        sm._agent_metadata.clear()

    @pytest.fixture
    def tool(self):
        return SendMessageTool()

    def test_action_parameter_enum(self, tool):
        assert tool.parameters["action"].enum == ["send", "list_agents", "send_to_team"]

    def test_action_is_required(self, tool):
        assert "action" in tool.required

    @pytest.mark.asyncio
    async def test_list_agents_empty(self, tool):
        """无 Agent 注册时返回空列表"""
        result = await tool.execute(action="list_agents")
        data = json.loads(result)
        assert data["success"] is True
        assert data["agents"] == []
        assert data["total"] == 0

    @pytest.mark.asyncio
    async def test_list_agents_with_registered(self, tool):
        """有注册 Agent 时返回列表"""
        import app.modules.tools.send_message as sm

        sm.register_agent("agent-1", "Agent One")
        sm.register_agent("agent-2", "Agent Two")

        result = await tool.execute(action="list_agents")
        data = json.loads(result)
        assert data["success"] is True
        assert data["total"] == 2
        assert len(data["agents"]) == 2
        ids = {a["agent_id"] for a in data["agents"]}
        assert ids == {"agent-1", "agent-2"}

    @pytest.mark.asyncio
    async def test_send_missing_target(self, tool):
        """send 缺少 target_agent"""
        result = await tool.execute(action="send")
        data = json.loads(result)
        assert data["success"] is False
        assert "target_agent" in data["error"]

    @pytest.mark.asyncio
    async def test_send_missing_message(self, tool):
        """send 缺少 message"""
        result = await tool.execute(action="send", target_agent="agent-1")
        data = json.loads(result)
        assert data["success"] is False
        assert "message" in data["error"]

    @pytest.mark.asyncio
    async def test_send_to_nonexistent_agent(self, tool):
        """发送给不存在的 Agent"""
        result = await tool.execute(
            action="send",
            target_agent="nonexistent",
            message="hello",
        )
        data = json.loads(result)
        assert data["success"] is False
        assert "不存在" in data["error"] or "离线" in data["error"]

    @pytest.mark.asyncio
    async def test_send_to_agent_success(self, tool):
        """发送成功"""
        import app.modules.tools.send_message as sm

        sm.register_agent("agent-1", "Agent One")

        result = await tool.execute(
            action="send",
            target_agent="agent-1",
            message="hello world",
        )
        data = json.loads(result)
        assert data["success"] is True
        assert "已发送" in data["message"]

    @pytest.mark.asyncio
    async def test_send_to_team_missing_team_id(self, tool):
        result = await tool.execute(action="send_to_team")
        data = json.loads(result)
        assert data["success"] is False
        assert "team_id" in data["error"]

    @pytest.mark.asyncio
    async def test_send_to_team_missing_message(self, tool):
        result = await tool.execute(action="send_to_team", team_id="team-1")
        data = json.loads(result)
        assert data["success"] is False
        assert "message" in data["error"]

    @pytest.mark.asyncio
    async def test_unknown_action(self, tool):
        result = await tool.execute(action="unknown_action")
        data = json.loads(result)
        assert data["success"] is False
        assert "未知操作" in data["error"]


# ============================================================
# EnterPlanModeTool / ExitPlanModeTool 测试
# ============================================================


class TestPlanModeTools:
    """测试 EnterPlanModeTool 和 ExitPlanModeTool"""

    @pytest.fixture(autouse=True)
    def _reset_plan_state(self):
        """每个测试前后重置 plan mode 全局状态"""
        import app.modules.tools.plan_mode as pm

        old_active = pm._plan_mode_active
        old_content = pm._plan_content
        old_session = pm._plan_session_start
        pm._plan_mode_active = False
        pm._plan_content = ""
        pm._plan_session_start = None
        yield
        pm._plan_mode_active = old_active
        pm._plan_content = old_content
        pm._plan_session_start = old_session

    @pytest.fixture
    def enter_tool(self):
        return EnterPlanModeTool()

    @pytest.fixture
    def exit_tool(self):
        return ExitPlanModeTool()

    @pytest.mark.asyncio
    async def test_enter_plan_mode_no_params(self, enter_tool):
        """EnterPlanMode 无参数，激活全局状态"""
        import app.modules.tools.plan_mode as pm

        result = await enter_tool.execute()
        assert "计划模式" in result
        assert pm.is_plan_mode_active() is True

    def test_exit_plan_mode_plan_required(self, exit_tool):
        assert "plan" in exit_tool.required

    @pytest.mark.asyncio
    async def test_exit_plan_mode_saves_file(self, exit_tool):
        """退出计划模式，写入 .md 到 .claude/plans/"""
        import app.modules.tools.plan_mode as pm

        pm._plan_mode_active = True
        pm._plan_session_start = "2026-05-11T00:00:00"

        plan_text = (
            "# Test Plan\n\n"
            "## Context\nThis is a test context for planning.\n\n"
            "## Plan\n1. Step one\n2. Step two\n\n"
            "## Verification\nRun tests and verify.\n"
        )

        with (
            patch("builtins.open", MagicMock()) as mock_open,
            patch("os.makedirs", MagicMock()),
        ):
            result = await exit_tool.execute(plan=plan_text)
            assert "计划已保存" in result
            assert pm.is_plan_mode_active() is False
            mock_open.assert_called_once()

    @pytest.mark.asyncio
    async def test_exit_plan_mode_short_plan_fails(self, exit_tool):
        """计划 < 50 字符返回错误"""
        import app.modules.tools.plan_mode as pm

        pm._plan_mode_active = True

        result = await exit_tool.execute(plan="Too short")
        assert "错误" in result or "太短" in result or "50" in result

    @pytest.mark.asyncio
    async def test_exit_plan_mode_empty_plan_fails(self, exit_tool):
        """空计划返回错误"""
        result = await exit_tool.execute(plan="")
        assert "错误" in result or "不能为空" in result

    @pytest.mark.asyncio
    async def test_enter_then_exit_flow(self, enter_tool, exit_tool):
        """完整的进入→退出流程"""
        import app.modules.tools.plan_mode as pm

        # 进入
        await enter_tool.execute()
        assert pm.is_plan_mode_active() is True

        # 退出
        plan = "Context: test\n\nPlan: do X then Y\n\nFiles: a.py, b.py\n\nVerify: run tests to confirm"
        with patch("builtins.open", MagicMock()), patch("os.makedirs", MagicMock()):
            result2 = await exit_tool.execute(plan=plan)
            assert "计划已保存" in result2
            assert pm.is_plan_mode_active() is False


# ============================================================
# BuiltinAgentType 枚举测试 (TT.2)
# ============================================================


class TestBuiltinAgentType:
    """测试 BuiltinAgentType 枚举"""

    def test_all_seven_types_present(self):
        values = {t.value for t in BuiltinAgentType}
        assert values == {
            "general",
            "research",
            "build",
            "explore",
            "plan",
            "verification",
            "guide",
        }

    def test_type_values_match_spawn_tool(self):
        """枚举值应与 SpawnTool 中使用的字符串匹配"""
        # SpawnTool 使用这些 agent type 字符串
        spawn_types = set(SpawnTool._AGENT_TYPE_TOOLS.keys())
        # BuiltinAgentType 涵盖了 SpawnTool 的所有类型
        builtin_values = {t.value for t in BuiltinAgentType}
        for st in spawn_types:
            assert st in builtin_values, (
                f"SpawnTool type '{st}' missing from BuiltinAgentType"
            )

    def test_is_string_enum(self):
        """验证是 str+Enum，可以直接用于字符串比较"""
        assert isinstance(BuiltinAgentType.GENERAL, str)
        assert BuiltinAgentType.GENERAL == "general"
        assert BuiltinAgentType.GENERAL.value == "general"


# ============================================================
# SpawnTool TT.2 新类型测试
# ============================================================


class TestSpawnToolTT:
    """SpawnTool 的 verification 和 guide 类型测试"""

    def test_five_agent_types_in_param_enum(self):
        param = SpawnTool.parameters["agent_type"]
        assert len(param.enum) == 5
        assert "verification" in param.enum
        assert "guide" in param.enum

    def test_verification_has_exec(self):
        tools = SpawnTool._AGENT_TYPE_TOOLS["verification"]
        assert "exec" in tools
        assert "read_file" in tools
        assert "edit_file" not in tools
        assert "spawn" not in tools

    def test_guide_has_knowledge(self):
        tools = SpawnTool._AGENT_TYPE_TOOLS["guide"]
        assert "knowledge_search" in tools
        assert "vector_memory_recall" in tools
        assert "file_memory_read" in tools
        assert "read_file" in tools
        assert "exec" not in tools
        assert "spawn" not in tools

    def test_all_types_have_tool_sets(self):
        """每种类型都有有效的工具集（None 或 set）"""
        for agent_type in SpawnTool._AGENT_TYPE_TOOLS:
            tools = SpawnTool._AGENT_TYPE_TOOLS[agent_type]
            assert tools is None or isinstance(tools, (set, frozenset))
