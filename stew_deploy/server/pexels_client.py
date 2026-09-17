"""
S.T.E.W Pexels Integration — real stock photography for generated websites.

Replaces (or supplements) the Pollinations AI-image fallback with genuine,
licensed real photographs from Pexels. Falls back cleanly to Pollinations
if PEXELS_API_KEY is missing, the request fails, or no results are found —
the website builder NEVER breaks because of this integration.
"""
import logging
import os
import urllib.parse

import httpx

logger = logging.getLogger(__name__)

PEXELS_SEARCH_URL = "https://api.pexels.com/v1/search"


async def search_pexels_photos(query: str, count: int = 6) -> list[dict]:
    """
    Search Pexels for real photos matching `query`.

    Returns a list of dicts: {"url": str, "alt": str, "photographer": str,
    "width": int, "height": int}. Returns [] on any failure (missing key,
    network error, no results) so callers can fall back safely.
    """
    api_key = os.environ.get("PEXELS_API_KEY", "")
    if not api_key:
        logger.info("Pexels: no PEXELS_API_KEY set - skipping real-photo search")
        return []

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                PEXELS_SEARCH_URL,
                headers={"Authorization": api_key},
                params={
                    "query": query,
                    "per_page": min(max(count, 1), 15),
                    "orientation": "landscape",
                },
            )
            if resp.status_code != 200:
                logger.warning(f"Pexels search failed ({resp.status_code}): {resp.text[:200]}")
                return []
            data = resp.json()
    except Exception as e:
        logger.warning(f"Pexels search error for '{query}': {e}")
        return []

    photos = []
    for p in data.get("photos", [])[:count]:
        src = p.get("src", {})
        photos.append({
            "url": src.get("large2x") or src.get("large") or src.get("original", ""),
            "alt": p.get("alt") or query,
            "photographer": p.get("photographer", "Pexels"),
            "width": p.get("width", 1600),
            "height": p.get("height", 900),
        })
    return [p for p in photos if p["url"]]


async def build_photo_kit(description: str, business_keywords: list[str] = None) -> dict:
    """
    Build a curated set of REAL photos for a website brief: hero photo,
    gallery/section photos, and portrait-style photos (for team/testimonials).

    Returns {"available": bool, "hero": dict|None, "gallery": list[dict],
    "portraits": list[dict]}. `available=False` means the caller should
    fall back to Pollinations AI-generated imagery entirely.
    """
    terms = business_keywords or []
    base_query = " ".join(terms) if terms else description[:60]

    hero_photos = await search_pexels_photos(f"{base_query} professional", count=3)
    gallery_photos = await search_pexels_photos(base_query, count=6)
    portrait_photos = await search_pexels_photos("african professional portrait smiling", count=4)

    if not hero_photos and not gallery_photos:
        return {"available": False, "hero": None, "gallery": [], "portraits": []}

    return {
        "available": True,
        "hero": hero_photos[0] if hero_photos else (gallery_photos[0] if gallery_photos else None),
        "gallery": gallery_photos,
        "portraits": portrait_photos,
    }


def format_photo_kit_for_prompt(kit: dict) -> str:
    """Render a photo kit as plain instructions the LLM can copy verbatim
    into <img> tags — no need for the model to invent or encode any URL."""
    if not kit.get("available"):
        return ""

    lines = ["REAL PHOTOGRAPHY AVAILABLE (from Pexels, licensed for commercial use) — "
             "COPY THESE URLS EXACTLY, do not modify or re-encode them:"]

    if kit.get("hero"):
        h = kit["hero"]
        lines.append(f'- HERO PHOTO: {h["url"]}  (alt="{h["alt"]}")')

    for i, g in enumerate(kit.get("gallery", []), 1):
        lines.append(f'- GALLERY/SECTION PHOTO {i}: {g["url"]}  (alt="{g["alt"]}")')

    for i, p in enumerate(kit.get("portraits", []), 1):
        lines.append(f'- PORTRAIT/TESTIMONIAL AVATAR {i}: {p["url"]}  (alt="{p["alt"]}")')

    lines.append(
        "Use the HERO PHOTO as the full-bleed hero background. Use the GALLERY/SECTION "
        "photos for About, Gallery, Services, or product cards. Use the PORTRAIT photos "
        "for team members or testimonial avatars. If you need MORE photos than provided "
        "here, you may generate additional ones using the Pollinations format described "
        "below — but PRIORITIZE these real photos first for the most important spots "
        "(hero, about)."
    )
    return "\n".join(lines)
