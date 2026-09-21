"""
S.T.E.W YouTube Learn + Connect
================================
Two features in one module:

1. LEARN MODE (no auth needed, works for any public video):
   Paste a YouTube link -> Stew pulls the transcript (via yt-dlp captions,
   no API key needed) -> an LLM turns it into a plain-English lesson,
   study notes, or a quiz -> the user can then ask follow-up questions
   about that exact video, answered from its own transcript.

2. CONNECT YOUR CHANNEL (OAuth, needs the user's explicit permission):
   /ytconnect sends a Google consent link. Once approved, Stew stores an
   access/refresh token per Telegram chat_id and can pull that person's
   own channel stats and analytics on demand via /ytstats.

Kept dependency-free: reuses yt-dlp (already vetted for Render's IP in
video_tools.py) for transcripts, and raw httpx calls for OAuth + YouTube
Data/Analytics APIs (no google-api-python-client - too heavy for the
512MB Render free tier).
"""
import os
import re
import json
import time
import asyncio
import logging
import subprocess
import tempfile
from datetime import datetime, timedelta
from typing import Optional
from urllib.parse import urlencode

import httpx

logger = logging.getLogger("stew.youtube_learn")

OAUTH_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
OAUTH_TOKEN_URL = "https://oauth2.googleapis.com/token"
YT_DATA_API = "https://www.googleapis.com/youtube/v3"
YT_ANALYTICS_API = "https://youtubeanalytics.googleapis.com/v2/reports"

SCOPES = [
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
]

_YOUTUBE_HOSTS = ("youtube.com", "youtu.be", "m.youtube.com", "music.youtube.com")


def is_youtube_url(text: str) -> bool:
    return any(h in (text or "").lower() for h in _YOUTUBE_HOSTS)


def extract_youtube_url(text: str) -> Optional[str]:
    m = re.search(r"https?://(?:www\.)?(?:youtube\.com|youtu\.be|m\.youtube\.com|music\.youtube\.com)/\S+", text or "")
    return m.group(0).rstrip(").,!?\"'") if m else None


def extract_video_id(url: str) -> Optional[str]:
    m = re.search(r"(?:v=|youtu\.be/|/shorts/|/embed/)([A-Za-z0-9_-]{11})", url or "")
    return m.group(1) if m else None


# ════════════════════════════════════════════════════════════════════════════
# TRANSCRIPT (no API key - public captions via yt-dlp)
# ════════════════════════════════════════════════════════════════════════════

def _vtt_to_text(vtt_path: str) -> str:
    """Strip VTT timing/markup down to plain, de-duplicated speech text."""
    try:
        with open(vtt_path, "r", encoding="utf-8", errors="ignore") as f:
            raw = f.read()
    except FileNotFoundError:
        return ""
    lines = []
    seen = set()
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.upper().startswith("WEBVTT") or "-->" in line:
            continue
        if re.match(r"^\d+$", line):  # cue index
            continue
        line = re.sub(r"<[^>]+>", "", line)  # inline tags e.g. <c>
        line = re.sub(r"&nbsp;", " ", line)
        if line and line not in seen:
            seen.add(line)
            lines.append(line)
    return " ".join(lines)


def fetch_transcript(url: str, timeout: int = 90) -> dict:
    """Returns {"ok": bool, "text": str, "title": str, "error": str}.
    Uses yt-dlp --skip-download with the same client-fallback trick that
    already works for video downloads on Render's datacenter IP."""
    try:
        subprocess.run(["pip", "install", "-q", "--upgrade", "yt-dlp"], capture_output=True, timeout=25)
    except Exception:
        pass

    title = ""
    try:
        info = subprocess.run(
            ["yt-dlp", "--skip-download", "--print", "%(title)s", "--no-warnings",
             "--extractor-args", "youtube:player_client=android", url],
            capture_output=True, timeout=25, text=True,
        )
        if info.returncode == 0:
            title = info.stdout.strip().splitlines()[0] if info.stdout.strip() else ""
    except Exception:
        pass

    attempts = ["android", "ios", "tv", "android_music"]
    with tempfile.TemporaryDirectory() as tmp:
        out_tpl = os.path.join(tmp, "sub")
        for client in attempts:
            try:
                cmd = [
                    "yt-dlp", "--skip-download", "--write-auto-sub", "--write-sub",
                    "--sub-lang", "en.*,en", "--sub-format", "vtt", "--no-warnings",
                    "--extractor-args", f"youtube:player_client={client}",
                    "-o", out_tpl, url,
                ]
                subprocess.run(cmd, capture_output=True, timeout=timeout)
                vtt_files = [f for f in os.listdir(tmp) if f.endswith(".vtt")]
                if vtt_files:
                    text = _vtt_to_text(os.path.join(tmp, vtt_files[0]))
                    if len(text) > 40:
                        return {"ok": True, "text": text, "title": title, "error": ""}
            except subprocess.TimeoutExpired:
                continue
            except FileNotFoundError:
                return {"ok": False, "text": "", "title": title, "error": "yt-dlp not installed"}
            except Exception as e:
                logger.warning(f"transcript attempt ({client}) failed: {e}")
                continue

    return {
        "ok": False, "text": "", "title": title,
        "error": "This video has no captions available (auto or manual), so Stew can't read it.",
    }


# ════════════════════════════════════════════════════════════════════════════
# LEARN MODE - transcript -> lesson / notes / quiz, + follow-up Q&A memory
# ════════════════════════════════════════════════════════════════════════════

_ACTIVE_VIDEO: dict = {}     # f"tg:{chat_id}" -> {"title", "text", "url", "ts"}
_TTL_SECONDS = 3600 * 6


def arm_active_video(key: str, title: str, text: str, url: str) -> None:
    _ACTIVE_VIDEO[key] = {"title": title, "text": text[:14000], "url": url, "ts": time.time()}


def get_active_video(key: str) -> Optional[dict]:
    v = _ACTIVE_VIDEO.get(key)
    if not v:
        return None
    if time.time() - v["ts"] > _TTL_SECONDS:
        _ACTIVE_VIDEO.pop(key, None)
        return None
    return v


def clear_active_video(key: str) -> None:
    _ACTIVE_VIDEO.pop(key, None)


def build_lesson(transcript: str, title: str) -> str:
    from server.llm_client import get_llm_client
    llm = get_llm_client()
    r = llm.chat([
        {"role": "system", "content":
            "You are a brilliant, friendly tutor. Turn this YouTube transcript into a short, "
            "easy-to-follow lesson someone can fully learn from on their phone, in Telegram. "
            "Structure it as:\n"
            "*What this video teaches* (1-2 sentences)\n"
            "*Key points* (4-8 short bullet lines, plain language, no jargon left unexplained)\n"
            "*In simple terms* (one short paragraph explaining the core idea like to a beginner)\n"
            "*Try this* (one small action step to apply it)\n"
            "Keep the whole thing under 350 words. No markdown headers (##), use plain text with "
            "*bold* only for the four section labels above."},
        {"role": "user", "content": f"Video title: {title}\n\nTranscript:\n{transcript[:12000]}"},
    ])
    return r.get("content", "").strip()


def build_notes(transcript: str, title: str) -> str:
    from server.llm_client import get_llm_client
    llm = get_llm_client()
    r = llm.chat([
        {"role": "system", "content":
            "Turn this transcript into tight bullet-point study notes: definitions, key facts, "
            "numbers/dates mentioned, and any step-by-step process. Max 15 bullets, no fluff."},
        {"role": "user", "content": f"Video title: {title}\n\nTranscript:\n{transcript[:12000]}"},
    ])
    return r.get("content", "").strip()


def build_video_quiz(transcript: str, title: str) -> str:
    from server.llm_client import get_llm_client
    llm = get_llm_client()
    r = llm.chat([
        {"role": "system", "content":
            "Quiz generator. From this exact video transcript, create 5 multiple choice "
            "questions testing real comprehension of what was said. Format:\n"
            "Q1. Question\nA) ..\nB) ..\nC) ..\nD) ..\nAnswer: X\n\nAnswer key at the end."},
        {"role": "user", "content": f"Video title: {title}\n\nTranscript:\n{transcript[:12000]}"},
    ])
    return r.get("content", "").strip()


def answer_from_video(question: str, transcript: str, title: str) -> str:
    from server.llm_client import get_llm_client
    llm = get_llm_client()
    r = llm.chat([
        {"role": "system", "content":
            f"You are answering questions ONLY about the YouTube video '{title}', using its "
            f"transcript below as your source of truth. If the transcript doesn't cover the "
            f"question, say so honestly instead of guessing. Be concise and clear.\n\n"
            f"TRANSCRIPT:\n{transcript[:12000]}"},
        {"role": "user", "content": question},
    ])
    return r.get("content", "").strip()


# ════════════════════════════════════════════════════════════════════════════
# CONNECT YOUR CHANNEL - OAuth 2.0
# ════════════════════════════════════════════════════════════════════════════

def build_auth_url(chat_id, redirect_uri: str, client_id: str) -> str:
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "state": str(chat_id),
        "include_granted_scopes": "true",
    }
    return f"{OAUTH_AUTH_URL}?{urlencode(params)}"


async def exchange_code(code: str, redirect_uri: str, client_id: str, client_secret: str) -> dict:
    """Returns {"ok": bool, "access_token", "refresh_token", "expires_in", "scope", "error"}."""
    async with httpx.AsyncClient(timeout=20) as client:
        try:
            resp = await client.post(OAUTH_TOKEN_URL, data={
                "code": code, "client_id": client_id, "client_secret": client_secret,
                "redirect_uri": redirect_uri, "grant_type": "authorization_code",
            })
            data = resp.json()
            if resp.status_code == 200 and "access_token" in data:
                return {"ok": True, **data, "error": ""}
            return {"ok": False, "error": data.get("error_description") or data.get("error") or str(data)}
        except Exception as e:
            return {"ok": False, "error": str(e)}


async def refresh_access_token(refresh_token: str, client_id: str, client_secret: str) -> dict:
    async with httpx.AsyncClient(timeout=20) as client:
        try:
            resp = await client.post(OAUTH_TOKEN_URL, data={
                "refresh_token": refresh_token, "client_id": client_id,
                "client_secret": client_secret, "grant_type": "refresh_token",
            })
            data = resp.json()
            if resp.status_code == 200 and "access_token" in data:
                return {"ok": True, **data, "error": ""}
            return {"ok": False, "error": data.get("error_description") or str(data)}
        except Exception as e:
            return {"ok": False, "error": str(e)}


async def get_my_channel(access_token: str) -> dict:
    """Basic channel info + lifetime stats for the connected account."""
    async with httpx.AsyncClient(timeout=20) as client:
        try:
            resp = await client.get(
                f"{YT_DATA_API}/channels",
                params={"part": "snippet,statistics", "mine": "true"},
                headers={"Authorization": f"Bearer {access_token}"},
            )
            data = resp.json()
            items = data.get("items") or []
            if not items:
                return {"ok": False, "error": data.get("error", {}).get("message", "No channel found")}
            ch = items[0]
            return {
                "ok": True,
                "channel_id": ch.get("id"),
                "title": ch.get("snippet", {}).get("title"),
                "subscribers": ch.get("statistics", {}).get("subscriberCount"),
                "views": ch.get("statistics", {}).get("viewCount"),
                "videos": ch.get("statistics", {}).get("videoCount"),
            }
        except Exception as e:
            return {"ok": False, "error": str(e)}


async def get_channel_analytics(access_token: str, days: int = 28) -> dict:
    """28-day (default) analytics: views, watch time, subscribers gained."""
    end = datetime.utcnow().date()
    start = end - timedelta(days=days)
    async with httpx.AsyncClient(timeout=20) as client:
        try:
            resp = await client.get(
                YT_ANALYTICS_API,
                params={
                    "ids": "channel==MINE",
                    "startDate": start.isoformat(),
                    "endDate": end.isoformat(),
                    "metrics": "views,estimatedMinutesWatched,subscribersGained,likes,comments",
                },
                headers={"Authorization": f"Bearer {access_token}"},
            )
            data = resp.json()
            rows = data.get("rows")
            if not rows:
                return {"ok": True, "views": 0, "minutes_watched": 0, "subs_gained": 0, "likes": 0, "comments": 0, "days": days}
            row = rows[0]
            return {
                "ok": True, "views": row[0], "minutes_watched": row[1],
                "subs_gained": row[2], "likes": row[3], "comments": row[4], "days": days,
            }
        except Exception as e:
            return {"ok": False, "error": str(e)}
