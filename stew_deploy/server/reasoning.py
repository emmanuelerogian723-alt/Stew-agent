"""
S.T.E.W Deep Reasoning Layer — "understand the FULL intent before acting".

Two-pass architecture (like o1/DeepSeek-R1 lite, tuned for speed):
  Pass 1 (fast model, ~300 tokens): a dedicated intent-analysis pass extracts
    what the user ACTUALLY wants — the core ask, the true intent category,
    complexity, hidden/implicit requirements, and which tools are needed.
  Pass 2 (flagship model): answers with the intent analysis injected and an
    explicit reason-first instruction.

This replaces naive keyword routing. Instead of pattern-matching words,
Stew now reasons about the request before choosing how to handle it.
"""
import asyncio
import json
import logging
import re
from typing import Callable, Optional

logger = logging.getLogger("stew.reasoning")

FAST_MODEL = "openai/gpt-oss-20b"  # fast Groq model for the analysis pass

_ANALYSIS_SYSTEM = """You are S.T.E.W's intent-analysis engine. Your ONLY job is to deeply understand
what a user message REALLY wants — including what they did NOT say explicitly.

Analyze the message and reply with ONLY this JSON (no markdown, no extra text):
{
  "core_ask": "one plain sentence: what the user actually wants",
  "intent": "one of: news, research, factual, howto, creative, code, math, document, social, task, other",
  "complexity": "simple | moderate | complex",
  "needs_tools": "none | search | news | calculator",
  "hidden_requirements": ["implicit expectations the user didn't state aloud"],
  "approach": "one short sentence: the smartest way to answer this"
}

Rules:
- "news" intent = wants recent real-world happenings/current events.
- "research" intent = wants a thorough multi-source investigation/report.
- "task" intent = wants Stew to DO something (generate a file, run code, create media).
- "social" intent = greetings, small talk, thanks, mood — no real question.
- Hidden requirements example: "make money online" → user is likely Nigerian, wants
  realistic low-capital options, not generic US advice.
- If the message is ambiguous, pick the MOST LIKELY meaning and say so in core_ask.
"""

# Messages that never need the analysis pass (pure social glue)
_SKIP_RE = re.compile(
    r"^(hi+|hello+|hey+|good\s*(morning|afternoon|evening)|thanks?|thank you|ok(ay)?|"
    r"cool|nice|great|lol|haha|lmao|yes|no|nope|yeah|yep|sup|how far|boss|good)\b",
    re.I)


def should_analyze(text: str) -> bool:
    t = (text or "").strip()
    if len(t) < 15 or len(t) > 2000:
        return False
    if _SKIP_RE.match(t):
        return False
    return True


def _extract_json(raw: str) -> Optional[dict]:
    if not raw:
        return None
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if not m:
        return None
    try:
        d = json.loads(m.group(0))
        return d if isinstance(d, dict) else None
    except Exception:
        return None


def analyze_intent_sync(user_text: str, llm, timeout_s: float = 10.0) -> Optional[dict]:
    """Pass 1: fast deep-intent analysis. Returns None on any failure (graceful)."""
    try:
        result = llm.chat(
            [{"role": "system", "content": _ANALYSIS_SYSTEM},
             {"role": "user", "content": user_text[:1500]}],
            model=FAST_MODEL, temperature=0.2, max_tokens=400)
        content = result.get("content", "") if isinstance(result, dict) else str(result)
        analysis = _extract_json(content)
        if analysis and analysis.get("core_ask"):
            # sanitize fields
            analysis["intent"] = str(analysis.get("intent", "other")).lower().strip()
            analysis["complexity"] = str(analysis.get("complexity", "moderate")).lower().strip()
            analysis["needs_tools"] = str(analysis.get("needs_tools", "none")).lower().strip()
            if not isinstance(analysis.get("hidden_requirements"), list):
                analysis["hidden_requirements"] = []
            analysis["hidden_requirements"] = [
                str(h)[:150] for h in analysis["hidden_requirements"][:5]]
            logger.info(f"intent: {analysis['intent']} | tools: {analysis['needs_tools']} | "
                        f"ask: {analysis['core_ask'][:60]}")
            return analysis
    except Exception as e:
        logger.warning(f"intent analysis failed: {e}")
    return None


async def analyze_intent(user_text: str, llm) -> Optional[dict]:
    """Async wrapper with a hard timeout so chat never stalls on the pre-pass."""
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(analyze_intent_sync, user_text, llm), timeout=12.0)
    except Exception as e:
        logger.debug(f"intent analysis timed out/skipped: {e}")
        return None


def build_reasoning_context(analysis: Optional[dict]) -> str:
    """Prompt block injected into the main answer pass."""
    if not analysis:
        return ""
    hidden = ""
    if analysis.get("hidden_requirements"):
        hidden = "\n- Unspoken requirements: " + "; ".join(analysis["hidden_requirements"])
    return (
        f"\n\nDEEP INTENT ANALYSIS (from S.T.E.W's reasoning pre-pass — trust it, "
        f"but verify against the user's exact words):\n"
        f"- What the user really wants: {analysis.get('core_ask', '')}"
        f"\n- Intent: {analysis.get('intent', 'other')} | Complexity: {analysis.get('complexity', 'moderate')}"
        f"- Tools needed: {analysis.get('needs_tools', 'none')}"
        f"{hidden}"
        f"\n- Best approach: {analysis.get('approach', 'reason carefully and answer directly')}\n"
        f"THINK BEFORE ANSWERING: first identify exactly what is being asked and what a truly "
        f"excellent answer contains, reason step-by-step internally, then produce the final "
        f"answer directly — concise, correct, and complete. Do not show your raw reasoning steps; "
        f"show the polished answer. If the analysis reveals the user wants something different "
        f"from the literal words, serve the deeper need."
    )
