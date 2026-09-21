"""S.T.E.W IMAGE STUDIO
════════════════════════════════════════════════════════════════════════
Two flagship capabilities (v6.5):

1. AI PHOTO EDITING — "send a picture, tell Stew what to do":
   Gemini 2.5 Flash Image ("nano banana") receives the user's photo plus
   their plain-English instruction and returns the edited image.
   A deterministic PIL fallback handles quick ops (brighten, grayscale,
   blur, rotate, vignette, sepia) if the AI engine is unavailable.

2. CANVA-STYLE DESIGN STUDIO — posters, banners, flyers, story cards:
   An LLM turns the brief into a design spec (headline, palette, copy),
   a background is generated (Gemini → Pollinations fallback), and PIL
   composites agency-grade typography on top: gradient scrims, auto-fit
   wrapped headlines, CTA pill, contact strip and accent border.
════════════════════════════════════════════════════════════════════════
"""
from __future__ import annotations

import base64
import io
import json
import logging
import os
import random
import re
import time
from typing import Callable, Optional

import requests
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont

logger = logging.getLogger("stew.image_studio")

# ── graceful settings access (works inside the server AND standalone) ────────
try:
    from server.config import settings  # type: ignore

    def _gemini_key() -> str:
        return getattr(settings, "GEMINI_API_KEY", "") or os.environ.get("GEMINI_API_KEY", "")
except Exception:  # standalone/testing
    settings = None  # type: ignore

    def _gemini_key() -> str:
        return os.environ.get("GEMINI_API_KEY", "")


GEMINI_IMAGE_MODEL = "gemini-2.5-flash-image"  # "nano banana"
GEMINI_ENDPOINT = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    f"{GEMINI_IMAGE_MODEL}:generateContent"
)

# keep AI inputs small for the 512MB RAM budget
_MAX_INPUT_MB = 12


# ════════════════════════════════════════════════════════════════════════════
# PENDING-PHOTO SESSION (mirrors server.video_editor's pending store)
# ════════════════════════════════════════════════════════════════════════════
_PENDING: dict = {}
PENDING_TTL_SECONDS = 600  # user has 10 minutes to say what to do with the photo


def arm_pending(key: str, file_id: str) -> None:
    _PENDING[key] = {"file_id": file_id, "expires": time.time() + PENDING_TTL_SECONDS}


def get_pending(key: str) -> Optional[dict]:
    p = _PENDING.get(key)
    if not p:
        return None
    if time.time() > p["expires"]:
        _PENDING.pop(key, None)
        return None
    return p


def clear_pending(key: str) -> None:
    _PENDING.pop(key, None)


# ════════════════════════════════════════════════════════════════════════════
# INTENT DETECTION
# ════════════════════════════════════════════════════════════════════════════
_EDIT_RE = re.compile(
    r"\b(edit|retouch|photoshop|remove|erase|delete|brighten|darken|enhance|fix|polish|improve|"
    r"upgrade|recreate|refine|beautify|upscale|touch up|make over|redo|regenerate|restyle|modernize|"
    r"clear up|sharpen|restore|colorise|colorize|blur|crop|swap|replace|change|"
    r"similar|like this|look better|look nicer|more beautiful|more professional)\b",
    re.IGNORECASE,
)
_IMG_WORD_RE = re.compile(r"\b(photo|image|picture|pic|it|this)\b", re.IGNORECASE)


def is_image_edit_intent(text: str) -> bool:
    t = (text or "").strip()
    if not t or t.startswith("/"):
        return False
    low = t.lower()
    if _EDIT_RE.search(low) and _IMG_WORD_RE.search(low):
        return True
    if re.search(r"\b(remove|clear)\s+(the\s+)?background\b", low):
        return True
    return False


_POSTER_KINDS = (
    ("poster", "poster"),
    ("banner", "banner"),
    ("flyer", "flyer"),
    ("thumbnail", "thumbnail"),
    ("story", "story"),
)


def is_poster_intent(text: str) -> Optional[tuple]:
    """Returns (kind, brief) when the text asks for a poster/banner/flyer."""
    t = (text or "").strip()
    if not t or t.startswith("/"):
        return None
    low = t.lower()
    for word, kind in _POSTER_KINDS:
        if re.search(rf"\b{re.escape(word)}\b", low):
            if re.search(r"\b(make|create|design|generate|draw|build|need|want)\b", low) or " for " in low:
                return (kind, t)
    return None


# ════════════════════════════════════════════════════════════════════════════
# GEMINI "NANO BANANA" IMAGE GENERATION / EDITING
# ════════════════════════════════════════════════════════════════════════════
def gemini_image(prompt: str, input_images: Optional[list] = None) -> Optional[bytes]:
    """Call Gemini 2.5 Flash Image. Returns image bytes or None."""
    key = _gemini_key()
    if not key:
        return None
    parts = [{"text": prompt}]
    for raw in (input_images or []):
        if len(raw) > _MAX_INPUT_MB * 1024 * 1024:
            logger.warning("input image too large for Gemini edit (%d bytes)", len(raw))
            continue
        b64 = base64.b64encode(raw).decode()
        mime = "image/jpeg"
        try:
            with Image.open(io.BytesIO(raw)) as im:
                mime = Image.MIME.get(im.format, "image/jpeg")
        except Exception:
            pass
        parts.append({"inline_data": {"mime_type": mime, "data": b64}})

    body = {
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {"responseModalities": ["TEXT", "IMAGE"]},
    }
    try:
        r = requests.post(
            GEMINI_ENDPOINT, params={"key": key}, json=body, timeout=120
        )
        if r.status_code != 200:
            logger.warning("gemini_image HTTP %s: %s", r.status_code, r.text[:200])
            return None
        data = r.json()
        for cand in data.get("candidates", []):
            for part in cand.get("content", {}).get("parts", []):
                ib = part.get("inlineData") or part.get("inline_data")
                if ib and ib.get("data"):
                    return base64.b64decode(ib["data"])
        logger.warning("gemini_image returned no image part: %s", json.dumps(data)[:200])
        return None
    except Exception as e:
        logger.warning("gemini_image failed: %s", e)
        return None


def pollinations_image(prompt: str, width: int = 1024, height: int = 1024) -> Optional[bytes]:
    """Fallback text-to-image via Pollinations (no image-to-image support)."""
    try:
        url = (
            "https://image.pollinations.ai/prompt/"
            + requests.utils.quote(prompt, safe="")
            + f"?width={width}&height={height}&nologo=true&seed={random.randint(1, 999999)}"
        )
        r = requests.get(url, timeout=120)
        if r.status_code == 200 and r.content[:3] in (b"\x89PN", b"\xff\xd8\xff"):
            return r.content
        return None
    except Exception as e:
        logger.warning("pollinations_image failed: %s", e)
        return None


def pollinations_edit(img_bytes: bytes, instruction: str) -> Optional[bytes]:
    """Image-to-image edit via Pollinations Kontext (free, no key needed).
    Accepts a base64 data URI in the JSON body — no public hosting required."""
    try:
        data_uri = "data:image/jpeg;base64," + base64.b64encode(img_bytes).decode()
        prompt = (
            f"Edit this image: {instruction}. Keep the composition and subjects "
            f"faithful to the original, apply only the requested change. High quality."
        )
        r = requests.post(
            "https://image.pollinations.ai/prompt/" + requests.utils.quote(prompt, safe=""),
            json={"image": data_uri, "model": "kontext"},
            timeout=180,
        )
        if r.status_code == 200 and r.content[:3] in (b"\x89PN", b"\xff\xd8\xff"):
            return r.content
        logger.warning("pollinations_edit HTTP %s: %s", r.status_code, r.text[:120])
        return None
    except Exception as e:
        logger.warning("pollinations_edit failed: %s", e)
        return None


# ════════════════════════════════════════════════════════════════════════════
# PHOTO EDITING
# ════════════════════════════════════════════════════════════════════════════
def _pil_quick_edit(img_bytes: bytes, instruction: str) -> Optional[bytes]:
    """Deterministic fallback edits for common quick ops."""
    low = instruction.lower()
    try:
        im = Image.open(io.BytesIO(img_bytes)).convert("RGB")
    except Exception:
        return None

    did = False
    if re.search(r"\bbrighten|brighter|lighter\b", low):
        im = ImageEnhance.Brightness(im).enhance(1.35)
        did = True
    if re.search(r"\bdark(en|er)\b", low):
        im = ImageEnhance.Brightness(im).enhance(0.72)
        did = True
    if re.search(r"\bsharpen|sharper\b", low):
        im = im.filter(ImageFilter.SHARPEN)
        did = True
    if re.search(r"\bgrayscale|greyscale|black.?and.?white|b&w\b", low):
        im = im.convert("L").convert("RGB")
        did = True
    if re.search(r"\bsepia|vintage|old.?school\b", low):
        r, g, b = im.split()
        im = Image.merge("RGB", (
            r.point(lambda x: min(255, int(x * 1.1 + 30))),
            g.point(lambda x: min(255, int(x * 0.95 + 10))),
            b.point(lambda x: max(0, int(x * 0.75))),
        ))
        did = True
    if re.search(r"\bblur\b", low):
        im = im.filter(ImageFilter.GaussianBlur(6))
        did = True
    if re.search(r"\bflip\b", low):
        im = im.transpose(Image.FLIP_LEFT_RIGHT)
        did = True
    if re.search(r"\binvert|negative\b", low):
        from PIL import ImageOps

        im = ImageOps.invert(im)
        did = True
    if re.search(r"\bvignette\b", low):
        w, h = im.size
        mask = Image.new("L", (w, h), 0)
        d = ImageDraw.Draw(mask)
        d.ellipse((-w // 4, -h // 4, w * 5 // 4, h * 5 // 4), fill=255)
        mask = mask.filter(ImageFilter.GaussianBlur(w // 8))
        black = Image.new("RGB", (w, h), (0, 0, 0))
        im = Image.composite(im, black, mask)
        did = True
    if not did:
        return None
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=92)
    return buf.getvalue()


def edit_image(img_bytes: bytes, instruction: str) -> dict:
    """Edit a photo per plain-English instruction.
    Returns {"ok": bool, "image": bytes|None, "engine": str|None, "error": str|None}."""
    instruction = (instruction or "").strip() or "enhance this photo naturally"
    prompt = (
        f"You are a professional photo editor. Edit this image with the "
        f"following instruction: {instruction}. "
        f"Apply ONLY the requested edit, keep everything else faithful to the "
        f"original: same framing, same composition, same subjects. "
        f"Photorealistic, high quality. Return the edited image."
    )
    out = gemini_image(prompt, [img_bytes])
    if out:
        return {"ok": True, "image": out, "engine": "Gemini Flash Image", "error": None}

    # Pollinations Kontext — free image-to-image, works without any API key
    out = pollinations_edit(img_bytes, instruction)
    if out:
        return {"ok": True, "image": out, "engine": "Pollinations Kontext", "error": None}

    quick = _pil_quick_edit(img_bytes, instruction)
    if quick:
        return {"ok": True, "image": quick, "engine": "Stew quick-edit", "error": None}

    return {
        "ok": False,
        "image": None,
        "engine": None,
        "error": "The AI image engine is unavailable right now and the instruction "
        "wasn't a quick-edit Stew can do offline. Try again in a minute.",
    }


# ════════════════════════════════════════════════════════════════════════════
# CANVA-STYLE POSTER / BANNER / FLYER STUDIO
# ════════════════════════════════════════════════════════════════════════════
CANVAS_SIZES = {
    "poster": (1080, 1350),
    "flyer": (1080, 1080),
    "banner": (1500, 500),
    "story": (1080, 1920),
    "thumbnail": (1280, 720),
}

_FONT_PATHS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
]
_FONT_URL = "https://cdn.jsdelivr.net/npm/dejavu-fonts-ttf@2.37.3/ttf/DejaVuSans-Bold.ttf"
_FONT_URL_R = "https://cdn.jsdelivr.net/npm/dejavu-fonts-ttf@2.37.3/ttf/DejaVuSans.ttf"
_font_cache: dict = {}


def _download_font(url: str, dest: str) -> bool:
    try:
        r = requests.get(url, timeout=30)
        if r.status_code == 200 and len(r.content) > 10000:
            with open(dest, "wb") as f:
                f.write(r.content)
            return True
    except Exception as e:
        logger.warning("font download failed: %s", e)
    return False


def _find_font(bold: bool = True) -> Optional[str]:
    for p in _FONT_PATHS:
        if bold and "Bold" not in p:
            continue
        if not bold and "Bold" in p:
            continue
        if os.path.exists(p):
            return p
    cache_dir = "/tmp/stew_fonts"
    os.makedirs(cache_dir, exist_ok=True)
    local = os.path.join(cache_dir, "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf")
    if not os.path.exists(local):
        if not _download_font(_FONT_URL if bold else _FONT_URL_R, local):
            return None
    return local


def _font(size: int, bold: bool = True):
    key = (size, bold)
    if key in _font_cache:
        return _font_cache[key]
    path = _find_font(bold)
    f = None
    if path:
        try:
            f = ImageFont.truetype(path, size)
        except Exception:
            f = None
    if f is None:
        try:
            f = ImageFont.load_default(size=size)  # Pillow ≥ 10.1
        except TypeError:
            f = ImageFont.load_default()
    _font_cache[key] = f
    return f


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, max_w: int) -> list:
    words, lines, cur = text.split(), [], ""
    for w in words:
        trial = (cur + " " + w).strip()
        if draw.textlength(trial, font=font) <= max_w or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def _fit_bg(img: Image.Image, w: int, h: int) -> Image.Image:
    """Cover-crop a background image to the canvas."""
    iw, ih = img.size
    scale = max(w / iw, h / ih)
    img = img.resize((int(iw * scale) + 1, int(ih * scale) + 1), Image.LANCZOS)
    iw, ih = img.size
    left = (iw - w) // 2
    top = (ih - h) // 2
    return img.crop((left, top, left + w, top + h))


def _scrim(base: Image.Image, color=(8, 8, 14)) -> Image.Image:
    """Dark gradient at top and bottom so text always pops."""
    w, h = base.size
    col = Image.new("L", (1, h), 0)
    for y in range(h):
        d = min(y, h - 1 - y)
        col.putpixel((0, y), int(min(205, max(0, 205 - d * 1.5))))
    col = col.resize((w, h))
    overlay = Image.new("RGB", (w, h), color)
    return Image.composite(overlay, base, col)


DEFAULT_SPEC = {
    "headline": "YOUR BIG HEADLINE HERE",
    "subheadline": "A short supporting line that sells the idea",
    "cta": "Get Started Today",
    "contact": "@yourhandle • +234 000 000 0000",
    "style_prompt": "premium minimal studio background, soft light, elegant",
    "palette": ["#F5F5F0", "#9A7BFF", "#FF5252"],
}


def create_poster(
    brief: str,
    kind: str = "poster",
    llm_json: Optional[Callable[[str], Optional[dict]]] = None,
) -> dict:
    """Design a poster/banner/flyer from a plain-English brief.
    Returns {"ok": bool, "image": bytes, "meta": dict, "error": str|None}."""
    kind = kind if kind in CANVAS_SIZES else "poster"
    w, h = CANVAS_SIZES[kind]
    spec = dict(DEFAULT_SPEC)

    # ── 1. design spec from the brief ─────────────────────────────────────
    if llm_json:
        try:
            j = llm_json(brief)
            if isinstance(j, dict):
                for k in ("headline", "subheadline", "cta", "contact"):
                    if isinstance(j.get(k), str) and j[k].strip():
                        spec[k] = j[k].strip()
                if isinstance(j.get("style_prompt"), str) and j["style_prompt"].strip():
                    spec["style_prompt"] = j["style_prompt"].strip()
                if isinstance(j.get("palette"), list) and len(j["palette"]) >= 2:
                    spec["palette"] = j["palette"][:3]
        except Exception as e:
            logger.warning("poster spec LLM failed: %s", e)
    if not llm_json:
        clean = re.sub(r"^(make|create|design|generate|draw|build)\s+(me\s+)?(a|an|the)?\s*",
                      "", brief.strip(), flags=re.I)
        spec["headline"] = (clean or brief).strip()[:64] or DEFAULT_SPEC["headline"]
        spec["subheadline"] = "Designed by S.T.E.W AI"
        spec["cta"] = "Learn More"
        spec["contact"] = "S.T.E.W • stew-agent.onrender.com"

    # ── 2. background ─────────────────────────────────────────────────────
    if kind in ("poster", "story", "flyer"):
        bg_prompt = (
            f"Professional {kind} background art for: {brief}. "
            f"{spec['style_prompt']}. Absolutely NO text, NO letters, NO words, "
            f"NO logos in the image. Keep the center area relatively empty for "
            f"typography. Cinematic lighting, premium advertising quality."
        )
    else:
        bg_prompt = (
            f"Professional wide {kind} background art for: {brief}. "
            f"{spec['style_prompt']}. NO text, NO letters, NO logos. "
            f"Center empty for typography. Premium advertising quality, "
            f"wide horizontal composition."
        )
    bg_bytes = gemini_image(bg_prompt)
    engine = "Gemini Flash Image"
    if not bg_bytes:
        bg_bytes = pollinations_image(bg_prompt, min(w, 1024), min(h, 1024))
        engine = "Pollinations"
    try:
        if bg_bytes:
            bg = Image.open(io.BytesIO(bg_bytes)).convert("RGB")
            canvas = _fit_bg(bg, w, h)
        else:
            engine = "Stew gradient"
            canvas = Image.new("RGB", (w, h), (16, 16, 26))
    except Exception as e:
        logger.warning("bg build failed: %s", e)
        canvas = Image.new("RGB", (w, h), (16, 16, 26))
        engine = "Stew gradient"

    canvas = _scrim(canvas)
    draw = ImageDraw.Draw(canvas)

    # ── 3. typography ─────────────────────────────────────────────────────
    fg = spec["palette"][0]
    accent = spec["palette"][1] if len(spec["palette"]) > 1 else "#FF5252"
    accent2 = spec["palette"][2] if len(spec["palette"]) > 2 else accent

    margin = int(w * 0.08)
    max_w = w - 2 * margin

    if kind == "banner":
        h_size, sub_size, block_top = int(h * 0.24), int(h * 0.11), int(h * 0.16)
    elif kind == "story":
        h_size, sub_size, block_top = int(w * 0.115), int(w * 0.05), int(h * 0.30)
    elif kind == "thumbnail":
        h_size, sub_size, block_top = int(w * 0.10), int(w * 0.045), int(h * 0.26)
    elif kind == "flyer":
        h_size, sub_size, block_top = int(w * 0.10), int(w * 0.045), int(h * 0.30)
    else:
        h_size, sub_size, block_top = int(w * 0.11), int(w * 0.05), int(h * 0.26)

    lines = []
    f_head = _font(h_size, bold=True)
    while h_size > 30:
        f_head = _font(h_size, bold=True)
        lines = _wrap(draw, spec["headline"], f_head, max_w)
        if len(lines) <= 3:
            break
        h_size -= 6
    y = block_top
    for line in lines:
        tw = draw.textlength(line, font=f_head)
        draw.text(((w - tw) / 2, y), line, font=f_head, fill=fg)
        y += int(h_size * 1.18)

    # subheadline
    y += int(h_size * 0.35)
    f_sub = _font(sub_size, bold=False)
    for line in _wrap(draw, spec["subheadline"], f_sub, max_w)[:3]:
        tw = draw.textlength(line, font=f_sub)
        draw.text(((w - tw) / 2, y), line, font=f_sub, fill=(235, 235, 240))
        y += int(sub_size * 1.35)

    # CTA pill
    f_cta = _font(int(sub_size * 0.95), bold=True)
    cta_txt = spec["cta"][:40]
    cw = draw.textlength(cta_txt, font=f_cta)
    pad_x, pad_y = int(sub_size * 1.4), int(sub_size * 0.8)
    pill_w, pill_h = int(cw + 2 * pad_x), int(sub_size * 1.9 + 2 * pad_y)
    pill_x, pill_y = (w - pill_w) // 2, y + int(h * 0.045)
    draw.rounded_rectangle(
        [pill_x, pill_y, pill_x + pill_w, pill_y + pill_h],
        radius=pill_h // 2, fill=accent,
    )
    draw.text((pill_x + pad_x, pill_y + pad_y), cta_txt, font=f_cta, fill="#FFFFFF")

    # contact strip
    f_c = _font(int(sub_size * 0.8), bold=False)
    contact = spec["contact"][:90]
    ctw = draw.textlength(contact, font=f_c)
    draw.text(((w - ctw) / 2, h - int(h * 0.085)), contact, font=f_c, fill=(210, 210, 220))

    # accent border
    b = max(4, int(w * 0.005))
    draw.rectangle([b, b, w - b, h - b], outline=accent2, width=max(2, b // 2))

    # subtle credit
    f_cr = _font(int(sub_size * 0.62), bold=False)
    draw.text(
        (w - margin - draw.textlength("⚡ S.T.E.W", font=f_cr), h - int(h * 0.045)),
        "⚡ S.T.E.W", font=f_cr, fill=(255, 255, 255),
    )

    buf = io.BytesIO()
    canvas.save(buf, "PNG")
    return {
        "ok": True,
        "image": buf.getvalue(),
        "error": None,
        "meta": {
            "kind": kind.title(),
            "headline": spec["headline"],
            "canvas": f"{w}x{h}",
            "engine": engine,
        },
    }

# ════════════════════════════════════════════════════════════════════════════
# MORNING MOTIVATION QUOTE CARD
# ════════════════════════════════════════════════════════════════════════════

def render_quote_image(quote: str, author: str = "Stew Daily Boost") -> bytes:
    """Motivational quote card: AI sunrise background + centered typography.
    Returns JPEG bytes (1080x1080). Falls back to a gradient if AI is down."""
    W = H = 1080

    bg = None
    try:
        bg = pollinations_image(
            "breathtaking golden sunrise over african savanna, warm orange and "
            "deep purple sky, acacia tree silhouettes, soft morning mist, "
            "cinematic, serene, photorealistic, no text", 1080, 1080)
    except Exception:
        bg = None

    if bg:
        img = Image.open(io.BytesIO(bg)).convert("RGB")
        img = _fit_bg(img, W, H)
    else:
        grad = Image.new("RGB", (1, H))
        top, bottom = (36, 22, 66), (255, 138, 40)
        for y in range(H):
            t = y / H
            grad.putpixel((0, y), tuple(int(a + (b - a) * t) for a, b in zip(top, bottom)))
        img = grad.resize((W, H))

    # dark veil so text pops on any background
    veil = Image.new("RGBA", (W, H), (10, 8, 20, 118))
    img = Image.composite(Image.new("RGB", (W, H), (10, 8, 20)), img,
                          veil.convert("L")).convert("RGB")

    draw = ImageDraw.Draw(img)
    draw.fontmode = "L"

    gold = (255, 204, 120)
    white = (255, 255, 255)

    # top label
    label = "STEW  DAILY  BOOST"
    draw.text((W // 2, 96), label, font=_font(30, bold=True), fill=gold, anchor="ma")
    lw = draw.textlength(label, font=_font(30, bold=True))
    draw.line([(W - lw) // 2, 128, (W + lw) // 2, 128], fill=gold, width=2)

    # big quote mark
    draw.text((W // 2, 268), '"', font=_font(150, bold=True), fill=gold, anchor="ma")

    # quote text — auto-fit size
    qfont = None
    for size in (58, 52, 46, 40, 34):
        qfont = _font(size, bold=True)
        lines = _wrap(draw, quote, qfont, 830)
        if len(lines) <= 7:
            break
    line_h = qfont.size + 18
    total_h = line_h * len(lines)
    y = (H - total_h) // 2 + 30
    for line in lines:
        draw.text((W // 2, y), line, font=qfont, fill=white, anchor="ma")
        y += line_h

    # author line
    draw.text((W // 2, H - 250), f"- {author}", font=_font(34, bold=False), fill=gold, anchor="ma")

    # footer
    draw.text((W // 2, H - 120), "S.T.E.W  •  your AI hustle partner", font=_font(24, bold=False),
              fill=(210, 210, 220), anchor="ma")

    out = io.BytesIO()
    img.save(out, "JPEG", quality=90)
    return out.getvalue()
