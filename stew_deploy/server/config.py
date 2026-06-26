"""
S.T.E.W Configuration — all secrets from environment variables ONLY.
"""
import os
from functools import lru_cache
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # App
    APP_NAME: str = "S.T.E.W Agent API"
    VERSION: str = "5.0.0"
    ENVIRONMENT: str = os.getenv("ENVIRONMENT", "development")
    DEBUG: bool = ENVIRONMENT != "production"

    # LLM Providers
    GROQ_API_KEY: str = os.getenv("GROQ_API_KEY", "")
    OPENROUTER_API_KEY: str = os.getenv("OPENROUTER_API_KEY", "")
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")

    # Search
    SERPER_API_KEY: str = os.getenv("SERPER_API_KEY", "")

    # Database
    DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite:///./stew.db")

    # Redis
    REDIS_URL: str = os.getenv("REDIS_URL", "")

    # Auth
    JWT_SECRET_KEY: str = os.getenv("JWT_SECRET_KEY", "change-me-in-production-please")
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_HOURS: int = 24

    # Paystack
    PAYSTACK_SECRET_KEY: str = os.getenv("PAYSTACK_SECRET_KEY", "")
    PAYSTACK_PUBLIC_KEY: str = os.getenv("PAYSTACK_PUBLIC_KEY", "")

    # Telegram
    TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")

    # App URL (used for password reset links, keepalive, webhook setup)
    APP_BASE_URL: str = os.getenv("APP_BASE_URL", "")

    # Admin
    STEW_ADMIN_SECRET: str = os.getenv("STEW_ADMIN_SECRET", "")

    # Email (SMTP) — for welcome emails + password reset
    # Works with Gmail, Mailgun, SendGrid SMTP, Brevo, etc.
    SMTP_HOST: str = os.getenv("SMTP_HOST", "smtp.gmail.com")
    SMTP_PORT: int = int(os.getenv("SMTP_PORT", "587"))
    SMTP_USER: str = os.getenv("SMTP_USER", "")         # your email address
    SMTP_PASSWORD: str = os.getenv("SMTP_PASSWORD", "") # Gmail app password or SMTP key
    SMTP_FROM_NAME: str = os.getenv("SMTP_FROM_NAME", "S.T.E.W Agent")
    SMTP_FROM_EMAIL: str = os.getenv("SMTP_FROM_EMAIL", os.getenv("SMTP_USER", ""))

    # Rate limits (requests per minute)
    RATE_LIMIT_FREE: int = 100
    RATE_LIMIT_PRO: int = 1000
    RATE_LIMIT_BUSINESS: int = 5000
    RATE_LIMIT_ENTERPRISE: int = 999999

    # Plan pricing (Naira)
    PLAN_PRICES: dict = {
        "free": 0,
        "pro": 9900,
        "business": 29000,
        "enterprise": 49000,
    }

    # Plan API call limits
    PLAN_CALL_LIMITS: dict = {
        "free": 1000,
        "pro": 10000,
        "business": 100000,
        "enterprise": 999999,
    }

    class Config:
        env_file = ".env"
        extra = "ignore"


@lru_cache()
def get_settings() -> Settings:
    return Settings()
