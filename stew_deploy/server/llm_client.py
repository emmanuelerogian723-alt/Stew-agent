"""
S.T.E.W LLM Client — multi-provider with automatic fallback.
Chain: Groq → OpenRouter → OpenAI
"""
import logging
from typing import Optional

from fastapi import HTTPException
from groq import Groq
from openai import OpenAI

from server.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

# Model aliases per provider
PROVIDER_MODELS = {
    "groq": "llama3-70b-8192",
    "openrouter": "meta-llama/llama-3-70b-instruct",
    "openai": "gpt-4o-mini",
    "huggingface": "meta-llama/Llama-3.1-8B-Instruct",
}


class LLMClient:
    def __init__(self):
        self.providers: dict = {}
        self._init_providers()

    def _init_providers(self):
        if settings.GROQ_API_KEY:
            try:
                self.providers["groq"] = Groq(api_key=settings.GROQ_API_KEY)
                logger.info("Groq provider initialized")
            except Exception as e:
                logger.warning(f"Groq init failed: {e}")

        if settings.OPENROUTER_API_KEY:
            try:
                self.providers["openrouter"] = OpenAI(
                    base_url="https://openrouter.ai/api/v1",
                    api_key=settings.OPENROUTER_API_KEY,
                )
                logger.info("OpenRouter provider initialized")
            except Exception as e:
                logger.warning(f"OpenRouter init failed: {e}")

        if settings.OPENAI_API_KEY:
            try:
                self.providers["openai"] = OpenAI(api_key=settings.OPENAI_API_KEY)
                logger.info("OpenAI provider initialized")
            except Exception as e:
                logger.warning(f"OpenAI init failed: {e}")

        if settings.HF_TOKEN:
            try:
                self.providers["huggingface"] = OpenAI(
                    base_url="https://router.huggingface.co/v1",
                    api_key=settings.HF_TOKEN,
                )
                logger.info("HuggingFace provider initialized")
            except Exception as e:
                logger.warning(f"HuggingFace init failed: {e}")

        if not self.providers:
            logger.error("No LLM providers available — set at least one API key")

    @property
    def fallback_order(self) -> list[str]:
        return [p for p in ["groq", "openrouter", "openai", "huggingface"] if p in self.providers]

    def _call_provider(
        self,
        provider_name: str,
        messages: list[dict],
        model: Optional[str],
        temperature: float,
    ) -> dict:
        client = self.providers[provider_name]
        chosen_model = model or PROVIDER_MODELS[provider_name]

        response = client.chat.completions.create(
            model=chosen_model,
            messages=messages,
            temperature=temperature,
        )
        content = response.choices[0].message.content
        usage = response.usage

        return {
            "content": content,
            "provider": provider_name,
            "model": chosen_model,
            "tokens": {
                "prompt": usage.prompt_tokens if usage else 0,
                "completion": usage.completion_tokens if usage else 0,
                "total": usage.total_tokens if usage else 0,
            },
        }

    def chat(
        self,
        messages: list[dict],
        model: Optional[str] = None,
        temperature: float = 0.7,
    ) -> dict:
        """Try each provider in fallback order until one succeeds."""
        last_error = None
        for provider_name in self.fallback_order:
            try:
                result = self._call_provider(provider_name, messages, model, temperature)
                logger.info(f"LLM success via {provider_name}")
                return result
            except Exception as e:
                last_error = e
                logger.warning(f"{provider_name} failed: {e}")
                continue

        raise HTTPException(
            status_code=503,
            detail=f"All LLM providers unavailable. Last error: {last_error}",
        )

    def complete(self, prompt: str, system: str = "You are S.T.E.W, a helpful AI agent.", **kwargs) -> str:
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
