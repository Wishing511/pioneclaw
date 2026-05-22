"""
Wiki 知识库模型
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:
    from app.models import Organization, User


def generate_uuid() -> str:
    return str(uuid.uuid4())


class WikiScope(str, enum.Enum):
    """Wiki 权限范围"""

    SYSTEM = "system"  # 系统级，超管创建，全局可用
    ORG = "org"  # 组织级，组织管理员审批，组织内可用
    USER = "user"  # 用户级，用户自建，仅自己可用


class WikiSpaceType(str, enum.Enum):
    """Wiki 空间类型"""

    USER = "user"  # 用户个人空间
    ORG = "org"  # 组织共享空间


class WikiSpace(Base):
    """Wiki 空间"""

    __tablename__ = "wiki_spaces"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    type: Mapped[str] = mapped_column(String(20), default="user")  # user/org
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    organization_id: Mapped[str | None] = mapped_column(
        ForeignKey("organizations.id"), nullable=True, index=True
    )
    schema: Mapped[dict | None] = mapped_column(
        JSON, nullable=True
    )  # 空间自定义字段定义
    settings: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # 空间级设置
    is_active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=func.now(), onupdate=func.now()
    )


class Wiki(Base):
    """Wiki 文档"""

    __tablename__ = "wikis"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    path: Mapped[str] = mapped_column(
        String(500), unique=True, index=True
    )  # 唯一路径，如 /docs/api/auth

    # 分类
    parent_id: Mapped[str | None] = mapped_column(
        ForeignKey("wikis.id"), nullable=True, index=True
    )
    tags: Mapped[list | None] = mapped_column(JSON, nullable=True, default=list)

    # 所有权
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    organization_id: Mapped[str | None] = mapped_column(
        ForeignKey("organizations.id"), nullable=True, index=True
    )

    # 空间
    space_id: Mapped[str | None] = mapped_column(
        ForeignKey("wiki_spaces.id"), nullable=True, index=True
    )

    # 权限范围
    scope: Mapped[str] = mapped_column(
        String(20), default="user", index=True
    )  # system/org/user

    # 版本控制
    version: Mapped[int] = mapped_column(Integer, default=1)

    # 状态
    status: Mapped[str] = mapped_column(
        String(20), default="published"
    )  # draft/published/archived

    # 知识库增强字段
    doc_type: Mapped[str] = mapped_column(
        String(50), default="markdown"
    )  # markdown, text, pdf, url
    source: Mapped[str | None] = mapped_column(
        String(500), nullable=True
    )  # 来源 URL 或文件路径
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)  # 分块数量
    is_indexed: Mapped[bool] = mapped_column(default=False)  # 是否已索引到向量库

    # 元数据（不能用 metadata，SQLAlchemy 保留字）
    meta_data: Mapped[dict | None] = mapped_column("metadata", JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=func.now(), onupdate=func.now()
    )

    # 关系
    parent: Mapped[Wiki | None] = relationship(
        "Wiki", back_populates="children", remote_side=[id], foreign_keys=[parent_id]
    )
    children: Mapped[list[Wiki]] = relationship(
        "Wiki", back_populates="parent", foreign_keys=[parent_id]
    )
    history: Mapped[list[WikiVersion]] = relationship(
        "WikiVersion",
        back_populates="wiki",
        cascade="all, delete-orphan",
        order_by="desc(WikiVersion.version)",
    )
    author: Mapped[User] = relationship(foreign_keys=[created_by])
    organization: Mapped[Organization | None] = relationship()

    def create_version(self, user_id: int, change_summary: str = None) -> WikiVersion:
        """创建新版本"""
        return WikiVersion(
            wiki_id=self.id,
            version=self.version,
            title=self.title,
            content=self.content,
            change_summary=change_summary,
            created_by=user_id,
        )

    def __repr__(self):
        return f"<Wiki(id={self.id}, title={self.title}, version={self.version})>"


class WikiVersion(Base):
    """Wiki 版本历史"""

    __tablename__ = "wiki_versions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    wiki_id: Mapped[str] = mapped_column(
        ForeignKey("wikis.id"), index=True, nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    change_summary: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())

    # 关系
    wiki: Mapped[Wiki] = relationship(back_populates="history")
    author: Mapped[User] = relationship()

    def __repr__(self):
        return f"<WikiVersion(wiki_id={self.wiki_id}, version={self.version})>"
