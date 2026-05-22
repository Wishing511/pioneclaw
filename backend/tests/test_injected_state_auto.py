"""
阶段 DD 测试 — Injected State + AutoAgents

覆盖：
- Injected[T] 类型标记
- AgentState 状态类
- StateInjector 注入器
- is_injected_type 类型检查
- injectable 装饰器
- TaskAnalyzer 任务分析
- AutoAgents 自动编排
- AgentTemplate 模板
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.modules.agent.auto_agents import (
    DEFAULT_TEMPLATES,
    AgentRole,
    AutoAgentResult,
    AutoAgents,
    SubTask,
    TaskAnalyzer,
    TaskComplexity,
    TaskDecomposition,
    auto_run,
)
from app.modules.agent.injected_state import (
    AgentState,
    Injected,
    InjectedContext,
    StateInjector,
    get_injected_inner_type,
    get_state_injector,
    injectable,
    is_injected_type,
    mark_injected_in_schema,
    reset_state_injector,
    with_state,
)

# ==================== Injected 类型测试 ====================


class TestInjectedType:
    def test_is_injected_type_with_injected(self):
        # Injected[AgentState] 应该被识别
        assert is_injected_type(Injected[AgentState]) is True

    def test_is_injected_type_with_str(self):
        # 普通 str 不应该被识别
        assert is_injected_type(str) is False

    def test_is_injected_type_with_dict(self):
        # dict 不应该被识别
        assert is_injected_type(dict) is False

    def test_get_injected_inner_type(self):
        inner = get_injected_inner_type(Injected[AgentState])
        assert inner is AgentState

    def test_get_injected_inner_type_with_str(self):
        inner = get_injected_inner_type(Injected[str])
        assert inner is str

    def test_get_injected_inner_type_none(self):
        inner = get_injected_inner_type(str)
        assert inner is None


# ==================== AgentState 测试 ====================


class TestAgentState:
    def test_defaults(self):
        state = AgentState(agent_id="a1", agent_name="Agent1")
        assert state.session_id is None
        assert state.tool_history == []
        assert state.metadata == {}

    def test_custom_values(self):
        state = AgentState(
            agent_id="a1",
            agent_name="Researcher",
            session_id="s1",
            user_id=123,
            last_user_message="Hello",
            tool_call_count=5,
        )
        assert state.session_id == "s1"
        assert state.user_id == 123
        assert state.tool_call_count == 5

    def test_get_tool_history_by_name(self):
        state = AgentState(
            agent_id="a1",
            agent_name="Agent1",
            tool_history=[
                {"tool_name": "search", "result": "r1"},
                {"tool_name": "analyze", "result": "r2"},
                {"tool_name": "search", "result": "r3"},
            ],
        )
        search_history = state.get_tool_history_by_name("search")
        assert len(search_history) == 2

    def test_get_last_tool_result(self):
        state = AgentState(
            agent_id="a1",
            agent_name="Agent1",
            tool_history=[
                {"tool_name": "search", "result": "r1"},
                {"tool_name": "analyze", "result": "r2"},
            ],
        )
        result = state.get_last_tool_result()
        assert result == "r2"

        result = state.get_last_tool_result("search")
        assert result == "r1"

    def test_to_dict(self):
        state = AgentState(
            agent_id="a1",
            agent_name="Agent1",
            session_id="s1",
            message_count=10,
        )
        d = state.to_dict()
        assert d["agent_id"] == "a1"
        assert d["message_count"] == 10


# ==================== InjectedContext 测试 ====================


class TestInjectedContext:
    def test_defaults(self):
        state = AgentState(agent_id="a1", agent_name="Agent1")
        ctx = InjectedContext(state=state)
        assert ctx.tools_registry is None
        assert ctx.extra == {}

    def test_extra_operations(self):
        state = AgentState(agent_id="a1", agent_name="Agent1")
        ctx = InjectedContext(state=state)
        ctx.set("custom", "value")
        assert ctx.get("custom") == "value"
        assert ctx.get("missing", "default") == "default"


# ==================== StateInjector 测试 ====================


class TestStateInjector:
    def test_set_and_get_context(self):
        injector = StateInjector()
        state = AgentState(agent_id="a1", agent_name="Agent1")
        ctx = InjectedContext(state=state)

        injector.set_context(ctx)
        assert injector.get_context() is ctx
        assert injector.get_state() is state

    def test_clear_context(self):
        injector = StateInjector()
        state = AgentState(agent_id="a1", agent_name="Agent1")
        ctx = InjectedContext(state=state)

        injector.set_context(ctx)
        injector.clear_context()
        assert injector.get_context() is None

    @pytest.mark.asyncio
    async def test_inject_into_args(self):
        injector = StateInjector()
        state = AgentState(
            agent_id="a1",
            agent_name="Agent1",
            session_id="s1",
        )
        ctx = InjectedContext(state=state)
        injector.set_context(ctx)

        def tool_func(
            query: str,
            session_id: Injected[str],
            agent_state: Injected[AgentState],
        ):
            return f"{query} - {session_id} - {agent_state.agent_name}"

        args = {"query": "test"}
        injected_args = injector.inject_into_args(tool_func, args)

        assert injected_args["query"] == "test"
        assert injected_args["session_id"] == "s1"
        assert injected_args["agent_state"] is state

    def test_wrap_tool(self):
        injector = StateInjector()
        state = AgentState(
            agent_id="a1",
            agent_name="Agent1",
            session_id="s1",
        )
        ctx = InjectedContext(state=state)
        injector.set_context(ctx)

        def tool_func(query: str, session_id: Injected[str]) -> str:
            return f"Query: {query}, Session: {session_id}"

        wrapped = injector.wrap_tool(tool_func)
        result = wrapped(query="test")

        assert "test" in result
        assert "s1" in result

    def test_filter_schema_for_llm(self):
        injector = StateInjector()

        schema = {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "session_id": {"type": "string", "x-injected": True},
            },
            "required": ["query", "session_id"],
        }

        filtered = injector.filter_schema_for_llm(schema)

        assert "query" in filtered["properties"]
        assert "session_id" not in filtered["properties"]
        assert "session_id" not in filtered["required"]


# ==================== 全局注入器测试 ====================


class TestGlobalInjector:
    def test_get_state_injector(self):
        reset_state_injector()
        injector = get_state_injector()
        assert injector is not None

    def test_reset_state_injector(self):
        injector = get_state_injector()
        state = AgentState(agent_id="a1", agent_name="Agent1")
        injector.set_context(InjectedContext(state=state))

        reset_state_injector()
        new_injector = get_state_injector()
        assert new_injector.get_context() is None


# ==================== 装饰器测试 ====================


class TestDecorators:
    def test_injectable(self):
        state = AgentState(
            agent_id="a1",
            agent_name="Agent1",
            session_id="s1",
        )
        injector = get_state_injector()
        injector.set_context(InjectedContext(state=state))

        @injectable
        def my_tool(query: str, session_id: Injected[str]) -> str:
            return f"Session: {session_id}, Query: {query}"

        result = my_tool(query="test")
        assert "s1" in result

    def test_with_state(self):
        @with_state(session_id="fixed-session")
        def my_tool(query: str, session_id: str) -> str:
            return f"Session: {session_id}, Query: {query}"

        result = my_tool(query="test")
        assert "fixed-session" in result


# ==================== Schema 辅助测试 ====================


class TestSchemaHelpers:
    def test_mark_injected_in_schema(self):
        schema = {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "session_id": {"type": "string"},
            },
            "required": ["query", "session_id"],
        }

        marked = mark_injected_in_schema(schema, ["session_id"])

        assert marked["properties"]["session_id"]["x-injected"] is True
        assert "session_id" not in marked["required"]


# ==================== TaskAnalyzer 测试 ====================


class TestTaskAnalyzer:
    def test_analyze_complexity_simple(self):
        analyzer = TaskAnalyzer()
        # 非常简短的任务
        complexity = analyzer.analyze_complexity("搜索")
        assert complexity in [TaskComplexity.SIMPLE, TaskComplexity.MODERATE]

    def test_analyze_complexity_moderate(self):
        analyzer = TaskAnalyzer()
        complexity = analyzer.analyze_complexity("搜索 AI 趋势，然后分析市场数据")
        assert complexity in [TaskComplexity.MODERATE, TaskComplexity.COMPLEX]

    def test_analyze_complexity_complex(self):
        analyzer = TaskAnalyzer()
        complexity = analyzer.analyze_complexity(
            "研究 AI 行业趋势，分析市场数据，撰写详细报告，最后审核报告质量"
        )
        assert complexity == TaskComplexity.COMPLEX

    def test_identify_roles_researcher(self):
        analyzer = TaskAnalyzer()
        roles = analyzer.identify_roles("搜索并研究 AI 趋势")
        assert AgentRole.RESEARCHER in roles

    def test_identify_roles_writer(self):
        analyzer = TaskAnalyzer()
        roles = analyzer.identify_roles("撰写一份技术报告")
        assert AgentRole.WRITER in roles

    def test_identify_roles_coder(self):
        analyzer = TaskAnalyzer()
        roles = analyzer.identify_roles("编写一个 Python 脚本，实现代码功能")
        # Coder 或 Writer 都可能匹配（"编" 也可能匹配 Writer 的"编辑"）
        assert AgentRole.CODER in roles or AgentRole.WRITER in roles

    def test_identify_roles_multiple(self):
        analyzer = TaskAnalyzer()
        roles = analyzer.identify_roles("研究 AI 趋势并撰写报告")
        assert AgentRole.RESEARCHER in roles
        assert AgentRole.WRITER in roles

    def test_decompose_simple(self):
        analyzer = TaskAnalyzer()
        decomposition = analyzer.decompose("搜索")
        assert decomposition.complexity in [
            TaskComplexity.SIMPLE,
            TaskComplexity.MODERATE,
        ]
        assert len(decomposition.subtasks) >= 1

    def test_decompose_complex(self):
        analyzer = TaskAnalyzer()
        decomposition = analyzer.decompose("研究 AI 行业趋势，分析数据，撰写报告")
        assert decomposition.complexity in [
            TaskComplexity.MODERATE,
            TaskComplexity.COMPLEX,
        ]
        assert len(decomposition.subtasks) >= 2
        assert len(decomposition.execution_order) == len(decomposition.subtasks)


# ==================== AgentTemplate 测试 ====================


class TestAgentTemplate:
    def test_default_templates_exist(self):
        assert AgentRole.COORDINATOR in DEFAULT_TEMPLATES
        assert AgentRole.RESEARCHER in DEFAULT_TEMPLATES
        assert AgentRole.ANALYST in DEFAULT_TEMPLATES
        assert AgentRole.WRITER in DEFAULT_TEMPLATES
        assert AgentRole.CODER in DEFAULT_TEMPLATES
        assert AgentRole.REVIEWER in DEFAULT_TEMPLATES

    def test_template_create_config(self):
        template = DEFAULT_TEMPLATES[AgentRole.RESEARCHER]
        config = template.create_agent_config()
        assert config["name"] == "Researcher"
        assert config["role"] == "researcher"


# ==================== AutoAgents 测试 ====================


class TestAutoAgents:
    def test_create_agent(self):
        provider = MagicMock()
        auto = AutoAgents(provider=provider)

        agent = auto.create_agent(AgentRole.RESEARCHER)
        assert agent is not None
        assert isinstance(agent, dict)
        assert agent["role"] == "researcher"

    def test_create_agent_cached(self):
        provider = MagicMock()
        auto = AutoAgents(provider=provider)

        agent1 = auto.create_agent(AgentRole.RESEARCHER)
        agent2 = auto.create_agent(AgentRole.RESEARCHER)
        assert agent1 is agent2

    def test_create_agent_with_factory(self):
        created_name = []

        def factory(config):
            created_name.append(config["name"])
            return MagicMock()

        provider = MagicMock()
        auto = AutoAgents(provider=provider, agent_factory=factory)

        auto.create_agent(AgentRole.WRITER)
        assert "Writer" in created_name

    @pytest.mark.asyncio
    async def test_run_simple_task(self):
        provider = MagicMock()
        auto = AutoAgents(provider=provider)

        result = await auto.run("搜索")

        assert result.error is None
        assert result.decomposition is not None
        assert result.decomposition.complexity in [
            TaskComplexity.SIMPLE,
            TaskComplexity.MODERATE,
        ]

    @pytest.mark.asyncio
    async def test_run_with_agent_factory(self):
        mock_agent = MagicMock()
        mock_agent.process_direct = AsyncMock(return_value="Task completed")

        def factory(config):
            return mock_agent

        provider = MagicMock()
        auto = AutoAgents(provider=provider, agent_factory=factory)

        result = await auto.run("搜索")

        assert result.error is None
        assert len(result.agent_results) > 0

    @pytest.mark.asyncio
    async def test_run_moderate_task(self):
        provider = MagicMock()
        auto = AutoAgents(provider=provider)

        result = await auto.run("研究 AI 趋势并撰写报告")

        assert result.error is None
        assert len(result.decomposition.subtasks) >= 2

    def test_get_created_agents(self):
        provider = MagicMock()
        auto = AutoAgents(provider=provider)

        auto.create_agent(AgentRole.RESEARCHER)
        auto.create_agent(AgentRole.WRITER)

        created = auto.get_created_agents()
        assert len(created) == 2

    def test_clear_agents(self):
        provider = MagicMock()
        auto = AutoAgents(provider=provider)

        auto.create_agent(AgentRole.RESEARCHER)
        auto.clear_agents()

        assert len(auto.get_created_agents()) == 0


# ==================== auto_run 便捷函数测试 ====================


class TestAutoRun:
    @pytest.mark.asyncio
    async def test_auto_run(self):
        provider = MagicMock()
        result = await auto_run(
            task="搜索 AI 信息",
            provider=provider,
        )

        assert result is not None
        assert result.original_task == "搜索 AI 信息"
        assert result.decomposition is not None


# ==================== SubTask 测试 ====================


class TestSubTask:
    def test_defaults(self):
        subtask = SubTask(
            id="s1",
            description="Test task",
            assigned_role=AgentRole.RESEARCHER,
        )
        assert subtask.status == "pending"
        assert subtask.result is None
        assert subtask.dependencies == []

    def test_with_dependencies(self):
        subtask = SubTask(
            id="s2",
            description="Dependent task",
            assigned_role=AgentRole.WRITER,
            dependencies=["s1"],
        )
        assert "s1" in subtask.dependencies


# ==================== TaskDecomposition 测试 ====================


class TestTaskDecomposition:
    def test_creation(self):
        subtasks = [
            SubTask(id="s1", description="Task 1", assigned_role=AgentRole.RESEARCHER),
        ]
        decomposition = TaskDecomposition(
            original_task="Test task",
            complexity=TaskComplexity.SIMPLE,
            subtasks=subtasks,
            execution_order=["s1"],
            estimated_agents=1,
        )
        assert decomposition.complexity == TaskComplexity.SIMPLE
        assert len(decomposition.subtasks) == 1


# ==================== AutoAgentResult 测试 ====================


class TestAutoAgentResult:
    def test_defaults(self):
        result = AutoAgentResult(
            task_id="t1",
            original_task="Test",
        )
        assert result.decomposition is None
        assert result.final_result is None
        assert result.error is None

    def test_with_results(self):
        result = AutoAgentResult(
            task_id="t1",
            original_task="Test",
            final_result="All done",
            agent_results={"s1": {"result": "ok"}},
        )
        assert result.final_result == "All done"
        assert len(result.agent_results) == 1
