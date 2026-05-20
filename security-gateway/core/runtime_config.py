"""运行时配置管理

集中管理内存级运行时配置覆盖，消除 api.config 和 engines.model_engine 之间的循环导入。
"""

from config import settings

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


def get_runtime_config() -> dict:
    """供内部模块获取最新运行时配置"""
    return _get_merged_config()


def update_runtime_config(update_data: dict) -> dict:
    """更新运行时配置，返回合并后的完整配置"""
    global _runtime_overrides
    _runtime_overrides.update(update_data)
    return _get_merged_config()
