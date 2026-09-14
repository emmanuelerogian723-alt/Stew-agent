"""
S.T.E.W Illustrated Storybook Engine — real story books with a picture on
every chapter, compiled into a beautiful PDF.

Flow: LLM writes the story chapter by chapter → every chapter gets its own
illustration (free Pollinations FLUX) → reportlab compiles a real book:
full-page cover, chapter pages with picture + text, page numbers.

Gen Z styles supported: anime, comic, watercolor, 3D Pixar-style, and more —
the style hint is passed through to both the writer and the artist.
"""
import asyncio
import io
import json
import logging
import re
import time
import urllib.parse
import urllib.request
from typing import Optional, Callable

logger = logging.getLogger("stew.storybook")

DEFAULT_CHAPTERS = 6
STYLE_HINTS = {
    "anime": "vibrant anime illustration, clean line art, expressive characters",
    "comic": "comic book art, bold ink lines, halftone shading, dynamic panels",
    "watercolor": "soft watercolor children's book illustration, gentle pastel palette",
    "3d": "3D animated movie style, Pixar-like, soft lighting, big expressive eyes",
    "pixel": "retro pixel art, 16-bit RPG style",
    "realistic": "cinematic digital painting, dramatic lighting",
}
DEFAULT_STYLE = "whimsical children's storybook illustration, warm colors, storybook lighting"


# ── image fetching (free, no key) ────────────────────────────────────────────
def _fetch_image(prompt: str, width: int = 768, height: int = 768,
                 seed: int = 0, retries: int = 3) -> Optional[bytes]:
    """Illustrate via the S.T.E.W Image Engine v2 (Cloudflare FLUX-2 first,
    Pollinations as the final fallback). Synchronous for thread-pool use."""
    from server.image_gen import generate_image_sync
    img, _provider = generate_image_sync(prompt, width, height)
    return img


# ── prompts ──────────────────────────────────────────────────────────────────
def _outline_prompt(topic: str, num_chapters: int, style: str) -> list[dict]:
    return [
        {"role": "system", "content":
            "You are a professional children's/young-adult story author. Reply with ONLY valid JSON."},
        {"role": "user", "content":
            f"Create a short illustrated storybook outline about: {topic}\n"
            f"Genre/audience style: {style}\n"
            f"Give me exactly {num_chapters} chapters.\n\n"
            'Reply as JSON only: {"title": string, "chapters": [{"title": string, "summary": string}]} '
            "Chapter summaries are 1-2 sentences. Make the story warm, vivid, with a clear beginning, "
            "middle (problem), and a satisfying ending."},
    ]


def _chapter_prompt(title: str, chapter_title: str, summary: str,
                    prev_ending: str, style: str) -> list[dict]:
    prev = f"Previous chapter ended with: {prev_ending}" if prev_ending else "This is the opening chapter."
    return [
        {"role": "system", "content":
            "You are a professional storybook author writing one illustrated chapter. "
            "Reply with ONLY valid JSON."},
        {"role": "user", "content":
            f"Book title: {title}\nChapter title: {chapter_title}\nChapter summary: {summary}\n{prev}\n"
            f"Visual style: {style}\n\n"
            "Write this chapter (120-220 words, vivid, emotional, suitable for reading aloud) "
            'and an illustration prompt for the single picture on this chapter page. '
            'Reply as JSON only: {"text": string, "image_prompt": string} — the image_prompt must '
            f"start with: {style}, and describe ONE scene from the chapter."},
    ]


def _safe_json(raw: str) -> Optional[dict]:
    if not raw:
        return None
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        try:
            return json.loads(m.group(0).replace("\n", " "))
        except Exception:
            return None


def _llm_json(llm_chat_fn: Callable, messages: list[dict]) -> Optional[dict]:
    try:
        result = llm_chat_fn(messages, max_tokens=1200)
        content = result.get("content") if isinstance(result, dict) else str(result)
        return _safe_json(content)
    except Exception as e:
        logger.warning(f"storybook LLM call failed: {e}")
        return None


# ── PDF compilation ──────────────────────────────────────────────────────────
def _build_pdf(book: dict) -> bytes:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import cm
    from reportlab.pdfgen import canvas
    from PIL import Image

    pw, ph = A4
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    title = book["title"]

    # ── cover page ──
    img = book.get("cover_image")
    if img:
        try:
            im = Image.open(io.BytesIO(img)).convert("RGB")
            iw, ih = im.size
            scale = min(pw / iw, (ph - 4 * cm) / ih)
            im = im.resize((int(iw * scale), int(ih * scale)))
            path = f"/tmp/stew_cover_{int(time.time())}.png"
            im.save(path)
            c.drawImage(path, (pw - im.size[0]) / 2, 2.6 * cm,
                        im.size[0], im.size[1], preserveAspectRatio=True)
        except Exception as e:
            logger.warning(f"cover draw failed: {e}")
    c.setFont("Helvetica-Bold", 26)
    c.drawCentredString(pw / 2, 1.9 * cm, title[:60])
    c.setFont("Helvetica-Oblique", 11)
    c.drawCentredString(pw / 2, 1.25 * cm, "An illustrated storybook by Stew")
    c.showPage()

    # ── chapter pages ──
    page_no = 2
    for ch in book["chapters"]:
        # illustration (full width top)
        img = ch.get("image")
        if img:
            try:
                im = Image.open(io.BytesIO(img)).convert("RGB")
                iw, ih = im.size
                scale = min((pw - 2 * cm) / iw, (ph * 0.55) / ih)
                im = im.resize((int(iw * scale), int(ih * scale)))
                path = f"/tmp/stew_ch_{page_no}_{int(time.time()*1000)%99999}.png"
                im.save(path)
                c.drawImage(path, (pw - im.size[0]) / 2, ph - 2 * cm - im.size[1],
                            im.size[0], im.size[1], preserveAspectRatio=True)
            except Exception as e:
                logger.warning(f"chapter image draw failed: {e}")
        # chapter title + text
        y = ph * 0.32
        c.setFont("Helvetica-Bold", 17)
        c.drawString(2 * cm, y, f"Chapter {ch.get('num', '?')} — {ch.get('title', '')}"[:70])
        c.setFont("Helvetica", 11.5)
        y -= 1.1 * cm
        for para in (ch.get("text") or "").split("\n"):
            para = para.strip()
            if not para:
                y -= 0.35 * cm
                continue
            from reportlab.lib.utils import simpleSplit
            for line in simpleSplit(para, "Helvetica", 11.5, pw - 4 * cm):
                if y < 1.6 * cm:
                    c.showPage()
                    page_no += 1
                    c.setFont("Helvetica", 11.5)
                    y = ph - 2 * cm
                c.drawString(2 * cm, y, line)
                y -= 0.52 * cm
        # page footer
        c.setFont("Helvetica", 9)
        c.drawCentredString(pw / 2, 0.9 * cm, f"— {page_no} —")
        c.showPage()
        page_no += 1

    c.save()
    return buf.getvalue()


# ── main entry ────────────────────────────────────────────────────────────────
async def generate_storybook(topic: str, llm_chat_fn: Callable,
                             chapters: int = DEFAULT_CHAPTERS,
                             style: str = "", progress_cb=None) -> tuple[Optional[bytes], dict]:
    """Returns (pdf_bytes, meta). meta: {'title', 'chapters', 'style'}"""
    topic = (topic or "").strip()[:300]
    if not topic:
        return None, {"error": "no topic"}

    # style detection from the topic text
    style_key = None
    low = topic.lower()
    for key in STYLE_HINTS:
        if key in low:
            style_key = key
            break
    style = STYLE_HINTS.get(style_key, DEFAULT_STYLE) if not style else style

    async def _say(msg):
        if progress_cb:
            try:
                await progress_cb(msg)
            except Exception:
                pass

    await _say(f"📖 Outlining your storybook ({chapters} chapters)…")
    outline = await asyncio.to_thread(_llm_json, llm_chat_fn, _outline_prompt(topic, chapters, style))
    if not outline or not outline.get("chapters"):
        return None, {"error": "could not build story outline"}
    title = (outline.get("title") or topic).strip()
    ch_list = outline["chapters"][:chapters]

    await _say(f'✍️ Writing "{title}" chapter by chapter…')
    book = {"title": title, "chapters": []}
    prev_ending = ""
    texts = []
    for i, ch in enumerate(ch_list, 1):
        data = await asyncio.to_thread(
            _llm_json, llm_chat_fn,
            _chapter_prompt(title, ch.get("title", f"Chapter {i}"),
                            ch.get("summary", ""), prev_ending, style))
        text = (data or {}).get("text") or (ch.get("summary") or "")
        image_prompt = (data or {}).get("image_prompt") or f"{style}, scene from: {ch.get('summary','')}"
        texts.append((ch.get("title", f"Chapter {i}"), text, image_prompt))
        prev_ending = text[-160:]

    # images in parallel batches (cover + all chapters)
    await _say("🎨 Illustrating every chapter…")
    prompts = [(f"{style}, storybook cover art for '{title}', no text on image", 768, 768)]
    for _, _, ip in texts:
        prompts.append((ip, 768, 640))
    # Pollinations rate-limits bursts — fetch sequentially with pacing
    images = []
    base_seed = int(time.time()) % 99999
    for i, (pr, w, h) in enumerate(prompts):
        if i > 0:
            await asyncio.sleep(3)
        img = await asyncio.to_thread(_fetch_image, pr, w, h, base_seed + i * 7)
        images.append(img)
        if progress_cb and img:
            try:
                await progress_cb(f"🎨 Illustrated {i}/{len(prompts)-1}…")
            except Exception:
                pass
    book["cover_image"] = images[0]

    for i, (ch_title, text, _) in enumerate(texts):
        book["chapters"].append({
            "num": i + 1, "title": ch_title, "text": text, "image": images[i + 1],
        })

    await _say("📕 Binding your storybook PDF…")
    pdf = await asyncio.to_thread(_build_pdf, book)
    if not pdf:
        return None, {"error": "pdf build failed"}
    return pdf, {"title": title, "chapters": len(texts),
                 "illustrated": sum(1 for im in images if im), "style": style}


def is_storybook_intent(text: str) -> Optional[str]:
    """/storybook X, 'write me a storybook about X', 'children's book about X' → topic."""
    import re as _re
    t = (text or "").strip()
    if not t:
        return None
    low = t.lower()
    if low.startswith("/storybook"):
        return t[len("/storybook"):].strip() or None
    m = _re.search(r"(?:write|make|create|generate|draw)\b.*?\b"
                   r"(?:illustrated\s+|picture\s+)?(?:storybook|story\s?book|children'?s?\s+book)\b\s*"
                   r"(?:about|on|for|of|called|titled|with)?\s*[:\-]?\s*(.+)$", low, _re.DOTALL)
    if m:
        return t[m.start(1):].strip()
    return None
