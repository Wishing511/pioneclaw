"""
审批流程模型

用户提交 Skill/文档共享请求，管理员审批。
"""

from __future__ import annotations

import enum
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import JSON, DateTime, ForeignKey, String, Text
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:
    from app.models import User


class ApprovalStatus(str, enum.Enum):
    """审批状态"""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


class ApprovalType(str, enum.Enum):
    """审批类型"""

    SKILL_TO_ORG = "skill_to_org"  # 用户级 Skill → 组织级
    SKILL_TO_SYSTEM = "skill_to_system"  # 组织/用户级 Skill → 系统级
    DOC_TO_ORG = "doc_to_org"  # 文档共享到组织
    DOC_TO_SYSTEM = "doc_to_system"  # 文档共享到全局
    USER_JOIN_ORG = "user_join_org"  # 用户申请加入组织
    TASK_APPROVAL = "task_approval"  # 任务审批
    SECURITY_GATEWAY = "security_gateway"  # 安全网关审批


class Approval(Base):
    """审批记录"""

    __tablename__ = "approvals"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    approval_type: Mapped[str] = mapped_column(
        SQLEnum(ApprovalType), default=ApprovalType.SKILL_TO_ORG
    )
    status: Mapped[str] = mapped_column(
        SQLEnum(ApprovalStatus), default=ApprovalStatus.PENDING, index=True
    )

    # 审批内容
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # 提交人
    requester_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    requester_org_id: Mapped[str | None] = mapped_column(
        ForeignKey("organizations.id"), nullable=True
    )

    # 审批人
    reviewer_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    review_comment: Mapped[str | None] = mapped_column(Text, nullable=True)

    # 关联资源
    resource_type: Mapped[str] = mapped_column(String(50))  # skill / wiki / document
    resource_id: Mapped[str] = mapped_column(String(100))  # 资源 ID

    # 目标范围
    target_scope: Mapped[str] = mapped_column(String(20))  # org / system
    target_org_id: Mapped[str | None] = mapped_column(
        ForeignKey("organizations.id"), nullable=True
    )

    # 元数据（避免使用 SQLAlchemy 保留字 metadata）
    extra_data: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # 关系
    requester: Mapped[User] = relationship(foreign_keys=[requester_id])
    reviewer: Mapped[User | None] = relationship(foreign_keys=[reviewer_id])

    @property
    def requester_name(self) -> str:
        return (
            (self.requester.display_name or self.requester.username)
            if self.requester
            else "Unknown"
        )

    def __repr__(self):
        return (
            f"<Approval(id={self.id}, type={self.approval_type}, status={self.status})>"
        )
