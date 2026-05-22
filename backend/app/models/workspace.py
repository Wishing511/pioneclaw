"""
Workspace 模型 - 用户工作空间

每个用户至少有一个 Workspace，存放个人配置和资源归属。
实际目录在用户机器上，此模型记录注册信息和设置。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:
    from app.models import Organization, User


class Workspace(Base):
    """用户工作空间"""

    __tablename__ = "workspaces"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100))  # 工作空间名称
    path: Mapped[str] = mapped_column(String(500), default="")  # 用户本地路径
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # 归属
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    organization_id: Mapped[str | None] = mapped_column(
        ForeignKey("organizations.id"), nullable=True, index=True
    )

    # 工作空间级设置
    settings: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # settings 结构：
    # {
    #   "output_language": "中文",
    #   "default_model_config_id": null,
    #   "user_name": "小明",        # 用户自定义称呼
    #   "user_address": "北京",
    #   "ai_name": "小爪",          # Agent 自定义名
    #   "personality": "professional",
    #   "custom_personality": ""
    # }

    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_active_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # 关系
    owner: Mapped[User] = relationship(foreign_keys=[owner_id])
    organization: Mapped[Organization | None] = relationship(
        foreign_keys=[organization_id]
    )

    def __repr__(self):
        return f"<Workspace(id={self.id}, name={self.name}, owner_id={self.owner_id})>"
