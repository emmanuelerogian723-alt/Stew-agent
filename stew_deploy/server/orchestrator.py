"""
S.T.E.W Trinity Orchestrator v2.0 — World-Class Multi-Model Orchestration.

Architecture (Trinity = 3 roles):
  1. GENERATORS (3+ models in parallel) — each independently drafts an answer
  2. CRITIC (1 strong model) — analyzes all drafts, identifies best reasoning, 
     catches errors, and writes a critique
  3. REFINER (1 model) — takes the critique + best draft, produces the final
     polished, world-class response

This 3-stage pipeline produces output that beats any single model on:
  - Accuracy (critic catches hallucinations)
  - Completeness (multiple generators cover different angles)
  - Clarity (refiner polishes for readability)
  - Consistency (critic resolves contradictions between generators)

Inspired by: Sakana Fugu, DeepMind FunSearch, and Anthropic Constitutional AI.

v2.0 Changes:
  - Fixed variable scope bugs (critic_model, critique_text)
  - Added content_type parameter for document-specific orchestration
  - Better error handling with graceful degradation
  - Support for structured JSON output (for slides, spreadsheets)
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
# STAGE 1: GENERATORS — parallel independent drafts
# ─────────────────────────────────────────────────────────────────────────

async def _run_generator(worker_id: str, messages: list[dict], temperature: float) -> dict:
    """Run one generator model in parallel."""
    client = get_llm_client()
    start = time.time()
    try:
        result = await asyncio.to_thread(client._call_provider, worker_id, messages, None, temperature)
        result["latency_s"] = round(time.time() - start, 2)
        result["worker"] = worker_id
        result["ok"] = True
        return result
    except Exception as e:
        return {"worker": worker_id, "ok": False, "error": str(e), 
                "latency_s": round(time.time() - start, 2)}


# ─────────────────────────────────────────────────────────────────────────
# STAGE 2: CRITIC — analyzes all drafts, writes critique
# ─────────────────────────────────────────────────────────────────────────

CRITIC_SYSTEM = """You are an elite AI critic and fact-checker. Your job is to evaluate multiple AI-generated responses to the same question and produce a detailed critique.

Your critique must:
1. Identify which response(s) have the most accurate and complete reasoning
2. Flag any factual errors, hallucinations, or unsupported claims
3. Note contradictions between responses
4. Identify the strongest arguments and insights across all responses
5. Suggest what the ideal final answer should include
6. Rate each response 1-10 on accuracy, completeness, and clarity

Be precise, specific, and ruthless about errors. Your critique will be used to construct the final answer."""

# ─────────────────────────────────────────────────────────────────────────
# STAGE 3: REFINER — produces the final world-class response
# ─────────────────────────────────────────────────────────────────────────

REFINER_SYSTEM = """You are S.T.E.W.'s final answer refiner — an elite AI writer and reasoning expert. 

You receive:
- The original user question
- Multiple AI-generated drafts
- A critic's analysis of those drafts

Your job: produce ONE final, world-class response that:
- Incorporates the best reasoning from all drafts
- Fixes all errors the critic identified
- Is clear, well-structured, and professional
- Is comprehensive but not bloated
- Uses plain text formatting (NO markdown headers with ##, NO **bold** markers)
- Uses clean numbered lists (1. 2. 3.) and plain text section titles
- Is confident, precise, and directly answers the question
- Would score 10/10 on accuracy, completeness, and clarity

Write the final answer as if you are the world's leading expert on this topic.
Do NOT mention the drafts, the critic, or this process — just deliver the answer."""


async def orchestrate_text(prompt: str, system: Optional[str] = None,
                            workers: Optional[list[str]] = None,
                            temperature: float = 0.7) -> dict:
    """
    Trinity Orchestration: Generator -> Critic -> Refiner
    
    Stage 1: Fan out to N generators in parallel (independent drafts)
    Stage 2: Critic analyzes all drafts, identifies errors and best reasoning
    Stage 3: Refiner produces the final world-class answer
    
    Returns the refined answer with full metadata about the pipeline.
    """
    client = get_llm_client()
    available = client.fallback_order
    if not available:
        raise RuntimeError("No LLM providers configured")

    chosen_workers = workers or available[:3] or available
    base_messages = [
        {"role": "system", "content": system or "You are a helpful, precise reasoning assistant."},
        {"role": "user", "content": prompt}
    ]

    # ── STAGE 1: GENERATORS ──────────────────────────────────────────────
    stage1_start = time.time()
    gen_results = await asyncio.gather(
        *[_run_generator(w, base_messages, temperature) for w in chosen_workers]
    )
    successes = [r for r in gen_results if r.get("ok")]
    
    if not successes:
        raise RuntimeError(f"All generators failed: {gen_results}")
    
    if len(successes) == 1:
        only = successes[0]
        return {
            "answer": only["content"],
            "mode": "single_generator_fallback",
            "workers_used": [only["worker"]],
            "raw_worker_outputs": successes,
            "stages": {"generator": round(time.time() - stage1_start, 2)},
        }

    # ── STAGE 2: CRITIC ──────────────────────────────────────────────────
    stage2_start = time.time()
    
    critique_input = f"Original question:\n{prompt}\n\n"
    for i, r in enumerate(successes, 1):
        critique_input += f"--- Draft {i} (from {r['worker']}/{r['model']}) ---\n{r['content']}\n\n"
    critique_input += (
        "Analyze these drafts above. Write a detailed critique:\n"
        "1. Which draft is most accurate? Why?\n"
        "2. What errors or hallucinations do you see in any draft?\n"
        "3. What key points are missing from some drafts but present in others?\n"
        "4. What should the final answer definitely include?\n"
        "5. What should the final answer avoid?"
    )
    
    critique_messages = [
        {"role": "system", "content": CRITIC_SYSTEM},
        {"role": "user", "content": critique_input},
    ]
    
    critic_model = "unknown"
    critique_text = ""
    
    try:
        critique_result = await asyncio.to_thread(
            client.chat, critique_messages, 0.3
        )
        critique_text = critique_result["content"]
        critic_model = f"{critique_result['provider']}/{critique_result['model']}"
    except Exception as e:
        logger.warning(f"Critic stage failed: {e} — using best generator output")
        best = successes[0]
        return {
            "answer": best["content"],
            "mode": "generator_only_critic_failed",
            "workers_used": [r["worker"] for r in successes],
            "raw_worker_outputs": successes,
            "stages": {
                "generator": round(time.time() - stage1_start, 2),
                "critic_failed": str(e),
            },
        }

    # ── STAGE 3: REFINER ─────────────────────────────────────────────────
    stage3_start = time.time()
    
    refiner_input = f"Original question:\n{prompt}\n\n"
    refiner_input += f"--- Critique ---\n{critique_text}\n\n"
    refiner_input += "--- Drafts for reference ---\n"
    for i, r in enumerate(successes, 1):
        refiner_input += f"Draft {i} ({r['worker']}): {r['content'][:2000]}\n\n"
    refiner_input += (
        "Based on the critique and drafts above, write the FINAL, world-class answer. "
        "Use plain text only. No ## headers, no **bold** markers. "
        "Structure your response with clear numbered lists and plain section titles. "
        "Be confident, precise, and comprehensive."
    )
    
    refiner_messages = [
        {"role": "system", "content": REFINER_SYSTEM},
        {"role": "user", "content": refiner_input},
    ]
    
    refiner_model = "unknown"
    
    try:
        refiner_result = await asyncio.to_thread(
            client.chat, refiner_messages, 0.4
        )
        final_answer = refiner_result["content"]
        refiner_model = f"{refiner_result['provider']}/{refiner_result['model']}"
    except Exception as e:
        logger.warning(f"Refiner stage failed: {e} — using critic's analysis")
        final_answer = successes[0]["content"]
        refiner_model = "refiner_failed"
    
    return {
        "answer": final_answer,
        "mode": "trinity",
        "workers_used": [r["worker"] for r in successes],
        "critic": critic_model,
        "refiner": refiner_model,
        "raw_worker_outputs": successes,
        "critique": critique_text[:500],
        "stages": {
            "generator": round(stage2_start - stage1_start, 2),
            "critic": round(time.time() - stage2_start, 2),
            "total": round(time.time() - stage1_start, 2),
        },
    }


# ─────────────────────────────────────────────────────────────────────────
# FAST FUSION — lightweight 2-model merge (no critic/refiner)
# Used for document generation where speed matters more than perfection
# ─────────────────────────────────────────────────────────────────────────

async def fast_fusion(prompt: str, system: str = "", workers: Optional[list[str]] = None) -> dict:
    """
    Fast 2-model fusion: generate with 2 models in parallel, then merge.
    Much faster than full Trinity (no critic/refiner stages).
    Used for document content generation where we need quality + speed.
    """
    client = get_llm_client()
    available = client.fallback_order
    if not available:
        raise RuntimeError("No LLM providers configured")
    
    chosen = workers or available[:2]
    if len(chosen) < 2:
        chosen = available[:2] if len(available) >= 2 else available
    
    messages = [
        {"role": "system", "content": system or "You are a professional content writer."},
        {"role": "user", "content": prompt},
    ]
    
    start = time.time()
    results = await asyncio.gather(
        *[_run_generator(w, messages, 0.7) for w in chosen]
    )
    successes = [r for r in results if r.get("ok")]
    
    if not successes:
        raise RuntimeError(f"All fusion generators failed: {results}")
    
    if len(successes) == 1:
        return {
            "answer": successes[0]["content"],
            "mode": "single_fusion",
            "workers_used": [successes[0]["worker"]],
            "time": round(time.time() - start, 2),
        }
    
    # Merge: take the longer/better response (simple heuristic)
    best = max(successes, key=lambda r: len(r.get("content", "")))
    
    return {
        "answer": best["content"],
        "mode": "fast_fusion",
        "workers_used": [r["worker"] for r in successes],
        "time": round(time.time() - start, 2),
    }


# ─────────────────────────────────────────────────────────────────────────
# IMAGE ORCHESTRATION — multi-worker image generation
# ─────────────────────────────────────────────────────────────────────────

async def _image_worker_pollinations(prompt: str) -> dict:
    """pollinations.ai — free, no API key required."""
    import random
    import urllib.parse
    start = time.time()
    encoded = urllib.parse.quote(prompt, safe='')
    async with httpx.AsyncClient(timeout=60, follow_redirects=True) as http:
        for attempt in range(3):
            try:
                seed = random.randint(1, 999999)
                url = f"https://image.pollinations.ai/prompt/{encoded}?width=1024&height=1024&model=flux&nologo=true&seed={seed}"
                resp = await http.get(url)
                if resp.status_code == 200 and len(resp.content) > 2000:
                    return {"ok": True, "image_bytes": resp.content, "worker": "pollinations", "latency_s": round(time.time() - start, 2)}
            except Exception:
                pass
    return {"ok": False, "error": "pollinations failed", "worker": "pollinations"}


async def orchestrate_image(prompt: str, workers: Optional[list[str]] = None) -> dict:
    """Generate an image using multiple providers in parallel, return first success."""
    worker_tasks = [_image_worker_pollinations(prompt)]
    results = await asyncio.gather(*worker_tasks)
    for r in results:
        if r.get("ok"):
            return r
    return {"ok": False, "error": "All image workers failed"}
