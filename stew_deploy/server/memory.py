"""
S.T.E.W Memory — strong, persistent conversation memory.
Primary store: PostgreSQL (UserMemory table) — survives Render restarts.
Secondary: numpy vector memory for semantic recall (ephemeral fallback).
Conversation history: PostgreSQL Conversation.messages JSON column.

Key design decisions:
- DB memories are the source of truth — they survive Render restarts/redeploys.
- Vector memory (/tmp) is a cache that may be wiped; never the sole source.
- "Core memories" (importance >= 8) are ALWAYS injected, not just keyword-matched.
- Deduplication: similar content is updated, not duplicated.
- Memory extraction runs as a fire-and-forget task (non-blocking).
"""
import logging
import re
from datetime import datetime
from typing import Optional

from sqlalchemy import select, desc, and_, or_, text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession

from server.models import Conversation, UserMemory
from server.vector_memory import store_memory, recall_relevant, format_memories_for_prompt

logger = logging.getLogger(__name__)

MAX_MESSAGES_PER_CONVERSATION = 120
MAX_MEMORIES_PER_USER = 300
CORE_MEMORY_THRESHOLD = 8


async def get_or_create_conversation(
    db: AsyncSession,
    user_id: str,
    conversation_id: Optional[str] = None,
) -> Conversation:
    if conversation_id:
        result = await db.execute(
            select(Conversation).where(
                Conversation.id == conversation_id,
                Conversation.user_id == user_id,
            )
        )
        conv = result.scalar_one_or_none()
        if conv:
            return conv

    conv = Conversation(
        user_id=user_id,
        messages=[],
        title=f"Conversation {datetime.utcnow().strftime('%Y-%m-%d %H:%M')}",
    )
    db.add(conv)
    await db.flush()
    return conv


async def append_message(
    db: AsyncSession,
    conversation: Conversation,
    role: str,
    content: str,
    platform: str = "api",
) -> None:
    messages = list(conversation.messages or [])
    messages.append({
        "role": role,
        "content": content,
        "timestamp": datetime.utcnow().isoformat(),
    })
    if len(messages) > MAX_MESSAGES_PER_CONVERSATION:
        messages = messages[-MAX_MESSAGES_PER_CONVERSATION:]
    conversation.messages = messages
    await db.flush()

    try:
        store_memory(
            user_id=conversation.user_id,
            role=role,
            content=content,
            platform=platform,
            conversation_id=conversation.id,
        )
    except Exception as e:
        logger.warning(f"Vector memory store failed (non-fatal): {e}")


def build_llm_messages(
    conversation: Conversation,
    system_prompt: str,
    recalled_memories: str = "",
) -> list[dict]:
    full_system = system_prompt
    if recalled_memories:
        full_system += recalled_memories
    msgs = [{"role": "system", "content": full_system}]
    for m in (conversation.messages or []):
        if m.get("role") in ("user", "assistant"):
            msgs.append({"role": m["role"], "content": m["content"]})
    return msgs


# ── DATABASE-BACKED PERSISTENT MEMORY ─────────────────────────────────────────

async def store_user_memory(
    db: AsyncSession,
    user_id: str,
    category: str,
    content: str,
    importance: int = 5,
    platform: str = "telegram",
    conversation_id: Optional[str] = None,
) -> UserMemory:
    """Store a persistent memory fact in PostgreSQL.
    Deduplicates: if a similar memory exists (same category, overlapping content),
    updates it instead of creating a duplicate."""
    content_clean = content[:2000].strip()
    if not content_clean:
        return None

    # Check for duplicates — same category + significant content overlap
    existing = await db.execute(
        select(UserMemory).where(
            and_(
                UserMemory.user_id == user_id,
                UserMemory.category == category,
                UserMemory.is_active == True,
                UserMemory.content.ilike(f"%{content_clean[:80]}%"),
            )
        ).limit(1)
    )
    existing_mem = existing.scalar_one_or_none()

    if existing_mem:
        existing_mem.content = content_clean
        existing_mem.importance = max(existing_mem.importance, min(importance, 10))
        existing_mem.updated_at = datetime.utcnow()
        await db.flush()
        return existing_mem

    # Cap total memories — if at limit, remove lowest-importance old ones
    count_result = await db.execute(
        select(UserMemory).where(
            and_(UserMemory.user_id == user_id, UserMemory.is_active == True)
        )
    )
    all_mems = list(count_result.scalars().all())
    if len(all_mems) >= MAX_MEMORIES_PER_USER:
        all_mems.sort(key=lambda m: (m.importance, m.updated_at))
        to_remove = all_mems[:len(all_mems) - MAX_MEMORIES_PER_USER + 1]
        for m in to_remove:
            m.is_active = False

    mem = UserMemory(
        user_id=user_id,
        category=category,
        content=content_clean,
        importance=max(1, min(importance, 10)),
        source_platform=platform,
        conversation_id=conversation_id,
    )
    db.add(mem)
    await db.flush()
    return mem


async def get_user_memories(
    db: AsyncSession,
    user_id: str,
    limit: int = 30,
    active_only: bool = True,
) -> list[UserMemory]:
    query = select(UserMemory).where(UserMemory.user_id == user_id)
    if active_only:
        query = query.where(UserMemory.is_active == True)
    query = query.order_by(desc(UserMemory.importance), desc(UserMemory.updated_at)).limit(limit)
    result = await db.execute(query)
    return list(result.scalars().all())


async def get_core_memories(db: AsyncSession, user_id: str, limit: int = 15) -> list[UserMemory]:
    """Get 'core' memories — high-importance facts that should ALWAYS be in context,
    regardless of keyword matching. These are the things Stew must never forget."""
    result = await db.execute(
        select(UserMemory).where(
            and_(
                UserMemory.user_id == user_id,
                UserMemory.is_active == True,
                UserMemory.importance >= CORE_MEMORY_THRESHOLD,
            )
        ).order_by(desc(UserMemory.importance), desc(UserMemory.updated_at)).limit(limit)
    )
    return list(result.scalars().all())


async def search_user_memories(
    db: AsyncSession,
    user_id: str,
    query_text: str,
    limit: int = 10,
) -> list[UserMemory]:
    keywords = [k.lower() for k in query_text.split() if len(k) > 2]
    if not keywords:
        return await get_user_memories(db, user_id, limit=limit)

    result = await db.execute(
        select(UserMemory).where(
            and_(
                UserMemory.user_id == user_id,
                UserMemory.is_active == True,
                or_(*[UserMemory.content.ilike(f"%{kw}%") for kw in keywords])
            )
        ).order_by(desc(UserMemory.importance), desc(UserMemory.updated_at)).limit(limit)
    )
    memories = list(result.scalars().all())

    if not memories:
        return await get_user_memories(db, user_id, limit=limit)

    return memories


async def get_relevant_context(db: AsyncSession, user_id: str, query: str, platform: str = "api") -> str:
    """Retrieve semantically relevant past context for the current query.
    Combines: core memories (always) + keyword-matched DB memories + vector recall."""
    parts = []

    core_mems = []
    # 1. Core memories — ALWAYS injected (high importance, must never be forgotten)
    try:
        core_mems = await get_core_memories(db, user_id, limit=15)
        if core_mems:
            lines = ["\n\n=== CORE MEMORIES — always remember these ==="]
            for i, m in enumerate(core_mems, 1):
                lines.append(f"{i}. [{m.category.upper()}] (importance: {m.importance}/10) {m.content[:300]}")
            lines.append("=== END CORE MEMORIES ===\n")
            parts.append("\n".join(lines))
    except Exception as e:
        logger.warning(f"Core memory recall failed (non-fatal): {e}")

    # 2. Keyword-matched DB memories (survives Render restarts)
    try:
        db_memories = await search_user_memories(db, user_id, query, limit=15)
        if core_mems:
            core_ids = {m.id for m in core_mems}
            db_memories = [m for m in db_memories if m.id not in core_ids]
        if db_memories:
            lines = ["\n=== RECALLED MEMORIES (relevant to current message) ==="]
            for i, m in enumerate(db_memories, 1):
                lines.append(f"{i}. [{m.category.upper()}] {m.content[:300]}")
            lines.append("=== END RECALLED MEMORIES ===\n")
            parts.append("\n".join(lines))
    except Exception as e:
        logger.warning(f"DB memory recall failed (non-fatal): {e}")

    # 3. Vector recall (ephemeral, may be empty after restart)
    try:
        vec_memories = recall_relevant(user_id, query, platform=platform)
        vec_text = format_memories_for_prompt(vec_memories)
        if vec_text:
            parts.append(vec_text)
    except Exception as e:
        logger.warning(f"Vector recall failed (non-fatal): {e}")

    # 4. Explicit instruction for the LLM to USE these memories
    if parts:
        parts.append(
            "\n=== MEMORY INSTRUCTIONS ===\n"
            "The memories above are REAL facts about this user that you have learned across "
            "conversations. USE them naturally in your response — reference them, build on them, "
            "and act on them. If a memory contradicts what the user just said, trust the user's "
            "current message. Never say 'I see from my memory' — just use the information naturally.\n"
        )

    return "\n".join(parts)


def _safe_content(result) -> str:
    """Defensively extract text from an LLM callback result."""
    if isinstance(result, dict):
        inner = result.get("content", "")
        if isinstance(inner, dict):
            inner = inner.get("content", "")
        return inner if isinstance(inner, str) else str(inner) if inner else ""
    if isinstance(result, str):
        return result
    return str(result) if result else ""


async def extract_and_store_memories(
    db: AsyncSession,
    user_id: str,
    user_message: str,
    assistant_reply: str,
    platform: str = "telegram",
    conversation_id: Optional[str] = None,
    llm_chat_fn=None,
) -> None:
    """Use the LLM to extract important facts from a conversation exchange and store them.
    Runs in background — non-blocking. Deduplicates against existing memories."""
    try:
        if not llm_chat_fn:
            return

        extract_prompt = (
            "You are a memory extraction engine for an AI assistant called S.T.E.W. "
            "Analyze the following conversation exchange and extract ALL important, durable "
            "facts worth remembering about the user for future conversations.\n\n"
            "Extract aggressively — better to store too much than too little. Focus on:\n"
            "- Personal details (name, location, age, family, pets, work)\n"
            "- Preferences (likes, dislikes, tastes, habits)\n"
            "- Goals and projects (what they are building, deadlines, aspirations)\n"
            "- Relationships (partner, friends, colleagues, children names)\n"
            "- Standing instructions (things they want done a certain way)\n"
            "- Technical context (tools they use, accounts, APIs, frameworks)\n"
            "- Emotional context (what excites them, what worries them)\n"
            "- Recurring topics (things they bring up repeatedly)\n\n"
            "Return ONLY a JSON array of objects with:\n"
            "  'category': one of 'fact', 'preference', 'instruction', 'context', 'relationship', 'goal', 'project'\n"
            "  'content': the memory text, max 200 chars, written as a clear factual statement\n"
            "  'importance': 1-10 (10 = critical/always remember, 1 = minor detail)\n"
            "    Guidelines: name/location = 9, major projects = 8, preferences = 5, "
            "minor context = 3, emotional state = 4\n"
            "If nothing worth remembering, return []."
        )

        exchange = f"User: {user_message[:800]}\nAssistant: {assistant_reply[:800]}"
        result = llm_chat_fn(
            [{"role": "system", "content": extract_prompt},
             {"role": "user", "content": exchange}],
            max_tokens=1500
        )

        raw = _safe_content(result)

        import json as _json
        json_match = re.search(r'\[.*\]', raw, re.DOTALL)
        if not json_match:
            return

        memories = _json.loads(json_match.group())
        if not isinstance(memories, list):
            return

        stored_count = 0
        for m in memories[:8]:
            if isinstance(m, dict) and m.get("content"):
                stored = await store_user_memory(
                    db, user_id,
                    category=m.get("category", "context"),
                    content=m["content"],
                    importance=int(m.get("importance", 5)),
                    platform=platform,
                    conversation_id=conversation_id,
                )
                if stored:
                    stored_count += 1

        if stored_count:
            logger.info(f"Stored/updated {stored_count} memories for user {user_id}")
    except Exception as e:
        logger.warning(f"Memory extraction failed (non-fatal): {e}")


async def list_conversations(db: AsyncSession, user_id: str) -> list[Conversation]:
    result = await db.execute(
        select(Conversation)
        .where(Conversation.user_id == user_id)
        .order_by(Conversation.updated_at.desc())
        .limit(50)
    )
    return list(result.scalars().all())
