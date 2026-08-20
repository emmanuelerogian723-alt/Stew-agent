"""
S.T.E.W Motion Design Website Builder

Generates a full single-page, self-contained animated website (HTML/CSS/JS)
from a plain-text description — inspired by the "Kimi K2 style" trend of
AI-generated, motion-heavy, premium-feeling landing pages built with vanilla
web tech (no frameworks, no build step, one file).

The generated HTML is hosted directly by Stew at /site/{id} so users get a
real, shareable, live link with zero external dependency.
"""
import asyncio
import logging
import re

from server.llm_client import get_llm_client

logger = logging.getLogger(__name__)


MOTION_DESIGN_SYSTEM_PROMPT = """You are an elite motion-design web developer specializing in the \
trending "Kimi K2 style" of AI-generated websites: highly animated, scroll-triggered, premium-feeling \
single-page sites built with vanilla HTML/CSS/JS — no frameworks, no build step, one self-contained file.

Hard requirements for every site you build:
1. ONE self-contained HTML file — inline <style> and <script> only. No external file dependencies \
except Google Fonts <link> and, if useful, a CDN icon script tag (e.g. lucide icons via unpkg/jsdelivr CDN).
2. Animated hero section: large headline with an animated gradient-text effect, 2-3 floating soft \
gradient "blob" shapes drifting with CSS keyframes, and a CTA button with a hover glow + scale transition.
3. Scroll-triggered reveal animations on every section using IntersectionObserver — fade + slide-up, \
staggered with transition-delay for cards/lists inside a section.
4. A sticky nav bar that gains a glassmorphism background (backdrop-filter: blur) once the user scrolls.
5. Smooth hover micro-interactions everywhere — buttons, cards, nav links (transform, box-shadow, \
color — all via CSS transition, never abrupt).
6. Fully responsive — looks great from 360px to 1920px wide. Use CSS clamp() for headline sizing.
7. Dark, premium aesthetic by default: deep near-black background (#0a0a0f / #0d0d14 style) with a \
vibrant gradient accent (purple-blue, or pick something that fits the business) — unless the user \
requests a different palette explicitly.
8. Sections to include, in order: sticky Nav, Hero, Features/Services (3-6 cards with icons — use inline \
SVG or emoji, never external image URLs), Social proof (3-5 short testimonials or trust badges), \
Pricing (only if relevant to the business), final CTA banner, Footer with links.
9. Write REAL, specific, persuasive copy for the exact business described — never lorem ipsum, never \
generic placeholders like "Company Name" or "Lorem ipsum dolor sit amet".
10. Load a modern sans-serif from Google Fonts (Inter, Poppins, Manrope, or Sora) via <link>.
11. NEVER use external <img> URLs (they may not load) — build every visual with CSS gradients, \
inline SVG icons, or emoji instead.
12. Keep total output focused and clean — no comments explaining the code, no markdown, just the site.

Output ONLY the complete HTML document: start with <!DOCTYPE html>, end with </html>. \
No markdown code fences. No explanation before or after. Just the raw HTML."""


STYLE_HINTS = {
    "premium-dark": "deep near-black background, vibrant purple-to-blue gradient accents, glassmorphism cards, feels expensive and modern",
    "vibrant": "bold saturated multi-color gradients (pink-orange-purple), high energy, playful bouncy animations, great for youth brands",
    "minimal": "clean white/off-white background, black/charcoal text, exactly ONE accent color, understated restrained animations, feels like a design studio",
    "corporate": "professional navy-and-white palette, clean grid layout, trust-building tone, subtle polished animations, feels enterprise-grade",
    "warm": "warm cream/terracotta palette, organic rounded shapes, soft shadows, inviting and human, great for hospitality/wellness brands",
}


def _strip_code_fences(raw: str) -> str:
    raw = raw.strip()
    raw = re.sub(r'^```(?:html)?\s*\n?', '', raw, flags=re.IGNORECASE)
    raw = re.sub(r'\n?```\s*$', '', raw)
    return raw.strip()


async def build_motion_website(description: str, style: str = "premium-dark") -> dict:
    """
    Generate a full animated single-page website from a text description.

    Args:
        description: What the site/business is (e.g. "a boutique coffee roastery in Lagos").
        style: One of premium-dark, vibrant, minimal, corporate, warm.

    Returns:
        {"success": bool, "html": str, "title": str, "size_bytes": int} or {"success": False, "error": str}
    """
    llm = get_llm_client()
    style_note = STYLE_HINTS.get(style, STYLE_HINTS["premium-dark"])

    user_prompt = (
        f"Build a motion-design landing page for: {description}\n\n"
        f"Visual style direction: {style_note}\n\n"
        f"Make it look like a $10,000 professionally designed website — polished, animated, "
        f"trustworthy, and modern. Write copy specific to this exact business, not generic filler."
    )

    try:
        result = await asyncio.to_thread(
            llm.chat,
            [
                {"role": "system", "content": MOTION_DESIGN_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
        )
    except Exception as e:
        logger.error(f"Motion website generation LLM error: {e}")
        return {"success": False, "error": str(e)[:200]}

    raw = _strip_code_fences(result.get("content", ""))

    if "<!doctype" not in raw.lower() and "<html" not in raw.lower():
        logger.warning(f"Motion website: model did not return HTML. First 200 chars: {raw[:200]}")
        return {"success": False, "error": "Generation did not produce valid HTML — please try again"}

    if len(raw) < 800:
        return {"success": False, "error": "Generated site was too short — please try again with more detail"}

    title_match = re.search(r'<title>(.*?)</title>', raw, re.IGNORECASE | re.DOTALL)
    title = title_match.group(1).strip() if title_match else description[:60].strip()
    if not title:
        title = description[:60].strip() or "My Website"

    return {
        "success": True,
        "html": raw,
        "title": title,
        "size_bytes": len(raw.encode("utf-8")),
    }
