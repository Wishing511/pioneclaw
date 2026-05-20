"""
安全网关服务入口

独立 FastAPI 服务，提供安全过滤检测和管理 API。
可独立启动：uvicorn main:app --host 0.0.0.0 --port 8001
"""

from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import logging

from api.filter import router as filter_router
from api.words import router as words_router
from api.audit import router as audit_router
from api.config import router as config_router
from api.dashboard import router as dashboard_router
from core.database import init_db
from services.filter_service import FilterService
from core.deps import get_db
from config import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    logger.info("Security Gateway starting up...")

    # 初始化数据库
    await init_db()
    logger.info("Database initialized")

    # 加载词库到引擎
    try:
        from sqlalchemy.ext.asyncio import AsyncSession
        from core.database import AsyncSessionLocal
        async with AsyncSessionLocal() as session:
            filter_service = FilterService()
            await filter_service.reload_engines(session)
            logger.info("Word engine loaded")
    except Exception as e:
        logger.warning(f"Failed to preload word engine: {e}")

    yield

    logger.info("Security Gateway shutting down...")


app = FastAPI(
    title="PioneerClaw Security Gateway",
    description="AI 模型安全过滤网关 - 提供输入/输出/工具调用的安全检测",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS
# 注意：生产环境应限制 allow_origins 为 PioneerClaw 前端的实际域名，
# 避免开放给任意来源。当前配置适用于开发/内网环境。
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册路由
app.include_router(filter_router, prefix="/api/v1")
app.include_router(words_router, prefix="/api/v1/admin")
app.include_router(audit_router, prefix="/api/v1/admin")
app.include_router(config_router, prefix="/api/v1/admin")
app.include_router(dashboard_router, prefix="/api/v1/admin")


@app.get("/health")
async def health_check():
    """健康检查"""
    return {"status": "ok", "service": "security-gateway"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=True,
    )
