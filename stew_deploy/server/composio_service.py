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
) -> Dict[str, Any]:
    """List app connection states for one S.T.E.W user."""
    session = await get_session(user_id)

    def _list() -> Any:
        return session.toolkits(
            limit=50,
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
) -> Dict[str, Any]:
    """Execute a discovered Composio tool in the current user's session."""
    slug = (tool_slug or "").strip().upper()
    if not slug:
        return {"success": False, "error": "A Composio tool slug is required."}
    if not isinstance(arguments, dict):
        return {"success": False, "error": "Tool arguments must be a JSON object."}

    session = await get_session(user_id)

    def _execute() -> Any:
        kwargs: Dict[str, Any] = {"arguments": arguments or {}}
        if account:
            kwargs["account"] = account
        return session.execute(slug, **kwargs)

    response = await asyncio.to_thread(_execute)
    data = _plain(response)
    error = data.get("error") if isinstance(data, dict) else None
    return {
        "success": not bool(error),
        "data": data.get("data") if isinstance(data, dict) else data,
        "error": error,
        "log_id": data.get("log_id") if isinstance(data, dict) else None,
        "tool_slug": slug,
    }


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
