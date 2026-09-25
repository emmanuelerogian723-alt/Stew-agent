"""Web image search — no API key required.

Openverse (CC-licensed, free, no auth) first, DuckDuckGo image search as
fallback. Used by the `search_web_images` agent tool so Stew can "go on the
internet, get images of X, and send them in the chat."
"""
from __future__ import annotations

import re
import urllib.parse
import urllib.request
from typing import Optional

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


def _get_json(url: str, timeout: int = 20):
    req = urllib.request.Request(url, headers={"User-Agent": _UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def _openverse_raw(query: str, count: int) -> list[dict]:
    q = urllib.parse.quote(query)
    url = (f"https://api.openverse.org/v1/images/?q={q}&page_size={min(count, 20)}"
           f"&mature=false")
    import json
    data = json.loads(_get_json(url))
    return data.get("results", [])


def _openverse(query: str, count: int) -> list[dict]:
    q = urllib.parse.quote(query)
    url = (f"https://api.openverse.org/v1/images/?q={q}&page_size={min(count, 20)}"
           f"&aspect_ratio=wide&size=large&mature=false")
    import json
    data = json.loads(_get_json(url))
    hits, skipped = [], 0
    for it in data.get("results", []):
        if len(hits) >= count:
            break
        u = it.get("url") or ""
        # Some CDNs (notably upload.wikimedia.org) aggressively rate-limit
        # datacenter IPs with 429s — skip hosts known to block servers so the
        # user actually receives files, and widen the net when skipped.
        if not u.startswith("https://") or "wikimedia" in u:
            skipped += 1
            continue
        hits.append({"url": u, "source": it.get("source", "openverse"),
                     "title": it.get("title", "")[:120], "license": it.get("license", "")})
    if skipped and len(hits) < count:
        try:
            extra = _openverse_raw(query, count + skipped + 5)
            for it in extra:
                if len(hits) >= count:
                    break
                u = it.get("url") or ""
                if u.startswith("https://") and "wikimedia" not in u:
                    if not any(h["url"] == u for h in hits):
                        hits.append({"url": u, "source": it.get("source", "openverse"),
                                     "title": it.get("title", "")[:120],
                                     "license": it.get("license", "")})
        except Exception:
            pass
    return hits


def _duckduckgo(query: str, count: int) -> list[dict]:
    vqd_m = re.search(r"vqd=[\"']?([\d-]+)[\"']?", _get_json("https://duckduckgo.com/", timeout=15))
    if not vqd_m:
        return []
    import json
    q = urllib.parse.quote(query)
    vqd = m_vqd = vqd_m.group(1)
    url = (f"https://duckduckgo.com/i.js?l=us-en&o=json&q={q}&vqd={vqd}"
           f"&f=,,,&p=1")
    try:
        data = json.loads(_get_json(url))
    except Exception:
        return []
    hits = []
    for it in data.get("results", [])[:count]:
        u = it.get("image") or ""
        if u.startswith("https://"):
            hits.append({"url": u, "source": it.get("source", "duckduckgo"),
                         "title": it.get("title", "")[:120], "license": ""})
    return hits


def search_images(query: str, count: int = 4) -> list[dict]:
    count = max(1, min(int(count or 4), 8))
    try:
        hits = _openverse(query, count)
        if hits:
            return hits
    except Exception:
        pass
    try:
        return _duckduckgo(query, count)
    except Exception:
        return []


def download_image(url: str, max_bytes: int = 8 * 1024 * 1024,
                    referer: str = "") -> Optional[bytes]:
    """Download an image; some hosts 403 without a Referer, so send the
    source page and retry once with a browser Accept header."""
    headers = {"User-Agent": _UA,
                "Accept": "image/avif,image/webp,image/png,image/jpeg,*/*;q=0.8"}
    if referer:
        headers["Referer"] = referer
    for attempt_headers in (headers, {**headers, "Referer": referer or "https://duckduckgo.com/"}):
        try:
            req = urllib.request.Request(url, headers=attempt_headers)
            with urllib.request.urlopen(req, timeout=25) as r:
                raw = r.read(max_bytes + 1)
                if len(raw) <= max_bytes and raw:
                    return raw
        except Exception:
            continue
    return None


def download_first(hits: list[dict], max_bytes: int = 8 * 1024 * 1024) -> Optional[bytes]:
    """Try each hit in order until one downloads (dead CDN links happen)."""
    for h in hits:
        raw = download_image(h.get("url", ""), max_bytes)
        if raw:
            return raw
    return None


def guess_ext(url: str, data: bytes) -> str:
    low = url.lower().split("?")[0]
    for ext in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
        if ext in low:
            return ext.lstrip(".")
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[:3] == b"GIF":
        return "gif"
    if data[:4] == b"RIFF":
        return "webp"
    return "jpg"
