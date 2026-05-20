"""
Security Gateway 配置
"""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """安全网关运行时配置"""

    # 数据库
    DATABASE_URL: str = "sqlite+aiosqlite:///./security_gateway.db"

    # 引擎开关
    ENABLE_WORD_ENGINE: bool = True
    ENABLE_REGEX_ENGINE: bool = True
    ENABLE_MODEL_ENGINE: bool = True

    # 模型引擎 LLM 增强（可选）
    ENABLE_MODEL_LLM: bool = False
    MODEL_ENGINE_LLM_URL: str = ""
    MODEL_ENGINE_LLM_MODEL: str = "qwen2.5:1.5b"
    MODEL_ENGINE_LLM_API_KEY: str = ""
    MODEL_ENGINE_LLM_TIMEOUT: float = 3.0

    # 词库缓存
    WORD_ENGINE_CACHE_TTL: int = 60

    # 告警通知
    ALERT_ENABLED: bool = False
    ALERT_WEBHOOK_URL: str = ""

    # 降级策略
    FAIL_OPEN: bool = True

    # 审计日志保留天数
    LOG_RETENTION_DAYS: int = 180

    # 服务端口
    PORT: int = 8001
    HOST: str = "0.0.0.0"

    class Config:
        env_prefix = "SG_"
        env_file = ".env"


settings = Settings()
