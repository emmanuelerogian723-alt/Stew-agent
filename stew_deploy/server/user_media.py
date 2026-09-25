"""User-sent media registry.

When a user sends a video/document/photo in Telegram with an instruction
("post this to my YouTube", "email this to John", "edit this with Capcut"),
Stew stores the file here so the tool agent can reference and act on it.
`host_media()` makes the file publicly URL-addressable (needed by Composio
upload/email actions that only accept URLs).
"""
from __future__ import annotations

import os
import time
from typing import Optional

# key -> {path, filename, mime, kind, ts}
_PENDING: dict[str, dict] = {}
_MAX_AGE = 45 * 60  # 45 minutes


def _cleanup() -> None:
    now = time.time()
    for k in [k for k, v in _PENDING.items() if now - v["ts"] > _MAX_AGE]:
        try:
            if os.path.exists(_PENDING[k]["path"]):
                os.remove(_PENDING[k]["path"])
        except Exception:
            pass
        _PENDING.pop(k, None)


def store_media(key: str, path: str, filename: str, mime: str, kind: str) -> None:
    _cleanup()
    old = _PENDING.get(key)
    if old and old.get("path") != path:
        try:
            if os.path.exists(old["path"]):
                os.remove(old["path"])
        except Exception:
            pass
    _PENDING[key] = {"path": path, "filename": filename, "mime": mime,
                     "kind": kind, "ts": time.time()}


def get_media(key: str) -> Optional[dict]:
    _cleanup()
    return _PENDING.get(key)


def pop_media(key: str) -> Optional[dict]:
    _cleanup()
    return _PENDING.pop(key, None)


async def host_media(key: str) -> tuple[Optional[str], Optional[dict]]:
    """Upload the user's pending file to public storage. Returns (url, meta)."""
    meta = get_media(key)
    if not meta:
        return None, None
    from server.persistent_memory import upload_file as _up
    try:
        with open(meta["path"], "rb") as f:
            raw = f.read()
        url = await _up(raw, meta["filename"], meta["mime"], "user-media")
        return (url, meta) if url else (None, meta)
    except Exception:
        return None, meta


# ── intent detection for user-sent files ─────────────────────────────────────

_PLATFORMS = ("youtube", "tiktok", "instagram", "facebook", "twitter", " x ",
              "snapchat", "pinterest", "linkedin", "threads", "whatsapp status",
              "my story", "my status", "social media", "reels", "shorts")

_UPLOAD_WORDS = ("post this", "upload this", "share this", "post it", "upload it",
                 "share it", "put this on", "put it on", "publish this", "publish it",
                 "upload to", "post to", "post on", "share on", "upload on",
                 "send this to my", "post this video", "upload this video")

_EMAIL_WORDS = ("email this", "mail this", "email it", "mail it",
                "send this document", "send this file", "send this pdf",
                "email this document", "email this file", "forward this")


def is_media_task_intent(text: str) -> bool:
    """Does this caption instruct Stew to DO something with the file
    (post/email/share) rather than just analyze it?"""
    t = " " + (text or "").lower() + " "
    if any(w in t for w in _EMAIL_WORDS):
        return True
    if any(w in t for w in _UPLOAD_WORDS) and any(pl in t for pl in _PLATFORMS):
        return True
    # "email/send to <someone>" with an @address or explicit 'email' noun
    if ("send to" in t or "send it to" in t) and "@" in t:
        return True
    if "email" in t and any(w in t for w in ("send", "share", "forward")):
        return True
    return False
