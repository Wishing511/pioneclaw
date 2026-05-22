"""
API 限流依赖

基于令牌桶算法，对认证等敏感端点按客户端 IP 进行限流。
使用现有的 RateLimiter 实现（app/modules/messaging/rate_limiter.py）。
"""

from fastapi import HTTPException, Request

from app.modules.messaging.rate_limiter import RateLimiter

# 全局限流器实例（应用级单例）
_limiter = RateLimiter(default_capacity=60)


def get_client_ip(request: Request) -> str:
    """获取客户端真实 IP（优先检查代理头）"""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    real_ip = request.headers.get("X-Real-IP")
    if real_ip:
        return real_ip.strip()
    return request.client.host if request.client else "unknown"


class RateLimit:
    """限流依赖工厂

    用法:
        @router.post("/login")
        async def login(..., _rate: None = Depends(RateLimit(times=10, seconds=60))):
    """

    def __init__(self, times: int, seconds: int):
        self.times = times
        self.seconds = seconds

    async def __call__(self, request: Request):
        from app.core.config import settings

        if not settings.RATE_LIMIT_ENABLED:
            return  # 显式禁用（需主动配置环境变量）
        client_ip = get_client_ip(request)
        key = f"rate:{request.url.path}:{client_ip}"

        # 容量 = times，填充速率 = times / seconds（令牌/秒）
        rate = self.times / self.seconds
        ok = await _limiter.acquire(
            key, tokens=1, capacity=self.times, refill_rate=rate
        )
        if not ok:
            retry_after = int(await _limiter.get_wait_time(key, tokens=1)) + 1
            raise HTTPException(
                status_code=429,
                detail=f"请求过于频繁，请 {retry_after} 秒后重试",
                headers={"Retry-After": str(retry_after)},
            )
