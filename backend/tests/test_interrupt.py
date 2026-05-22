"""
阶段 FF 测试 — Interrupt 中断与恢复机制

覆盖：
- InterruptReason 枚举
- InterruptStatus 枚举
- InterruptOption 数据类
- InterruptPoint 数据类
- Checkpoint 数据类
- InterruptManager 管理器
- AgentLoop 中断集成
"""

import asyncio
from unittest.mock import MagicMock

import pytest

from app.modules.agent.interrupt import (
    Checkpoint,
    InterruptManager,
    InterruptOption,
    InterruptPoint,
    InterruptReason,
    InterruptStatus,
    get_interrupt_manager,
    interrupt_options,
    reset_interrupt_manager,
)

# ==================== InterruptReason 测试 ====================


class TestInterruptReason:
    def test_reason_values(self):
        assert InterruptReason.HUMAN_REVIEW.value == "human_review"
        assert InterruptReason.SENSITIVE_ACTION.value == "sensitive"
        assert InterruptReason.ERROR_RECOVERY.value == "error_recovery"
        assert InterruptReason.CHECKPOINT.value == "checkpoint"
        assert InterruptReason.TIMEOUT.value == "timeout"
        assert InterruptReason.CUSTOM.value == "custom"

    def test_all_reasons_exist(self):
        reasons = {r.value for r in InterruptReason}
        expected = {
            "human_review",
            "sensitive",
            "error_recovery",
            "checkpoint",
            "timeout",
            "custom",
        }
        assert reasons == expected


# ==================== InterruptStatus 测试 ====================


class TestInterruptStatus:
    def test_status_values(self):
        assert InterruptStatus.PENDING.value == "pending"
        assert InterruptStatus.APPROVED.value == "approved"
        assert InterruptStatus.REJECTED.value == "rejected"
        assert InterruptStatus.MODIFIED.value == "modified"
        assert InterruptStatus.EXPIRED.value == "expired"
        assert InterruptStatus.CANCELLED.value == "cancelled"


# ==================== InterruptOption 测试 ====================


class TestInterruptOption:
    def test_defaults(self):
        option = InterruptOption(label="OK", value="ok")
        assert option.label == "OK"
        assert option.value == "ok"
        assert option.description == ""
        assert option.requires_input is False
        assert option.style == "default"

    def test_custom_values(self):
        option = InterruptOption(
            label="修改后批准",
            value="modify",
            description="修改内容后批准",
            requires_input=True,
            input_placeholder="输入修改内容",
            style="warning",
        )
        assert option.requires_input is True
        assert option.style == "warning"


# ==================== InterruptPoint 测试 ====================


class TestInterruptPoint:
    def test_defaults(self):
        point = InterruptPoint(
            id="test-1",
            reason=InterruptReason.HUMAN_REVIEW,
        )
        assert point.status == InterruptStatus.PENDING
        assert point.state_snapshot == {}
        assert point.message_snapshot == []
        assert point.options == []

    def test_custom_values(self):
        point = InterruptPoint(
            id="test-2",
            reason=InterruptReason.SENSITIVE_ACTION,
            agent_id="agent-1",
            agent_name="Researcher",
            title="敏感操作",
            message="确认删除？",
            options=[
                InterruptOption(label="确认", value="approve"),
                InterruptOption(label="取消", value="reject"),
            ],
        )
        assert point.agent_id == "agent-1"
        assert len(point.options) == 2

    def test_is_expired(self):
        import time

        point = InterruptPoint(
            id="test-3",
            reason=InterruptReason.HUMAN_REVIEW,
            expires_at=time.time() - 1,  # 已过期
        )
        assert point.is_expired() is True

        point2 = InterruptPoint(
            id="test-4",
            reason=InterruptReason.HUMAN_REVIEW,
            expires_at=time.time() + 3600,  # 未过期
        )
        assert point2.is_expired() is False

        point3 = InterruptPoint(
            id="test-5",
            reason=InterruptReason.HUMAN_REVIEW,
            expires_at=None,  # 无过期时间
        )
        assert point3.is_expired() is False

    def test_is_resolved(self):
        point = InterruptPoint(
            id="test-6",
            reason=InterruptReason.HUMAN_REVIEW,
            status=InterruptStatus.PENDING,
        )
        assert point.is_resolved() is False

        point.status = InterruptStatus.APPROVED
        assert point.is_resolved() is True

    def test_to_dict(self):
        point = InterruptPoint(
            id="test-7",
            reason=InterruptReason.HUMAN_REVIEW,
            message="Test message",
        )
        d = point.to_dict()
        assert d["id"] == "test-7"
        assert d["reason"] == "human_review"
        assert d["message"] == "Test message"

    def test_from_dict(self):
        data = {
            "id": "test-8",
            "reason": "sensitive",
            "status": "approved",
            "message": "Test",
            "options": [
                {
                    "label": "OK",
                    "value": "ok",
                    "description": "",
                    "requires_input": False,
                    "input_placeholder": "",
                    "style": "default",
                }
            ],
        }
        point = InterruptPoint.from_dict(data)
        assert point.id == "test-8"
        assert point.reason == InterruptReason.SENSITIVE_ACTION
        assert point.status == InterruptStatus.APPROVED


# ==================== Checkpoint 测试 ====================


class TestCheckpoint:
    def test_defaults(self):
        cp = Checkpoint(id="cp-1", agent_id="agent-1")
        assert cp.name == ""
        assert cp.state == {}
        assert cp.messages == []
        assert cp.iteration == 0

    def test_custom_values(self):
        cp = Checkpoint(
            id="cp-2",
            agent_id="agent-1",
            name="Before sensitive action",
            state={"key": "value"},
            messages=[{"role": "user", "content": "hi"}],
            iteration=5,
            tool_calls=[{"name": "search"}],
        )
        assert cp.name == "Before sensitive action"
        assert cp.iteration == 5
        assert len(cp.messages) == 1

    def test_to_dict(self):
        cp = Checkpoint(
            id="cp-3",
            agent_id="agent-1",
            state={"x": 1},
        )
        d = cp.to_dict()
        assert d["id"] == "cp-3"
        assert d["state"] == {"x": 1}

    def test_from_dict(self):
        data = {
            "id": "cp-4",
            "agent_id": "agent-2",
            "name": "Test",
            "state": {},
            "messages": [],
            "iteration": 0,
            "tool_calls": [],
            "created_at": 0.0,
            "metadata": {},
        }
        cp = Checkpoint.from_dict(data)
        assert cp.id == "cp-4"
        assert cp.agent_id == "agent-2"


# ==================== InterruptManager 测试 ====================


class TestInterruptManager:
    @pytest.mark.asyncio
    async def test_create_interrupt(self):
        manager = InterruptManager()
        interrupt = await manager.create_interrupt(
            reason=InterruptReason.HUMAN_REVIEW,
            message="需要审核",
        )
        assert interrupt.id is not None
        assert interrupt.reason == InterruptReason.HUMAN_REVIEW
        assert interrupt.status == InterruptStatus.PENDING

    @pytest.mark.asyncio
    async def test_create_interrupt_with_options(self):
        manager = InterruptManager()
        interrupt = await manager.create_interrupt(
            reason=InterruptReason.SENSITIVE_ACTION,
            message="确认删除？",
            options=interrupt_options.approve_reject(),
        )
        assert len(interrupt.options) == 2
        assert interrupt.options[0].value == "approve"

    @pytest.mark.asyncio
    async def test_resolve_interrupt(self):
        manager = InterruptManager()
        interrupt = await manager.create_interrupt(
            reason=InterruptReason.HUMAN_REVIEW,
            message="需要审核",
        )

        resolved = await manager.resolve_interrupt(
            interrupt_id=interrupt.id,
            resolution="approve",
            resolved_by=1,
        )

        assert resolved.status == InterruptStatus.APPROVED
        assert resolved.resolution == "approve"
        assert resolved.resolved_by == 1

    @pytest.mark.asyncio
    async def test_resolve_nonexistent_interrupt(self):
        manager = InterruptManager()
        with pytest.raises(ValueError, match="not found"):
            await manager.resolve_interrupt("nonexistent", "approve")

    @pytest.mark.asyncio
    async def test_get_pending_interrupts(self):
        manager = InterruptManager()
        await manager.create_interrupt(
            reason=InterruptReason.HUMAN_REVIEW,
            message="审核1",
            agent_id="agent-1",
        )
        await manager.create_interrupt(
            reason=InterruptReason.SENSITIVE_ACTION,
            message="审核2",
            agent_id="agent-2",
        )

        pending = await manager.get_pending_interrupts()
        assert len(pending) == 2

        # 按 agent_id 过滤
        pending_agent1 = await manager.get_pending_interrupts(agent_id="agent-1")
        assert len(pending_agent1) == 1

    @pytest.mark.asyncio
    async def test_cancel_interrupt(self):
        manager = InterruptManager()
        interrupt = await manager.create_interrupt(
            reason=InterruptReason.HUMAN_REVIEW,
            message="审核",
        )

        await manager.cancel_interrupt(interrupt.id)

        # 取消后应该不在待处理列表中
        pending = await manager.get_pending_interrupts()
        assert len(pending) == 0

    @pytest.mark.asyncio
    async def test_interrupt_with_ttl(self):
        manager = InterruptManager()
        interrupt = await manager.create_interrupt(
            reason=InterruptReason.HUMAN_REVIEW,
            message="审核",
            ttl=0.001,  # 1ms 过期
        )

        # 等待过期
        await asyncio.sleep(0.01)

        assert interrupt.is_expired() is True

    # ==================== 检查点测试 ====================

    @pytest.mark.asyncio
    async def test_save_checkpoint(self):
        manager = InterruptManager()
        cp = await manager.save_checkpoint(
            agent_id="agent-1",
            name="Before action",
            state={"key": "value"},
            messages=[{"role": "user", "content": "hi"}],
        )
        assert cp.id is not None
        assert cp.agent_id == "agent-1"
        assert cp.state == {"key": "value"}

    @pytest.mark.asyncio
    async def test_list_checkpoints(self):
        manager = InterruptManager()
        await manager.save_checkpoint(agent_id="agent-1", state={}, messages=[])
        await manager.save_checkpoint(agent_id="agent-1", state={}, messages=[])
        await manager.save_checkpoint(agent_id="agent-2", state={}, messages=[])

        cps = await manager.list_checkpoints(agent_id="agent-1")
        assert len(cps) == 2

    @pytest.mark.asyncio
    async def test_restore_checkpoint(self):
        manager = InterruptManager()
        cp = await manager.save_checkpoint(
            agent_id="agent-1",
            state={"x": 1, "y": 2},
            messages=[{"role": "user", "content": "test"}],
            iteration=5,
        )

        restored = await manager.restore_checkpoint(cp.id)
        assert restored["state"] == {"x": 1, "y": 2}
        assert restored["iteration"] == 5
        assert len(restored["messages"]) == 1

    @pytest.mark.asyncio
    async def test_restore_nonexistent_checkpoint(self):
        manager = InterruptManager()
        with pytest.raises(ValueError, match="not found"):
            await manager.restore_checkpoint("nonexistent")

    @pytest.mark.asyncio
    async def test_delete_checkpoint(self):
        manager = InterruptManager()
        cp = await manager.save_checkpoint(agent_id="agent-1", state={}, messages=[])

        await manager.delete_checkpoint(cp.id)

        with pytest.raises(ValueError):
            await manager.restore_checkpoint(cp.id)


# ==================== 全局管理器测试 ====================


class TestGlobalManager:
    def test_get_interrupt_manager(self):
        reset_interrupt_manager()
        manager = get_interrupt_manager()
        assert manager is not None

    def test_reset_interrupt_manager(self):
        manager = get_interrupt_manager()
        manager._pending_interrupts["test"] = InterruptPoint(
            id="test",
            reason=InterruptReason.CUSTOM,
        )

        reset_interrupt_manager()
        new_manager = get_interrupt_manager()
        assert len(new_manager._pending_interrupts) == 0


# ==================== 预置选项测试 ====================


class TestInterruptOptions:
    def test_approve_reject(self):
        options = interrupt_options.approve_reject()
        assert len(options) == 2
        assert options[0].value == "approve"
        assert options[1].value == "reject"

    def test_approve_reject_modify(self):
        options = interrupt_options.approve_reject_modify()
        assert len(options) == 3
        assert options[1].requires_input is True  # modify 需要输入

    def test_confirm_cancel(self):
        options = interrupt_options.confirm_cancel()
        assert len(options) == 2
        assert options[1].value == "cancel"

    def test_retry_skip_abort(self):
        options = interrupt_options.retry_skip_abort()
        assert len(options) == 3
        assert options[0].value == "retry"
        assert options[2].value == "abort"


# ==================== AgentLoop 中断集成测试 ====================


class TestAgentLoopInterrupt:
    def setup_method(self):
        """每个测试前重置全局管理器"""
        reset_interrupt_manager()

    def test_interrupt_manager_parameter(self):
        from app.modules.agent.loop import AgentLoop

        provider = MagicMock()
        manager = InterruptManager()

        loop = AgentLoop(provider=provider, interrupt_manager=manager)
        assert loop._interrupt_manager is manager

    def test_status_property(self):
        from app.modules.agent.loop import AgentLoop, AgentStatus

        provider = MagicMock()
        loop = AgentLoop(provider=provider)

        assert loop.status == AgentStatus.IDLE

    @pytest.mark.asyncio
    async def test_interrupt_method(self):
        from app.modules.agent.loop import AgentLoop, AgentStatus

        provider = MagicMock()
        manager = InterruptManager()  # 使用独立管理器
        loop = AgentLoop(
            provider=provider,
            agent_id="agent-1",
            agent_name="TestAgent",
            interrupt_manager=manager,
        )

        interrupt = await loop.interrupt(
            reason=InterruptReason.SENSITIVE_ACTION,
            message="确认删除？",
        )

        assert interrupt is not None
        assert interrupt.agent_id == "agent-1"
        assert loop.status == AgentStatus.WAITING_INTERRUPT

    @pytest.mark.asyncio
    async def test_resume_method(self):
        from app.modules.agent.loop import AgentLoop, AgentStatus

        provider = MagicMock()
        manager = InterruptManager()
        loop = AgentLoop(provider=provider, interrupt_manager=manager)

        interrupt = await loop.interrupt(
            reason=InterruptReason.HUMAN_REVIEW,
            message="审核",
        )

        resolved = await loop.resume(
            interrupt_id=interrupt.id,
            resolution="approve",
            resolved_by=1,
        )

        assert resolved.status == InterruptStatus.APPROVED
        assert loop.status == AgentStatus.RUNNING

    @pytest.mark.asyncio
    async def test_get_pending_interrupts(self):
        from app.modules.agent.loop import AgentLoop

        provider = MagicMock()
        manager = InterruptManager()
        loop = AgentLoop(
            provider=provider, agent_id="agent-1", interrupt_manager=manager
        )

        await loop.interrupt(reason=InterruptReason.HUMAN_REVIEW, message="审核1")
        await loop.interrupt(reason=InterruptReason.SENSITIVE_ACTION, message="审核2")

        pending = await loop.get_pending_interrupts()
        assert len(pending) == 2

    @pytest.mark.asyncio
    async def test_save_checkpoint(self):
        from app.modules.agent.loop import AgentLoop

        provider = MagicMock()
        manager = InterruptManager()
        loop = AgentLoop(
            provider=provider, agent_id="agent-1", interrupt_manager=manager
        )

        cp = await loop.save_checkpoint(
            name="Before action",
            state={"step": 1},
            messages=[{"role": "user", "content": "hi"}],
            iteration=3,
        )

        assert cp is not None
        assert cp.name == "Before action"

    @pytest.mark.asyncio
    async def test_restore_checkpoint(self):
        from app.modules.agent.loop import AgentLoop

        provider = MagicMock()
        manager = InterruptManager()
        loop = AgentLoop(
            provider=provider, agent_id="agent-1", interrupt_manager=manager
        )

        cp = await loop.save_checkpoint(
            state={"key": "value"},
            messages=[],
            iteration=5,
        )

        restored = await loop.restore_checkpoint(cp.id)
        assert restored["state"] == {"key": "value"}
        assert restored["iteration"] == 5

    @pytest.mark.asyncio
    async def test_list_checkpoints(self):
        from app.modules.agent.loop import AgentLoop

        provider = MagicMock()
        manager = InterruptManager()
        loop = AgentLoop(
            provider=provider, agent_id="agent-1", interrupt_manager=manager
        )

        await loop.save_checkpoint(state={}, messages=[])
        await loop.save_checkpoint(state={}, messages=[])

        checkpoints = await loop.list_checkpoints()
        assert len(checkpoints) == 2

    @pytest.mark.asyncio
    async def test_confirm_sensitive_action(self):
        from app.modules.agent.loop import AgentLoop

        provider = MagicMock()
        manager = InterruptManager()
        loop = AgentLoop(provider=provider, interrupt_manager=manager)

        # 测试中断创建
        interrupt = await loop.interrupt(
            reason=InterruptReason.SENSITIVE_ACTION,
            message="删除文件",
        )
        assert interrupt is not None

        # 手动解决
        resolved = await loop.resume(interrupt.id, "approve", resolved_by=1)
        assert resolved.status == InterruptStatus.APPROVED

    @pytest.mark.asyncio
    async def test_request_human_review(self):
        from app.modules.agent.loop import AgentLoop

        provider = MagicMock()
        manager = InterruptManager()
        loop = AgentLoop(provider=provider, interrupt_manager=manager)

        interrupt = await loop.request_human_review("待审核内容")
        assert interrupt is not None
        assert interrupt.reason == InterruptReason.HUMAN_REVIEW
