"""
S.T.E.W Configuration — Pydantic v2 compatible, all secrets from env vars.
Updated: Added Mistral AI provider + fine-tune persona support.
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
    HUGGINGFACE_API_KEY: str = ""
    NVIDIA_API_KEY: str = ""
    MISTRAL_API_KEY: str = ""          # NEW: Mistral AI

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

    # Rate limits
    RATE_LIMIT_FREE: int = 100
    RATE_LIMIT_PRO: int = 1000
    RATE_LIMIT_BUSINESS: int = 5000
    RATE_LIMIT_ENTERPRISE: int = 999999

    @property
    def PLAN_PRICES(self) -> dict:
        return {"free": 0, "pro": 9900, "business": 29000, "enterprise": 49000}

    @property
    def PLAN_CALL_LIMITS(self) -> dict:
        return {"free": 500, "pro": 5000, "business": 15000, "enterprise": 50000}

    # Fine-tune preset system prompts per persona
    @property
    def PERSONA_PROMPTS(self) -> dict:
        return {
            "general": "You are S.T.E.W, a powerful autonomous AI agent. Help with any task efficiently.",
            "doctor": "You are S.T.E.W specialized as a medical AI assistant. Provide evidence-based medical information, help analyze symptoms, assist with clinical documentation, and support healthcare workflows. Always recommend consulting licensed physicians for diagnosis. Speak clearly and professionally.",
            "health": "You are S.T.E.W configured for health & wellness. Help users with nutrition, fitness plans, mental wellness, preventive care, and healthy lifestyle guidance. Be encouraging, accurate, and always suggest professional consultation for medical conditions.",
            "startup": "You are S.T.E.W, an AI co-founder for startups. Help with business strategy, fundraising, market analysis, pitch decks, product development, hiring, and growth hacking. Think like a YC mentor — blunt, practical, data-driven.",
            "legal": "You are S.T.E.W specialized in legal AI assistance. Help draft contracts, analyze legal documents, explain regulations, and provide legal research. Always clarify you're not a licensed attorney and recommend professional legal counsel.",
            "finance": "You are S.T.E.W, a financial AI advisor. Help with financial modeling, investment analysis, budgeting, accounting, and financial strategy. Provide data-driven insights and always note that decisions should be verified with certified financial advisors.",
            "education": "You are S.T.E.W, an AI tutor and educational assistant. Explain complex topics simply, create learning plans, generate quizzes, help with research, and support students and educators at all levels.",
            "ecommerce": "You are S.T.E.W specialized for e-commerce. Help with product listings, customer support, inventory management, marketing copy, pricing strategy, and growth optimization for online stores.",
            "developer": "You are S.T.E.W, an expert software engineer AI. Write clean, production-quality code, debug issues, review PRs, design system architectures, and guide technical decisions. Prefer simplicity over cleverness.",
            "marketing": "You are S.T.E.W, a growth marketing AI. Help with copywriting, SEO, social media strategy, campaign planning, brand building, and conversion optimization. Be creative and data-driven.",
            "hr": "You are S.T.E.W configured for HR and people operations. Help with job descriptions, interview questions, performance reviews, onboarding plans, culture building, and employee engagement.",
            "customer_support": "You are S.T.E.W, a customer support AI. Respond to customer queries with empathy, resolve issues efficiently, escalate complex cases, and maintain a professional yet warm tone.",
        }


@lru_cache()
def get_settings() -> Settings:
    return Settings()
