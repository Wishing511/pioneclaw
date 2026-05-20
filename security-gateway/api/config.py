"""
配置管理 API

提供安全网关运行配置的查询和更新。
支持运行时热更新（无需重启服务）。
"""

import logging
from fastapi import APIRouter, Depends, HTTPException, Header
from schemas.security import SecurityGatewayConfig, TestLLMRequest
from config import settings
from core.utils import normalize_llm_url

logger = logging.getLogger(__name__)

router = APIRouter(tags=["config"])

# 运行时配置覆盖（内存级，服务重启后恢复为环境变量值）
_runtime_overrides: dict = {}


def _get_merged_config() -> dict:
    """合并环境变量默认值 + 运行时覆盖"""
    base = {
        "enable_word_engine": settings.ENABLE_WORD_ENGINE,
        "enable_regex_engine": settings.ENABLE_REGEX_ENGINE,
        "enable_model_engine": settings.ENABLE_MODEL_ENGINE,
        "enable_model_llm": settings.ENABLE_MODEL_LLM,
        "ai_config_id": None,
        "model_engine_llm_url": settings.MODEL_ENGINE_LLM_URL,
        "model_engine_llm_model": settings.MODEL_ENGINE_LLM_MODEL,
        "model_engine_llm_api_key": settings.MODEL_ENGINE_LLM_API_KEY,
        "model_engine_llm_timeout": settings.MODEL_ENGINE_LLM_TIMEOUT,
        "word_engine_cache_ttl": settings.WORD_ENGINE_CACHE_TTL,
        "fail_open": settings.FAIL_OPEN,
        "log_retention_days": settings.LOG_RETENTION_DAYS,
    }
    base.update(_runtime_overrides)
    return base


def verify_admin(x_api_key: str = Header("", alias="X-API-Key")):
    """校验管理接口权限

    通过 X-API-Key 请求头校验。ADMIN_API_KEY 为空时不校验（开发环境便利）。
    """
    if not settings.ADMIN_API_KEY:
        return
    if x_api_key != settings.ADMIN_API_KEY:
        raise HTTPException(status_code=403, detail="无效的 API Key")


@router.get("/config", response_model=SecurityGatewayConfig)
async def get_config():
    """获取当前配置（环境变量 + 运行时覆盖）"""
    return SecurityGatewayConfig(**_get_merged_config())


@router.put("/config", response_model=SecurityGatewayConfig)
async def update_config(
    data: SecurityGatewayConfig,
    _: None = Depends(verify_admin),
):
    """更新运行时配置

    注意：运行时配置仅在当前服务进程生效，重启后恢复为环境变量值。
    如需持久化，请在 .env 文件中设置对应的环境变量。
    """
    global _runtime_overrides
    update_data = data.model_dump(exclude_unset=True)
    _runtime_overrides.update(update_data)

    # 如果 LLM 相关配置变更，清理旧的 LLM detector 缓存
    # 下次 model_engine.check() 时会使用新配置重新创建
    from engines.model_engine import LLMDetector
    LLMDetector._clear_instance_cache()

    logger.info(f"Runtime config updated: {list(update_data.keys())}")
    return SecurityGatewayConfig(**_get_merged_config())


@router.post("/config/test-llm")
async def test_llm_connection(
    data: TestLLMRequest,
    _: None = Depends(verify_admin),
):
    """测试 LLM 连接是否可用

    接收临时配置参数，尝试发送一个简单请求验证连通性。
    """
    import time
    import httpx

    url = normalize_llm_url(data.url)
    if not url:
        return {"success": False, "message": "LLM API 地址不能为空"}

    start = time.time()
    try:
        headers = {"Content-Type": "application/json"}
        if data.api_key:
            headers["Authorization"] = f"Bearer {data.api_key}"

        async with httpx.AsyncClient(timeout=httpx.Timeout(data.timeout)) as client:
            resp = await client.post(
                url,
                headers=headers,
                json={
                    "model": data.model or "test",
                    "messages": [{"role": "user", "content": "Hi"}],
                    "max_tokens": 5,
                },
            )
            latency_ms = int((time.time() - start) * 1000)

            if resp.status_code == 200:
                resp_data = resp.json()
                choices = resp_data.get("choices", [])
                content = ""
                if choices:
                    msg = choices[0].get("message", {})
                    raw_content = msg.get("content")
                    content = (raw_content or "")[:100]
                return {
                    "success": True,
                    "message": "连接成功",
                    "latency_ms": latency_ms,
                    "response": content,
                }
            else:
                return {
                    "success": False,
                    "message": f"HTTP {resp.status_code}: {resp.text[:200]}",
                    "latency_ms": int((time.time() - start) * 1000),
                }
    except httpx.TimeoutException:
        return {"success": False, "message": f"连接超时（{data.timeout}s）"}
    except Exception as e:
        return {"success": False, "message": f"连接失败: {str(e)}"}


def get_runtime_config() -> dict:
    """供内部模块获取最新运行时配置"""
    return _get_merged_config()
