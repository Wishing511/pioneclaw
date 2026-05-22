"""
TaskFlow 持久化工作流模型

借鉴 OpenClaw skills/taskflow/SKILL.md 的 managed-flow 模式

核心思路：
- 工作流状态持久化到数据库（支持 waiting/resume）
- revision 版本号冲突安全
- 启动时可恢复未完成的流程
"""

import enum
from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class TaskFlowState(str, enum.Enum):
    CREATED = "created"
    RUNNING = "running"
    WAITING = "waiting"  # 等待外部输入/确认
    COMPLETED = "completed"
    FAILED = "failed"


class TaskFlow(Base):
    """持久化工作流

    借鉴 OpenClaw TaskFlow managed-flow：
    - createManaged → create
    - runTask → run_task
    - setWaiting → set_waiting
    - resume → resume
    - finish/fail → finish/fail
    - revision tracking 冲突安全
    """

    __tablename__ = "task_flows"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    goal: Mapped[str] = mapped_column(Text, nullable=False)
    current_step: Mapped[str] = mapped_column(Text, nullable=False, default="")
    state: Mapped[str] = mapped_column(
        String(20), default=TaskFlowState.CREATED.value, index=True
    )

    # 所有权
    owner_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id"), index=True
    )
    session_id: Mapped[str | None] = mapped_column(String(100))

    # 状态持久化
    context: Mapped[dict] = mapped_column(JSON, default=dict)
    wait_reason: Mapped[str | None] = mapped_column(Text)

    # 冲突安全（借鉴 OpenClaw revision tracking）
    revision: Mapped[int] = mapped_column(Integer, default=1)

    # 子任务关联
    child_task_ids: Mapped[list] = mapped_column(JSON, default=list)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.now, onupdate=datetime.now
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
