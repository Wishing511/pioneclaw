import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import router
from app.core.auth_middleware import AuthMiddleware
from app.core.config import settings
from app.core.database import init_db
from app.core.security_headers import SecurityHeadersMiddleware

# Windows 信号处理：防止 uvicorn reload 模式下 CancelledError 崩溃
if sys.platform == "win32":
    import signal

    def _win_sighandler(signum, frame):
        pass

    signal.signal(signal.SIGINT, _win_sighandler)
    signal.signal(signal.SIGTERM, _win_sighandler)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    # 启动时初始化数据库
    await init_db()
    # 初始化默认数据
    from app.init_data import init_default_data

    await init_default_data()

    # MCP 自动发现：从 DB 加载启用的 MCP 服务器并注册为命名空间工具
    from app.modules.tools.mcp_client import auto_discover_mcp_servers

    mcp_summary = await auto_discover_mcp_servers()
    if mcp_summary.get("servers"):
        import logging

        _logger = logging.getLogger(__name__)
        _logger.info(f"[Startup] MCP 自动发现: {mcp_summary}")

    # Cron 启动恢复：从 DB 恢复启用的定时任务到调度器
    from app.core.cron_scheduler import reconcile_cron_jobs

    cron_summary = await reconcile_cron_jobs()
    if cron_summary.get("registered"):
        import logging

        _logger = logging.getLogger(__name__)
        _logger.info(f"[Startup] Cron 启动恢复: {cron_summary}")

    # Provider 预检（Stage QQ — 可配置启用，默认关闭）
    if settings.PROVIDER_PREFLIGHT_ENABLED:
        import logging as _logging2

        _p_logger = _logging2.getLogger(__name__)
        _p_logger.info("[Startup] Provider 预检开始...")
        from app.modules.llm.provider_health import run_startup_preflight

        preflight_results = await run_startup_preflight()
        healthy = sum(1 for s in preflight_results if s.healthy)
        _p_logger.info(
            f"[Startup] Provider 预检完成: {healthy}/{len(preflight_results)} healthy"
        )

    yield
    # 关闭时清理资源


app = FastAPI(
    title=settings.APP_NAME,
    description="PioneClaw - 企业级智能协作平台",
    version=settings.VERSION,
    lifespan=lifespan,
)

# 配置 CORS
# 注意：allow_credentials=True 时不能使用 ["*"]，浏览器会拒绝（CORS 规范限制）
# 生产环境通过 CORS_ORIGINS 环境变量指定；开发环境自动包含常用本地端口
_cors_origins = list(settings.CORS_ORIGINS)
if settings.DEBUG:
    for port in ("5173", "3000", "3001", "8080", "80"):
        _cors_origins.append(f"http://localhost:{port}")
        _cors_origins.append(f"http://127.0.0.1:{port}")
    _cors_origins = list(set(_cors_origins))  # 去重

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 全局认证中间件（本地直通 / 远程 JWT 验证 / 公开路径放行）
app.add_middleware(AuthMiddleware, local_bypass=True)

# 安全响应头
app.add_middleware(SecurityHeadersMiddleware)

# 注册路由
app.include_router(router, prefix=settings.API_PREFIX)


@app.get("/")
async def root():
    """根路径"""
    return {
        "message": f"Welcome to {settings.APP_NAME}",
        "version": settings.VERSION,
        "docs": "/docs",
    }


@app.get("/health")
async def health_check():
    """健康检查"""
    return {"status": "healthy", "app": settings.APP_NAME}
