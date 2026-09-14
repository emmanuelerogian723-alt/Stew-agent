"""
S.T.E.W Model Pool — free high-class models via OpenRouter ("outer.js").

Dynamically discovers OpenRouter's free-tier models at runtime
(https://openrouter.ai/api/v1/models — public, no auth) and exposes them
as orchestration workers ("openrouter:<model-id>") so the MoA generators
and fusion engine can fan out across many flagship-class models at
zero cost. Failures are harmless: the orchestrator treats a failed
worker as a dropped draft and continues with the rest.
"""
import logging
import time
from typing import Optional

logger = logging.getLogger("stew.model_pool")

_cache: dict = {"ts": 0.0, "models": []}
CACHE_TTL = 24 * 3600

# families that make great text generators; ranked roughly by capability
PREFERRED = [
    "llama-3.3-70b", "llama-4", "gpt-oss", "deepseek-r1", "deepseek-chat",
    "qwen3", "gemma-4", "gemini-2", "mistral-large", "nemotron",
]
EXCLUDE = ("image", "audio", "tts", "whisper", "embed", "vision-language",
           "vidu", "kling", "flux")


def fetch_openrouter_free_models(limit: int = 8) -> list[str]:
    """Live-discover free OpenRouter models, best-first. Cached 24h."""
    if _cache["models"] and time.time() - _cache["ts"] < CACHE_TTL:
        return _cache["models"][:limit]
    try:
        import httpx
        r = httpx.get("https://openrouter.ai/api/v1/models", timeout=20)
        r.raise_for_status()
        data = r.json().get("data", [])
        free = []
        for m in data:
            mid = m.get("id", "")
            if not mid.endswith(":free"):
                continue
            name = mid.lower()
            if any(x in name for x in EXCLUDE):
                continue
            # score: preferred family rank, then context length
            fam_rank = next((i for i, fam in enumerate(PREFERRED) if fam in name), len(PREFERRED))
            free.append((fam_rank, -(m.get("context_length") or 0), mid))
        free.sort()
        models = [mid for _, _, mid in free]
        if models:
            _cache["ts"] = time.time()
            _cache["models"] = models
            logger.info(f"model pool: {len(models)} free models discovered — top: {models[:5]}")
        return models[:limit]
    except Exception as e:
        logger.warning(f"model pool discovery failed: {e}")
        return _cache["models"][:limit] if _cache["models"] else []


# Puter AI Gateway — flagship-class models available through one OpenAI-
# compatible endpoint once PUTER_AUTH_TOKEN is configured.
PUTER_MODELS = [
    "claude-sonnet-5",        # Anthropic flagship
    "gpt-5.4-nano",           # OpenAI fast flagship
    "gemini-3.5-flash-lite",  # Google fast
    "grok-4.6",               # xAI
]


def puter_workers(n: int = 2, enabled: bool = True) -> list[str]:
    if not enabled:
        return []
    return [f"puter:{m}" for m in PUTER_MODELS[:n]]


def pool_workers(n: int = 3, puter_enabled: bool = True,
                 openrouter_enabled: bool = True) -> list[str]:
    """Orchestration workers from the free model pool: Puter flagship models
    first, then OpenRouter free models. Empty list if nothing is available."""
    out = puter_workers(n, puter_enabled)
    if len(out) < n:
        out += [f"openrouter:{m}" for m in fetch_openrouter_free_models(n - len(out))]
    return out[:n]
