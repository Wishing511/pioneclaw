from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.models import Base


class ConnectionEvent(Base):
    __tablename__ = "runner_connection_events"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    runner_id: Mapped[int] = mapped_column(ForeignKey("runners.id"), index=True)
    event_type: Mapped[str] = mapped_column(
        String(30)
    )  # online/offline/disconnect/heartbeat_fail/token_rotate
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
