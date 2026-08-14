"""
S.T.E.W Memory — strong, persistent conversation memory.
Primary store: PostgreSQL (UserMemory table) — survives Render restarts.
Secondary: ChromaDB/numpy vector memory for semantic recall (ephemeral fallback).
Conversation history: PostgreSQL Conversation.messages JSON column.
"""
import logging
import re
from datetime import datetime
from typing import Optional

from sqlalchemy import select, desc, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession

from server.models import Conversation, UserMemory
from server.vector_memory import store_memory, recall_relevant, format_memories_for_prompt

logger = logging.getLogger(__name__)

MAX_MESSAGES_PER_CONVERSATION = 80  # per-conversation window
MAX_MEMORIES_PER_USER = 200          # cap in DB


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
    # Trim old messages to keep context manageable
    if len(messages) > MAX_MESSAGES_PER_CONVERSATION:
        messages = messages[-MAX_MESSAGES_PER_CONVERSATION:]
    conversation.messages = messages
    await db.flush()

    # Also store in vector memory for semantic recall across sessions
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
    """Build the messages list for LLM API call from conversation history + memory."""
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
    """Store a persistent memory fact in PostgreSQL."""
    mem = UserMemory(
        user_id=user_id,
        category=category,
        content=content[:2000],
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
    """Retrieve all persistent memories for a user, most important first."""
    query = select(UserMemory).where(UserMemory.user_id == user_id)
    if active_only:
        query = query.where(UserMemory.is_active == True)
    query = query.order_by(desc(UserMemory.importance), desc(UserMemory.updated_at)).limit(limit)
    result = await db.execute(query)
    return list(result.scalars().all())


async def search_user_memories(
    db: AsyncSession,
    user_id: str,
    query_text: str,
    limit: int = 10,
) -> list[UserMemory]:
    """Search persistent memories by keyword matching."""
    # Simple keyword search — no external dependencies, works on any PostgreSQL
    keywords = [k.lower() for k in query_text.split() if len(k) > 2]
    if not keywords:
        # Fall back to getting most important memories
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

    # If keyword search found nothing, fall back to most important memories
    if not memories:
        return await get_user_memories(db, user_id, limit=limit)

    return memories


async def get_relevant_context(db: AsyncSession, user_id: str, query: str, platform: str = "api") -> str:
    """Retrieve semantically relevant past context for the current query.
    Combines persistent DB memories (survives restarts) with ephemeral vector recall.
    """
    parts = []

    # 1. Persistent DB memories (always available, survives Render restarts)
    try:
        db_memories = await search_user_memories(db, user_id, query, limit=15)
        if db_memories:
            lines = ["\n\n=== WHAT YOU KNOW ABOUT THIS USER (persistent memory) ==="]
            for i, m in enumerate(db_memories, 1):
                lines.append(f"{i}. [{m.category.upper()}] {m.content[:300]}")
            lines.append("=== END USER MEMORY ===\n")
            parts.append("\n".join(lines))
    except Exception as e:
        logger.warning(f"DB memory recall failed (non-fatal): {e}")

    # 2. Vector recall (ephemeral, may be empty after restart)
    try:
        vec_memories = recall_relevant(user_id, query, platform=platform)
        vec_text = format_memories_for_prompt(vec_memories)
        if vec_text:
            parts.append(vec_text)
    except Exception as e:
        logger.warning(f"Vector recall failed (non-fatal): {e}")

    return "\n".join(parts)


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
    Runs in background — non-blocking."""
    try:
        if not llm_chat_fn:
            return

        extract_prompt = (
            "You are a memory extraction engine. Analyze the following conversation exchange "
            "and extract any important, durable facts worth remembering about the user. "
            "Focus on: personal details, preferences, goals, projects, deadlines, relationships, "
            "instructions, and recurring topics. Ignore small talk, greetings, and transient questions.\n\n"
            "Return ONLY a JSON array of objects with 'category' (one of: fact, preference, instruction, context) "
            "and 'content' (the memory text, max 200 chars). If nothing worth remembering, return []."
        )

        exchange = f"User: {user_message[:500]}\nAssistant: {assistant_reply[:500]}"
        result = llm_chat_fn(
            [{"role": "system", "content": extract_prompt},
             {"role": "user", "content": exchange}],
            max_tokens=1000
        )

        raw = result.get("content", "") if isinstance(result, dict) else str(result)

        # Parse JSON array
        import json as _json
        json_match = re.search(r'\[.*\]', raw, re.DOTALL)
        if not json_match:
            return

        memories = _json.loads(json_match.group())
        if not isinstance(memories, list):
            return

        for m in memories[:5]:  # Cap at 5 memories per exchange
            if isinstance(m, dict) and m.get("content"):
                await store_user_memory(
                    db, user_id,
                    category=m.get("category", "context"),
                    content=m["content"],
                    importance=6 if m.get("category") in ("fact", "instruction") else 4,
                    platform=platform,
                    conversation_id=conversation_id,
                )

        logger.info(f"Stored {len(memories[:5])} memories for user {user_id}")
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
