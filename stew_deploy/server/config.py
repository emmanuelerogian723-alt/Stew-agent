"""
S.T.E.W Configuration — Pydantic v2 compatible, all secrets from env vars.
"""
import os
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # App
    APP_NAME: str = "S.T.E.W Agent API"
    VERSION: str = "6.0.0"
    ENVIRONMENT: str = "development"
    DEBUG: bool = False

    # LLM Providers
    GROQ_API_KEY: str = ""
    OPENROUTER_API_KEY: str = ""
    OPENAI_API_KEY: str = ""
    HF_TOKEN: str = ""
    NVIDIA_API_KEY: str = ""  # build.nvidia.com NIM — free tier, OpenAI-compatible

    # Image generation workers (free tiers)
    HF_TOKEN_IMAGE: str = ""       # reuse HF_TOKEN if unset (Stable Diffusion / FLUX on HF Inference)
    TOGETHER_API_KEY: str = ""     # optional extra image worker
    POLLINATIONS_ENABLED: bool = True  # pollinations.ai needs no key at all

    # Search
    SERPER_API_KEY: str = ""

    # Database
    DATABASE_URL: str = "sqlite:///./stew.db"

    # Redis
    REDIS_URL: str = ""

    # Auth
    JWT_SECRET_KEY: str = "change-me-in-production-stew-2026"
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_HOURS: int = 24

    # Paystack
    PAYSTACK_SECRET_KEY: str = ""
    PAYSTACK_PUBLIC_KEY: str = ""

    # Telegram
    TELEGRAM_BOT_TOKEN: str = ""

    # App URL
    APP_BASE_URL: str = ""

    # Admin
    STEW_ADMIN_SECRET: str = ""

    # Email (SMTP)
    SMTP_HOST: str = "smtp.gmail.com"
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_FROM_NAME: str = "S.T.E.W Agent"
    SMTP_FROM_EMAIL: str = ""

    # Rate limits (calls/month per plan)
    RATE_LIMIT_FREE: int = 4500
    RATE_LIMIT_PRO: int = 20000
    RATE_LIMIT_BUSINESS: int = 150000
    RATE_LIMIT_ENTERPRISE: int = 999999

    @property
    def PLAN_PRICES(self) -> dict:
        # NGN, monthly. Enterprise = custom/contact sales.
        return {"free": 0, "pro": 12000, "business": 39000, "enterprise": 0}

    @property
    def PLAN_CALL_LIMITS(self) -> dict:
        return {"free": 4500, "pro": 20000, "business": 150000, "enterprise": 999999}


@lru_cache()
def get_settings() -> Settings:
    return Settings()
