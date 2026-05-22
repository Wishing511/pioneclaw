"""
组织模型 - 树形结构
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:
    from app.models import User


def generate_uuid() -> str:
    return str(uuid.uuid4())


class Organization(Base):
    """组织模型 - 树形结构"""

    __tablename__ = "organizations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    code: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # 树形结构
    parent_id: Mapped[str | None] = mapped_column(
        ForeignKey("organizations.id"), nullable=True, index=True
    )
    level: Mapped[int] = mapped_column(
        Integer, default=1
    )  # 层级：1=公司, 2=部门, 3=团队
    path: Mapped[str] = mapped_column(
        String(500), default=""
    )  # 路径：如 "uuid1/uuid2/uuid3"

    # 管理信息
    manager_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    type: Mapped[str] = mapped_column(
        String(20), default="department"
    )  # company/department/team
    status: Mapped[str] = mapped_column(String(20), default="active")  # active/inactive

    # 元数据（不能用 metadata，SQLAlchemy 保留字）
    meta_data: Mapped[dict | None] = mapped_column("metadata", JSON, nullable=True)

    # 组织级设置
    model_config_ids: Mapped[dict | None] = mapped_column(
        JSON, nullable=True
    )  # 分配给该组织的模型配置 ID 列表
    settings: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # settings 结构：
    # {
    #   "default_output_language": "中文",
    #   "max_agents_per_user": 10,
    #   "max_tasks_per_day": 100
    # }

    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=func.now(), onupdate=func.now()
    )

    # 关系
    parent: Mapped[Organization | None] = relationship(
        "Organization",
        back_populates="children",
        remote_side=[id],
        foreign_keys=[parent_id],
    )
    children: Mapped[list[Organization]] = relationship(
        "Organization",
        back_populates="parent",
        foreign_keys=[parent_id],
        cascade="all, delete-orphan",
    )
    users: Mapped[list[User]] = relationship(
        "User", back_populates="organization", foreign_keys="User.organization_id"
    )

    def update_path(self, parent_path: str = ""):
        """更新路径"""
        if parent_path:
            self.path = f"{parent_path}/{self.id}"
        else:
            self.path = self.id

        # 更新所有子节点
        for child in self.children:
            child.update_path(self.path)

    def __repr__(self):
        return f"<Organization(id={self.id}, name={self.name}, level={self.level})>"
