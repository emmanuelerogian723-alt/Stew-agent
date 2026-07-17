"""
S.T.E.W LLM Client — multi-provider with automatic fallback.
Updated 2026-07-01: Added NVIDIA NIM (free tier) as a provider.
Chain: Groq (llama-3.3-70b) → NVIDIA NIM → OpenRouter → HuggingFace → OpenAI
"""
import logging
from typing import Optional

from fastapi import HTTPException
from groq import Groq
from openai import OpenAI

from server.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

# ─── Active model list (as of July 2026) ────────────────────────────────────
PROVIDER_MODELS = {
    "groq":         "llama-3.3-70b-versatile",
    "groq_fast":    "meta-llama/llama-4-scout-17b-16e-instruct",  # faster option
    "nvidia":       "meta/llama-3.3-70b-instruct",   # free on build.nvidia.com NIM
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

# Fallback chain for NVIDIA NIM if primary fails (all free-tier models)
NVIDIA_FALLBACKS = [
    "meta/llama-3.3-70b-instruct",
    "meta/llama-3.1-405b-instruct",
    "nvidia/llama-3.1-nemotron-70b-instruct",
    "mistralai/mixtral-8x22b-instruct-v0.1",
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

        if settings.NVIDIA_API_KEY:
            try:
                self.providers["nvidia"] = OpenAI(
                    base_url="https://integrate.api.nvidia.com/v1",
                    api_key=settings.NVIDIA_API_KEY,
                )
                logger.info("NVIDIA NIM provider initialized (free tier)")
            except Exception as e:
                logger.warning(f"NVIDIA NIM init failed: {e}")

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
        return [p for p in ["groq", "nvidia", "openrouter", "huggingface", "openai"] if p in self.providers]

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

    def _call_nvidia_with_fallback(self, messages: list[dict], temperature: float) -> dict:
        """Try each NVIDIA NIM free-tier model in fallback order."""
        client = self.providers["nvidia"]
        last_error = None
        for model in NVIDIA_FALLBACKS:
            try:
                response = client.chat.completions.create(
                    model=model,
                    messages=messages,
                    temperature=temperature,
                )
                content = response.choices[0].message.content
                usage = response.usage
                logger.info(f"NVIDIA NIM success with model: {model}")
                return {
                    "content": content,
                    "provider": "nvidia",
                    "model": model,
                    "tokens": {
                        "prompt": usage.prompt_tokens if usage else 0,
                        "completion": usage.completion_tokens if usage else 0,
                        "total": usage.total_tokens if usage else 0,
                    },
                }
            except Exception as e:
                last_error = e
                logger.warning(f"NVIDIA model {model} failed, trying next... ({e})")
                continue
        raise last_error

    def _call_provider(self, provider_name: str, messages: list[dict],
                       model: Optional[str], temperature: float) -> dict:
        if provider_name == "groq":
            m = model or self._groq_model
            if model is None:
                return self._call_groq_with_fallback(messages, temperature)
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

        if provider_name == "nvidia":
            if model is None:
                return self._call_nvidia_with_fallback(messages, temperature)
            client = self.providers["nvidia"]
            try:
                response = client.chat.completions.create(
                    model=model, messages=messages, temperature=temperature)
                content = response.choices[0].message.content
                usage = response.usage
                return {
                    "content": content, "provider": "nvidia", "model": model,
                    "tokens": {
                        "prompt": usage.prompt_tokens if usage else 0,
                        "completion": usage.completion_tokens if usage else 0,
                        "total": usage.total_tokens if usage else 0,
                    },
                }
            except Exception:
                return self._call_nvidia_with_fallback(messages, temperature)

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
             temperature: float = 0.7, _retry: int = 0) -> dict:
        """Try each provider in fallback order until one succeeds. Auto-retries once on 429."""
        import time as _time
        last_error = None
        providers = self.fallback_order
        if not providers:
            raise HTTPException(status_code=503, detail="No LLM providers configured. Set GROQ_API_KEY.")
        for provider_name in providers:
            try:
                result = self._call_provider(provider_name, messages, model, temperature)
                logger.info(f"LLM success via {provider_name}/{result['model']}")
                return result
            except Exception as e:
                last_error = e
                err_str = str(e)
                if any(x in err_str for x in ["429", "rate_limit", "rate limit", "temporarily"]):
                    logger.warning(f"{provider_name} rate-limited, trying next provider...")
                elif any(x in err_str for x in ["401", "invalid_api_key", "Unauthorized"]):
                    logger.warning(f"{provider_name} auth error, trying next provider...")
                elif any(x in err_str for x in ["model_not_active", "decommissioned", "404"]):
                    logger.warning(f"{provider_name} model unavailable, trying next provider...")
                else:
                    logger.warning(f"{provider_name} failed: {e}")
                continue

        # All providers failed — wait 3s and retry once automatically
        if _retry == 0:
            logger.warning("All providers failed on first pass, retrying after 3s...")
            _time.sleep(3)
            return self.chat(messages, model, temperature, _retry=1)

        raise HTTPException(
            status_code=503,
            detail="S.T.E.W is temporarily overloaded — all AI providers are rate-limited. Please retry in 30 seconds.",
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

