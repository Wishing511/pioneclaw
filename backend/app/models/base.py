"""
PioneClaw 基础模型类
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.ext.asyncio import AsyncAttrs
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def generate_uuid() -> str:
    """生成 UUID 字符串"""
    return str(uuid.uuid4())


class TimestampMixin:
    """时间戳混入类"""

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=func.now(), onupdate=func.now(), nullable=False
    )


class UUIDMixin:
    """UUID 主键混入类"""

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)


class SoftDeleteMixin:
    """软删除混入类"""

    is_deleted: Mapped[bool] = mapped_column(default=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class BaseModel(Base, AsyncAttrs):
    """所有模型的基类"""

    __abstract__ = True

    # 不在这里定义 id，让各个模型自己决定主键类型
    # 保持与 CircleBot 兼容，使用 int 自增主键
