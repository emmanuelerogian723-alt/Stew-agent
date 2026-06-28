"""
S.T.E.W Memory — conversation history stored in PostgreSQL.
"""
import logging
from datetime import datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from server.models import Conversation

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


def build_llm_messages(conversation: Conversation, system_prompt: str) -> list[dict]:
    """Build the messages list for LLM API call from conversation history."""
    msgs = [{"role": "system", "content": system_prompt}]
    for m in (conversation.messages or []):
        if m.get("role") in ("user", "assistant"):
            msgs.append({"role": m["role"], "content": m["content"]})
    return msgs


async def list_conversations(db: AsyncSession, user_id: str) -> list[Conversation]:
    result = await db.execute(
        select(Conversation)
        .where(Conversation.user_id == user_id)
        .order_by(Conversation.updated_at.desc())
        .limit(50)
    )
    return list(result.scalars().all())
