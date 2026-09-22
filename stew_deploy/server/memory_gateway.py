"""
S.T.E.W Memory Gateway — dual-provider long-term memory (Mem0 + Letta).

Design goals:
- Mem0 is the PRIMARY store (semantic search, auto-extraction, categories).
- Letta core-memory blocks are the SECONDARY/durable store. Every memory is
  ALSO mirrored into a per-user Letta block so that if Mem0's free limits are
  hit (429/403/quota), Stew keeps remembering — Letta continues seamlessly.
- If Mem0 is unavailable, we enter a cooldown (1h) and serve reads from Letta.
- Memories cover: preferences, facts, intents, activities, thoughts,
  conversations, workflows, documents, videos, audio/songs, files.

user_key: stable per-platform id, e.g. "tg_5547996257" for Telegram.
"""
import asyncio
import logging
import os
import re
import time
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

MEM0_BASE = "https://api.mem0.ai"
LETTA_BASE = "https://api.letta.com"

# Block char budgets — Letta blocks are capped; keep the newest memories.
_LETTA_BLOCK_LIMIT = 40000
_LETTA_MAX_APPEND = 12000
_MEM0_COOLDOWN = 3600  # seconds to skip Mem0 after a quota/auth failure

# Provider health state (module-level; fine for a single-worker deploy)
_mem0_disabled_until: float = 0.0
_letta_disabled_until: float = 0.0


def _env(name: str) -> Optional[str]:
    return os.environ.get(name)


def _mem0_key() -> Optional[str]:
    return (_env("MEMO_API_KEY") or "").strip() or None


def _letta_key() -> Optional[str]:
    """Return the Letta key, restoring base64 padding a secret-scanner may
    have clipped (Letta keys of the form sk-let-... can end with '==' )."""
    key = (_env("LETTA_API_KEY") or "").strip()
    if not key:
        return None
    if key.startswith("sk-let-") and not key.endswith("="):
        body = key[len("sk-let-"):]
        rem = len(body) % 4
        if rem == 2:
            key += "=="
        elif rem == 3:
            key += "="
    return key


def _safe_key(user_key: str) -> str:
    """Mem0/Letta-friendly id: alnum, underscore, dash only."""
    k = re.sub(r"[^a-zA-Z0-9_-]", "_", str(user_key or "anon"))
    return k[:80]


def memory_status() -> Dict[str, Any]:
    now = time.time()
    return {
        "mem0": {
            "configured": bool(_mem0_key()),
            "active": bool(_mem0_key()) and now >= _mem0_disabled_until,
        },
        "letta": {
            "configured": bool(_letta_key()),
            "active": bool(_letta_key()) and now >= _letta_disabled_until,
        },
    }


# ───────────────────────────── Mem0 layer ─────────────────────────────

async def _mem0_add(user_key: str, messages: List[Dict[str, str]],
                    mem_type: str, metadata: Dict[str, Any]) -> bool:
    global _mem0_disabled_until
    key = _mem0_key()
    if not key or time.time() < _mem0_disabled_until:
        return False
    payload = {
        "messages": messages,
        "user_id": _safe_key(user_key),
        "metadata": {"source": "stew", "memory_type": mem_type, **(metadata or {})},
    }
    try:
        async with httpx.AsyncClient(timeout=25) as client:
            r = await client.post(
                f"{MEM0_BASE}/v3/memories/add/",
                headers={"Authorization": f"Token {key}", "Content-Type": "application/json"},
                json=payload,
            )
        if r.status_code in (401, 402, 403, 429):
            _mem0_disabled_until = time.time() + _MEM0_COOLDOWN
            logger.warning(f"Mem0 quota/auth issue ({r.status_code}) — falling back to Letta for 1h")
            return False
        return r.status_code in (200, 201, 202)
    except Exception as e:
        logger.warning(f"Mem0 add failed (transient): {e}")
        return False


async def _mem0_search(user_key: str, query: str, top_k: int = 8) -> List[str]:
    global _mem0_disabled_until
    key = _mem0_key()
    if not key or time.time() < _mem0_disabled_until:
        return []
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.post(
                f"{MEM0_BASE}/v3/memories/search/",
                headers={"Authorization": f"Token {key}", "Content-Type": "application/json"},
                json={"query": query[:500], "filters": {"user_id": _safe_key(user_key)},
                      "top_k": max(1, min(top_k, 20))},
            )
        if r.status_code in (401, 402, 403, 429):
            _mem0_disabled_until = time.time() + _MEM0_COOLDOWN
            logger.warning(f"Mem0 search hit limits ({r.status_code}) — using Letta")
            return []
        if r.status_code != 200:
            return []
        data = r.json()
        return [m.get("memory", "") for m in data.get("results", []) if m.get("memory")]
    except Exception as e:
        logger.warning(f"Mem0 search failed: {e}")
        return []


# ───────────────────────────── Letta layer ─────────────────────────────

def _block_label(user_key: str) -> str:
    return f"stew_{_safe_key(user_key)[:60]}"


async def _letta_request(method: str, path: str, json_body: Optional[dict] = None) -> Optional[httpx.Response]:
    global _letta_disabled_until
    key = _letta_key()
    if not key or time.time() < _letta_disabled_until:
        return None
    try:
        async with httpx.AsyncClient(timeout=25) as client:
            r = await client.request(
                method, f"{LETTA_BASE}{path}",
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json=json_body,
            )
        if r.status_code in (401, 403, 429):
            _letta_disabled_until = time.time() + _MEM0_COOLDOWN
            logger.warning(f"Letta auth/quota issue ({r.status_code}) — Letta paused 1h")
            return None
        return r
    except Exception as e:
        logger.warning(f"Letta request failed: {e}")
        return None


async def _letta_get_block(block_label: str) -> Optional[Dict[str, Any]]:
    r = await _letta_request("GET", f"/v1/blocks?label={block_label}")
    if r is None or r.status_code != 200:
        return None
    try:
        data = r.json()
        blocks = data if isinstance(data, list) else data.get("blocks", [])
        for b in blocks:
            if b.get("label") == block_label:
                return b
    except Exception:
        pass
    return None


def _trim_to_budget(value: str, budget: int) -> str:
    """Keep the newest memory lines within the block's char budget."""
    value = value.strip()
    if len(value) <= budget:
        return value
    lines = value.splitlines()
    kept: List[str] = []
    total = 0
    for line in reversed(lines):  # newest lines are at the end
        if total + len(line) + 1 > budget:
            break
        kept.append(line)
        total += len(line) + 1
    return "\n".join(reversed(kept))


async def letta_append(user_key: str, fact: str, mem_type: str = "fact") -> bool:
    """Append one fact line to the user's Letta memory block (create if needed)."""
    fact = (fact or "").strip()
    if not fact:
        return False
    label = _block_label(user_key)
    stamp = time.strftime("%Y-%m-%d")
    line = f"[{stamp}] ({mem_type}) {fact}"[:600]
    block = await _letta_get_block(label)
    if block:
        new_value = f"{(block.get('value') or '').rstrip()}\n{line}".strip()
        new_value = _trim_to_budget(new_value, _LETTA_MAX_APPEND)
        r = await _letta_request("PATCH", f"/v1/blocks/{block['id']}", {"value": new_value})
        return bool(r and r.status_code in (200, 201))
    r = await _letta_request("POST", "/v1/blocks", {
        "label": label,
        "description": "S.T.E.W durable long-term memory for this user (mirrors Mem0).",
        "value": line,
        "limit": _LETTA_BLOCK_LIMIT,
    })
    return bool(r and r.status_code in (200, 201))


async def letta_profile(user_key: str) -> str:
    """Return the raw Letta memory-block content for this user."""
    block = await _letta_get_block(_block_label(user_key))
    return (block or {}).get("value", "") or ""


def _letta_keyword_recall(profile: str, query: str, max_lines: int = 20) -> List[str]:
    """Local keyword scoring over the Letta block when Mem0 is unavailable."""
    q_words = {w for w in re.findall(r"[a-zA-Z0-9']{4,}", query.lower())}
    scored = []
    for line in profile.splitlines():
        if not line.strip():
            continue
        l_words = {w for w in re.findall(r"[a-zA-Z0-9']{4,}", line.lower())}
        score = len(q_words & l_words)
        scored.append((score, line))
    scored.sort(key=lambda x: -x[0])
    return [line for score, line in scored[:max_lines] if line.strip()]


# ─────────────────────── Public gateway API ───────────────────────────

async def save_memory(user_key: str, content: str, mem_type: str = "fact",
                      assistant_reply: str = "",
                      metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Save a memory to BOTH providers. Never raises.
    mem_type: preference|fact|intent|activity|thought|conversation|workflow|
              document|video|audio|file
    """
    content = (content or "").strip()
    if not content:
        return {"saved": False}
    messages = [{"role": "user", "content": content[:4000]}]
    if assistant_reply:
        messages.append({"role": "assistant", "content": assistant_reply[:4000]})
    mem0_ok, letta_ok = await asyncio.gather(
        _mem0_add(user_key, messages, mem_type, metadata or {}),
        letta_append(user_key, content, mem_type),
    )
    return {"saved": mem0_ok or letta_ok, "mem0": mem0_ok, "letta": letta_ok}


async def save_conversation_turn(user_key: str, user_text: str, assistant_text: str,
                                 platform: str = "telegram") -> None:
    """Best-effort background save of a whole conversation turn."""
    try:
        await save_memory(
            user_key, user_text[:2000], mem_type="conversation",
            assistant_reply=assistant_text[:2000],
            metadata={"platform": platform},
        )
    except Exception as e:
        logger.debug(f"save_conversation_turn skipped: {e}")


async def recall(user_key: str, query: str, top_k: int = 8) -> Dict[str, Any]:
    """Recall memories for a query. Tries Mem0 first; falls back to the Letta
    block (keyword-scored). Returns {'memories': [...], 'provider': str}."""
    mem0_hits = await _mem0_search(user_key, query, top_k)
    if mem0_hits:
        return {"memories": mem0_hits, "provider": "mem0"}
    profile = await letta_profile(user_key)
    if profile:
        hits = _letta_keyword_recall(profile, query)
        if hits:
            return {"memories": hits, "provider": "letta"}
    return {"memories": [], "provider": "none"}


async def build_recall_context(user_key: str, query: str, top_k: int = 8) -> str:
    """Ready-to-inject LONG-TERM MEMORY context block for the system prompt."""
    try:
        res = await recall(user_key, query, top_k)
        if not res["memories"]:
            return ""
        lines = [f"- {m[:300]}" for m in res["memories"][:10]]
        return ("\n\nLONG-TERM MEMORY (from " + res["provider"] + " — reference these "
                "naturally, never mention the storage systems):\n" + "\n".join(lines))
    except Exception as e:
        logger.debug(f"build_recall_context skipped: {e}")
        return ""


async def full_profile_context(user_key: str, max_chars: int = 2500) -> str:
    """The always-visible user profile (Letta block) — injected in every reply."""
    try:
        profile = await letta_profile(user_key)
        if not profile:
            return ""
        if len(profile) > max_chars:
            profile = profile[-max_chars:]
        return "\n\nUSER PROFILE MEMORY (accumulated across all past chats):\n" + profile
    except Exception as e:
        logger.debug(f"full_profile_context skipped: {e}")
        return ""
