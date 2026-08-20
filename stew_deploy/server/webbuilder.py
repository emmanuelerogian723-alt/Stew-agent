"""
S.T.E.W Motion Design Website Builder v2.0

Generates a full single-page, self-contained animated website (HTML/CSS/JS)
from a plain-text description. The LLM deeply analyzes the user's prompt
to understand the business type, target audience, desired aesthetic, unique
patterns, and any specific features requested — then builds a premium-quality
website that follows the prompt exactly.
"""
import asyncio
import logging
import re

from server.llm_client import get_llm_client

logger = logging.getLogger(__name__)


MOTION_DESIGN_SYSTEM_PROMPT = """You are a world-class web designer and frontend developer who creates \
the most stunning, animated, conversion-optimized single-page websites on the internet. You specialize \
in the "Kimi K2 style" of AI-generated websites: highly animated, scroll-triggered, premium-feeling \
sites built with vanilla HTML/CSS/JS — no frameworks, no build step, one self-contained file.

YOUR DESIGN PHILOSOPHY:
Every website you build should feel like it cost $10,000+ to make. You achieve this through:
- Thoughtful, purposeful animations (not gratuitous — every motion serves the narrative)
- Sophisticated color theory — you pick palettes that match the BRAND PERSONALITY, not generic templates
- Typography hierarchy that guides the eye effortlessly (use 3+ font sizes, weight contrasts, letter-spacing)
- Whitespace as a design element — sections breathe, content doesn't feel cramped
- Micro-interactions that reward engagement (hover states, button presses, card tilts)
- A clear visual story from top to bottom — hero sets the mood, features build trust, CTA converts

HARD REQUIREMENTS FOR EVERY SITE:

1. ONE self-contained HTML file — inline <style> and <script> only. No external file dependencies \
except Google Fonts <link> and optionally a CDN icon script tag (lucide icons via unpkg/jsdelivr).

2. HERO SECTION that immediately captivates:
   - Large headline with animated gradient-text effect (shift colors smoothly via CSS keyframes)
   - Subheadline with clear value proposition (1-2 sentences max)
   - 2-3 floating soft gradient "blob" shapes drifting with CSS keyframes (position: absolute, filter: blur(60-100px), opacity: 0.3-0.5)
   - CTA button with hover glow + scale transition (transform: scale(1.05), box-shadow expansion)
   - Hero background should set the emotional tone for the entire site

3. SCROLL-TRIGGERED REVEAL ANIMATIONS on EVERY section using IntersectionObserver:
   - Fade + slide-up (translateY(30px) to 0, opacity 0 to 1)
   - Staggered with transition-delay for cards/lists inside a section (0.1s increments)
   - Different reveal directions for visual variety (some slide from left, some from right)

4. STICKY NAV BAR:
   - Transparent at top, gains glassmorphism background (backdrop-filter: blur(12px)) on scroll
   - Logo (text-based or inline SVG) + nav links that smooth-scroll to sections
   - Mobile: collapses to a hamburger menu (functional — JS toggle)

5. HOVER MICRO-INTERACTIONS everywhere:
   - Buttons: scale + glow + shadow expansion
   - Cards: lift (translateY(-8px)) + shadow + subtle border color shift
   - Nav links: underline animation or color transition
   - All via CSS transition (0.3s cubic-bezier), never abrupt

6. FULLY RESPONSIVE from 320px to 2560px:
   - Use CSS clamp() for fluid typography: clamp(2rem, 5vw, 4.5rem) for headlines
   - CSS Grid and Flexbox for layouts, with @media queries for mobile adjustments
   - Touch-friendly tap targets (min 44px) on mobile

7. COLOR AND STYLE — ADAPT TO THE USER'S REQUEST:
   - If the user specifies a color scheme, mood, or aesthetic — FOLLOW IT EXACTLY
   - If the user says "like Apple's website" — study and replicate that aesthetic
   - If the user says "dark with gold accents" — use a dark base with gold highlights
   - If the user wants a specific pattern (geometric, organic, brutalist, glassmorphism) — implement it
   - Default (if no style specified): dark premium aesthetic with vibrant gradient accents that MATCH THE INDUSTRY:
     * Tech/AI startup: deep blue-purple gradients (#6366f1 to #8b5cf6)
     * Coffee/food: warm browns and cream or modern dark + amber
     * Fashion/beauty: bold black/white with one vibrant accent, or soft pastels
     * Fitness/health: energetic greens/blacks or bold orange
     * Finance/legal: navy and gold, trust-building
     * Music/entertainment: dark with neon accents (purple, electric blue, magenta)
     * Education: clean blue-and-white with playful elements
     * Real estate: sophisticated dark or clean white with blue/green accents
   - Use CSS custom properties (--accent, --bg, --text, --card) for consistency

8. SECTIONS — include what makes sense for the business (not a rigid template):
   - Sticky Nav (always)
   - Hero (always)
   - Features/Services (3-6 cards with inline SVG icons or emoji)
   - How It Works / Process (3-4 numbered steps)
   - Stats/Numbers section (animated count-up on scroll)
   - Social Proof (3-5 testimonials with names, roles, star ratings)
   - Pricing (ONLY if relevant — skip if it doesn't fit)
   - Gallery/Portfolio (if relevant)
   - FAQ accordion (if relevant)
   - Contact section (form with name/email/message)
   - Final CTA banner (full-width, bold gradient, single button)
   - Footer (links, social icons, copyright)
   - CUSTOM SECTIONS: If the user mentions specific needs (booking, team, map, blog, etc.) — include them

9. COPYWRITING — REAL, SPECIFIC, PERSUASIVE:
   - Write copy for the EXACT business described — never "Company Name", never "Lorem ipsum"
   - Use the business name from the prompt or invent a fitting one
   - Include realistic testimonials with Nigerian/African names when context suggests it
   - Address the target audience directly
   - Include a unique selling proposition in the hero

10. GOOGLE FONTS:
   - Load 1-2 fonts via <link> (e.g. Sora for headings + Inter for body)
   - Use font-weight variations (400, 500, 600, 700, 800) for hierarchy

11. NEVER use external <img> URLs — build every visual with:
   - CSS gradients (linear, radial, conic)
   - Inline SVG icons and illustrations
   - Emoji for quick visual elements
   - CSS shapes (border-radius, clip-path)
   - Box shadows and pseudo-elements

12. ADVANCED TECHNIQUES (use 3+ per site):
   - CSS conic-gradient for unique patterns
   - backdrop-filter: blur for glassmorphism
   - clip-path for unique section shapes (diagonal cuts, waves)
   - CSS scroll-snap for gallery sections
   - Staggered text reveal
   - Animated gradient backgrounds
   - Custom scrollbar styling
   - Section dividers with SVG shapes

13. OUTPUT FORMAT:
   - Output ONLY the complete HTML document: start with <!DOCTYPE html>, end with </html>
   - NO markdown code fences
   - NO explanation before or after
   - NO comments inside the code
   - Just the raw HTML

CRITICAL: You MUST include EVERY section the user mentioned. If they said "gallery, team, contact form, testimonials" — you MUST include ALL four. Do not skip any requested section. If the user mentions a feature you haven't seen before, improvise and build it.

Remember: You are building a REAL website for a REAL business. The user's prompt is your creative brief — follow it precisely. Every detail matters. Make it beautiful. Make it work. Make it convert."""


def _extract_design_keywords(description: str) -> dict:
    """Parse the user's description for explicit design instructions."""
    desc_lower = description.lower()

    colors = []
    color_map = {
        "gold": "#D4AF37", "golden": "#FFD700", "purple": "#8b5cf6", "blue": "#3b82f6",
        "green": "#16a34a", "red": "#ef4444", "orange": "#f97316", "pink": "#ec4899",
        "black": "#0a0a0f", "white": "#ffffff", "navy": "#1e3a5f", "teal": "#14b8a6",
        "cyan": "#06b6d4", "amber": "#f59e0b", "cream": "#fef3c7", "coral": "#fb7185",
        "lavender": "#a78bfa", "mint": "#6ee7b7", "charcoal": "#1f2937", "silver": "#c0c0c0",
        "burgundy": "#7c1d1d", "rose": "#e11d48", "indigo": "#4f46e5", "emerald": "#059669",
    }
    for name, hex_val in color_map.items():
        if name in desc_lower:
            colors.append(f"{name} ({hex_val})")

    styles = []
    style_keywords = [
        "dark", "light", "minimal", "vibrant", "bold", "clean", "corporate", "warm",
        "glassmorphism", "neumorphism", "brutalist", "retro", "futuristic", "elegant",
        "luxury", "playful", "professional", "edgy", "organic", "geometric",
        "art deco", "scandinavian", "industrial", "cyberpunk", "neon",
        "pastel", "monochrome", "gradient", "flat",
    ]
    for kw in style_keywords:
        if kw in desc_lower:
            styles.append(kw)

    features = []
    feature_keywords = {
        "booking": "online booking section with date/time picker UI",
        "contact": "contact form (name, email, message)",
        "team": "team section with member cards and photos (CSS avatar placeholders)",
        "gallery": "gallery/portfolio section with CSS gradient image placeholders",
        "pricing": "pricing section with tiers",
        "testimonial": "testimonials section with star ratings",
        "faq": "FAQ accordion section",
        "blog": "blog/news preview section",
        "map": "location section",
        "newsletter": "newsletter signup section",
        "shop": "product showcase section",
        "stats": "animated stats/numbers section",
        "portfolio": "portfolio showcase section",
        "about": "about section",
        "services": "services section",
        "menu": "menu/price list section",
        "events": "events section",
        "countdown": "countdown timer",
        "social": "social media links section",
    }
    for kw, feature_desc in feature_keywords.items():
        if kw in desc_lower:
            features.append(feature_desc)

    references = []
    brand_refs = ["apple", "stripe", "linear", "vercel", "notion", "airbnb", "spotify", "netflix", "tesla", "nike"]
    for brand in brand_refs:
        if f"like {brand}" in desc_lower or f"{brand}'s" in desc_lower or f"{brand} style" in desc_lower:
            references.append(brand)

    return {"colors": colors, "styles": styles, "features": features, "references": references}


def _strip_code_fences(raw: str) -> str:
    raw = raw.strip()
    raw = re.sub(r'^```(?:html)?\s*\n?', '', raw, flags=re.IGNORECASE)
    raw = re.sub(r'\n?```\s*$', '', raw)
    raw = re.sub(r'```(?:html)?\n?', '', raw, flags=re.IGNORECASE)
    return raw.strip()


async def build_motion_website(description: str, style: str = "auto") -> dict:
    """
    Generate a full animated single-page website from a text description.

    The LLM deeply analyzes the user's prompt to understand the business type,
    target audience, desired aesthetic, and any specific features or styles
    requested — then builds a premium website that follows the prompt.

    Args:
        description: What the site/business is (e.g. "a boutique coffee roastery in Lagos").
        style: Style hint (auto, premium-dark, vibrant, minimal, corporate, warm).

    Returns:
        {"success": bool, "html": str, "title": str, "size_bytes": int} or {"success": False, "error": str}
    """
    llm = get_llm_client()
    design_hints = _extract_design_keywords(description)

    parts = [f"Build a motion-design landing page for: {description}\n"]

    if design_hints["colors"]:
        parts.append(f"COLORS DETECTED IN REQUEST: {', '.join(design_hints['colors'])} - use these colors prominently in the design.\n")

    if design_hints["styles"]:
        parts.append(f"STYLE KEYWORDS DETECTED: {', '.join(design_hints['styles'])} - implement these aesthetic directions faithfully.\n")

    if design_hints["features"]:
        parts.append(f"SPECIFIC SECTIONS/FEATURES REQUESTED (you MUST include ALL of these):\n" + "\n".join(f"  - {f}" for f in design_hints["features"]) + "\n")

    if design_hints["references"]:
        parts.append(f"BRAND REFERENCE: The user mentioned {', '.join(design_hints['references'])} - study their design language and replicate that aesthetic.\n")

    if style and style != "auto":
        style_notes = {
            "premium-dark": "deep near-black background (#0a0a0f), vibrant gradient accents, glassmorphism cards, feels expensive and modern",
            "vibrant": "bold saturated multi-color gradients, high energy, playful bouncy animations, great for youth brands",
            "minimal": "clean white/off-white background, black/charcoal text, exactly ONE accent color, understated animations",
            "corporate": "professional navy-and-white palette, clean grid layout, trust-building tone, subtle polished animations",
            "warm": "warm cream/terracotta palette, organic rounded shapes, soft shadows, inviting and human",
        }
        style_note = style_notes.get(style, style_notes["premium-dark"])
        parts.append(f"VISUAL STYLE DIRECTION: {style_note}\n")
    else:
        parts.append("VISUAL STYLE DIRECTION: Choose the best aesthetic for this business type and target audience. Consider the industry, mood, and any style keywords the user mentioned. Be creative.\n")

    parts.append("Make it look like a $10,000 professionally designed website - polished, animated, trustworthy, and modern. Write copy specific to this exact business, not generic filler. The user's prompt is your creative brief - follow it precisely. Include EVERY section they mentioned.")

    user_prompt = "\n".join(parts)

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
        return {"success": False, "error": "Generation did not produce valid HTML - please try again"}

    if len(raw) < 800:
        return {"success": False, "error": "Generated site was too short - please try again with more detail"}

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
