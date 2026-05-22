"""
Interrupt 中断与恢复机制

借鉴 LangGraph interrupt/resume 机制：
- 在 Agent 执行过程中创建中断点
- 保存状态快照，暂停执行
- 等待人工干预后恢复执行

使用场景：
- 敏感操作确认（删除、支付等）
- 人工审核（内容发布、审批流程）
- 错误恢复（异常后人工介入）
- 检查点（长时间任务的中间状态）
"""

import asyncio
import logging
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class InterruptReason(Enum):
    """中断原因"""

    HUMAN_REVIEW = "human_review"  # 需要人工审核
    SENSITIVE_ACTION = "sensitive"  # 敏感操作确认
    ERROR_RECOVERY = "error_recovery"  # 错误恢复
    CHECKPOINT = "checkpoint"  # 检查点
    TIMEOUT = "timeout"  # 超时等待
    CUSTOM = "custom"  # 自定义


class InterruptStatus(Enum):
    """中断状态"""

    PENDING = "pending"  # 待处理
    APPROVED = "approved"  # 已批准
    REJECTED = "rejected"  # 已拒绝
    MODIFIED = "modified"  # 已修改
    EXPIRED = "expired"  # 已过期
    CANCELLED = "cancelled"  # 已取消


@dataclass
class InterruptOption:
    """中断选项"""

    label: str  # 显示标签
    value: str  # 选项值
    description: str = ""  # 选项描述
    requires_input: bool = False  # 是否需要输入
    input_placeholder: str = ""  # 输入占位符
    style: str = "default"  # default, primary, danger, warning


@dataclass
class InterruptPoint:
    """中断点

    记录 Agent 执行中断的位置和状态
    """

    id: str
    reason: InterruptReason
    status: InterruptStatus = InterruptStatus.PENDING

    # Agent 上下文
    agent_id: str = ""
    agent_name: str = ""
    conversation_id: str = ""
    session_id: str = ""
    user_id: int | None = None

    # 状态快照
    state_snapshot: dict[str, Any] = field(default_factory=dict)
    message_snapshot: list[dict] = field(default_factory=list)

    # 中断信息
    title: str = ""
    message: str = ""
    details: dict[str, Any] = field(default_factory=dict)
    options: list[InterruptOption] = field(default_factory=list)

    # 时间戳
    created_at: float = 0.0
    expires_at: float | None = None
    resolved_at: float | None = None

    # 解决信息
    resolution: str | None = None
    resolved_by: int | None = None
    resolution_note: str = ""
    modified_state: dict[str, Any] = field(default_factory=dict)

    # 元数据
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not self.created_at:
            self.created_at = time.time()
        if not self.id:
            self.id = str(uuid.uuid4())[:8]

    def is_expired(self) -> bool:
        """检查是否已过期"""
        if self.expires_at is None:
            return False
        return time.time() > self.expires_at

    def is_resolved(self) -> bool:
        """检查是否已解决"""
        return self.status != InterruptStatus.PENDING

    def to_dict(self) -> dict[str, Any]:
        """转换为字典"""
        return {
            "id": self.id,
            "reason": self.reason.value,
            "status": self.status.value,
            "agent_id": self.agent_id,
            "agent_name": self.agent_name,
            "conversation_id": self.conversation_id,
            "session_id": self.session_id,
            "user_id": self.user_id,
            "title": self.title,
            "message": self.message,
            "details": self.details,
            "options": [
                {
                    "label": o.label,
                    "value": o.value,
                    "description": o.description,
                    "requires_input": o.requires_input,
                    "input_placeholder": o.input_placeholder,
                    "style": o.style,
                }
                for o in self.options
            ],
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "resolved_at": self.resolved_at,
            "resolution": self.resolution,
            "resolved_by": self.resolved_by,
            "resolution_note": self.resolution_note,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "InterruptPoint":
        """从字典创建"""
        options = [InterruptOption(**o) for o in data.pop("options", [])]
        return cls(
            reason=InterruptReason(data.pop("reason")),
            status=InterruptStatus(data.pop("status", "pending")),
            options=options,
            **data,
        )


@dataclass
class Checkpoint:
    """检查点

    用于保存 Agent 执行状态，支持恢复
    """

    id: str
    agent_id: str
    name: str = ""

    # 状态快照
    state: dict[str, Any] = field(default_factory=dict)
    messages: list[dict] = field(default_factory=list)
    iteration: int = 0
    tool_calls: list[dict] = field(default_factory=list)

    # 时间戳
    created_at: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not self.created_at:
            self.created_at = time.time()
        if not self.id:
            self.id = str(uuid.uuid4())[:8]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "agent_id": self.agent_id,
            "name": self.name,
            "state": self.state,
            "messages": self.messages,
            "iteration": self.iteration,
            "tool_calls": self.tool_calls,
            "created_at": self.created_at,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Checkpoint":
        return cls(**data)


class InterruptManager:
    """中断管理器

    管理 Agent 执行中断和恢复
    """

    def __init__(
        self,
        storage: Any | None = None,
        default_ttl: float = 3600.0,  # 默认 1 小时过期
    ):
        """
        Args:
            storage: 持久化存储（可选）
            default_ttl: 默认过期时间（秒）
        """
        self.storage = storage
        self.default_ttl = default_ttl

        # 内存缓存
        self._pending_interrupts: dict[str, InterruptPoint] = {}
        self._checkpoints: dict[str, list[Checkpoint]] = {}

        # 事件回调
        self._on_interrupt: Callable | None = None
        self._on_resolve: Callable | None = None

    def set_callbacks(
        self,
        on_interrupt: Callable | None = None,
        on_resolve: Callable | None = None,
    ) -> None:
        """设置事件回调"""
        self._on_interrupt = on_interrupt
        self._on_resolve = on_resolve

    async def create_interrupt(
        self,
        reason: InterruptReason,
        message: str,
        agent_id: str = "",
        agent_name: str = "",
        conversation_id: str = "",
        session_id: str = "",
        user_id: int | None = None,
        title: str = "",
        details: dict[str, Any] = None,
        options: list[InterruptOption] = None,
        state_snapshot: dict[str, Any] = None,
        message_snapshot: list[dict] = None,
        ttl: float | None = None,
        metadata: dict[str, Any] = None,
    ) -> InterruptPoint:
        """创建中断点

        Args:
            reason: 中断原因
            message: 中断消息
            agent_id: Agent ID
            agent_name: Agent 名称
            conversation_id: 对话 ID
            session_id: 会话 ID
            user_id: 用户 ID
            title: 标题
            details: 详细信息
            options: 可选项
            state_snapshot: 状态快照
            message_snapshot: 消息快照
            ttl: 过期时间（秒）
            metadata: 元数据

        Returns:
            InterruptPoint: 创建的中断点
        """
        interrupt_id = str(uuid.uuid4())[:8]
        created_at = time.time()
        expires_at = created_at + (ttl or self.default_ttl) if ttl != 0 else None

        # 默认选项
        if not options:
            options = [
                InterruptOption(label="继续", value="approve", style="primary"),
                InterruptOption(label="取消", value="reject", style="danger"),
            ]

        interrupt = InterruptPoint(
            id=interrupt_id,
            reason=reason,
            status=InterruptStatus.PENDING,
            agent_id=agent_id,
            agent_name=agent_name,
            conversation_id=conversation_id,
            session_id=session_id,
            user_id=user_id,
            state_snapshot=state_snapshot or {},
            message_snapshot=message_snapshot or [],
            title=title or self._default_title(reason),
            message=message,
            details=details or {},
            options=options,
            created_at=created_at,
            expires_at=expires_at,
            metadata=metadata or {},
        )

        # 存储
        self._pending_interrupts[interrupt_id] = interrupt
        await self._persist_interrupt(interrupt)

        # 触发回调
        if self._on_interrupt:
            try:
                result = self._on_interrupt(interrupt)
                if asyncio.iscoroutine(result):
                    await result
            except Exception as e:
                logger.warning(f"Interrupt callback failed: {e}")

        logger.info(
            f"Created interrupt {interrupt_id}: {reason.value} - {message[:50]}"
        )
        return interrupt

    def _default_title(self, reason: InterruptReason) -> str:
        """获取默认标题"""
        titles = {
            InterruptReason.HUMAN_REVIEW: "需要人工审核",
            InterruptReason.SENSITIVE_ACTION: "敏感操作确认",
            InterruptReason.ERROR_RECOVERY: "错误恢复",
            InterruptReason.CHECKPOINT: "检查点",
            InterruptReason.TIMEOUT: "等待超时",
            InterruptReason.CUSTOM: "中断",
        }
        return titles.get(reason, "中断")

    async def resolve_interrupt(
        self,
        interrupt_id: str,
        resolution: str,
        resolved_by: int | None = None,
        resolution_note: str = "",
        modified_state: dict[str, Any] = None,
    ) -> InterruptPoint:
        """解决中断

        Args:
            interrupt_id: 中断 ID
            resolution: 解决方式（approve/reject/modify）
            resolved_by: 解决者用户 ID
            resolution_note: 解决备注
            modified_state: 修改后的状态

        Returns:
            InterruptPoint: 更新后的中断点

        Raises:
            ValueError: 中断不存在或已解决
        """
        interrupt = self._pending_interrupts.get(interrupt_id)
        if not interrupt:
            # 尝试从存储加载
            interrupt = await self._load_interrupt(interrupt_id)
            if not interrupt:
                raise ValueError(f"Interrupt {interrupt_id} not found")

        if interrupt.is_resolved():
            raise ValueError(f"Interrupt {interrupt_id} already resolved")

        if interrupt.is_expired():
            interrupt.status = InterruptStatus.EXPIRED
            raise ValueError(f"Interrupt {interrupt_id} has expired")

        # 更新状态
        status_map = {
            "approve": InterruptStatus.APPROVED,
            "reject": InterruptStatus.REJECTED,
            "modify": InterruptStatus.MODIFIED,
            "cancel": InterruptStatus.CANCELLED,
        }
        interrupt.status = status_map.get(resolution, InterruptStatus.APPROVED)
        interrupt.resolution = resolution
        interrupt.resolved_by = resolved_by
        interrupt.resolution_note = resolution_note
        interrupt.resolved_at = time.time()

        if modified_state:
            interrupt.modified_state = modified_state

        # 移除待处理列表
        if interrupt_id in self._pending_interrupts:
            del self._pending_interrupts[interrupt_id]

        # 持久化
        await self._persist_interrupt(interrupt)

        # 触发回调
        if self._on_resolve:
            try:
                result = self._on_resolve(interrupt)
                if asyncio.iscoroutine(result):
                    await result
            except Exception as e:
                logger.warning(f"Resolve callback failed: {e}")

        logger.info(f"Resolved interrupt {interrupt_id}: {resolution}")
        return interrupt

    async def get_interrupt(self, interrupt_id: str) -> InterruptPoint | None:
        """获取中断点"""
        # 先从内存获取
        interrupt = self._pending_interrupts.get(interrupt_id)
        if interrupt:
            return interrupt

        # 从存储加载
        return await self._load_interrupt(interrupt_id)

    async def get_pending_interrupts(
        self,
        conversation_id: str | None = None,
        session_id: str | None = None,
        user_id: int | None = None,
        agent_id: str | None = None,
    ) -> list[InterruptPoint]:
        """获取待处理中断列表"""
        result = []

        for interrupt in self._pending_interrupts.values():
            # 过滤条件
            if conversation_id and interrupt.conversation_id != conversation_id:
                continue
            if session_id and interrupt.session_id != session_id:
                continue
            if user_id and interrupt.user_id != user_id:
                continue
            if agent_id and interrupt.agent_id != agent_id:
                continue

            # 检查过期
            if interrupt.is_expired():
                interrupt.status = InterruptStatus.EXPIRED
                continue

            result.append(interrupt)

        # 按时间倒序
        result.sort(key=lambda x: x.created_at, reverse=True)
        return result

    async def cancel_interrupt(self, interrupt_id: str) -> None:
        """取消中断"""
        interrupt = self._pending_interrupts.get(interrupt_id)
        if interrupt:
            interrupt.status = InterruptStatus.CANCELLED
            interrupt.resolved_at = time.time()
            del self._pending_interrupts[interrupt_id]
            await self._persist_interrupt(interrupt)

    # ==================== 检查点管理 ====================

    async def save_checkpoint(
        self,
        agent_id: str,
        state: dict[str, Any],
        messages: list[dict],
        iteration: int = 0,
        tool_calls: list[dict] = None,
        name: str = "",
        metadata: dict[str, Any] = None,
    ) -> Checkpoint:
        """保存检查点"""
        checkpoint = Checkpoint(
            id=str(uuid.uuid4())[:8],
            agent_id=agent_id,
            name=name,
            state=state,
            messages=messages,
            iteration=iteration,
            tool_calls=tool_calls or [],
            metadata=metadata or {},
        )

        if agent_id not in self._checkpoints:
            self._checkpoints[agent_id] = []
        self._checkpoints[agent_id].append(checkpoint)

        # 保留最近 10 个检查点
        if len(self._checkpoints[agent_id]) > 10:
            self._checkpoints[agent_id] = self._checkpoints[agent_id][-10:]

        await self._persist_checkpoint(checkpoint)
        logger.debug(f"Saved checkpoint {checkpoint.id} for agent {agent_id}")
        return checkpoint

    async def load_checkpoint(self, checkpoint_id: str) -> Checkpoint | None:
        """加载检查点"""
        for checkpoints in self._checkpoints.values():
            for cp in checkpoints:
                if cp.id == checkpoint_id:
                    return cp

        # 从存储加载
        return await self._load_checkpoint(checkpoint_id)

    async def list_checkpoints(
        self,
        agent_id: str,
        limit: int = 10,
    ) -> list[Checkpoint]:
        """列出检查点"""
        checkpoints = self._checkpoints.get(agent_id, [])
        return checkpoints[-limit:]

    async def restore_checkpoint(
        self,
        checkpoint_id: str,
    ) -> dict[str, Any]:
        """恢复到检查点

        Returns:
            Dict: 包含 state, messages, iteration, tool_calls 的字典
        """
        checkpoint = await self.load_checkpoint(checkpoint_id)
        if not checkpoint:
            raise ValueError(f"Checkpoint {checkpoint_id} not found")

        return {
            "state": checkpoint.state,
            "messages": checkpoint.messages,
            "iteration": checkpoint.iteration,
            "tool_calls": checkpoint.tool_calls,
        }

    async def delete_checkpoint(self, checkpoint_id: str) -> None:
        """删除检查点"""
        for _agent_id, checkpoints in self._checkpoints.items():
            for i, cp in enumerate(checkpoints):
                if cp.id == checkpoint_id:
                    del checkpoints[i]
                    await self._delete_checkpoint(checkpoint_id)
                    return

    # ==================== 持久化方法（可由子类重写） ====================

    async def _persist_interrupt(self, interrupt: InterruptPoint) -> None:
        """持久化中断"""
        if self.storage:
            try:
                await self.storage.save_interrupt(interrupt)
            except Exception as e:
                logger.warning(f"Failed to persist interrupt: {e}")

    async def _load_interrupt(self, interrupt_id: str) -> InterruptPoint | None:
        """加载中断"""
        if self.storage:
            try:
                return await self.storage.get_interrupt(interrupt_id)
            except Exception as e:
                logger.warning(f"Failed to load interrupt: {e}")
        return None

    async def _persist_checkpoint(self, checkpoint: Checkpoint) -> None:
        """持久化检查点"""
        if self.storage:
            try:
                await self.storage.save_checkpoint(checkpoint)
            except Exception as e:
                logger.warning(f"Failed to persist checkpoint: {e}")

    async def _load_checkpoint(self, checkpoint_id: str) -> Checkpoint | None:
        """加载检查点"""
        if self.storage:
            try:
                return await self.storage.get_checkpoint(checkpoint_id)
            except Exception as e:
                logger.warning(f"Failed to load checkpoint: {e}")
        return None

    async def _delete_checkpoint(self, checkpoint_id: str) -> None:
        """删除检查点"""
        if self.storage:
            try:
                await self.storage.delete_checkpoint(checkpoint_id)
            except Exception as e:
                logger.warning(f"Failed to delete checkpoint: {e}")


# ==================== 全局实例 ====================

_global_manager: InterruptManager | None = None


def get_interrupt_manager() -> InterruptManager:
    """获取全局中断管理器"""
    global _global_manager
    if _global_manager is None:
        _global_manager = InterruptManager()
    return _global_manager


def reset_interrupt_manager() -> None:
    """重置全局管理器"""
    global _global_manager
    _global_manager = None


# ==================== 预置选项 ====================


class interrupt_options:
    """预置中断选项"""

    @staticmethod
    def approve_reject() -> list[InterruptOption]:
        """批准/拒绝"""
        return [
            InterruptOption(label="批准", value="approve", style="primary"),
            InterruptOption(label="拒绝", value="reject", style="danger"),
        ]

    @staticmethod
    def approve_reject_modify() -> list[InterruptOption]:
        """批准/拒绝/修改"""
        return [
            InterruptOption(label="批准", value="approve", style="primary"),
            InterruptOption(
                label="修改后批准",
                value="modify",
                style="warning",
                requires_input=True,
                input_placeholder="输入修改内容",
            ),
            InterruptOption(label="拒绝", value="reject", style="danger"),
        ]

    @staticmethod
    def confirm_cancel() -> list[InterruptOption]:
        """确认/取消"""
        return [
            InterruptOption(label="确认", value="approve", style="primary"),
            InterruptOption(label="取消", value="cancel", style="default"),
        ]

    @staticmethod
    def retry_skip_abort() -> list[InterruptOption]:
        """重试/跳过/中止"""
        return [
            InterruptOption(label="重试", value="retry", style="primary"),
            InterruptOption(label="跳过", value="skip", style="warning"),
            InterruptOption(label="中止", value="abort", style="danger"),
        ]
