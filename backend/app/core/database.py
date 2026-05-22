import logging

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.core.config import settings

logger = logging.getLogger(__name__)

# 数据库引擎参数
engine_kwargs = {
    "echo": settings.DEBUG,
    "future": True,
}

# SQLite 需要额外配置
if settings.DATABASE_URL.startswith("sqlite"):
    engine_kwargs["connect_args"] = {"check_same_thread": False}

# 创建异步引擎
engine = create_async_engine(
    settings.DATABASE_URL,
    **engine_kwargs,
)

# 创建异步会话工厂
async_session_maker = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    """所有模型的基类"""

    pass


async def get_db():
    """获取数据库会话的依赖"""
    async with async_session_maker() as session:
        try:
            yield session
        finally:
            await session.close()


async def init_db():
    """初始化数据库（创建表）"""
    # 确保所有模型已导入
    import app.models  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # 兼容性：为已存在的表添加模型中新增但数据库中缺失的列（仅限 SQLite）
        if settings.DATABASE_URL.startswith("sqlite"):
            await conn.run_sync(_add_missing_columns)


def _add_missing_columns(sync_conn):
    """SQLite 专用：检测并添加缺失的列"""
    from sqlalchemy import inspect, text

    inspector = inspect(sync_conn)

    # chat_messages 表：添加 reasoning_content 列（如果不存在）
    if "chat_messages" in inspector.get_table_names():
        existing_cols = {c["name"] for c in inspector.get_columns("chat_messages")}
        if "reasoning_content" not in existing_cols:
            logger.warning(
                "[SQLite] Auto-adding missing column 'reasoning_content' to 'chat_messages'. "
                "This is a dev convenience only; please write a proper Alembic migration for production."
            )
            sync_conn.execute(
                text("ALTER TABLE chat_messages ADD COLUMN reasoning_content TEXT")
            )
            sync_conn.commit()
