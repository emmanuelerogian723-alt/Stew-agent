"""
S.T.E.W Orchestrator — Mixture-of-Agents (Fugu-style) multi-model dispatch.

Inspired by Sakana AI's "Fugu": instead of trusting one model's answer,
fan a prompt out to N worker models in PARALLEL, collect their independent
answers, then run a synthesis pass over all of them to produce one
best-of-all-worlds final answer — all behind a single API call.

Two entry points:
  - orchestrate_text(prompt)   -> mixture-of-agents for text/reasoning tasks
  - orchestrate_image(prompt)  -> multi-worker image generation (first-to-finish
                                   or all-results mode, using only free APIs)
"""
import asyncio
import logging
import time
from typing import Optional

import httpx

from server.config import get_settings
from server.llm_client import get_llm_client, PROVIDER_MODELS, NVIDIA_FALLBACKS, GROQ_FALLBACKS

logger = logging.getLogger(__name__)
settings = get_settings()


# ─────────────────────────────────────────────────────────────────────────
# TEXT ORCHESTRATION — mixture-of-agents (Fugu-style)
# ─────────────────────────────────────────────────────────────────────────

async def _run_worker(worker_id: str, messages: list[dict], temperature: float) -> dict:
    """Run one worker model in a background thread (SDKs here are sync)."""
    client = get_llm_client()
    start = time.time()
    try:
        # force a specific provider by temporarily restricting fallback order
        result = await asyncio.to_thread(client._call_provider, worker_id, messages, None, temperature)
        result["latency_s"] = round(time.time() - start, 2)
        result["worker"] = worker_id
        result["ok"] = True
        return result
    except Exception as e:
        return {"worker": worker_id, "ok": False, "error": str(e), "latency_s": round(time.time() - start, 2)}


async def orchestrate_text(prompt: str, system: Optional[str] = None,
                            workers: Optional[list[str]] = None,
                            temperature: float = 0.7) -> dict:
    """
    Fugu-style mixture-of-agents:
      1. Dispatch the prompt to N worker models in parallel (independent answers).
      2. Feed all independent answers to a synthesizer model.
      3. Return one consolidated, higher-quality answer.

    `workers` defaults to every provider the user has configured (up to 3),
    so it scales with whatever API keys are set — Groq, NVIDIA NIM, OpenRouter,
    HuggingFace, OpenAI.
    """
    client = get_llm_client()
    available = client.fallback_order
    if not available:
        raise RuntimeError("No LLM providers configured")

    chosen_workers = workers or available[:3] or available
    messages = [{"role": "system", "content": system or "You are a helpful, precise reasoning assistant."},
                {"role": "user", "content": prompt}]

    # Step 1: fan out in parallel
    results = await asyncio.gather(*[_run_worker(w, messages, temperature) for w in chosen_workers])
    successes = [r for r in results if r.get("ok")]

    if not successes:
        raise RuntimeError(f"All workers failed: {results}")

    if len(successes) == 1:
        # Only one worker responded — nothing to synthesize, return it directly
        only = successes[0]
        return {
            "answer": only["content"],
            "mode": "single_worker_fallback",
            "workers_used": [only["worker"]],
            "raw_worker_outputs": successes,
        }

    # Step 2: synthesis — a capable model reviews all independent answers
    synthesis_prompt = "You are an expert synthesizer. Multiple AI models independently answered the same question.\n\n"
    for i, r in enumerate(successes, 1):
        synthesis_prompt += f"--- Worker {i} ({r['worker']}/{r['model']}) ---\n{r['content']}\n\n"
    synthesis_prompt += (
        "Compare the reasoning across all workers above. Where they agree, keep that. "
        "Where they disagree, decide which reasoning is more coherent and correct. "
        "Produce ONE final, best-possible answer to the original question below. "
        "Do not mention the workers or this process — just answer directly and confidently.\n\n"
        f"Original question: {prompt}"
    )

    synth_messages = [
        {"role": "system", "content": "You are the final synthesizer in a mixture-of-agents system. Be decisive and accurate."},
        {"role": "user", "content": synthesis_prompt},
    ]
    synth_result = client.chat(synth_messages, temperature=0.3)

    return {
        "answer": synth_result["content"],
        "mode": "mixture_of_agents",
        "workers_used": [r["worker"] for r in successes],
        "synthesizer": f"{synth_result['provider']}/{synth_result['model']}",
        "raw_worker_outputs": successes,
    }


# ─────────────────────────────────────────────────────────────────────────
# IMAGE ORCHESTRATION — multi-worker image generation
# ─────────────────────────────────────────────────────────────────────────

async def _image_worker_pollinations(prompt: str) -> dict:
    """pollinations.ai — free, no API key required."""
    start = time.time()
    try:
        url = f"https://image.pollinations.ai/prompt/{httpx.QueryParams({'p': prompt})['p']}"
        url = f"https://image.pollinations.ai/prompt/{prompt.replace(' ', '%20')}?nologo=true"
        async with httpx.AsyncClient(timeout=60) as http:
            resp = await http.get(url)
            resp.raise_for_status()
        return {"worker": "pollinations", "ok": True, "image_url": url,
                "latency_s": round(time.time() - start, 2)}
    except Exception as e:
        return {"worker": "pollinations", "ok": False, "error": str(e),
                "latency_s": round(time.time() - start, 2)}


async def _image_worker_hf(prompt: str) -> dict:
    """HuggingFace Inference API — FLUX/Stable Diffusion (needs HF_TOKEN)."""
    start = time.time()
    token = settings.HF_TOKEN_IMAGE or settings.HF_TOKEN
    if not token:
        return {"worker": "huggingface_image", "ok": False, "error": "no HF token configured",
                "latency_s": 0}
    try:
        async with httpx.AsyncClient(timeout=90) as http:
            resp = await http.post(
                "https://router.huggingface.co/hf-inference/models/black-forest-labs/FLUX.1-schnell",
                headers={"Authorization": f"Bearer {token}"},
                json={"inputs": prompt},
            )
            resp.raise_for_status()
            content_type = resp.headers.get("content-type", "")
            if "image" not in content_type:
                raise RuntimeError(f"unexpected response type: {content_type}")
        return {"worker": "huggingface_image", "ok": True, "image_bytes_len": len(resp.content),
                "content_type": content_type, "latency_s": round(time.time() - start, 2)}
    except Exception as e:
        return {"worker": "huggingface_image", "ok": False, "error": str(e),
                "latency_s": round(time.time() - start, 2)}


async def orchestrate_image(prompt: str, mode: str = "first") -> dict:
    """
    Multi-worker image generation, Fugu-style but for images:
      - mode="first": return whichever free image worker finishes first.
      - mode="all": return every worker's result so the caller can pick/compare.

    Workers today: pollinations.ai (no key), HuggingFace FLUX.1-schnell (HF_TOKEN).
    Designed to grow — add a new _image_worker_* function and register it below
    to add another model/provider as a worker with zero changes elsewhere.
    """
    workers = [_image_worker_pollinations(prompt)]
    if settings.HF_TOKEN_IMAGE or settings.HF_TOKEN:
        workers.append(_image_worker_hf(prompt))

    if mode == "all":
        results = await asyncio.gather(*workers)
        return {"mode": "all", "results": results}

    # mode == "first": race workers, return the first success
    pending = {asyncio.ensure_future(w) for w in workers}
    result = None
    while pending:
        done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
        for d in done:
            r = d.result()
            if r.get("ok"):
                result = r
                for p in pending:
                    p.cancel()
                pending = set()
                break
    if not result:
        raise RuntimeError("All image workers failed")
    return {"mode": "first", "result": result}
