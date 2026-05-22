"""
阶段 Z 测试 — TaskFlow 持久化工作流

覆盖：
- TaskFlow 数据模型（TaskFlowState 枚举、字段默认值）
- TaskFlowManager 核心流程（create/start/run_step/set_waiting/resume/finish/fail）
- 乐观锁 revision 冲突检测
- 非法状态转换校验
- 子任务关联
- 列表查询
- recover_pending 启动恢复
- WorkflowEngine 可选 TaskFlow 绑定
- VALID_TRANSITIONS 状态转换表
"""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.task_flow import TaskFlow, TaskFlowState
from app.modules.agent.taskflow import (
    VALID_TRANSITIONS,
    InvalidStateTransition,
    RevisionConflictError,
    TaskFlowManager,
)

# ==================== TaskFlowState 枚举 ====================


class TestTaskFlowState:
    def test_values(self):
        assert TaskFlowState.CREATED.value == "created"
        assert TaskFlowState.RUNNING.value == "running"
        assert TaskFlowState.WAITING.value == "waiting"
        assert TaskFlowState.COMPLETED.value == "completed"
        assert TaskFlowState.FAILED.value == "failed"

    def test_all_states(self):
        states = {s.value for s in TaskFlowState}
        assert states == {"created", "running", "waiting", "completed", "failed"}


# ==================== TaskFlow 模型字段 ====================


class TestTaskFlowModel:
    def test_tablename(self):
        assert TaskFlow.__tablename__ == "task_flows"

    def test_default_values(self):
        flow = TaskFlow(id="test", name="n", goal="g")
        # SQLAlchemy column defaults apply at DB level, not Python level
        # When instantiated without passing, they are None
        assert flow.current_step is None or flow.current_step == ""
        assert flow.state is None or flow.state == TaskFlowState.CREATED.value
        assert flow.context is None or flow.context == {}
        assert flow.revision is None or flow.revision == 1
        assert flow.child_task_ids is None or flow.child_task_ids == []


# ==================== 状态转换表 ====================


class TestValidTransitions:
    def test_created_can_start(self):
        assert "running" in VALID_TRANSITIONS["created"]

    def test_running_can_wait(self):
        assert "waiting" in VALID_TRANSITIONS["running"]

    def test_running_can_complete(self):
        assert "completed" in VALID_TRANSITIONS["running"]

    def test_running_can_fail(self):
        assert "failed" in VALID_TRANSITIONS["running"]

    def test_waiting_can_resume(self):
        assert "running" in VALID_TRANSITIONS["waiting"]

    def test_waiting_can_fail(self):
        assert "failed" in VALID_TRANSITIONS["waiting"]

    def test_completed_is_terminal(self):
        assert VALID_TRANSITIONS["completed"] == set()

    def test_failed_is_terminal(self):
        assert VALID_TRANSITIONS["failed"] == set()

    def test_cannot_go_created_to_completed(self):
        assert "completed" not in VALID_TRANSITIONS["created"]

    def test_cannot_go_created_to_waiting(self):
        assert "waiting" not in VALID_TRANSITIONS["created"]

    def test_cannot_go_waiting_to_completed(self):
        assert "completed" not in VALID_TRANSITIONS["waiting"]


# ==================== 异常类 ====================


class TestExceptions:
    def test_revision_conflict_is_exception(self):
        assert issubclass(RevisionConflictError, Exception)

    def test_invalid_transition_is_exception(self):
        assert issubclass(InvalidStateTransition, Exception)


# ==================== TaskFlowManager 核心流程 ====================


def _make_mock_db():
    """创建 mock AsyncSession"""
    db = AsyncMock()
    # commit / refresh 默认成功
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    db.add = MagicMock()
    return db


def _make_flow(**overrides):
    """创建内存中的 TaskFlow 对象"""
    defaults = dict(
        id="flow-1",
        name="Test Flow",
        goal="Test goal",
        current_step="",
        state=TaskFlowState.CREATED.value,
        owner_id=None,
        session_id=None,
        context={},
        wait_reason=None,
        revision=1,
        child_task_ids=[],
        created_at=datetime.now(),
        updated_at=datetime.now(),
        completed_at=None,
    )
    defaults.update(overrides)
    return TaskFlow(**defaults)


class TestTaskFlowManagerCreate:
    @pytest.mark.asyncio
    async def test_create_sets_defaults(self):
        db = _make_mock_db()
        mgr = TaskFlowManager(db)

        flow = await mgr.create(name="My Flow", goal="Do something")

        db.add.assert_called_once()
        db.commit.assert_called_once()
        db.refresh.assert_called_once()
        assert flow.name == "My Flow"
        assert flow.goal == "Do something"
        assert flow.state == TaskFlowState.CREATED.value
        assert flow.revision == 1
        assert flow.context == {}
        assert flow.child_task_ids == []

    @pytest.mark.asyncio
    async def test_create_with_optional_fields(self):
        db = _make_mock_db()
        mgr = TaskFlowManager(db)

        flow = await mgr.create(
            name="Flow",
            goal="Goal",
            owner_id="user-1",
            session_id="sess-1",
            context={"key": "val"},
        )
        assert flow.owner_id == "user-1"
        assert flow.session_id == "sess-1"
        assert flow.context == {"key": "val"}


class TestTaskFlowManagerStart:
    @pytest.mark.asyncio
    async def test_start_transitions_to_running(self):
        db = _make_mock_db()
        mgr = TaskFlowManager(db)
        flow = _make_flow(state=TaskFlowState.CREATED.value)

        with patch.object(mgr, "_get_flow", return_value=flow):
            result = await mgr.start("flow-1", initial_step="step-1")
            assert result.state == TaskFlowState.RUNNING.value
            assert result.current_step == "step-1"

    @pytest.mark.asyncio
    async def test_start_without_initial_step(self):
        db = _make_mock_db()
        mgr = TaskFlowManager(db)
        flow = _make_flow(state=TaskFlowState.CREATED.value, current_step="")

        with patch.object(mgr, "_get_flow", return_value=flow):
            result = await mgr.start("flow-1")
            assert result.state == TaskFlowState.RUNNING.value
            assert result.current_step == ""

    @pytest.mark.asyncio
    async def test_start_from_running_fails(self):
        db = _make_mock_db()
        mgr = TaskFlowManager(db)
        flow = _make_flow(state=TaskFlowState.RUNNING.value)

        with patch.object(mgr, "_get_flow", return_value=flow), pytest.raises(InvalidStateTransition):
            await mgr.start("flow-1")


class TestTaskFlowManagerRunStep:
    @pytest.mark.asyncio
    async def test_run_step_updates_current_step(self):
        db = _make_mock_db()
        mgr = TaskFlowManager(db)
        flow = _make_flow(state=TaskFlowState.RUNNING.value)

        with patch.object(mgr, "_get_flow", return_value=flow):
            result = await mgr.run_step("flow-1", "data-collection")
            assert result.current_step == "data-collection"

    @pytest.mark.asyncio
    async def test_run_step_stores_result_in_context(self):
        db = _make_mock_db()
        mgr = TaskFlowManager(db)
        flow = _make_flow(state=TaskFlowState.RUNNING.value)

        with patch.object(mgr, "_get_flow", return_value=flow):
            result = await mgr.run_step("flow-1", "analysis", {"status": "ok"})
            assert result.context["step:analysis"] == {"status": "ok"}

    @pytest.mark.asyncio
    async def test_run_step_increments_revision(self):
        db = _make_mock_db()
        mgr = TaskFlowManager(db)
        flow = _make_flow(state=TaskFlowState.RUNNING.value, revision=3)

        with patch.object(mgr, "_get_flow", return_value=flow):
            result = await mgr.run_step("flow-1", "step")
            assert result.revision == 4

    @pytest.mark.asyncio
    async def test_run_step_in_created_fails(self):
        db = _make_mock_db()
        mgr = TaskFlowManager(db)
        flow = _make_flow(state=TaskFlowState.CREATED.value)

        with patch.object(mgr, "_get_flow", return_value=flow), pytest.raises(InvalidStateTransition):
            await mgr.run_step("flow-1", "step")


class TestTaskFlowManagerSetWaiting:
    @pytest.mark.asyncio
    async def test_set_waiting_from_running(self):
        db = _make_mock_db()
        mgr = TaskFlowManager(db)
        flow = _make_flow(state=TaskFlowState.RUNNING.value)

        with patch.object(mgr, "_get_flow", return_value=flow):
            result = await mgr.set_waiting("flow-1", "Waiting for approval")
            assert result.state == TaskFlowState.WAITING.value
            assert result.wait_reason == "Waiting for approval"

    @pytest.mark.asyncio
    async def test_set_waiting_with_checkpoint(self):
        db = _make_mock_db()
        mgr = TaskFlowManager(db)
        flow = _make_flow(state=TaskFlowState.RUNNING.value)

        with patch.object(mgr, "_get_flow", return_value=flow):
            result = await mgr.set_waiting("flow-1", "paused", {"step": 3, "data": "x"})
            assert result.context["_checkpoint"] == {"step": 3, "data": "x"}

    @pytest.mark.asyncio
    async def test_set_waiting_from_created_fails(self):
        db = _make_mock_db()
        mgr = TaskFlowManager(db)
        flow = _make_flow(state=TaskFlowState.CREATED.value)

        with patch.object(mgr, "_get_flow", return_value=flow), pytest.raises(InvalidStateTransition):
            await mgr.set_waiting("flow-1", "reason")


class TestTaskFlowManagerResume:
    @pytest.mark.asyncio
    async def test_resume_from_waiting(self):
        db = _make_mock_db()
        mgr = TaskFlowManager(db)
        flow = _make_flow(state=TaskFlowState.WAITING.value, revision=5)

        with patch.object(mgr, "_get_flow", return_value=flow):
            result = await mgr.resume("flow-1")
            assert result.state == TaskFlowState.RUNNING.value
            assert result.wait_reason is None
            assert result.revision == 6

    @pytest.mark.asyncio
    async def test_resume_with_input(self):
        db = _make_mock_db()
        mgr = TaskFlowManager(db)
        flow = _make_flow(state=TaskFlowState.WAITING.value)

        with patch.object(mgr, "_get_flow", return_value=flow):
            result = await mgr.resume("flow-1", resume_input={"approved": True})
            assert result.context["_resume_input"] == {"approved": True}

    @pytest.mark.asyncio
    async def test_resume_revision_conflict(self):
        db = _make_mock_db()
        mgr = TaskFlowManager(db)
        flow = _make_flow(state=TaskFlowState.WAITING.value, revision=5)

        with patch.object(mgr, "_get_flow", return_value=flow), pytest.raises(RevisionConflictError):
            await mgr.resume("flow-1", expected_revision=3)

    @pytest.mark.asyncio
    async def test_resume_revision_matches(self):
        db = _make_mock_db()
        mgr = TaskFlowManager(db)
        flow = _make_flow(state=TaskFlowState.WAITING.value, revision=5)

        with patch.object(mgr, "_get_flow", return_value=flow):
            result = await mgr.resume("flow-1", expected_revision=5)
            assert result.state == TaskFlowState.RUNNING.value

    @pytest.mark.asyncio
    async def test_resume_from_running_fails(self):
        db = _make_mock_db()
        mgr = TaskFlowManager(db)
        flow = _make_flow(state=TaskFlowState.RUNNING.value)

        with patch.object(mgr, "_get_flow", return_value=flow), pytest.raises(InvalidStateTransition):
            await mgr.resume("flow-1")


class TestTaskFlowManagerFinish:
    @pytest.mark.asyncio
    async def test_finish_from_running(self):
        db = _make_mock_db()
        mgr = TaskFlowManager(db)
        flow = _make_flow(state=TaskFlowState.RUNNING.value)

        with patch.object(mgr, "_get_flow", return_value=flow):
            result = await mgr.finish("flow-1", {"summary": "done"})
            assert result.state == TaskFlowState.COMPLETED.value
            assert result.context["_final_result"] == {"summary": "done"}
            assert result.completed_at is not None

    @pytest.mark.asyncio
    async def test_finish_increments_revision(self):
        db = _make_mock_db()
        mgr = TaskFlowManager(db)
        flow = _make_flow(state=TaskFlowState.RUNNING.value, revision=2)

        with patch.object(mgr, "_get_flow", return_value=flow):
            result = await mgr.finish("flow-1")
            assert result.revision == 3

    @pytest.mark.asyncio
    async def test_finish_from_created_fails(self):
        db = _make_mock_db()
        mgr = TaskFlowManager(db)
        flow = _make_flow(state=TaskFlowState.CREATED.value)

        with patch.object(mgr, "_get_flow", return_value=flow), pytest.raises(InvalidStateTransition):
            await mgr.finish("flow-1")


class TestTaskFlowManagerFail:
    @pytest.mark.asyncio
    async def test_fail_from_running(self):
        db = _make_mock_db()
        mgr = TaskFlowManager(db)
        flow = _make_flow(state=TaskFlowState.RUNNING.value)

        with patch.object(mgr, "_get_flow", return_value=flow):
            result = await mgr.fail("flow-1", "API timeout")
            assert result.state == TaskFlowState.FAILED.value
            assert result.context["_error"] == "API timeout"

    @pytest.mark.asyncio
    async def test_fail_from_waiting(self):
        db = _make_mock_db()
        mgr = TaskFlowManager(db)
        flow = _make_flow(state=TaskFlowState.WAITING.value)

        with patch.object(mgr, "_get_flow", return_value=flow):
            result = await mgr.fail("flow-1", "Cancelled by user")
            assert result.state == TaskFlowState.FAILED.value
            assert result.context["_error"] == "Cancelled by user"

    @pytest.mark.asyncio
    async def test_fail_clears_wait_reason(self):
        db = _make_mock_db()
        mgr = TaskFlowManager(db)
        flow = _make_flow(state=TaskFlowState.WAITING.value, wait_reason="Need input")

        with patch.object(mgr, "_get_flow", return_value=flow):
            result = await mgr.fail("flow-1", "abandoned")
            assert result.wait_reason is None

    @pytest.mark.asyncio
    async def test_fail_from_completed_fails(self):
        db = _make_mock_db()
        mgr = TaskFlowManager(db)
        flow = _make_flow(state=TaskFlowState.COMPLETED.value)

        with patch.object(mgr, "_get_flow", return_value=flow), pytest.raises(InvalidStateTransition):
            await mgr.fail("flow-1", "too late")


class TestTaskFlowManagerAddChildTask:
    @pytest.mark.asyncio
    async def test_add_child(self):
        db = _make_mock_db()
        mgr = TaskFlowManager(db)
        flow = _make_flow(child_task_ids=[])

        with patch.object(mgr, "_get_flow", return_value=flow):
            result = await mgr.add_child_task("flow-1", "task-1")
            assert "task-1" in result.child_task_ids

    @pytest.mark.asyncio
    async def test_add_child_no_duplicate(self):
        db = _make_mock_db()
        mgr = TaskFlowManager(db)
        flow = _make_flow(child_task_ids=["task-1"])

        with patch.object(mgr, "_get_flow", return_value=flow):
            result = await mgr.add_child_task("flow-1", "task-1")
            assert result.child_task_ids.count("task-1") == 1

    @pytest.mark.asyncio
    async def test_add_multiple_children(self):
        db = _make_mock_db()
        mgr = TaskFlowManager(db)
        flow = _make_flow(child_task_ids=[])

        with patch.object(mgr, "_get_flow", return_value=flow):
            await mgr.add_child_task("flow-1", "task-1")
            # flow.child_task_ids 已更新，再次添加
            await mgr.add_child_task("flow-1", "task-2")
            assert "task-1" in flow.child_task_ids
            assert "task-2" in flow.child_task_ids


class TestTaskFlowManagerGetFlow:
    @pytest.mark.asyncio
    async def test_get_flow_not_found(self):
        db = _make_mock_db()
        db.execute = AsyncMock(
            return_value=MagicMock(
                scalars=MagicMock(
                    return_value=MagicMock(first=MagicMock(return_value=None))
                )
            )
        )
        mgr = TaskFlowManager(db)

        result = await mgr.get_flow("nonexistent")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_flow_found(self):
        flow = _make_flow()
        db = _make_mock_db()
        db.execute = AsyncMock(
            return_value=MagicMock(
                scalars=MagicMock(
                    return_value=MagicMock(first=MagicMock(return_value=flow))
                )
            )
        )
        mgr = TaskFlowManager(db)

        result = await mgr.get_flow("flow-1")
        assert result.id == "flow-1"


class TestTaskFlowManagerInternalGetFlow:
    @pytest.mark.asyncio
    async def test_get_flow_raises_on_missing(self):
        db = _make_mock_db()
        mgr = TaskFlowManager(db)

        with patch.object(mgr, "get_flow", return_value=None), pytest.raises(ValueError, match="TaskFlow not found"):
            await mgr._get_flow("nonexistent")


class TestTaskFlowManagerRecoverPending:
    @pytest.mark.asyncio
    async def test_recover_running_flows(self):
        db = _make_mock_db()
        mgr = TaskFlowManager(db)
        flow1 = _make_flow(
            id="f1", state=TaskFlowState.RUNNING.value, name="Running Flow"
        )
        flow2 = _make_flow(
            id="f2", state=TaskFlowState.CREATED.value, name="Created Flow"
        )

        db.execute = AsyncMock(
            return_value=MagicMock(
                scalars=MagicMock(
                    return_value=MagicMock(all=MagicMock(return_value=[flow1, flow2]))
                )
            )
        )

        recovered = await mgr.recover_pending()
        assert len(recovered) == 2
        for f in recovered:
            assert f.state == TaskFlowState.WAITING.value
            assert f.wait_reason is not None
            assert "Recovered" in f.wait_reason

    @pytest.mark.asyncio
    async def test_recover_increments_revision(self):
        db = _make_mock_db()
        mgr = TaskFlowManager(db)
        flow = _make_flow(state=TaskFlowState.RUNNING.value, revision=3)

        db.execute = AsyncMock(
            return_value=MagicMock(
                scalars=MagicMock(
                    return_value=MagicMock(all=MagicMock(return_value=[flow]))
                )
            )
        )

        recovered = await mgr.recover_pending()
        assert recovered[0].revision == 4

    @pytest.mark.asyncio
    async def test_recover_no_pending(self):
        db = _make_mock_db()
        mgr = TaskFlowManager(db)

        db.execute = AsyncMock(
            return_value=MagicMock(
                scalars=MagicMock(
                    return_value=MagicMock(all=MagicMock(return_value=[]))
                )
            )
        )

        recovered = await mgr.recover_pending()
        assert recovered == []


# ==================== 完整生命周期测试 ====================


class TestTaskFlowFullLifecycle:
    @pytest.mark.asyncio
    async def test_happy_path(self):
        """created → running → waiting → running → completed"""
        db = _make_mock_db()
        mgr = TaskFlowManager(db)
        flow = _make_flow(state=TaskFlowState.CREATED.value)

        with patch.object(mgr, "_get_flow", return_value=flow):
            # Start
            flow = await mgr.start("flow-1", initial_step="init")
            assert flow.state == "running"

            # Run step
            flow = await mgr.run_step("flow-1", "process", {"count": 10})
            assert flow.current_step == "process"

            # Wait
            flow = await mgr.set_waiting(
                "flow-1", "Need approval", {"checkpoint_step": "process"}
            )
            assert flow.state == "waiting"

            # Resume
            flow = await mgr.resume("flow-1", {"approved": True})
            assert flow.state == "running"

            # Finish
            flow = await mgr.finish("flow-1", {"result": "success"})
            assert flow.state == "completed"
            assert flow.completed_at is not None

    @pytest.mark.asyncio
    async def test_failure_path(self):
        """created → running → failed"""
        db = _make_mock_db()
        mgr = TaskFlowManager(db)
        flow = _make_flow(state=TaskFlowState.CREATED.value)

        with patch.object(mgr, "_get_flow", return_value=flow):
            flow = await mgr.start("flow-1")
            flow = await mgr.fail("flow-1", "Crashed")
            assert flow.state == "failed"
            assert flow.context["_error"] == "Crashed"

    @pytest.mark.asyncio
    async def test_wait_then_fail(self):
        """created → running → waiting → failed"""
        db = _make_mock_db()
        mgr = TaskFlowManager(db)
        flow = _make_flow(state=TaskFlowState.CREATED.value)

        with patch.object(mgr, "_get_flow", return_value=flow):
            flow = await mgr.start("flow-1")
            flow = await mgr.set_waiting("flow-1", "Approval needed")
            flow = await mgr.fail("flow-1", "Rejected")
            assert flow.state == "failed"


# ==================== WorkflowEngine TaskFlow 集成 ====================


class TestWorkflowTaskFlowIntegration:
    def test_bind_taskflow_without_db(self):
        """无 db 时不绑定"""
        from app.modules.agent.workflow import WorkflowEngine

        engine = WorkflowEngine(agent_loop=MagicMock())
        assert engine._taskflow_db is None
        assert engine._taskflow_id is None

    def test_bind_taskflow_with_db(self):
        """有 db 时可绑定"""
        from app.modules.agent.workflow import WorkflowEngine

        db = AsyncMock()
        engine = WorkflowEngine(agent_loop=MagicMock(), taskflow_db=db)
        assert engine._taskflow_db is db

    @pytest.mark.asyncio
    async def test_taskflow_step_noop_without_db(self):
        """无 db 时 _taskflow_step 不报错"""
        from app.modules.agent.workflow import WorkflowEngine

        engine = WorkflowEngine(agent_loop=MagicMock())
        # 不应抛异常
        await engine._taskflow_step("step", {"data": 1})

    @pytest.mark.asyncio
    async def test_taskflow_finish_noop_without_db(self):
        from app.modules.agent.workflow import WorkflowEngine

        engine = WorkflowEngine(agent_loop=MagicMock())
        await engine._taskflow_finish({"result": "ok"})

    @pytest.mark.asyncio
    async def test_taskflow_fail_noop_without_db(self):
        from app.modules.agent.workflow import WorkflowEngine

        engine = WorkflowEngine(agent_loop=MagicMock())
        await engine._taskflow_fail("error msg")

    @pytest.mark.asyncio
    async def test_taskflow_step_with_taskflow(self):
        from app.modules.agent.workflow import WorkflowEngine

        db = _make_mock_db()
        engine = WorkflowEngine(agent_loop=MagicMock(), taskflow_db=db)
        engine._taskflow_id = "flow-1"

        mock_flow = _make_flow(state="running")
        with patch("app.modules.agent.taskflow.TaskFlowManager") as MockMgr:
            MockMgr.return_value.run_step = AsyncMock(return_value=mock_flow)
            await engine._taskflow_step("step-1", {"k": "v"})
            MockMgr.return_value.run_step.assert_called_once()

    @pytest.mark.asyncio
    async def test_taskflow_step_handles_error(self):
        """持久化失败不中断主流程"""
        from app.modules.agent.workflow import WorkflowEngine

        db = _make_mock_db()
        engine = WorkflowEngine(agent_loop=MagicMock(), taskflow_db=db)
        engine._taskflow_id = "flow-1"

        with patch("app.modules.agent.taskflow.TaskFlowManager") as MockMgr:
            MockMgr.return_value.run_step = AsyncMock(side_effect=Exception("DB error"))
            # 不应抛异常
            await engine._taskflow_step("step-1")

    @pytest.mark.asyncio
    async def test_bind_taskflow_creates_flow(self):
        from app.modules.agent.workflow import WorkflowEngine

        db = _make_mock_db()
        engine = WorkflowEngine(
            agent_loop=MagicMock(), taskflow_db=db, session_id="sess-1"
        )

        mock_flow = _make_flow(id="new-flow", name="test", goal="test")
        with patch("app.modules.agent.taskflow.TaskFlowManager") as MockMgr:
            MockMgr.return_value.create = AsyncMock(return_value=mock_flow)
            flow_id = await engine.bind_taskflow("test", "test goal", owner_id="user-1")
            assert flow_id == "new-flow"
            assert engine._taskflow_id == "new-flow"
            MockMgr.return_value.create.assert_called_once_with(
                name="test", goal="test goal", owner_id="user-1", session_id="sess-1"
            )

    @pytest.mark.asyncio
    async def test_bind_taskflow_no_db_returns_none(self):
        from app.modules.agent.workflow import WorkflowEngine

        engine = WorkflowEngine(agent_loop=MagicMock())
        result = await engine.bind_taskflow("test", "goal")
        assert result is None
