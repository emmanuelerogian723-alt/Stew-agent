"""Knowledge connectors — RAG over the user's Google Drive + Sheets.

Sync pulls the user's recent Drive files and Sheet rows through Composio,
chunks them, and stores them in the knowledge_chunks table. The
search_knowledge agent tool then retrieves the best-matching chunks with a
keyword-tfidf score — no embedding API or vector DB needed (free, offline-
capable). This gives "connect my Drive and answer questions about my files."
"""
from __future__ import annotations

import logging
import re
from typing import Optional

from sqlalchemy import delete, select

from server.database import AsyncSessionLocal
from server.models import KnowledgeChunk

logger = logging.getLogger(__name__)
_STOP = set("the a an and or of to in for on with is are was were be been at by from this that it as".split())


def _chunk_text(text: str, size: int = 900) -> list[str]:
    text = re.sub(r"\s+", " ", (text or "")).strip()
    if len(text) <= size:
        return [text] if text else []
    parts = []
    for i in range(0, len(text), size - 100):
        parts.append(text[i:i + size])
    return parts[:40]


def _keywords(text: str) -> str:
    words = re.findall(r"[a-zA-Z0-9]{3,}", (text or "").lower())
    return " ".join(w for w in words if w not in _STOP)[:4000]


async def sync_knowledge(user_id: str, source: str = "gdrive") -> dict:
    """Pull files from Google Drive / Sheets via Composio and index chunks."""
    from server.composio_service import search_tools, execute_action
    uid = str(user_id)
    queries = {
        "gdrive": "google drive list my files documents",
        "gsheets": "google sheets list my spreadsheets and get values",
    }
    q = queries.get(source, queries["gdrive"])
    found = await search_tools(uid, q)
    results = found.get("results") or []
    statuses = found.get("toolkit_connection_statuses") or []
    primary = (results[0].get("primary_tool_slugs") or [None])[0] if results else None
    toolkit = (results[0].get("toolkits") or [None])[0] if results else None
    st = next((x for x in statuses if isinstance(x, dict) and x.get("toolkit") == toolkit), None)
    if not (primary and st and st.get("has_active_connection")):
        return {"ok": False,
                "error": f"{source} isn't connected yet — tell the user to open the Apps tab in the Mini App and connect Google (Drive/Sheets), then run /knowledge sync again."}
    try:
        data = await execute_action(uid, primary, {})
    except Exception as exc:
        return {"ok": False, "error": f"listing files failed: {exc}"}

    items = []
    if isinstance(data, dict):
        for key in ("files", "items", "data", "results", "documents"):
            v = data.get(key)
            if isinstance(v, list):
                items = v
                break
    if not items and isinstance(data, dict):
        items = [data.get("data")] if isinstance(data.get("data"), list) else []
    chunks_stored = 0
    docs = 0
    async with AsyncSessionLocal() as db:
        await db.execute(delete(KnowledgeChunk).where(
            KnowledgeChunk.telegram_user_id == uid,
            KnowledgeChunk.source_app == source))
        for it in (items or [])[:25]:
            if not isinstance(it, dict):
                continue
            title = str(it.get("name") or it.get("title") or it.get("fileName") or "untitled")[:500]
            body = str(it.get("content") or it.get("description") or it.get("body")
                       or it.get("snippet") or it.get("mimeType") or "")[:6000]
            file_id = str(it.get("id") or it.get("fileId") or it.get("file_id") or title)[:255]
            for piece in _chunk_text(body):
                db.add(KnowledgeChunk(telegram_user_id=uid, source_app=source,
                                      source_id=file_id, title=title,
                                      text=piece, keywords=_keywords(title + " " + piece)))
                chunks_stored += 1
            docs += 1
        await db.commit()
    return {"ok": True, "docs": docs, "chunks": chunks_stored,
            "note": f"Indexed {docs} {source} file(s) into {chunks_stored} chunk(s)." if docs
            else "Connected, but no readable file content was returned. Files with text (Docs, Sheets) index best."}


async def search_knowledge(user_id: str, query: str, top_k: int = 4) -> dict:
    uid = str(user_id)
    q_words = [w for w in re.findall(r"[a-zA-Z0-9]{3,}", (query or "").lower()) if w not in _STOP]
    if not q_words:
        return {"ok": False, "error": "a search query is required"}
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(
            select(KnowledgeChunk).where(KnowledgeChunk.telegram_user_id == uid)
            .limit(2000))).scalars().all()
    if not rows:
        return {"ok": False, "empty": True,
                "error": "No knowledge indexed yet — tell the user to run /knowledge sync after connecting Google Drive/Sheets."}
    scored = []
    for r in rows:
        hay = (r.keywords or "").lower()
        score = sum(1 for w in q_words if w in hay)
        if score > 0:
            scored.append((score, r))
    scored.sort(key=lambda x: -x[0])
    hits = [{"title": r.title, "text": r.text[:800], "source": r.source_app}
            for score, r in scored[:top_k] if score > 0]
    return {"ok": True, "hits": hits, "total_chunks": len(rows),
            "note": f"Matched {len(hits)} of {len(rows)} indexed chunks."
            if hits else "No match for those words in your indexed files."}
