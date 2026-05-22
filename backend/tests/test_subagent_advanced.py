"""
阶段 W 测试 — OpenClaw 借鉴增强功能

覆盖：
- 深度与角色系统（SubagentRole, resolve_subagent_role, resolve_subagent_capabilities）
- Push-based 结果回传（SubagentAnnouncer）
- 并发隔离 Lane（SubagentLane, LaneType）
- Agent 间访问控制（SubagentTargetPolicy）
- SubagentConfig
- SubagentTask 新字段
- 子 Agent 专用系统提示词
- max_children_per_agent 限制
- create_task 深度/角色/策略集成
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.modules.agent.subagent import (
    SUBAGENT_SYSTEM_PROMPT_TEMPLATE,
    LaneType,
    SubagentAnnouncer,
    SubagentConfig,
    SubagentLane,
    SubagentManager,
    SubagentRole,
    SubagentTargetPolicy,
    SubagentTask,
    TaskStatus,
    TaskType,
    resolve_subagent_capabilities,
    resolve_subagent_role,
)

# ==================== 深度与角色系统 ====================


class TestResolveSubagentRole:
    """resolve_subagent_role 测试"""

    def test_depth_zero_is_main(self):
        assert resolve_subagent_role(0) == SubagentRole.MAIN

    def test_depth_negative_is_main(self):
        assert resolve_subagent_role(-1) == SubagentRole.MAIN

    def test_depth_one_default_is_leaf(self):
        """默认 max_spawn_depth=1，深度1=leaf（扁平架构）"""
        assert resolve_subagent_role(1) == SubagentRole.LEAF

    def test_depth_two_default_is_leaf(self):
        assert resolve_subagent_role(2) == SubagentRole.LEAF

    def test_depth_one_max_depth_two_is_orchestrator(self):
        """max_spawn_depth=2 时，深度1=orchestrator"""
        assert resolve_subagent_role(1, max_spawn_depth=2) == SubagentRole.ORCHESTRATOR

    def test_depth_two_max_depth_three_is_orchestrator(self):
        assert resolve_subagent_role(2, max_spawn_depth=3) == SubagentRole.ORCHESTRATOR

    def test_depth_two_max_depth_two_is_leaf(self):
        assert resolve_subagent_role(2, max_spawn_depth=2) == SubagentRole.LEAF

    def test_depth_three_max_depth_five_is_orchestrator(self):
        assert resolve_subagent_role(3, max_spawn_depth=5) == SubagentRole.ORCHESTRATOR

    def test_depth_five_max_depth_five_is_leaf(self):
        assert resolve_subagent_role(5, max_spawn_depth=5) == SubagentRole.LEAF


class TestResolveSubagentCapabilities:
    """resolve_subagent_capabilities 测试"""

    def test_main_can_spawn(self):
        caps = resolve_subagent_capabilities(SubagentRole.MAIN)
        assert caps["can_spawn"] is True
        assert caps["can_control_children"] is True
        assert caps["role"] == SubagentRole.MAIN

    def test_orchestrator_can_spawn(self):
        caps = resolve_subagent_capabilities(SubagentRole.ORCHESTRATOR)
        assert caps["can_spawn"] is True
        assert caps["can_control_children"] is True
        assert caps["role"] == SubagentRole.ORCHESTRATOR

    def test_leaf_cannot_spawn(self):
        caps = resolve_subagent_capabilities(SubagentRole.LEAF)
        assert caps["can_spawn"] is False
        assert caps["can_control_children"] is False
        assert caps["role"] == SubagentRole.LEAF


class TestSubagentConfig:
    """SubagentConfig 测试"""

    def test_defaults(self):
        config = SubagentConfig()
        assert config.max_spawn_depth == 1
        assert config.max_children_per_agent == 5
        assert config.max_concurrent == 8
        assert config.agent_max_concurrent == 4

    def test_custom(self):
        config = SubagentConfig(max_spawn_depth=3, max_children_per_agent=10)
        assert config.max_spawn_depth == 3
        assert config.max_children_per_agent == 10


# ==================== Push-based 结果回传 ====================


class TestSubagentAnnouncer:
    """SubagentAnnouncer 测试"""

    def test_init(self):
        announcer = SubagentAnnouncer()
        assert announcer._pending_announcements == {}

    @pytest.mark.asyncio
    async def test_announce_no_parent(self):
        """无父任务时无需推送"""
        announcer = SubagentAnnouncer()
        manager = MagicMock()
        manager.tasks = {}
        # 不应报错
        await announcer.announce("child-1", None, "result", "completed", manager)
        assert len(announcer._pending_announcements) == 0

    @pytest.mark.asyncio
    async def test_announce_with_parent_and_callback(self):
        """有父任务时推送事件给父任务的 callback"""
        announcer = SubagentAnnouncer()
        manager = MagicMock()
        manager.tasks = {}

        # 创建父任务（带 mock callback）
        parent_task = MagicMock()
        parent_task.event_callback = AsyncMock()
        manager.tasks["parent-1"] = parent_task

        await announcer.announce("child-1", "parent-1", "done", "completed", manager)

        # 验证 callback 被调用
        parent_task.event_callback.assert_called_once()
        call_args = parent_task.event_callback.call_args
        assert call_args[0][0] == "subagent_child_completed"
        event = call_args[0][1]
        assert event["type"] == "subagent_completed"
        assert event["task_id"] == "child-1"
        assert event["result"] == "done"
        assert event["status"] == "completed"

    @pytest.mark.asyncio
    async def test_announce_caches_pending(self):
        """推送事件被缓存到 _pending_announcements"""
        announcer = SubagentAnnouncer()
        manager = MagicMock()
        manager.tasks = {}

        # 无 callback 的父任务
        parent_task = MagicMock()
        parent_task.event_callback = None
        manager.tasks["parent-1"] = parent_task

        await announcer.announce(
            "child-1", "parent-1", "result-1", "completed", manager
        )
        await announcer.announce(
            "child-2", "parent-1", "result-2", "completed", manager
        )

        pending = announcer.get_pending_announcements("parent-1")
        assert len(pending) == 2
        assert pending[0]["task_id"] == "child-1"
        assert pending[1]["task_id"] == "child-2"

    def test_get_pending_announcements_empty(self):
        announcer = SubagentAnnouncer()
        assert announcer.get_pending_announcements("nonexistent") == []

    def test_get_pending_announcements_consumes(self):
        """get_pending_announcements 是消费性的（pop）"""
        announcer = SubagentAnnouncer()
        announcer._pending_announcements["p1"] = [{"task_id": "c1"}]

        result1 = announcer.get_pending_announcements("p1")
        assert len(result1) == 1
        result2 = announcer.get_pending_announcements("p1")
        assert len(result2) == 0

    def test_clear(self):
        announcer = SubagentAnnouncer()
        announcer._pending_announcements["p1"] = [{"task_id": "c1"}]
        announcer.clear("p1")
        assert "p1" not in announcer._pending_announcements


# ==================== 并发隔离 Lane ====================


class TestSubagentLane:
    """SubagentLane 测试"""

    def test_init(self):
        lane = SubagentLane(LaneType.SUBAGENT, max_concurrent=4)
        assert lane.lane_type == LaneType.SUBAGENT
        assert lane.active_count == 0

    @pytest.mark.asyncio
    async def test_acquire_release(self):
        lane = SubagentLane(LaneType.NESTED, max_concurrent=2)
        await lane.acquire()
        assert lane.active_count == 1
        await lane.acquire()
        assert lane.active_count == 2
        lane.release()
        assert lane.active_count == 1
        lane.release()
        assert lane.active_count == 0

    @pytest.mark.asyncio
    async def test_context_manager(self):
        lane = SubagentLane(LaneType.CRON, max_concurrent=1)
        async with lane:
            assert lane.active_count == 1
        assert lane.active_count == 0

    @pytest.mark.asyncio
    async def test_concurrency_limit(self):
        """并发限制：超过 max_concurrent 时阻塞"""
        lane = SubagentLane(LaneType.SUBAGENT, max_concurrent=1)
        acquired = []

        async def task_fn(task_id):
            async with lane:
                acquired.append(task_id)
                await asyncio.sleep(0.1)

        # 启动两个任务，第二个应等待
        t1 = asyncio.create_task(task_fn("t1"))
        t2 = asyncio.create_task(task_fn("t2"))
        await asyncio.sleep(0.05)  # t1 should have acquired
        assert lane.active_count == 1
        assert acquired == ["t1"]

        await asyncio.gather(t1, t2)
        assert acquired == ["t1", "t2"]

    def test_release_below_zero_safe(self):
        """release 不会让 active_count 变成负数"""
        lane = SubagentLane(LaneType.NESTED, max_concurrent=2)
        lane.release()
        assert lane.active_count == 0


class TestLaneType:
    def test_values(self):
        assert LaneType.NESTED.value == "nested"
        assert LaneType.SUBAGENT.value == "subagent"
        assert LaneType.CRON.value == "cron"


# ==================== Agent 间访问控制 ====================


class TestSubagentTargetPolicy:
    """SubagentTargetPolicy 测试"""

    def test_default_none_only_same_agent(self):
        """默认（allow_agents=None）：仅允许同 Agent spawn"""
        policy = SubagentTargetPolicy()
        assert policy.can_spawn_target("agent-a", "agent-a") is True
        assert policy.can_spawn_target("agent-a", "agent-b") is False

    def test_wildcard_allows_any(self):
        """allow_agents=["*"]：允许任意 Agent"""
        policy = SubagentTargetPolicy(allow_agents=["*"])
        assert policy.can_spawn_target("agent-a", "agent-b") is True
        assert policy.can_spawn_target("agent-a", "agent-c") is True

    def test_whitelist(self):
        """白名单模式"""
        policy = SubagentTargetPolicy(allow_agents=["agent-b", "agent-c"])
        assert policy.can_spawn_target("agent-a", "agent-b") is True
        assert policy.can_spawn_target("agent-a", "agent-c") is True
        assert policy.can_spawn_target("agent-a", "agent-d") is False

    def test_same_agent_with_wildcard(self):
        policy = SubagentTargetPolicy(allow_agents=["*"])
        assert policy.can_spawn_target("agent-a", "agent-a") is True

    def test_same_agent_with_whitelist(self):
        """白名单中即使没有自己，同 Agent 也可能被拒绝"""
        policy = SubagentTargetPolicy(allow_agents=["agent-b"])
        assert policy.can_spawn_target("agent-a", "agent-a") is False
        assert policy.can_spawn_target("agent-a", "agent-b") is True


# ==================== SubagentTask 新字段 ====================


class TestSubagentTask:
    """SubagentTask 新字段测试"""

    def test_default_depth_and_role(self):
        task = SubagentTask(task_id="t1", label="test", message="msg")
        assert task.depth == 0
        assert task.role == SubagentRole.MAIN
        assert task.parent_task_id is None
        assert task.agent_id is None

    def test_can_spawn_property_main(self):
        task = SubagentTask(
            task_id="t1", label="test", message="msg", role=SubagentRole.MAIN
        )
        assert task.can_spawn is True

    def test_can_spawn_property_leaf(self):
        task = SubagentTask(
            task_id="t1", label="test", message="msg", role=SubagentRole.LEAF
        )
        assert task.can_spawn is False

    def test_can_spawn_property_orchestrator(self):
        task = SubagentTask(
            task_id="t1", label="test", message="msg", role=SubagentRole.ORCHESTRATOR
        )
        assert task.can_spawn is True

    def test_to_dict_includes_new_fields(self):
        task = SubagentTask(
            task_id="t1",
            label="test",
            message="msg",
            depth=1,
            role=SubagentRole.LEAF,
            parent_task_id="p1",
            agent_id="a1",
        )
        d = task.to_dict()
        assert d["depth"] == 1
        assert d["role"] == "leaf"
        assert d["parent_task_id"] == "p1"
        assert d["agent_id"] == "a1"


# ==================== SubagentManager 集成测试 ====================


class TestSubagentManagerAdvanced:
    """SubagentManager OpenClaw 增强集成测试"""

    def test_init_with_config(self):
        config = SubagentConfig(max_spawn_depth=3, max_children_per_agent=10)
        policy = SubagentTargetPolicy(allow_agents=["*"])
        manager = SubagentManager(config=config, target_policy=policy)

        assert manager.config.max_spawn_depth == 3
        assert manager.config.max_children_per_agent == 10
        assert manager.target_policy.allow_agents == ["*"]
        assert isinstance(manager.announcer, SubagentAnnouncer)
        assert len(manager._lanes) == 3

    def test_create_task_depth_zero(self):
        manager = SubagentManager()
        task_id = manager.create_task(label="root", message="root task", depth=0)
        task = manager.get_task(task_id)
        assert task.depth == 0
        assert task.role == SubagentRole.MAIN
        assert task.can_spawn is True

    def test_create_task_depth_one_default(self):
        """默认 max_spawn_depth=1，深度1=leaf"""
        manager = SubagentManager()
        task_id = manager.create_task(label="child", message="child task", depth=1)
        task = manager.get_task(task_id)
        assert task.depth == 1
        assert task.role == SubagentRole.LEAF
        assert task.can_spawn is False

    def test_create_task_depth_one_max_depth_two(self):
        """max_spawn_depth=2，深度1=orchestrator"""
        config = SubagentConfig(max_spawn_depth=2)
        manager = SubagentManager(config=config)
        task_id = manager.create_task(label="mid", message="mid task", depth=1)
        task = manager.get_task(task_id)
        assert task.role == SubagentRole.ORCHESTRATOR
        assert task.can_spawn is True

    def test_create_task_with_parent(self):
        manager = SubagentManager()
        parent_id = manager.create_task(label="parent", message="parent task", depth=0)
        child_id = manager.create_task(
            label="child", message="child task", depth=1, parent_task_id=parent_id
        )

        child = manager.get_task(child_id)
        assert child.parent_task_id == parent_id
        assert child.role == SubagentRole.LEAF

    def test_create_task_max_children_limit(self):
        """子任务数量限制"""
        config = SubagentConfig(max_children_per_agent=2)
        manager = SubagentManager(config=config)
        parent_id = manager.create_task(label="parent", message="parent task")

        # 创建2个子任务（正常）
        manager.create_task(label="child-1", message="c1", parent_task_id=parent_id)
        manager.create_task(label="child-2", message="c2", parent_task_id=parent_id)

        # 第3个子任务应报错
        with pytest.raises(ValueError, match="already has 2 children"):
            manager.create_task(label="child-3", message="c3", parent_task_id=parent_id)

    def test_create_task_target_policy_blocks(self):
        """Target policy 阻止跨 Agent spawn"""
        policy = SubagentTargetPolicy()  # None = same agent only
        manager = SubagentManager(target_policy=policy)

        parent_id = manager.create_task(
            label="parent", message="p", depth=0, agent_id="agent-a"
        )
        # 尝试 spawn 不同 agent 的子任务 → 被阻止
        with pytest.raises(ValueError, match="not allowed to spawn target"):
            manager.create_task(
                label="child",
                message="c",
                depth=1,
                parent_task_id=parent_id,
                agent_id="agent-b",
            )

    def test_create_task_target_policy_allows_wildcard(self):
        """Target policy 通配符允许跨 Agent spawn"""
        policy = SubagentTargetPolicy(allow_agents=["*"])
        manager = SubagentManager(target_policy=policy)

        parent_id = manager.create_task(
            label="parent", message="p", depth=0, agent_id="agent-a"
        )
        # 通配符允许
        child_id = manager.create_task(
            label="child",
            message="c",
            depth=1,
            parent_task_id=parent_id,
            agent_id="agent-b",
        )
        assert child_id is not None

    @pytest.mark.asyncio
    async def test_execute_subagent_announces_to_parent(self):
        """子任务完成后 Push-based 通知父任务"""
        manager = SubagentManager()

        # 创建父任务（带 callback 接收子任务完成事件）
        parent_events = []

        def parent_callback(event_type, data):
            parent_events.append((event_type, data))

        parent_id = manager.create_task(
            label="parent",
            message="parent task",
            event_callback=parent_callback,
        )

        # 创建子任务
        child_id = manager.create_task(
            label="child",
            message="child task",
            depth=1,
            parent_task_id=parent_id,
        )

        # 执行子任务并等待完成
        await manager.execute_task(child_id)
        await manager.wait_for_task(child_id, timeout=5.0)

        child_task = manager.get_task(child_id)
        assert child_task.status == TaskStatus.COMPLETED

        # 验证 announce 推送了事件到父任务的 callback
        assert any(e[0] == "subagent_child_completed" for e in parent_events)

    @pytest.mark.asyncio
    async def test_announce_pending_cache(self):
        """announce 将事件缓存到 _pending_announcements"""
        manager = SubagentManager()
        parent_id = manager.create_task(label="parent", message="p")

        child_id = manager.create_task(
            label="child",
            message="c",
            depth=1,
            parent_task_id=parent_id,
        )

        await manager.execute_task(child_id)
        await manager.wait_for_task(child_id, timeout=5.0)

        # 通过 get_pending_announcements 验证缓存
        pending = manager.announcer.get_pending_announcements(parent_id)
        assert len(pending) == 1
        assert pending[0]["type"] == "subagent_completed"
        assert pending[0]["task_id"] == child_id

    def test_resolve_lane_depth_zero(self):
        """depth=0 使用 SUBAGENT lane"""
        manager = SubagentManager()
        task = SubagentTask(task_id="t1", label="test", message="msg", depth=0)
        lane = manager._resolve_lane(task)
        assert lane.lane_type == LaneType.SUBAGENT

    def test_resolve_lane_depth_positive(self):
        """depth>0 使用 NESTED lane"""
        manager = SubagentManager()
        task = SubagentTask(task_id="t1", label="test", message="msg", depth=1)
        lane = manager._resolve_lane(task)
        assert lane.lane_type == LaneType.NESTED

    def test_lanes_initialized(self):
        manager = SubagentManager()
        assert LaneType.NESTED in manager._lanes
        assert LaneType.SUBAGENT in manager._lanes
        assert LaneType.CRON in manager._lanes


# ==================== 子 Agent 专用系统提示词 ====================


class TestSubagentSystemPrompt:
    """子 Agent 专用系统提示词测试"""

    def test_template_format(self):
        result = SUBAGENT_SYSTEM_PROMPT_TEMPLATE.format(
            parent_label="父 Agent（Root Task）",
            task_description="**类型**: research\n**目标**: 分析数据\n**详情**: 分析用户数据",
            role="leaf",
            depth=1,
            can_spawn="否",
        )
        assert "子 Agent" in result
        assert "父 Agent（Root Task）" in result
        assert "leaf" in result
        assert "不要轮询" in result

    def test_build_default_prompt_depth_zero(self):
        """depth=0 使用原有逻辑"""
        manager = SubagentManager()
        task = SubagentTask(task_id="t1", label="test task", message="msg", depth=0)
        prompt = manager._build_default_prompt(task)
        assert "通用子智能体" in prompt
        assert "test task" in prompt
        assert "子 Agent" not in prompt

    def test_build_default_prompt_depth_positive(self):
        """depth>0 使用子 Agent 专用提示词"""
        manager = SubagentManager()
        parent_id = manager.create_task(label="Root", message="root")

        task = SubagentTask(
            task_id="t2",
            label="sub task",
            message="do research",
            depth=1,
            role=SubagentRole.LEAF,
            parent_task_id=parent_id,
            task_type=TaskType.RESEARCH,
        )
        manager.tasks["t2"] = task

        prompt = manager._build_default_prompt(task)
        assert "子 Agent" in prompt
        assert "父 Agent（Root）" in prompt
        assert "leaf" in prompt
        assert "不要轮询" in prompt
        assert "research" in prompt

    def test_build_default_prompt_depth_positive_no_parent(self):
        """depth>0 但无 parent_task_id"""
        manager = SubagentManager()
        task = SubagentTask(
            task_id="t2",
            label="sub task",
            message="msg",
            depth=1,
            role=SubagentRole.LEAF,
        )
        manager.tasks["t2"] = task

        prompt = manager._build_default_prompt(task)
        assert "父 Agent" in prompt
        assert "leaf" in prompt


# ==================== 统计信息扩展 ====================


class TestStatsWithDepth:
    """统计信息包含深度/角色信息"""

    def test_stats_basic(self):
        manager = SubagentManager()
        manager.create_task(label="root-1", message="r1", depth=0)
        manager.create_task(label="root-2", message="r2", depth=0)
        manager.create_task(label="child-1", message="c1", depth=1)

        stats = manager.get_stats()
        assert stats["total"] == 3
        assert stats["pending"] == 3

    def test_list_by_depth(self):
        """通过 depth 字段过滤"""
        manager = SubagentManager()
        manager.create_task(label="root", message="r", depth=0)
        manager.create_task(label="child", message="c", depth=1)

        all_tasks = manager.list_tasks()
        roots = [t for t in all_tasks if t.depth == 0]
        children = [t for t in all_tasks if t.depth > 0]
        assert len(roots) == 1
        assert len(children) == 1
