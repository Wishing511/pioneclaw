"""
分层记忆模型 — L0(摘要)/L1(概述)/L2(全文) 三级记忆体系

L0 和 L1 作为独立行存储，通过 parent_uri 关联到 L2 父记录。
URI 格式: viking://user/{user_id}/session/{session_id}/{name}
L0 URI:   {parent_uri}/.level_0
L1 URI:   {parent_uri}/.level_1
"""

from __future__ import annotations

import enum
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:
    from app.models import Agent, User


class MemoryLayer(int, enum.Enum):
    """记忆层级"""

    WORKING = 0  # L0 工作记忆 — 一句话摘要
    SESSION = 1  # L1 会话记忆 — 段落概述
    LONG_TERM = 2  # L2 长期记忆 — 完整内容


class ContextType(str, enum.Enum):
    """上下文类型"""

    MEMORY = "memory"
    RESOURCE = "resource"
    SKILL = "skill"


class LayeredMemory(Base):
    __tablename__ = "layered_memories"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    uri: Mapped[str] = mapped_column(String(500), unique=True, index=True)
    parent_uri: Mapped[str | None] = mapped_column(
        String(500), nullable=True, index=True
    )
    layer: Mapped[int] = mapped_column(Integer, index=True)  # 0/1/2
    context_type: Mapped[str] = mapped_column(String(20), default="memory")
    name: Mapped[str] = mapped_column(String(200))

    # 三级内容
    abstract: Mapped[str | None] = mapped_column(Text, nullable=True)  # L0 摘要
    overview: Mapped[str | None] = mapped_column(Text, nullable=True)  # L1 概述
    content: Mapped[str] = mapped_column(Text)  # L2 全文

    tags: Mapped[dict | None] = mapped_column(JSON, nullable=True, default=list)
    source: Mapped[str | None] = mapped_column(String(100), nullable=True)
    importance: Mapped[int] = mapped_column(Integer, default=3)
    access_count: Mapped[int] = mapped_column(Integer, default=0)

    session_id: Mapped[str | None] = mapped_column(
        String(100), nullable=True, index=True
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    agent_id: Mapped[int | None] = mapped_column(ForeignKey("agents.id"), nullable=True)
    vector_id: Mapped[str | None] = mapped_column(String(200), nullable=True)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    user: Mapped[User] = relationship(foreign_keys=[user_id])
    agent: Mapped[Agent | None] = relationship(foreign_keys=[agent_id])

    __table_args__ = (
        Index("ix_layered_memories_layer_user", "layer", "user_id"),
        Index("ix_layered_memories_session", "session_id", "layer"),
    )

    def get_text_for_embedding(self) -> str:
        """获取用于向量嵌入的文本（按层级返回对应内容）"""
        if self.layer == 0:
            return self.abstract or self.overview or self.content
        elif self.layer == 1:
            return self.overview or self.content
        return self.content

    def format_for_context(self, level: int = None) -> str:
        """格式化为 Agent 上下文注入文本"""
        target = level if level is not None else self.layer
        if target == 0:
            text = self.abstract or self.overview or self.content
        elif target == 1:
            text = self.overview or self.content
        else:
            text = self.content
        return f"[{self.context_type}][L{self.layer}] {self.name}: {text}"
