"""
S.T.E.W TRINITY Coordinator — Multi-Agent Orchestration Engine
Inspired by Sakana AI's TRINITY + Conductor architecture (ICLR 2026).

Core concepts implemented:
1. Dynamic agent assembly — builds a team from the available LLM pool per task
2. Role assignment — Thinker (plan), Worker (execute), Verifier (check)
3. Multi-turn coordination — agents collaborate across turns with feedback loops
4. Conductor-style prompt design — focused, natural-language coordination prompts
5. Non-obvious collaboration — verifier can loop back to thinker if quality is low

This is a hidden feature — not exposed in the public API docs.
Activated via /trinity/orchestrate with the admin secret.
"""
import logging
import asyncio
import time
import json
from typing import Optional
from server.llm_client import get_llm_client

logger = logging.getLogger(__name__)

# Role definitions (Conductor-style natural language prompts)
ROLE_PROMPTS = {
    "thinker": {
        "system": (
            "You are the THINKER in a multi-agent system. Your job is to analyze the task, "
            "break it into clear steps, and produce a strategic plan. "
            "Think deeply about the best approach. Consider edge cases. "
            "Output a numbered plan with specific instructions for the WORKER to execute. "
            "Be concise but thorough. Do not solve the task yourself — plan how it should be solved."
        ),
    },
    "worker": {
        "system": (
            "You are the WORKER in a multi-agent system. You receive a plan from the THINKER "
            "and execute it precisely. Follow each step. Produce the actual answer, code, analysis, or content. "
            "Be accurate and complete. If the plan has gaps, use your best judgment to fill them. "
            "Output only the final work product — no meta-commentary about the process."
        ),
    },
    "verifier": {
        "system": (
            "You are the VERIFIER in a multi-agent system. Your job is to check the WORKER's output "
            "against the original task and the THINKER's plan. "
            "Score the result from 0-10 on accuracy, completeness, and quality. "
            "If the score is 8 or above, output: PASS followed by a brief justification. "
            "If below 8, output: FAIL followed by specific issues that need fixing. "
            "Be strict but fair. Do not redo the work — only evaluate it."
        ),
    },
    "synthesizer": {
        "system": (
            "You are the SYNTHESIZER. Multiple workers have produced different approaches to the same task. "
            "Combine the best elements from each into a single, superior response. "
            "Eliminate redundancy. Keep the strongest arguments. Produce one clean, authoritative answer."
        ),
    },
}

# Task type detection (for dynamic model pool routing)
TASK_CATEGORIES = {
    "coding": ["code", "function", "bug", "debug", "program", "algorithm", "script", "api", "class", "refactor"],
    "math": ["calculate", "solve", "equation", "math", "integral", "derivative", "proof", "number theory"],
    "reasoning": ["why", "explain", "analyze", "compare", "evaluate", "reason", "logic", "deduce", "infer"],
    "knowledge": ["what is", "who is", "when did", "where is", "history", "define", "describe", "summarize"],
    "creative": ["write", "create", "generate", "story", "poem", "essay", "article", "content", "copy"],
    "research": ["research", "find", "search", "investigate", "study", "report", "gather"],
}


def _detect_task_type(prompt: str) -> str:
    """Detect the category of task for intelligent model routing."""
    p = prompt.lower()
    scores = {}
    for cat, keywords in TASK_CATEGORIES.items():
        scores[cat] = sum(1 for kw in keywords if kw in p)
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else "general"


def _get_model_for_role(role: str, task_type: str, llm_client) -> tuple:
    """Pick the best available provider+model for a given role and task type.
    
    TRINITY insight: different models excel at different roles.
    - Thinker: wants a strong reasoning model
    - Worker: wants a fast, capable model matched to task type
    - Verifier: wants a different model than the worker (cross-validation)
    """
    available = llm_client.providers
    
    if role == "thinker":
        prefs = ["groq", "huggingface", "openrouter", "nvidia", "mistral", "openai", "pollinations"]
    elif role == "worker":
        if task_type == "coding":
            prefs = ["groq", "openrouter", "huggingface", "nvidia", "mistral", "openai", "pollinations"]
        elif task_type == "math":
            prefs = ["groq", "huggingface", "openrouter", "nvidia", "mistral", "openai", "pollinations"]
        elif task_type == "creative":
            prefs = ["huggingface", "groq", "openrouter", "mistral", "nvidia", "openai", "pollinations"]
        else:
            prefs = ["groq", "huggingface", "openrouter", "nvidia", "mistral", "openai", "pollinations"]
    elif role == "verifier":
        # Cross-validation — deliberately pick a DIFFERENT provider than worker
        prefs = ["openrouter", "nvidia", "mistral", "huggingface", "groq", "openai", "pollinations"]
    elif role == "synthesizer":
        prefs = ["groq", "huggingface", "openrouter", "nvidia", "mistral", "openai", "pollinations"]
    else:
        prefs = list(available.keys())

    for p in prefs:
        if p in available:
            return p, None
    
    if available:
        return list(available.keys())[0], None
    return None, None


class TrinityCoordinator:
    """
    Orchestrates a Thinker -> Worker -> Verifier pipeline with feedback loops.
    Inspired by Sakana Fugu's TRINITY architecture.
    """
    
    MAX_VERIFICATION_ROUNDS = 2
    
    async def orchestrate(
        self,
        prompt: str,
        temperature: float = 0.7,
        multi_worker: bool = False,
        api_key: Optional[str] = None,
    ) -> dict:
        """
        Run the full TRINITY pipeline on a task.
        
        Flow:
        1. Thinker analyzes the task and creates a plan
        2. Worker(s) execute the plan
        3. Verifier checks the output
        4. If FAIL, loop back to Worker with verifier feedback (up to MAX_ROUNDS)
        5. Return final result with full trace
        
        If multi_worker=True, spawns parallel workers with different providers
        and synthesizes their outputs (Conductor-style ensemble).
        """
        start_time = time.time()
        llm = get_llm_client()
        task_type = _detect_task_type(prompt)
        
        trace = {
            "task_type": task_type,
            "phases": [],
            "rounds": 0,
            "providers_used": [],
        }
        
        # Phase 1: THINK
        thinker_provider, thinker_model = _get_model_for_role("thinker", task_type, llm)
        thinker_messages = [
            {"role": "system", "content": ROLE_PROMPTS["thinker"]["system"]},
            {"role": "user", "content": f"Task: {prompt}\n\nTask type detected: {task_type}\n\nCreate a detailed execution plan."},
        ]
        
        thinker_result = await asyncio.to_thread(
            llm._call_provider, thinker_provider, thinker_messages, thinker_model, 0.3
        )
        plan = thinker_result["content"]
        trace["phases"].append({
            "role": "thinker",
            "provider": thinker_result["provider"],
            "model": thinker_result["model"],
            "plan": plan[:500] + "..." if len(plan) > 500 else plan,
            "tokens": thinker_result["tokens"],
        })
        trace["providers_used"].append(thinker_result["provider"])
        
        # Phase 2: WORK (single or multi-worker ensemble)
        if multi_worker:
            worker_result = await self._multi_worker_execute(
                prompt, plan, task_type, llm, temperature
            )
        else:
            worker_provider, worker_model = _get_model_for_role("worker", task_type, llm)
            worker_messages = [
                {"role": "system", "content": ROLE_PROMPTS["worker"]["system"]},
                {"role": "user", "content": f"Original task: {prompt}\n\nThinker's plan:\n{plan}\n\nExecute the plan and produce the final output."},
            ]
            worker_result_raw = await asyncio.to_thread(
                llm._call_provider, worker_provider, worker_messages, worker_model, temperature
            )
            worker_result = {
                "content": worker_result_raw["content"],
                "provider": worker_result_raw["provider"],
                "model": worker_result_raw["model"],
                "tokens": worker_result_raw["tokens"],
                "workers": [{"provider": worker_result_raw["provider"], "model": worker_result_raw["model"]}],
            }
        
        trace["phases"].append({
            "role": "worker",
            "provider": worker_result["provider"],
            "model": worker_result["model"],
            "workers": worker_result.get("workers", []),
            "output_preview": worker_result["content"][:500] + "..." if len(worker_result["content"]) > 500 else worker_result["content"],
            "tokens": worker_result["tokens"],
        })
        for w in worker_result.get("workers", []):
            if w["provider"] not in trace["providers_used"]:
                trace["providers_used"].append(w["provider"])
        
        current_output = worker_result["content"]
        
        # Phase 3: VERIFY (with feedback loop)
        passed = False
        for round_num in range(1, self.MAX_VERIFICATION_ROUNDS + 1):
            trace["rounds"] = round_num
            
            verifier_provider, verifier_model = _get_model_for_role("verifier", task_type, llm)
            verifier_messages = [
                {"role": "system", "content": ROLE_PROMPTS["verifier"]["system"]},
                {"role": "user", "content": (
                    f"Original task: {prompt}\n\n"
                    f"Thinker's plan:\n{plan[:1000]}\n\n"
                    f"Worker's output:\n{current_output[:3000]}\n\n"
                    f"Evaluate the output. Score 0-10. PASS if >= 8, FAIL with specific issues if < 8."
                )},
            ]
            
            verifier_result = await asyncio.to_thread(
                llm._call_provider, verifier_provider, verifier_messages, verifier_model, 0.2
            )
            
            verdict = verifier_result["content"]
            passed = verdict.strip().upper().startswith("PASS")
            
            trace["phases"].append({
                "role": "verifier",
                "round": round_num,
                "provider": verifier_result["provider"],
                "model": verifier_result["model"],
                "verdict": verdict[:300],
                "passed": passed,
                "tokens": verifier_result["tokens"],
            })
            if verifier_result["provider"] not in trace["providers_used"]:
                trace["providers_used"].append(verifier_result["provider"])
            
            if passed:
                break
            
            # Feedback loop: send verifier issues back to worker
            if round_num < self.MAX_VERIFICATION_ROUNDS:
                feedback_messages = [
                    {"role": "system", "content": ROLE_PROMPTS["worker"]["system"]},
                    {"role": "user", "content": (
                        f"Original task: {prompt}\n\n"
                        f"Previous plan:\n{plan[:500]}\n\n"
                        f"Your previous output:\n{current_output[:2000]}\n\n"
                        f"Verifier feedback:\n{verdict}\n\n"
                        f"Fix the issues identified by the verifier and produce an improved output."
                    )},
                ]
                revision_result = await asyncio.to_thread(
                    llm._call_provider, worker_provider, feedback_messages, worker_model, temperature
                )
                current_output = revision_result["content"]
                trace["phases"].append({
                    "role": "worker_revision",
                    "round": round_num,
                    "provider": revision_result["provider"],
                    "model": revision_result["model"],
                    "output_preview": current_output[:300] + "..." if len(current_output) > 300 else current_output,
                    "tokens": revision_result["tokens"],
                })
        
        elapsed = round(time.time() - start_time, 2)
        total_tokens = sum(p.get("tokens", {}).get("total", 0) for p in trace["phases"])
        
        return {
            "response": current_output,
            "plan": plan,
            "verified": passed,
            "rounds": trace["rounds"],
            "task_type": task_type,
            "providers_used": list(set(trace["providers_used"])),
            "total_tokens": total_tokens,
            "execution_time": elapsed,
            "trace": trace,
        }
    
    async def _multi_worker_execute(
        self, prompt: str, plan: str, task_type: str, llm, temperature: float
    ) -> dict:
        """
        Conductor-style ensemble: spawn 2-3 workers on different providers in parallel,
        then synthesize their outputs into one superior response.
        """
        available = list(llm.providers.keys())
        worker_providers = []
        for pref in ["groq", "huggingface", "openrouter", "nvidia", "mistral"]:
            if pref in available and pref not in worker_providers:
                worker_providers.append(pref)
            if len(worker_providers) >= 3:
                break
        for p in available:
            if p not in worker_providers and len(worker_providers) < 3:
                worker_providers.append(p)
            if len(worker_providers) >= 3:
                break
        
        if not worker_providers:
            worker_providers = [available[0]] if available else ["pollinations"]
        
        worker_messages = [
            {"role": "system", "content": ROLE_PROMPTS["worker"]["system"]},
            {"role": "user", "content": f"Original task: {prompt}\n\nThinker's plan:\n{plan}\n\nExecute the plan and produce the final output."},
        ]
        
        async def run_worker(provider_name):
            return await asyncio.to_thread(
                llm._call_provider, provider_name, worker_messages, None, temperature
            )
        
        results = await asyncio.gather(
            *[run_worker(p) for p in worker_providers],
            return_exceptions=True,
        )
        
        worker_outputs = []
        worker_info = []
        total_tokens = 0
        for i, r in enumerate(results):
            if isinstance(r, Exception):
                logger.warning(f"Worker {worker_providers[i]} failed: {r}")
                continue
            worker_outputs.append(r["content"])
            worker_info.append({"provider": r["provider"], "model": r["model"]})
            total_tokens += r["tokens"].get("total", 0)
        
        if not worker_outputs:
            raise RuntimeError("All workers failed")
        
        if len(worker_outputs) == 1:
            return {
                "content": worker_outputs[0],
                "provider": worker_info[0]["provider"],
                "model": worker_info[0]["model"],
                "tokens": {"prompt": 0, "completion": 0, "total": total_tokens},
                "workers": worker_info,
            }
        
        # Synthesize multiple worker outputs
        synth_provider, synth_model = _get_model_for_role("synthesizer", task_type, llm)
        combined = "\n\n---\n\n".join(
            f"[Worker {i+1} ({info['provider']}/{info['model']})]:\n{out}"
            for i, (out, info) in enumerate(zip(worker_outputs, worker_info))
        )
        synth_messages = [
            {"role": "system", "content": ROLE_PROMPTS["synthesizer"]["system"]},
            {"role": "user", "content": f"Original task: {prompt}\n\nWorker outputs:\n{combined}\n\nSynthesize the best elements into one superior response."},
        ]
        synth_result = await asyncio.to_thread(
            llm._call_provider, synth_provider, synth_messages, synth_model, temperature
        )
        total_tokens += synth_result["tokens"].get("total", 0)
        
        return {
            "content": synth_result["content"],
            "provider": synth_result["provider"],
            "model": synth_result["model"],
            "tokens": {"prompt": 0, "completion": 0, "total": total_tokens},
            "workers": worker_info,
        }


# Singleton
_trinity = None


def get_trinity():
    global _trinity
    if _trinity is None:
        _trinity = TrinityCoordinator()
    return _trinity
