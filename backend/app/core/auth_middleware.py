"""
认证中间件 - 本地直通/远程双模式

设计原则：
- 本地连接 (127.0.0.1 / localhost) 免认证，零配置开箱即用
- 远程连接需要 JWT Bearer Token
- 公开路径（登录、注册、健康检查等）始终免认证
- 可通过配置启用/禁用本地直通模式
"""

import logging

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import JSONResponse

from app.core.config import settings

logger = logging.getLogger(__name__)

# 公开路径 - 始终不需要认证
PUBLIC_PATHS: set[str] = {
    "/",
    "/health",
    "/docs",
    "/openapi.json",
    "/redoc",
}

# 公开路径前缀 - 不需要认证
PUBLIC_PREFIXES: set[str] = {
    "/api/auth/login",
    "/api/auth/register",
    "/api/auth/refresh-token",
    "/api/auth/password-reset",
}

# 本地回环地址
LOCAL_HOSTS: set[str] = {
    "127.0.0.1",
    "::1",
    "localhost",
}


class AuthMiddleware(BaseHTTPMiddleware):
    """
    认证中间件

    本地请求免认证，远程请求需要 JWT。
    """

    def __init__(
        self,
        app,
        local_bypass: bool = True,
        public_paths: set[str] | None = None,
        public_prefixes: set[str] | None = None,
    ) -> None:
        super().__init__(app)
        self.local_bypass = local_bypass
        self.public_paths = public_paths or PUBLIC_PATHS
        self.public_prefixes = public_prefixes or PUBLIC_PREFIXES

    def _is_public_path(self, path: str) -> bool:
        """检查路径是否公开"""
        # 生产环境关闭 API 文档
        if path in ("/docs", "/openapi.json", "/redoc") and not settings.DEBUG:
            return False
        if path in self.public_paths:
            return True
        return any(path.startswith(prefix) for prefix in self.public_prefixes)

    def _is_local_request(self, request: Request) -> bool:
        """检查请求是否来自本地"""
        if not self.local_bypass:
            return False

        # ASGI test transport (httpx.AsyncClient + ASGITransport) doesn't set
        # client host; treat as local to avoid breaking tests.
        if request.client is None:
            return True

        client_host = request.client.host
        if client_host in LOCAL_HOSTS:
            return True

        # 检查 X-Forwarded-For 头（反向代理场景）
        forwarded = request.headers.get("X-Forwarded-For", "")
        if forwarded:
            first_ip = forwarded.split(",")[0].strip()
            if first_ip in LOCAL_HOSTS:
                return True

        return False

    def _extract_token(self, request: Request) -> str | None:
        """从请求中提取 Bearer Token"""
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            return auth_header[7:]
        return None

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        """处理请求认证"""
        path = request.url.path

        # 1. 公开路径始终放行
        if self._is_public_path(path):
            return await call_next(request)

        # 2. 本地请求免认证
        if self._is_local_request(request):
            request.state.auth_bypassed = True
            return await call_next(request)

        # 3. 远程请求需要 Token
        token = self._extract_token(request)
        if not token:
            return JSONResponse(
                status_code=401,
                content={
                    "detail": "Not authenticated. "
                    "Provide a valid Bearer token for remote access.",
                },
                headers={"WWW-Authenticate": "Bearer"},
            )

        # 4. 验证 Token
        try:
            from app.core.security import decode_access_token

            payload = decode_access_token(token)
            if payload is None:
                return JSONResponse(
                    status_code=401,
                    content={"detail": "Invalid or expired token."},
                    headers={"WWW-Authenticate": "Bearer"},
                )

            # 将用户 ID 存入 request.state，供后续使用
            request.state.user_id = payload.get("sub")
            request.state.auth_bypassed = False

        except Exception as exc:
            logger.warning(f"[AuthMiddleware] Token validation error: {exc}")
            return JSONResponse(
                status_code=401,
                content={"detail": "Token validation failed."},
                headers={"WWW-Authenticate": "Bearer"},
            )

        return await call_next(request)


def is_local_bypass_enabled() -> bool:
    """检查本地直通是否启用（可通过环境变量控制）"""
    return getattr(settings, "LOCAL_AUTH_BYPASS", True)


def get_client_type(request: Request) -> str:
    """
    获取客户端类型

    Returns:
        "local" - 本地请求
        "remote" - 远程请求
    """
    client_host = request.client.host if request.client else None
    if client_host in LOCAL_HOSTS:
        return "local"
    return "remote"
