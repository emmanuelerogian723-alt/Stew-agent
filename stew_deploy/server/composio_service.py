"""Composio Platform integration for S.T.E.W.

Each S.T.E.W user gets an isolated Composio identity. Connections created for one
Telegram/API user are never shared with another user. The SDK reads
COMPOSIO_API_KEY from the environment; credentials are never stored here.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
from collections import OrderedDict
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_MAX_SESSIONS = 500
_sessions: "OrderedDict[str, Any]" = OrderedDict()
_client: Any = None
_lock = asyncio.Lock()


def is_configured() -> bool:
    """Return True when the server has a usable-looking Composio project key."""
    value = os.getenv("COMPOSIO_API_KEY", "").strip()
    return bool(value and not value.lower().startswith(("your_", "replace_", "changeme")))


def _safe_user_id(user_id: str | int | None) -> str:
    """Create a stable, Composio-safe identity without exposing personal data."""
    raw = str(user_id or "anonymous").strip()
    safe = re.sub(r"[^a-zA-Z0-9_-]", "_", raw)[:96]
    return f"stew_{safe or 'anonymous'}"


def _plain(value: Any) -> Any:
    """Convert Pydantic/SDK response objects into JSON-compatible values."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if hasattr(value, "model_dump"):
        return _plain(value.model_dump())
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_plain(v) for v in value]
    # Some SDK helper objects, including ConnectionRequest, expose public
    # attributes without being Pydantic models.
    if hasattr(value, "__dict__"):
        public = {k: v for k, v in vars(value).items() if not k.startswith("_")}
        if public:
            return _plain(public)
    return str(value)


def _get_client() -> Any:
    global _client
    if not is_configured():
        raise RuntimeError("Composio is not configured on this server")
    if _client is None:
        from composio import Composio

        # The SDK loads COMPOSIO_API_KEY from the environment.
        _client = Composio()
    return _client


def _create_session_sync(user_id: str) -> Any:
    client = _get_client()
    # Keep connection management available so users receive a Composio Connect
    # Link when an app needs OAuth. Disable remote sandbox tools because S.T.E.W
    # already has a controlled execution sandbox of its own.
    return client.sessions.create(
        user_id=_safe_user_id(user_id),
        manage_connections={"enable": True, "wait_for_connections": False},
        sandbox={"enable": False},
    )


async def get_session(user_id: str | int | None) -> Any:
    """Return a process-cached session scoped to one stable S.T.E.W user ID."""
    key = _safe_user_id(user_id)
    session = _sessions.get(key)
    if session is not None:
        _sessions.move_to_end(key)
        return session

    async with _lock:
        session = _sessions.get(key)
        if session is None:
            session = await asyncio.to_thread(_create_session_sync, key)
            _sessions[key] = session
            while len(_sessions) > _MAX_SESSIONS:
                _sessions.popitem(last=False)
        else:
            _sessions.move_to_end(key)
    return session


async def search_tools(user_id: str | int | None, query: str) -> Dict[str, Any]:
    """Discover real tool slugs and schemas for a user's requested task."""
    query = (query or "").strip()
    if not query:
        return {"success": False, "error": "Describe what you want the connected app to do."}
    session = await get_session(user_id)
    response = await asyncio.to_thread(session.search, query=query[:1000])
    data = _plain(response)
    # Bound the response passed back to the LLM while retaining plans, slugs,
    # schemas, connection status, and next-step guidance.
    schemas = data.get("tool_schemas") if isinstance(data, dict) else None
    if isinstance(schemas, dict):
        schemas = dict(list(schemas.items())[:10])
    elif isinstance(schemas, list):
        schemas = schemas[:10]
    else:
        schemas = []
    statuses = data.get("toolkit_connection_statuses") if isinstance(data, dict) else None
    if isinstance(statuses, dict):
        statuses = dict(list(statuses.items())[:20])
    elif isinstance(statuses, list):
        statuses = statuses[:20]
    else:
        statuses = []
    return {
        "success": bool(data.get("success", True)) if isinstance(data, dict) else True,
        "results": (data.get("results") or [])[:5] if isinstance(data, dict) else [],
        "tool_schemas": schemas,
        "toolkit_connection_statuses": statuses,
        "next_steps_guidance": data.get("next_steps_guidance", []) if isinstance(data, dict) else [],
        "error": data.get("error") if isinstance(data, dict) else None,
    }


async def connect_app(
    user_id: str | int | None,
    toolkit: str,
    callback_url: Optional[str] = None,
) -> Dict[str, Any]:
    """Create a managed Composio Connect Link for an app/toolkit."""
    toolkit = re.sub(r"[^a-z0-9_-]", "", (toolkit or "").strip().lower())
    if not toolkit:
        return {"success": False, "error": "Please name the app you want to connect."}
    session = await get_session(user_id)

    def _authorize() -> Any:
        kwargs: Dict[str, Any] = {}
        if callback_url:
            kwargs["callback_url"] = callback_url
        return session.authorize(toolkit, **kwargs)

    request = await asyncio.to_thread(_authorize)
    data = _plain(request)
    return {
        "success": True,
        "toolkit": toolkit,
        "status": data.get("status", "INITIATED") if isinstance(data, dict) else "INITIATED",
        "connect_url": data.get("redirect_url") if isinstance(data, dict) else None,
        "connected_account_id": data.get("id") if isinstance(data, dict) else None,
    }


async def list_connections(
    user_id: str | int | None,
    search: Optional[str] = None,
    connected_only: bool = False,
    next_cursor: Optional[str] = None,
    limit: int = 50,
    toolkits: Optional[list[str]] = None,
) -> Dict[str, Any]:
    """List app connection states for one S.T.E.W user."""
    session = await get_session(user_id)

    def _list() -> Any:
        return session.toolkits(
            toolkits=([re.sub(r"[^a-z0-9_-]", "", str(x).lower()) for x in toolkits[:50]] if toolkits else None),
            limit=max(1, min(int(limit or 50), 50)),
            next_cursor=(next_cursor or None),
            search=(search or None),
            is_connected=True if connected_only else None,
        )

    response = await asyncio.to_thread(_list)
    data = _plain(response)
    return {
        "success": True,
        "items": data.get("items", []) if isinstance(data, dict) else [],
        "next_cursor": data.get("next_cursor") if isinstance(data, dict) else None,
    }


async def execute_action(
    user_id: str | int | None,
    tool_slug: str,
    arguments: Optional[Dict[str, Any]] = None,
    account: Optional[str] = None,
    approved: bool = False,
) -> Dict[str, Any]:
    """Execute a discovered Composio tool in the current user's session."""
    slug = (tool_slug or "").strip().upper()
    if not slug:
        return {"success": False, "error": "A Composio tool slug is required."}
    if not isinstance(arguments, dict):
        return {"success": False, "error": "Tool arguments must be a JSON object."}

    from server.agent_activity import is_write_action, queue_approval, record_activity
    stable_user_id = str(user_id or "anonymous")
    # Consult provider behavior tags as well as conservative name classification.
    # This blocks mutating actions even if their names look read-only.
    toolkit = slug.split("_", 1)[0].lower()
    available = await list_app_actions(toolkit)
    action = next((item for item in available.get("items", []) if item["slug"] == slug and not item["deprecated"]), None)
    if not action:
        return {"success": False, "error": "Action is not available in the current Composio catalog."}
    requires_approval = is_write_action(slug) or action["permission"] != "read_only"
    connection = await list_connections(stable_user_id, toolkits=[toolkit])
    if not any(item.get("slug") == toolkit and (item.get("connection") or {}).get("is_active") for item in connection.get("items", [])):
        return {"success": False, "error": f"{toolkit} is not connected for this user. Connect it first."}
    if requires_approval and not approved:
        pending = await queue_approval(stable_user_id, slug, arguments, account)
        try:
            await record_activity(stable_user_id, slug, "awaiting_approval", arguments, approval_required=True)
        except Exception as audit_exc:
            logger.warning("Approval audit write failed: %s", audit_exc)
        return {
            "success": False,
            "approval_required": True,
            "approval_id": pending.id,
            "tool_slug": slug,
            "summary": pending.summary,
            "message": "I prepared this action but have not executed it. Reply APPROVE to continue or CANCEL to discard it.",
        }

    session = await get_session(user_id)

    def _execute() -> Any:
        kwargs: Dict[str, Any] = {"arguments": arguments or {}}
        if account:
            kwargs["account"] = account
        return session.execute(slug, **kwargs)

    try:
        response = await asyncio.to_thread(_execute)
        data = _plain(response)
        error = data.get("error") if isinstance(data, dict) else None
        provider_data = data.get("data") if isinstance(data, dict) else None
        if isinstance(data, dict) and data.get("successful") is False:
            error = error or "Provider reported the action was unsuccessful."
        if isinstance(provider_data, dict) and provider_data.get("successful") is False:
            error = error or provider_data.get("error") or "Provider reported failure."
        log_id = data.get("log_id") if isinstance(data, dict) else None
        result = {
            "success": not bool(error),
            "data": data.get("data") if isinstance(data, dict) else data,
            "error": error,
            "log_id": log_id,
            "tool_slug": slug,
        }
        try:
            await record_activity(stable_user_id, slug, "failed" if error else "completed", arguments, result.get("data"), log_id=log_id)
        except Exception as audit_exc:
            # Never turn a successful external action into an apparent failure,
            # because an automatic retry could duplicate an email or post.
            logger.warning("Completed action audit write failed: %s", audit_exc)
        return result
    except Exception as exc:
        try:
            await record_activity(stable_user_id, slug, "failed", arguments, {"error": str(exc)})
        except Exception as audit_exc:
            logger.warning("Failed action audit write also failed: %s", audit_exc)
        raise


async def approve_pending_action(user_id: str | int, action_id: Optional[str] = None) -> Dict[str, Any]:
    """Claim a pending action atomically; never replay a possibly completed send."""
    from sqlalchemy import update
    from server.database import AsyncSessionLocal
    from server.models import PendingAgentAction
    from server.agent_activity import get_pending, decide_pending
    pending = await get_pending(str(user_id), action_id)
    if not pending:
        return {"success": False, "error": "No unexpired action is waiting for approval."}
    async with AsyncSessionLocal() as db:
        claim = await db.execute(update(PendingAgentAction).where(
            PendingAgentAction.id == pending.id, PendingAgentAction.telegram_user_id == str(user_id),
            PendingAgentAction.status == "pending"
        ).values(status="executing"))
        await db.commit()
        if claim.rowcount != 1:
            return {"success": False, "error": "This action was already claimed; check Activities."}
    try:
        result = await execute_action(
            str(user_id), pending.tool_slug, pending.arguments or {},
            account=pending.account, approved=True,
        )
    except Exception as exc:
        result = {"success": False, "error": f"Outcome is uncertain: {exc}. Check the provider before trying again."}
    await decide_pending(str(user_id), pending.id, "completed" if result.get("success") else "needs_review")
    try:
        from server.automation_engine import on_approval
        goal_result = await on_approval(str(user_id), pending.id, result)
        if goal_result:
            result["goal"] = goal_result
    except Exception as exc:
        logger.warning("Goal resumption failed after approval; durable goal remains: %s", exc)
    return {**result, "approval_id": pending.id, "approved": True}


async def cancel_pending_action(user_id: str | int, action_id: Optional[str] = None) -> Dict[str, Any]:
    from server.agent_activity import get_pending, decide_pending
    pending = await get_pending(str(user_id), action_id)
    if not pending:
        return {"success": False, "error": "No unexpired action is waiting for approval."}
    await decide_pending(str(user_id), pending.id, "cancelled")
    from server.automation_engine import on_cancellation
    await on_cancellation(str(user_id), pending.id)
    return {"success": True, "cancelled": True, "approval_id": pending.id, "tool_slug": pending.tool_slug}


def verify_telegram_init_data(
    init_data: str,
    bot_token: str,
    max_age_seconds: int = 86400,
) -> Dict[str, Any]:
    """Validate Telegram Mini App initData and return its authenticated user.

    Implements Telegram's HMAC-SHA256 WebAppData verification. Untrusted user
    IDs supplied directly by the browser are never accepted.
    """
    import hashlib
    import hmac
    import json
    import time
    from urllib.parse import parse_qsl

    if not init_data or not bot_token:
        raise ValueError("Missing Telegram authorization data")
    values = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = values.pop("hash", "")
    if not received_hash:
        raise ValueError("Invalid Telegram authorization data")
    check_string = "\n".join(f"{key}={values[key]}" for key in sorted(values))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret_key, check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, received_hash):
        raise ValueError("Telegram authorization failed")
    auth_date = int(values.get("auth_date", "0") or 0)
    if auth_date <= 0 or abs(int(time.time()) - auth_date) > max_age_seconds:
        raise ValueError("Telegram authorization has expired. Reopen the Mini App.")
    try:
        user = json.loads(values.get("user", "{}"))
    except json.JSONDecodeError as exc:
        raise ValueError("Invalid Telegram user data") from exc
    if not user.get("id"):
        raise ValueError("Telegram user identity is missing")
    return user


def _find_video_ids(value: Any, found: Optional[list[str]] = None) -> list[str]:
    """Extract YouTube video IDs from playlist/search response variants."""
    found = found if found is not None else []
    if isinstance(value, dict):
        # Known YouTube response locations: resourceId.videoId, id.videoId,
        # contentDetails.videoId, and direct videoId.
        for key, item in value.items():
            if key == "videoId" and isinstance(item, str) and item and item not in found:
                found.append(item)
            else:
                _find_video_ids(item, found)
    elif isinstance(value, list):
        for item in value:
            _find_video_ids(item, found)
    return found


async def get_youtube_analytics(
    user_id: str | int | None,
    max_videos: int = 10,
) -> Dict[str, Any]:
    """Read a user's YouTube channel snapshot and recent video performance.

    This uses only read-only Composio actions. The response includes provider
    log IDs for support diagnostics without exposing credentials.
    """
    max_videos = max(1, min(int(max_videos or 10), 25))
    session = await get_session(user_id)

    def _run() -> Dict[str, Any]:
        channel = session.execute(
            "YOUTUBE_GET_CHANNEL_STATISTICS",
            arguments={"mine": True, "part": "snippet,statistics,contentDetails"},
        )
        recent = session.execute(
            "YOUTUBE_LIST_CHANNEL_VIDEOS",
            arguments={"mine": True, "part": "snippet", "maxResults": max_videos},
        )
        channel_data = _plain(channel)
        recent_data = _plain(recent)
        video_ids = _find_video_ids(recent_data)[:max_videos]
        details_data: Any = {"data": {"items": []}, "error": None, "log_id": None}
        if video_ids:
            details = session.execute(
                "YOUTUBE_GET_VIDEO_DETAILS_BATCH",
                arguments={"id": video_ids, "parts": ["snippet", "statistics", "contentDetails"]},
            )
            details_data = _plain(details)
        return {
            "channel": channel_data,
            "recent_videos": recent_data,
            "video_details": details_data,
            "video_ids": video_ids,
        }

    result = await asyncio.to_thread(_run)
    errors = [
        part.get("error")
        for part in (result.get("channel"), result.get("recent_videos"), result.get("video_details"))
        if isinstance(part, dict) and part.get("error")
    ]
    return {
        "success": not bool(errors),
        **result,
        "errors": errors,
        "log_ids": [
            part.get("log_id")
            for part in (result.get("channel"), result.get("recent_videos"), result.get("video_details"))
            if isinstance(part, dict) and part.get("log_id")
        ],
    }


async def list_app_actions(toolkit: str, limit: int = 500) -> Dict[str, Any]:
    """Return the provider's current actions with explicit permission behavior.

    Permission labels use Composio's official MCP behavior tags when present.
    Unknown or mutating actions default to human approval.
    """
    toolkit = re.sub(r"[^a-z0-9_-]", "", (toolkit or "").lower())
    if not toolkit:
        return {"success": False, "items": [], "error": "Toolkit is required"}

    def _list() -> Any:
        return _get_client().tools.get_raw_composio_tools(
            toolkits=[toolkit], limit=max(1, min(int(limit or 500), 500))
        )

    raw = await asyncio.to_thread(_list)
    from server.agent_activity import is_write_action
    items = []
    for tool in raw:
        data = _plain(tool)
        if not isinstance(data, dict):
            continue
        tags = [str(x) for x in (data.get("tags") or [])]
        slug = str(data.get("slug") or "")
        if "destructiveHint" in tags:
            permission = "approval_destructive"
            permission_label = "Approval required · destructive"
        elif any(x in tags for x in ("createHint", "updateHint")) or is_write_action(slug):
            permission = "approval_required"
            permission_label = "Approval required"
        else:
            permission = "read_only"
            permission_label = "Runs automatically · read-only"
        schema = data.get("input_parameters") or {}
        required = schema.get("required", []) if isinstance(schema, dict) else []
        # Parameter names and types are part of the public provider schema. Keep
        # the full schema server-side; expose a bounded, UI-safe form spec only.
        properties = schema.get("properties", {}) if isinstance(schema, dict) else {}
        parameters = []
        for key, field in list(properties.items())[:40]:
            if not isinstance(field, dict):
                field = {}
            parameters.append({
                "name": str(key)[:100], "type": field.get("type", "object"),
                "title": str(field.get("title") or key)[:100],
                "description": str(field.get("description") or "")[:220],
                "required": key in required,
            })
        items.append({
            "slug": slug,
            "name": data.get("name") or slug.replace("_", " ").title(),
            "description": data.get("description") or "",
            "permission": permission,
            "permission_label": permission_label,
            "tags": tags,
            "required_fields": required,
            "parameters": parameters,
            "scope_requirements": data.get("scope_requirements") or {},
            "deprecated": bool(data.get("is_deprecated")),
            "version": data.get("version"),
        })
    items.sort(key=lambda x: (x["deprecated"], x["permission"] != "read_only", x["name"]))
    return {
        "success": True,
        "toolkit": toolkit,
        "total": len(items),
        "read_only": sum(x["permission"] == "read_only" for x in items),
        "approval_required": sum(x["permission"] != "read_only" for x in items),
        "write_actions": sum(x["permission"] != "read_only" for x in items),
        "items": items,
    }
