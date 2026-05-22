"""
阶段 BB 测试 — Handoff 统一委托机制

覆盖：
- ContextPolicy 各模式过滤
- Handoff 创建和配置
- Handoff.to_tool() 工具定义
- 循环检测
- 深度限制
- handoff_filters 预置过滤器
- parallel_handoffs 并行执行
- HandoffTracker 追踪器
- AgentLoop Handoff 集成
- SubagentManager handoff_to/parallel_handoffs
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.modules.agent.handoff import (
    ContextPolicy,
    CycleDetectedError,
    Handoff,
    HandoffConfig,
    HandoffDepthExceededError,
    HandoffResult,
    HandoffTracker,
    get_handoff_tracker,
    handoff_filters,
    parallel_handoffs,
    reset_handoff_tracker,
)

# ==================== ContextPolicy 测试 ====================


class TestContextPolicy:
    def test_values(self):
        assert ContextPolicy.FULL.value == "full"
        assert ContextPolicy.SUMMARY.value == "summary"
        assert ContextPolicy.NONE.value == "none"
        assert ContextPolicy.LAST_N.value == "last_n"

    def test_all_policies_exist(self):
        policies = {p.value for p in ContextPolicy}
        assert policies == {"full", "summary", "none", "last_n"}


class TestContextPolicyFiltering:
    """上下文策略过滤测试"""

    def _make_messages(self) -> list[dict]:
        return [
            {"role": "system", "content": "You are helpful"},
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi"},
            {"role": "user", "content": "How are you?"},
            {"role": "assistant", "content": "Good"},
            {"role": "tool", "content": "tool result"},
        ]

    def test_none_policy_returns_empty(self):
        config = HandoffConfig(context_policy=ContextPolicy.NONE)
        handoff = Handoff(target_agent=MagicMock(), config=config)
        result = handoff._apply_context_policy(self._make_messages())
        assert result == []

    def test_full_policy_returns_all(self):
        config = HandoffConfig(context_policy=ContextPolicy.FULL)
        handoff = Handoff(target_agent=MagicMock(), config=config)
        result = handoff._apply_context_policy(self._make_messages())
        assert len(result) == 6

    def test_last_n_policy_keeps_last_n(self):
        config = HandoffConfig(
            context_policy=ContextPolicy.LAST_N,
            max_context_messages=2,
        )
        handoff = Handoff(target_agent=MagicMock(), config=config)
        result = handoff._apply_context_policy(self._make_messages())
        # 应该保留 system + 最后 2 条非 system
        assert len(result) == 3
        assert result[0]["role"] == "system"

    def test_last_n_without_preserve_system(self):
        config = HandoffConfig(
            context_policy=ContextPolicy.LAST_N,
            max_context_messages=2,
            preserve_system=False,
        )
        handoff = Handoff(target_agent=MagicMock(), config=config)
        result = handoff._apply_context_policy(self._make_messages())
        assert len(result) == 2

    def test_summary_policy_keeps_system_and_recent(self):
        config = HandoffConfig(context_policy=ContextPolicy.SUMMARY)
        handoff = Handoff(target_agent=MagicMock(), config=config)
        result = handoff._apply_context_policy(self._make_messages())
        # SUMMARY 保留 system + 最近 3 条
        assert result[0]["role"] == "system"

    def test_empty_messages_returns_empty(self):
        config = HandoffConfig(context_policy=ContextPolicy.FULL)
        handoff = Handoff(target_agent=MagicMock(), config=config)
        result = handoff._apply_context_policy([])
        assert result == []


# ==================== HandoffConfig 测试 ====================


class TestHandoffConfig:
    def test_defaults(self):
        config = HandoffConfig()
        assert config.context_policy == ContextPolicy.SUMMARY
        assert config.max_context_tokens == 4000
        assert config.max_context_messages == 10
        assert config.preserve_system is True
        assert config.detect_cycles is True
        assert config.max_depth == 10
        assert config.timeout_seconds == 300.0

    def test_custom_values(self):
        config = HandoffConfig(
            context_policy=ContextPolicy.NONE,
            max_depth=5,
            detect_cycles=False,
        )
        assert config.context_policy == ContextPolicy.NONE
        assert config.max_depth == 5
        assert config.detect_cycles is False


# ==================== Handoff 创建和配置 ====================


class TestHandoffCreation:
    def test_basic_creation(self):
        target = MagicMock()
        target.name = "Researcher"
        handoff = Handoff(target)
        assert handoff.target_agent is target
        assert "researcher" in handoff.tool_name

    def test_tool_name_override(self):
        target = MagicMock()
        target.name = "Researcher"
        handoff = Handoff(target, tool_name_override="ask_researcher")
        assert handoff.tool_name == "ask_researcher"

    def test_tool_description_override(self):
        target = MagicMock()
        target.name = "Researcher"
        handoff = Handoff(
            target, tool_description_override="Ask the researcher for help"
        )
        assert handoff.tool_description == "Ask the researcher for help"

    def test_config_passed(self):
        target = MagicMock()
        config = HandoffConfig(max_depth=5)
        handoff = Handoff(target, config=config)
        assert handoff.config.max_depth == 5

    def test_repr(self):
        target = MagicMock()
        target.name = "Analyst"
        handoff = Handoff(target)
        assert "Analyst" in repr(handoff)
        assert "summary" in repr(handoff)


# ==================== Handoff.to_tool() ====================


class TestHandoffToTool:
    def test_returns_openai_format(self):
        target = MagicMock()
        target.name = "Researcher"
        handoff = Handoff(target)
        tool = handoff.to_tool()

        assert tool["type"] == "function"
        assert "function" in tool
        assert tool["function"]["name"] == handoff.tool_name
        assert "parameters" in tool["function"]

    def test_parameters_schema(self):
        target = MagicMock()
        target.name = "Researcher"
        handoff = Handoff(target)
        tool = handoff.to_tool()

        params = tool["function"]["parameters"]
        assert params["type"] == "object"
        assert "prompt" in params["properties"]
        assert "prompt" in params["required"]

    def test_custom_name_in_tool(self):
        target = MagicMock()
        target.name = "Researcher"
        handoff = Handoff(target, tool_name_override="delegate_to_research")
        tool = handoff.to_tool()
        assert tool["function"]["name"] == "delegate_to_research"


# ==================== 循环检测 ====================


class TestCycleDetection:
    def test_direct_cycle_detected(self):
        # Agent 委托给自己
        target = MagicMock()
        target.id = "agent-1"
        target.name = "Self"

        handoff = Handoff(target)
        handoff._execution_chain = ["agent-1"]

        with pytest.raises(CycleDetectedError):
            handoff._check_cycle("agent-1")

    def test_indirect_cycle_detected(self):
        target = MagicMock()
        target.id = "agent-1"
        target.name = "Agent1"

        handoff = Handoff(target)
        # agent-1 -> agent-2 -> agent-1
        handoff._execution_chain = ["agent-1", "agent-2"]

        with pytest.raises(CycleDetectedError):
            handoff._check_cycle("agent-1")

    def test_no_cycle_allowed(self):
        """检测没有循环的情况"""
        target = MagicMock()
        target.id = "agent-2"
        target.name = "Agent2"

        handoff = Handoff(target)
        handoff._execution_chain = ["agent-0"]  # 执行链是 agent-0
        handoff._target_id = "agent-2"  # 目标是 agent-2

        # 检查 source=agent-1 是否有循环
        # agent-1 != agent-2（不是直接循环）
        # agent-1 not in ["agent-0"]（不是间接循环）
        # 所以不应该抛异常
        handoff._check_cycle("agent-1")

    def test_cycle_detection_disabled(self):
        target = MagicMock()
        target.id = "agent-1"
        target.name = "Self"

        config = HandoffConfig(detect_cycles=False)
        handoff = Handoff(target, config=config)
        handoff._execution_chain = ["agent-1"]

        # 禁用检测后不抛异常
        handoff._check_cycle("agent-1")


# ==================== 深度限制 ====================


class TestDepthLimit:
    @pytest.mark.asyncio
    async def test_depth_exceeded_raises(self):
        target = MagicMock()
        target.name = "Target"
        target.run = AsyncMock(return_value="result")

        config = HandoffConfig(max_depth=2)
        handoff = Handoff(target, config=config)

        with pytest.raises(HandoffDepthExceededError):
            await handoff.execute(
                source_agent=MagicMock(),
                prompt="test",
                depth=2,  # 等于 max_depth
            )

    @pytest.mark.asyncio
    async def test_depth_within_limit_ok(self):
        target = MagicMock()
        target.name = "Target"
        target.run = AsyncMock(return_value="result")

        config = HandoffConfig(max_depth=5)
        handoff = Handoff(target, config=config)

        result = await handoff.execute(
            source_agent=MagicMock(),
            prompt="test",
            depth=2,  # 小于 max_depth
        )
        assert result.error is None


# ==================== Handoff 执行 ====================


class TestHandoffExecute:
    @pytest.mark.asyncio
    async def test_execute_with_run_method(self):
        target = MagicMock()
        target.name = "Researcher"
        target.run = AsyncMock(return_value="research result")

        handoff = Handoff(target)
        result = await handoff.execute(
            source_agent=MagicMock(),
            prompt="Research AI trends",
        )

        assert result.error is None
        assert result.result == "research result"
        assert result.target_agent_name == "Researcher"

    @pytest.mark.asyncio
    async def test_execute_with_process_direct_method(self):
        target = MagicMock()
        target.name = "Analyst"
        target.id = "analyst-1"
        # 删除 run 方法，让代码使用 process_direct
        delattr(target, "run")
        # process_direct 需要是 AsyncMock
        target.process_direct = AsyncMock(return_value="analysis result")

        handoff = Handoff(target)
        result = await handoff.execute(
            source_agent=MagicMock(),
            prompt="Analyze data",
        )

        assert result.error is None
        assert result.result == "analysis result"

    @pytest.mark.asyncio
    async def test_execute_with_callback(self):
        target = MagicMock()
        target.name = "Writer"
        target.run = AsyncMock(return_value="written")

        callback_called = []

        async def callback(source, result):
            callback_called.append((source, result))

        handoff = Handoff(target, on_handoff=callback)
        await handoff.execute(
            source_agent=MagicMock(),
            prompt="Write report",
        )

        assert len(callback_called) == 1

    @pytest.mark.asyncio
    async def test_execute_error_handling(self):
        target = MagicMock()
        target.name = "FailingAgent"
        target.run = AsyncMock(side_effect=Exception("Agent failed"))

        handoff = Handoff(target)
        result = await handoff.execute(
            source_agent=MagicMock(),
            prompt="test",
        )

        assert result.error == "Agent failed"


# ==================== handoff_filters ====================


class TestHandoffFilters:
    def test_remove_all_tools(self):
        messages = [
            {"role": "user", "content": "hi"},
            {"role": "tool", "content": "result"},
            {"role": "assistant", "content": "ok", "tool_call_id": "123"},
        ]
        result = handoff_filters.remove_all_tools(messages)
        assert len(result) == 1
        assert result[0]["role"] == "user"

    def test_keep_last_n_messages(self):
        messages = [{"role": "user", "content": str(i)} for i in range(10)]
        filter_fn = handoff_filters.keep_last_n_messages(3)
        result = filter_fn(messages)
        assert len(result) == 3
        assert result[0]["content"] == "7"

    def test_keep_only_user_assistant(self):
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "ok"},
            {"role": "tool", "content": "result"},
        ]
        result = handoff_filters.keep_only_user_assistant(messages)
        assert len(result) == 2

    def test_remove_system(self):
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hi"},
        ]
        result = handoff_filters.remove_system(messages)
        assert len(result) == 1
        assert result[0]["role"] == "user"


# ==================== parallel_handoffs ====================


class TestParallelHandoffs:
    @pytest.mark.asyncio
    async def test_parallel_execution(self):
        # 创建多个目标 Agent
        targets = []
        for i in range(3):
            agent = MagicMock()
            agent.name = f"Agent{i}"
            agent.run = AsyncMock(return_value=f"result{i}")
            targets.append(agent)

        results = await parallel_handoffs(
            source_agent=MagicMock(),
            targets=[(t, f"task{i}") for i, t in enumerate(targets)],
            max_concurrent=2,
        )

        assert len(results) == 3
        for i, r in enumerate(results):
            assert r.error is None
            assert r.result == f"result{i}"

    @pytest.mark.asyncio
    async def test_parallel_with_custom_config(self):
        agent = MagicMock()
        agent.name = "Agent"
        agent.run = AsyncMock(return_value="result")

        config = HandoffConfig(context_policy=ContextPolicy.NONE)
        results = await parallel_handoffs(
            source_agent=MagicMock(),
            targets=[(agent, "task", config)],
            max_concurrent=1,
        )

        assert len(results) == 1
        assert results[0].error is None


# ==================== HandoffTracker ====================


class TestHandoffTracker:
    def test_push_pop(self):
        tracker = HandoffTracker()
        tracker.push("agent-1")
        tracker.push("agent-2")
        assert tracker.get_chain() == ["agent-1", "agent-2"]
        assert tracker.pop() == "agent-2"
        assert tracker.get_chain() == ["agent-1"]

    def test_check_cycle(self):
        tracker = HandoffTracker()
        tracker.push("agent-1")
        tracker.push("agent-2")
        assert tracker.check_cycle("agent-1") is True
        assert tracker.check_cycle("agent-3") is False

    def test_add_result(self):
        tracker = HandoffTracker()
        result = HandoffResult(
            target_agent_id="a1",
            target_agent_name="Agent1",
            result="ok",
        )
        tracker.add_result(result)
        assert len(tracker.get_results()) == 1

    def test_clear(self):
        tracker = HandoffTracker()
        tracker.push("agent-1")
        tracker.add_result(HandoffResult("a1", "A", "r"))
        tracker.clear()
        assert tracker.get_chain() == []
        assert tracker.get_results() == []


class TestGlobalTracker:
    def test_get_tracker(self):
        reset_handoff_tracker()
        tracker = get_handoff_tracker()
        assert tracker is not None

    def test_reset_tracker(self):
        tracker = get_handoff_tracker()
        tracker.push("agent-1")
        reset_handoff_tracker()
        # 新 tracker 应该是空的
        new_tracker = get_handoff_tracker()
        assert new_tracker.get_chain() == []


# ==================== AgentLoop Handoff 集成 ====================


class TestAgentLoopHandoffIntegration:
    def test_handoffs_parameter(self):
        from app.modules.agent.loop import AgentLoop

        provider = MagicMock()
        handoff = Handoff(MagicMock())

        loop = AgentLoop(provider=provider, handoffs=[handoff])
        assert len(loop._handoffs) == 1

    def test_get_handoff_tools(self):
        from app.modules.agent.loop import AgentLoop

        provider = MagicMock()
        target = MagicMock()
        target.name = "Researcher"
        handoff = Handoff(target)

        loop = AgentLoop(provider=provider, handoffs=[handoff])
        tools = loop.get_handoff_tools()

        assert len(tools) == 1
        assert tools[0]["type"] == "function"

    def test_add_handoff(self):
        from app.modules.agent.loop import AgentLoop

        provider = MagicMock()
        loop = AgentLoop(provider=provider)
        handoff = Handoff(MagicMock())

        loop.add_handoff(handoff)
        assert len(loop._handoffs) == 1

    @pytest.mark.asyncio
    async def test_handle_handoff_tool(self):
        from app.modules.agent.loop import AgentLoop

        provider = MagicMock()
        target = MagicMock()
        target.name = "Researcher"
        target.run = AsyncMock(return_value="research result")

        handoff = Handoff(target)
        loop = AgentLoop(provider=provider, handoffs=[handoff])

        result = await loop._handle_handoff_tool(
            handoff.tool_name,
            {"prompt": "Research AI"},
        )

        assert result == "research result"

    @pytest.mark.asyncio
    async def test_handle_non_handoff_tool_returns_none(self):
        from app.modules.agent.loop import AgentLoop

        provider = MagicMock()
        loop = AgentLoop(provider=provider, handoffs=[])

        result = await loop._handle_handoff_tool("other_tool", {"x": 1})
        assert result is None


# ==================== SubagentManager Handoff 集成 ====================


class TestSubagentManagerHandoff:
    @pytest.mark.asyncio
    async def test_handoff_to(self):
        from app.modules.agent.subagent import SubagentManager, SubagentTask

        manager = SubagentManager()

        # 创建源任务
        task = SubagentTask(
            task_id="task-1",
            label="Test",
            message="test",
        )
        manager.tasks["task-1"] = task

        # 目标 Agent
        target = MagicMock()
        target.name = "Researcher"
        target.run = AsyncMock(return_value="result")

        result = await manager.handoff_to("task-1", target, "Research AI")

        assert result.error is None
        assert result.result == "result"

    @pytest.mark.asyncio
    async def test_handoff_to_missing_task(self):
        from app.modules.agent.subagent import SubagentManager

        manager = SubagentManager()
        target = MagicMock()

        with pytest.raises(ValueError, match="not found"):
            await manager.handoff_to("nonexistent", target, "test")

    @pytest.mark.asyncio
    async def test_parallel_handoffs(self):
        from app.modules.agent.subagent import SubagentManager, SubagentTask

        manager = SubagentManager()

        task = SubagentTask(
            task_id="task-1",
            label="Test",
            message="test",
        )
        manager.tasks["task-1"] = task

        targets = []
        for i in range(2):
            agent = MagicMock()
            agent.name = f"Agent{i}"
            agent.run = AsyncMock(return_value=f"result{i}")
            targets.append((agent, f"task{i}"))

        results = await manager.parallel_handoffs("task-1", targets, max_concurrent=2)

        assert len(results) == 2
