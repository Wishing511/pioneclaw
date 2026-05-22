from pydantic import model_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # 应用配置
    APP_NAME: str = "PioneClaw"
    DEBUG: bool = False
    API_PREFIX: str = "/api"
    VERSION: str = "1.0.0"

    # 数据库配置
    # SQLite (开发环境): sqlite+aiosqlite:///./pioneclaw.db
    # PostgreSQL (生产环境): postgresql+asyncpg://user:pass@localhost:5432/pioneclaw
    DATABASE_URL: str = "sqlite+aiosqlite:///./pioneclaw.db"

    # JWT 配置（生产环境必须通过环境变量设置）
    SECRET_KEY: str = ""
    REFRESH_SECRET_KEY: str = ""
    RESET_SECRET_KEY: str = ""
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 10080  # 7天
    REFRESH_TOKEN_EXPIRE_DAYS: int = 30  # 30天

    # 密码重置
    PASSWORD_RESET_EXPIRE_MINUTES: int = 30
    MAX_LOGIN_ATTEMPTS: int = 5
    LOCKOUT_DURATION_MINUTES: int = 30

    # 限流配置
    RATE_LIMIT_ENABLED: bool = True  # 设为 false 可禁用全局限流（仅开发环境）

    # 外部 API Key
    BRAVE_API_KEY: str = ""  # Brave Search API Key（可选）

    # Redis 配置 (可选)
    REDIS_URL: str | None = None

    # CORS 配置
    CORS_ORIGINS: list[str] = ["http://localhost:5173", "http://localhost:3000"]

    # 前端地址（用于构建密码重置等邮件中的链接）
    FRONTEND_URL: str = "http://localhost:5173"

    # 文件沙箱
    WORKSPACE_DIR: str = "./workspace"

    # API Key 加密 (Fernet AES-128-CBC + HMAC)
    # 生产环境必须设置！未设置时 API Key 明文存储（开发兼容模式）
    # 可用 openssl rand -base64 32 生成
    ENCRYPTION_KEY: str = ""

    # SMTP 邮件配置（密码重置等）
    SMTP_HOST: str = "smtp.example.com"
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_FROM: str = "noreply@pioneclaw.com"
    SMTP_USE_TLS: bool = True

    # Stage VV: 持久化记忆增强
    VV_MEMORY_EXTRACTION_ENABLED: bool = True
    VV_SESSION_MEMORY_ENABLED: bool = True
    VV_DREAM_ENABLED: bool = False  # P2 可选，默认关闭
    VV_MAGIC_DOCS_ENABLED: bool = False  # P2 可选，默认关闭

    # Stage QQ: Provider 预检（启动时验证 LLM provider 连通性）
    PROVIDER_PREFLIGHT_ENABLED: bool = False  # 默认关闭，生产环境可开启

    class Config:
        env_file = ".env"
        case_sensitive = True
        extra = "ignore"

    @model_validator(mode="after")
    def validate_jwt_keys(self):
        """确保 JWT 密钥已设置，避免使用默认弱密钥导致 token 可被伪造"""
        weak_keys = (
            "your-secret-key-change-in-production",
            "your-secret-key-change-in-production-please",
            "change-this-secret-key-in-production",
        )
        if not self.SECRET_KEY or self.SECRET_KEY in weak_keys:
            raise ValueError(
                "SECRET_KEY 未设置或仍使用默认弱密钥。"
                "请在 .env 文件或环境变量中设置强密钥（可用 openssl rand -hex 32 生成）。"
            )
        if not self.REFRESH_SECRET_KEY or self.REFRESH_SECRET_KEY in weak_keys:
            raise ValueError(
                "REFRESH_SECRET_KEY 未设置或仍使用默认弱密钥。"
                "请在 .env 文件或环境变量中设置强密钥（可用 openssl rand -hex 32 生成）。"
            )
        if not self.RESET_SECRET_KEY or self.RESET_SECRET_KEY in weak_keys:
            raise ValueError(
                "RESET_SECRET_KEY 未设置或仍使用默认弱密钥。"
                "请在 .env 文件或环境变量中设置强密钥（可用 openssl rand -hex 32 生成）。"
            )
        return self


settings = Settings()
