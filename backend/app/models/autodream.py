"""
AutoDream 数据模型

- AutoDreamConfig: 单例配置表（只存一行 id=1）
- AutoDreamLog: 每次记忆整理的执行日志
"""

from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class AutoDreamConfig(Base):
    """AutoDream 配置（单例表，id=1）"""

    __tablename__ = "autodream_config"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    enabled: Mapped[bool] = mapped_column(default=True)
    cron_expression: Mapped[str] = mapped_column(
        String(100), default="0 2 * * *"
    )  # 每天凌晨 2 点
    batch_size: Mapped[int] = mapped_column(Integer, default=50)
    max_consolidated_per_run: Mapped[int] = mapped_column(Integer, default=10)
    archive_after_days: Mapped[int] = mapped_column(Integer, default=90)
    delete_after_days: Mapped[int | None] = mapped_column(
        Integer, nullable=True, default=None
    )  # None 表示不自动删除

    # 功能开关
    enable_dedup: Mapped[bool] = mapped_column(default=True)
    enable_consolidation: Mapped[bool] = mapped_column(default=True)
    enable_archival: Mapped[bool] = mapped_column(default=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class AutoDreamLog(Base):
    """AutoDream 执行日志"""

    __tablename__ = "autodream_logs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    triggered_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    triggered_by: Mapped[str] = mapped_column(
        String(20), default="cron"
    )  # "cron" | "manual"
    status: Mapped[str] = mapped_column(
        String(20), default="running"
    )  # "running" | "success" | "failed"

    # 统计
    total_memories: Mapped[int] = mapped_column(Integer, default=0)
    duplicates_found: Mapped[int] = mapped_column(Integer, default=0)
    merged: Mapped[int] = mapped_column(Integer, default=0)
    consolidated: Mapped[int] = mapped_column(Integer, default=0)
    archived: Mapped[int] = mapped_column(Integer, default=0)
    deleted: Mapped[int] = mapped_column(Integer, default=0)

    # 性能
    llm_calls: Mapped[int] = mapped_column(Integer, default=0)
    llm_tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    llm_tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    duration_seconds: Mapped[float] = mapped_column(Float, default=0.0)

    # 错误
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    # 详细记录（JSON）
    details: Mapped[str | None] = mapped_column(Text, nullable=True)
