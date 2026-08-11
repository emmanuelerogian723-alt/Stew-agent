"""
S.T.E.W Memory — conversation history stored in PostgreSQL + ChromaDB vector memory.
"""
import logging
from datetime import datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from server.models import Conversation
from server.vector_memory import store_memory, recall_relevant, format_memories_for_prompt

logger = logging.getLogger(__name__)

MAX_MESSAGES_PER_CONVERSATION = 100


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
        # Keep system message (if any) + last N messages
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
    """Build the messages list for LLM API call from conversation history + vector memory."""
    full_system = system_prompt
    if recalled_memories:
        full_system += recalled_memories
    msgs = [{"role": "system", "content": full_system}]
    for m in (conversation.messages or []):
        if m.get("role") in ("user", "assistant"):
            msgs.append({"role": m["role"], "content": m["content"]})
    return msgs


async def get_relevant_context(user_id: str, query: str, platform: str = "api") -> str:
    """Retrieve semantically relevant past memories for the current query."""
    try:
        memories = recall_relevant(user_id, query, platform=platform)
        return format_memories_for_prompt(memories)
    except Exception as e:
        logger.warning(f"Vector recall failed (non-fatal): {e}")
        return ""


async def list_conversations(db: AsyncSession, user_id: str) -> list[Conversation]:
    result = await db.execute(
        select(Conversation)
        .where(Conversation.user_id == user_id)
        .order_by(Conversation.updated_at.desc())
        .limit(50)
    )
    return list(result.scalars().all())
