"""
TaskFlow 持久化工作流管理器

借鉴 OpenClaw skills/taskflow/SKILL.md 的 managed-flow 模式

核心思路：
- 工作流状态持久化到数据库（支持 waiting/resume）
- revision 版本号冲突安全（乐观锁）
- 启动时可恢复未完成的流程
- 与 WorkflowEngine 可选集成
"""

import logging
import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.task_flow import TaskFlow, TaskFlowState

logger = logging.getLogger(__name__)


class RevisionConflictError(Exception):
    """revision 冲突（乐观锁失败）"""

    pass


class InvalidStateTransition(Exception):
    """非法状态转换"""

    pass


# 合法状态转换表
VALID_TRANSITIONS: dict[str, set] = {
    TaskFlowState.CREATED.value: {TaskFlowState.RUNNING.value},
    TaskFlowState.RUNNING.value: {
        TaskFlowState.WAITING.value,
        TaskFlowState.COMPLETED.value,
        TaskFlowState.FAILED.value,
    },
    TaskFlowState.WAITING.value: {
        TaskFlowState.RUNNING.value,
        TaskFlowState.FAILED.value,
    },
    # 终态不可转换
    TaskFlowState.COMPLETED.value: set(),
    TaskFlowState.FAILED.value: set(),
}


class TaskFlowManager:
    """持久化工作流管理器

    借鉴 OpenClaw TaskFlow managed-flow：
    - createManaged → create
    - runTask → run_step
    - setWaiting → set_waiting
    - resume → resume
    - finish/fail → finish/fail
    - revision tracking 冲突安全
    - recover_pending → 恢复未完成流程
    """

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def create(
        self,
        name: str,
        goal: str,
        owner_id: str | None = None,
        session_id: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> TaskFlow:
        """创建新的 TaskFlow

        对应 OpenClaw createManaged
        """
        flow = TaskFlow(
            id=str(uuid.uuid4()),
            name=name,
            goal=goal,
            current_step="",
            state=TaskFlowState.CREATED.value,
            owner_id=owner_id,
            session_id=session_id,
            context=context or {},
            revision=1,
            child_task_ids=[],
        )
        self.db.add(flow)
        await self.db.commit()
        await self.db.refresh(flow)
        logger.info(f"[TaskFlow] Created flow '{name}' (id={flow.id}, rev=1)")
        return flow

    async def start(self, flow_id: str, initial_step: str = "") -> TaskFlow:
        """启动工作流 CREATED → RUNNING"""
        flow = await self._get_flow(flow_id)
        self._validate_transition(flow.state, TaskFlowState.RUNNING.value)
        flow.state = TaskFlowState.RUNNING.value
        if initial_step:
            flow.current_step = initial_step
        flow.updated_at = datetime.now()
        await self.db.commit()
        await self.db.refresh(flow)
        logger.info(f"[TaskFlow] Started flow '{flow.name}' (id={flow.id})")
        return flow

    async def run_step(
        self,
        flow_id: str,
        step_name: str,
        step_result: dict[str, Any] | None = None,
    ) -> TaskFlow:
        """执行一步并记录到 context

        对应 OpenClaw runTask
        """
        flow = await self._get_flow(flow_id)
        if flow.state not in (TaskFlowState.RUNNING.value,):
            raise InvalidStateTransition(
                f"Cannot run step in state '{flow.state}', expected 'running'"
            )
        flow.current_step = step_name
        if step_result:
            flow.context[f"step:{step_name}"] = step_result
        flow.updated_at = datetime.now()
        await self._safe_commit(flow)
        logger.info(
            f"[TaskFlow] Step '{step_name}' on flow '{flow.name}' (rev={flow.revision})"
        )
        return flow

    async def set_waiting(
        self,
        flow_id: str,
        wait_reason: str,
        checkpoint: dict[str, Any] | None = None,
    ) -> TaskFlow:
        """暂停工作流等待外部输入/确认

        对应 OpenClaw setWaiting
        """
        flow = await self._get_flow(flow_id)
        self._validate_transition(flow.state, TaskFlowState.WAITING.value)
        flow.state = TaskFlowState.WAITING.value
        flow.wait_reason = wait_reason
        if checkpoint:
            flow.context["_checkpoint"] = checkpoint
        flow.updated_at = datetime.now()
        await self._safe_commit(flow)
        logger.info(
            f"[TaskFlow] Flow '{flow.name}' waiting: {wait_reason} (rev={flow.revision})"
        )
        return flow

    async def resume(
        self,
        flow_id: str,
        resume_input: dict[str, Any] | None = None,
        expected_revision: int | None = None,
    ) -> TaskFlow:
        """恢复等待中的工作流

        对应 OpenClaw resume
        支持 expected_revision 乐观锁检查
        """
        flow = await self._get_flow(flow_id)
        self._validate_transition(flow.state, TaskFlowState.RUNNING.value)

        # 乐观锁检查
        if expected_revision is not None and flow.revision != expected_revision:
            raise RevisionConflictError(
                f"Revision conflict: expected {expected_revision}, got {flow.revision}"
            )

        flow.state = TaskFlowState.RUNNING.value
        flow.wait_reason = None
        if resume_input:
            flow.context["_resume_input"] = resume_input
        flow.updated_at = datetime.now()
        flow.revision += 1
        await self.db.commit()
        await self.db.refresh(flow)
        logger.info(f"[TaskFlow] Resumed flow '{flow.name}' (rev={flow.revision})")
        return flow

    async def finish(
        self, flow_id: str, final_result: dict[str, Any] | None = None
    ) -> TaskFlow:
        """完成工作流

        对应 OpenClaw finish
        """
        flow = await self._get_flow(flow_id)
        self._validate_transition(flow.state, TaskFlowState.COMPLETED.value)
        flow.state = TaskFlowState.COMPLETED.value
        flow.wait_reason = None
        if final_result:
            flow.context["_final_result"] = final_result
        flow.updated_at = datetime.now()
        flow.completed_at = datetime.now()
        flow.revision += 1
        await self.db.commit()
        await self.db.refresh(flow)
        logger.info(f"[TaskFlow] Finished flow '{flow.name}' (rev={flow.revision})")
        return flow

    async def fail(self, flow_id: str, error: str) -> TaskFlow:
        """标记工作流失败

        对应 OpenClaw fail
        """
        flow = await self._get_flow(flow_id)
        self._validate_transition(flow.state, TaskFlowState.FAILED.value)
        flow.state = TaskFlowState.FAILED.value
        flow.wait_reason = None
        flow.context["_error"] = error
        flow.updated_at = datetime.now()
        flow.revision += 1
        await self.db.commit()
        await self.db.refresh(flow)
        logger.info(
            f"[TaskFlow] Failed flow '{flow.name}': {error} (rev={flow.revision})"
        )
        return flow

    async def add_child_task(self, flow_id: str, child_task_id: str) -> TaskFlow:
        """关联子任务"""
        flow = await self._get_flow(flow_id)
        if child_task_id not in flow.child_task_ids:
            flow.child_task_ids = [*flow.child_task_ids, child_task_id]
            flow.updated_at = datetime.now()
            await self._safe_commit(flow)
        return flow

    async def get_flow(self, flow_id: str) -> TaskFlow | None:
        """获取单个 TaskFlow"""
        result = await self.db.execute(select(TaskFlow).where(TaskFlow.id == flow_id))
        return result.scalars().first()

    async def list_flows(
        self,
        owner_id: str | None = None,
        state: str | None = None,
        session_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[Sequence[TaskFlow], int]:
        """列出 TaskFlow，返回 (flows, total_count)"""
        conditions = []
        if owner_id:
            conditions.append(TaskFlow.owner_id == owner_id)
        if state:
            conditions.append(TaskFlow.state == state)
        if session_id:
            conditions.append(TaskFlow.session_id == session_id)

        # 总数查询
        count_query = select(func.count(TaskFlow.id))
        if conditions:
            count_query = count_query.where(and_(*conditions))
        count_result = await self.db.execute(count_query)
        total = count_result.scalar() or 0

        # 数据查询
        query = (
            select(TaskFlow)
            .order_by(TaskFlow.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        if conditions:
            query = query.where(and_(*conditions))
        result = await self.db.execute(query)
        return result.scalars().all(), total

    async def recover_pending(self) -> list[TaskFlow]:
        """恢复未完成的流程（启动时调用）

        将 RUNNING 状态的流程重置为 WAITING，
        以便人工/系统检查后决定 resume 还是 fail。
        CREATED 状态的流程也一并恢复。
        """
        result = await self.db.execute(
            select(TaskFlow).where(
                TaskFlow.state.in_(
                    [
                        TaskFlowState.RUNNING.value,
                        TaskFlowState.CREATED.value,
                    ]
                )
            )
        )
        pending = result.scalars().all()
        recovered = []
        for flow in pending:
            old_state = flow.state
            flow.state = TaskFlowState.WAITING.value
            flow.wait_reason = f"Recovered from {old_state} (auto-recovery on startup)"
            flow.revision += 1
            flow.updated_at = datetime.now()
            recovered.append(flow)
            logger.info(
                f"[TaskFlow] Recovered flow '{flow.name}' "
                f"from {old_state} → waiting (rev={flow.revision})"
            )

        if recovered:
            await self.db.commit()
            for f in recovered:
                await self.db.refresh(f)

        return recovered

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    async def _get_flow(self, flow_id: str) -> TaskFlow:
        """获取 TaskFlow，不存在则抛异常"""
        flow = await self.get_flow(flow_id)
        if not flow:
            raise ValueError(f"TaskFlow not found: {flow_id}")
        return flow

    def _validate_transition(self, current: str, target: str) -> None:
        """验证状态转换合法性"""
        allowed = VALID_TRANSITIONS.get(current, set())
        if target not in allowed:
            raise InvalidStateTransition(
                f"Cannot transition from '{current}' to '{target}'"
            )

    async def _safe_commit(self, flow: TaskFlow) -> None:
        """带 revision 递增的安全提交"""
        flow.revision += 1
        await self.db.commit()
        await self.db.refresh(flow)
