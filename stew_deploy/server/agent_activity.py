"""Audit trail and human approval controls for STEW connected-app actions."""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional
from sqlalchemy import func, select

from server.database import AsyncSessionLocal
from server.models import AgentActivity, PendingAgentAction

# Conservative by design: if an action name looks like it changes the outside
# world, STEW prepares it and waits for the user to approve it.
_WRITE_WORDS = {
    "SEND", "REPLY", "POST", "PUBLISH", "CREATE", "UPDATE", "EDIT", "DELETE",
    "REMOVE", "UPLOAD", "INVITE", "ADD", "CANCEL", "ARCHIVE", "MOVE", "COPY",
    "SUBSCRIBE", "UNSUBSCRIBE", "FOLLOW", "UNFOLLOW", "LIKE", "COMMENT", "RATE",
    "PAY", "PURCHASE", "TRANSFER", "REFUND", "ISSUE", "MERGE", "CLOSE", "OPEN",
    "SCHEDULE", "BOOK", "START", "STOP", "TRIGGER", "RUN", "WRITE", "MODIFY",
}
_READ_WORDS = {
    "GET", "LIST", "FETCH", "SEARCH", "FIND", "READ", "CHECK", "LOOKUP", "QUERY",
    "VIEW", "DESCRIBE", "RETRIEVE", "ANALYZE", "ANALYTICS", "STATISTICS", "STATUS",
}

# A write that lands on something OTHER people can see (a public post, a
# published video, a broadcast) is a different risk tier than a private write
# (send one email, update your own calendar). Claude/ChatGPT-style connectors
# gate exactly this: irreversible-and-visible actions get a real pause.
_PUBLIC_WORDS = {
    "PUBLISH", "TWEET", "POST", "BROADCAST", "ANNOUNCE", "RETWEET", "SHARE",
}
_PRIVACY_WORDS = {"PRIVACY", "VISIBILITY"}  # e.g. UPDATE_VIDEO with privacyStatus -> public


# Toolkits whose whole point is public, follower-visible content, so an
# upload there is a publish even though "UPLOAD" isn't a public verb by itself.
_PUBLIC_TOOLKIT_PREFIXES = (
    "youtube", "twitter", "x", "instagram", "tiktok", "facebook",
    "linkedin", "pinterest", "threads", "reddit", "medium", "substack",
)


def is_public_action(tool_slug: str, arguments: Optional[Dict[str, Any]] = None) -> bool:
    """True if this write is likely to become visible to people other than
    the user themselves (posting, publishing, broadcasting)."""
    words = set(re.findall(r"[A-Z0-9]+", (tool_slug or "").upper()))
    if words & _PUBLIC_WORDS:
        return True
    slug_l = (tool_slug or "").lower()
    # An upload into a public-facing platform publishes content even though
    # "upload" by itself (e.g. to Google Drive) is a private write.
    if "UPLOAD" in words and slug_l.startswith(_PUBLIC_TOOLKIT_PREFIXES):
        return True
    # "update video" etc only goes public if the args actually flip something
    # to a public-facing value — otherwise it is just a private metadata edit.
    if isinstance(arguments, dict) and arguments:
        blob = json.dumps(arguments, default=str).lower()
        keys = " ".join(str(k) for k in arguments.keys()).lower()
        if "public" in blob and ("privacy" in keys or "visibility" in keys
                                 or "privacy" in blob or "visibility" in blob):
            return True
    return False


def toolkit_from_slug(tool_slug: str) -> str:
    return (tool_slug or "unknown").split("_", 1)[0].lower()


def is_write_action(tool_slug: str) -> bool:
    words = set(re.findall(r"[A-Z0-9]+", (tool_slug or "").upper()))
    if words & _WRITE_WORDS:
        return True
    if words & _READ_WORDS:
        return False
    # Unknown actions require approval. False negatives are more dangerous than
    # one extra confirmation for a rare provider action.
    return True


def action_summary(tool_slug: str, arguments: Dict[str, Any]) -> str:
    action = (tool_slug or "app action").replace("_", " ").title()
    safe = {k: v for k, v in (arguments or {}).items() if not any(x in k.lower() for x in ("token", "secret", "password", "key"))}
    preview = json.dumps(safe, ensure_ascii=False, default=str)
    if len(preview) > 700:
        preview = preview[:697] + "..."
    return f"{action} with: {preview}"


async def queue_approval(user_id: str, tool_slug: str, arguments: Dict[str, Any], account: Optional[str] = None) -> PendingAgentAction:
    toolkit = toolkit_from_slug(tool_slug)
    row = PendingAgentAction(
        telegram_user_id=str(user_id), toolkit=toolkit, tool_slug=tool_slug,
        arguments=arguments or {}, account=account, summary=action_summary(tool_slug, arguments or {}),
        expires_at=datetime.utcnow() + timedelta(minutes=20),
    )
    async with AsyncSessionLocal() as db:
        db.add(row)
        await db.commit(); await db.refresh(row)
        return row


async def pending_dashboard(user_id: str, toolkit: Optional[str] = None) -> list[Dict[str, Any]]:
    """Only unexpired, user-owned approval requests; never expose raw arguments."""
    async with AsyncSessionLocal() as db:
        q = select(PendingAgentAction).where(
            PendingAgentAction.telegram_user_id == str(user_id),
            PendingAgentAction.status == "pending",
            PendingAgentAction.expires_at > datetime.utcnow(),
        )
        if toolkit:
            q = q.where(PendingAgentAction.toolkit == toolkit.lower())
        rows = (await db.execute(q.order_by(PendingAgentAction.created_at.desc()).limit(30))).scalars().all()
        return [{"id": r.id, "toolkit": r.toolkit, "tool_slug": r.tool_slug,
                 "summary": r.summary, "expires_at": r.expires_at.isoformat() + "Z",
                 "created_at": r.created_at.isoformat() + "Z" if r.created_at else None}
                for r in rows]


async def latest_pending(user_id: str) -> Optional[PendingAgentAction]:
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(PendingAgentAction).where(
            PendingAgentAction.telegram_user_id == str(user_id),
            PendingAgentAction.status == "pending",
            PendingAgentAction.expires_at > datetime.utcnow(),
        ).order_by(PendingAgentAction.created_at.desc()).limit(1))
        return result.scalars().first()


async def get_pending(user_id: str, action_id: Optional[str] = None) -> Optional[PendingAgentAction]:
    async with AsyncSessionLocal() as db:
        q = select(PendingAgentAction).where(
            PendingAgentAction.telegram_user_id == str(user_id),
            PendingAgentAction.status == "pending",
            PendingAgentAction.expires_at > datetime.utcnow(),
        )
        if action_id:
            q = q.where(PendingAgentAction.id == action_id)
        q = q.order_by(PendingAgentAction.created_at.desc()).limit(1)
        result = await db.execute(q)
        return result.scalars().first()


async def decide_pending(user_id: str, action_id: str, status: str) -> None:
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(PendingAgentAction).where(
            PendingAgentAction.id == action_id,
            PendingAgentAction.telegram_user_id == str(user_id),
        ))
        row = result.scalars().first()
        if row:
            row.status = status; row.decided_at = datetime.utcnow(); await db.commit()


async def record_activity(user_id: str, tool_slug: str, status: str, arguments: Dict[str, Any], result: Any = None, log_id: Optional[str] = None, approval_required: bool = False) -> None:
    preview = result if isinstance(result, dict) else {"value": str(result)[:1000]} if result is not None else None
    if isinstance(preview, dict):
        preview = json.loads(json.dumps(preview, default=str)[:5000]) if len(json.dumps(preview, default=str)) <= 5000 else {"summary": str(preview)[:4800]}
    row = AgentActivity(
        telegram_user_id=str(user_id), toolkit=toolkit_from_slug(tool_slug), tool_slug=tool_slug,
        status=status, read_only=not is_write_action(tool_slug), approval_required=approval_required,
        log_id=log_id, summary=action_summary(tool_slug, arguments), arguments=arguments or {}, result_preview=preview,
    )
    async with AsyncSessionLocal() as db:
        db.add(row); await db.commit()


async def activity_dashboard(user_id: str, toolkit: Optional[str] = None, limit: int = 30) -> Dict[str, Any]:
    limit = max(1, min(int(limit or 30), 100))
    async with AsyncSessionLocal() as db:
        q = select(AgentActivity).where(AgentActivity.telegram_user_id == str(user_id))
        if toolkit:
            q = q.where(AgentActivity.toolkit == toolkit.lower())
        rows = (await db.execute(q.order_by(AgentActivity.created_at.desc()).limit(limit))).scalars().all()
        items = [{
            "id": r.id, "toolkit": r.toolkit, "tool_slug": r.tool_slug, "status": r.status,
            "read_only": r.read_only, "approval_required": r.approval_required,
            "summary": r.summary, "log_id": r.log_id,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        } for r in rows]
        return {
            "total": len(items), "completed": sum(x["status"] == "completed" for x in items),
            "failed": sum(x["status"] == "failed" for x in items),
            "awaiting_approval": sum(x["status"] == "awaiting_approval" for x in items),
            "items": items,
        }
