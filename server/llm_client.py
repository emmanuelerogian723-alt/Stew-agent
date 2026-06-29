"""
S.T.E.W LLM Client — multi-provider with automatic fallback.
Updated 2026-06-29: Dead Llama models replaced with active ones.
Chain: Groq (llama-3.3-70b) → OpenRouter → HuggingFace → OpenAI
"""
import logging
from typing import Optional

from fastapi import HTTPException
from groq import Groq
from openai import OpenAI

from server.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

# ─── Active model list (as of June 2026) ────────────────────────────────────
# Groq: llama3-70b-8192 DEAD → use llama-3.3-70b-versatile (best free)
# OpenRouter: updated to current Llama 4
# HuggingFace: Qwen3 is now the best free model on HF Router
PROVIDER_MODELS = {
    "groq":         "llama-3.3-70b-versatile",
    "groq_fast":    "meta-llama/llama-4-scout-17b-16e-instruct",  # faster option
    "openrouter":   "meta-llama/llama-3.3-70b-instruct:free",
    "openai":       "gpt-4o-mini",
    "huggingface":  "Qwen/Qwen3-235B-A22B",  # best free on HF Router
}

# Fallback chain for Groq if primary fails
GROQ_FALLBACKS = [
    "llama-3.3-70b-versatile",
    "meta-llama/llama-4-scout-17b-16e-instruct",
    "llama-3.1-8b-instant",
    "qwen/qwen3-32b",
]


class LLMClient:
    def __init__(self):
        self.providers: dict = {}
        self._groq_model: str = PROVIDER_MODELS["groq"]
        self._init_providers()

    def _init_providers(self):
        if settings.GROQ_API_KEY:
            try:
                self.providers["groq"] = Groq(api_key=settings.GROQ_API_KEY)
                logger.info(f"Groq provider initialized — model: {self._groq_model}")
            except Exception as e:
                logger.warning(f"Groq init failed: {e}")

        if settings.OPENROUTER_API_KEY:
            try:
                self.providers["openrouter"] = OpenAI(
                    base_url="https://openrouter.ai/api/v1",
                    api_key=settings.OPENROUTER_API_KEY,
                    default_headers={
                        "HTTP-Referer": settings.APP_BASE_URL or "https://stew-agent.io",
                        "X-Title": "S.T.E.W Agent",
                    },
                )
                logger.info("OpenRouter provider initialized")
            except Exception as e:
                logger.warning(f"OpenRouter init failed: {e}")

        if settings.HF_TOKEN:
            try:
                self.providers["huggingface"] = OpenAI(
                    base_url="https://router.huggingface.co/v1",
                    api_key=settings.HF_TOKEN,
                )
                logger.info("HuggingFace provider initialized")
            except Exception as e:
                logger.warning(f"HuggingFace init failed: {e}")

        if settings.OPENAI_API_KEY:
            try:
                self.providers["openai"] = OpenAI(api_key=settings.OPENAI_API_KEY)
                logger.info("OpenAI provider initialized")
            except Exception as e:
                logger.warning(f"OpenAI init failed: {e}")

        if not self.providers:
            logger.error("No LLM providers available — set GROQ_API_KEY at minimum")

    @property
    def fallback_order(self) -> list[str]:
        return [p for p in ["groq", "openrouter", "huggingface", "openai"] if p in self.providers]

    def _call_groq_with_fallback(self, messages: list[dict], temperature: float) -> dict:
        """Try each Groq model in fallback order."""
        client = self.providers["groq"]
        last_error = None
        for model in GROQ_FALLBACKS:
            try:
                response = client.chat.completions.create(
                    model=model,
                    messages=messages,
                    temperature=temperature,
                )
                content = response.choices[0].message.content
                usage = response.usage
                logger.info(f"Groq success with model: {model}")
                return {
                    "content": content,
                    "provider": "groq",
                    "model": model,
                    "tokens": {
                        "prompt": usage.prompt_tokens if usage else 0,
                        "completion": usage.completion_tokens if usage else 0,
                        "total": usage.total_tokens if usage else 0,
                    },
                }
            except Exception as e:
                last_error = e
                if "model_not_active" in str(e) or "decommissioned" in str(e) or "404" in str(e):
                    logger.warning(f"Groq model {model} not available, trying next...")
                    continue
                else:
                    raise  # Non-model error — don't retry
        raise last_error

    def _call_provider(self, provider_name: str, messages: list[dict],
                       model: Optional[str], temperature: float) -> dict:
        if provider_name == "groq":
            # Use smart fallback for Groq
            m = model or self._groq_model
            # If caller didn't specify a model, use full fallback chain
            if model is None:
                return self._call_groq_with_fallback(messages, temperature)
            # If they specified a model, try it then fall back
            client = self.providers["groq"]
            try:
                response = client.chat.completions.create(
                    model=m, messages=messages, temperature=temperature)
                content = response.choices[0].message.content
                usage = response.usage
                return {
                    "content": content, "provider": "groq", "model": m,
                    "tokens": {
                        "prompt": usage.prompt_tokens if usage else 0,
                        "completion": usage.completion_tokens if usage else 0,
                        "total": usage.total_tokens if usage else 0,
                    },
                }
            except Exception:
                return self._call_groq_with_fallback(messages, temperature)

        client = self.providers[provider_name]
        chosen_model = model or PROVIDER_MODELS.get(provider_name, "gpt-4o-mini")
        response = client.chat.completions.create(
            model=chosen_model, messages=messages, temperature=temperature)
        content = response.choices[0].message.content
        usage = response.usage
        return {
            "content": content, "provider": provider_name, "model": chosen_model,
            "tokens": {
                "prompt": usage.prompt_tokens if usage else 0,
                "completion": usage.completion_tokens if usage else 0,
                "total": usage.total_tokens if usage else 0,
            },
        }

    def chat(self, messages: list[dict], model: Optional[str] = None,
             temperature: float = 0.7) -> dict:
        """Try each provider in fallback order until one succeeds."""
        last_error = None
        for provider_name in self.fallback_order:
            try:
                result = self._call_provider(provider_name, messages, model, temperature)
                logger.info(f"LLM success via {provider_name}/{result['model']}")
                return result
            except Exception as e:
                last_error = e
                logger.warning(f"{provider_name} failed: {e}")
                continue

        raise HTTPException(
            status_code=503,
            detail=f"All LLM providers unavailable. Last error: {last_error}",
        )

    def complete(self, prompt: str,
                 system: str = "You are S.T.E.W, a powerful AI agent. Be concise and accurate.",
                 **kwargs) -> str:
        """Convenience wrapper — returns just the text content."""
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ]
        return self.chat(messages, **kwargs)["content"]


# Singleton
_llm_client: Optional[LLMClient] = None


def get_llm_client() -> LLMClient:
    global _llm_client
    if _llm_client is None:
        _llm_client = LLMClient()
    return _llm_client


def reset_llm_client():
    """Force re-initialization (useful after config changes)."""
    global _llm_client
    _llm_client = None
