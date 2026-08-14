"""
S.T.E.W Book Generator — Professional long-form book creation engine.
Generates books up to 200 pages with front/back cover design.
"""
import io
import os
import re
import time
import logging
import requests
from urllib.parse import quote

logger = logging.getLogger(__name__)

# ── COVER IMAGE GENERATION (Pollinations) ──────────────────────────────────

def _fetch_cover_image(prompt: str, width: int = 1024, height: int = 1536,
                       max_retries: int = 3) -> bytes | None:
    """Fetch a book cover image from Pollinations AI."""
    url = f"https://image.pollinations.ai/prompt/{quote(prompt)}"
    params = {"width": width, "height": height, "model": "flux", "nologo": "true", "seed": 42}
    for attempt in range(max_retries):
        try:
            r = requests.get(url, params=params, timeout=90)
            if r.status_code == 200 and len(r.content) > 5000:
                return r.content
            logger.warning(f"Cover image attempt {attempt+1}: status={r.status_code}, size={len(r.content)}")
        except Exception as e:
            logger.warning(f"Cover image attempt {attempt+1} error: {e}")
        time.sleep(3 * (attempt + 1))
    return None


def _generate_cover_prompt(title: str, genre: str, is_back: bool = False) -> str:
    """Build an AI image prompt for a professional book cover."""
    if is_back:
        return (
            f"Professional book back cover design, {genre} genre, "
            f"dark elegant background with subtle texture, "
            f"space for author bio and barcode, "
            f"matching the front cover style of '{title}', "
            f"minimalist, high quality book design, 4k"
        )
    styles = {
        "fiction": "dramatic cinematic lighting, compelling character silhouette",
        "nonfiction": "clean professional typography layout, abstract geometric design",
        "business": "corporate professional design, gold and navy blue accents",
        "self_help": "inspirational sunrise motif, warm uplifting colors",
        "religion": "sacred majestic light from above, peaceful serene atmosphere",
        "children": "colorful playful illustration, whimsical cartoon style",
        "romance": "soft romantic pastel colors, elegant floral motifs",
        "thriller": "dark mysterious atmosphere, dramatic shadows, suspenseful",
        "biography": "dignified portrait-style composition, warm sepia tones",
        "academic": "scholarly clean design, university press style",
        "fantasy": "epic fantasy landscape, magical glowing elements",
        "scifi": "futuristic sci-fi design, neon and dark space theme",
    }
    style_desc = styles.get(genre, styles["nonfiction"])
    return (
        f"Professional book front cover for '{title}', {genre} genre, "
        f"{style_desc}, book title prominently displayed, author name at bottom, "
        f"publishing quality, 4k high resolution, portrait orientation"
    )


# ── BOOK OUTLINE GENERATION ─────────────────────────────────────────────────

def build_book_outline_prompt(topic: str, num_chapters: int, pages: int) -> str:
    """System prompt for generating a book outline."""
    return (
        f"You are a professional book author and editor. Create a detailed book outline "
        f"for a {pages}-page book about '{topic}'. The book should have {num_chapters} chapters. "
        f"Return ONLY a JSON array of objects, each with 'title' (chapter title) and "
        f"'summary' (2-3 sentence description of what the chapter covers). "
        f"The first chapter should be an introduction/overview. "
        f"The last chapter should be a conclusion. "
        f"Make chapter titles compelling and professional. JSON array only."
    )


def build_chapter_content_prompt(topic: str, chapter_title: str, chapter_summary: str,
                                  chapter_num: int, total_chapters: int, pages_per_chapter: int) -> str:
    """System prompt for generating a single chapter's content."""
    return (
        f"You are a professional book author. Write Chapter {chapter_num} of {total_chapters} "
        f"for a book about '{topic}'.\n\n"
        f"Chapter title: {chapter_title}\n"
        f"Chapter summary: {chapter_summary}\n\n"
        f"Write approximately {pages_per_chapter} pages of rich, engaging, professional content. "
        f"Use clear section headings (prefixed with '## '), sub-headings (prefixed with '### '), "
        f"and well-structured paragraphs. Include examples, anecdotes, and practical insights. "
        f"Write in a flowing narrative style appropriate for a published book.\n\n"
        f"DO NOT include the chapter number or title at the top — just start with the first '## ' section. "
        f"DO NOT use markdown bold (**text**) or italic (*text*) markers. "
        f"End with a brief transition to the next chapter."
    )


# ── BOOK COMPILATION (DOCX with covers) ────────────────────────────────────

def generate_book_docx(chapters: list[dict], title: str, author: str,
                       genre: str, front_cover_bytes: bytes | None = None,
                       back_cover_bytes: bytes | None = None) -> dict:
    """Generate a professional book DOCX with front cover, TOC, chapters, back cover."""
    from docx import Document
    from docx.shared import Pt, Inches, RGBColor, Emu
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml.ns import qn
    from docx.oxml import OxmlElement
    import tempfile, base64

    doc = Document()

    # Set page size to 6x9 (trade paperback)
    section = doc.sections[0]
    section.page_width = Inches(6)
    section.page_height = Inches(9)
    section.top_margin = Inches(0.75)
    section.bottom_margin = Inches(0.75)
    section.left_margin = Inches(0.75)
    section.right_margin = Inches(0.75)

    # ── FRONT COVER ─────────────────────────────────────────────────────
    if front_cover_bytes:
        try:
            cover_path = tempfile.mktemp(suffix=".jpg")
            with open(cover_path, "wb") as f:
                f.write(front_cover_bytes)
            doc.add_picture(cover_path, width=Inches(4.5))
            # Center the cover image
            last_paragraph = doc.paragraphs[-1]
            last_paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
            os.unlink(cover_path)
        except Exception as e:
            logger.warning(f"Could not embed front cover: {e}")
            # Text-only cover fallback
            cover_title = doc.add_paragraph()
            cover_title.alignment = WD_ALIGN_PARAGRAPH.CENTER
            run = cover_title.add_run(title)
            run.font.size = Pt(28)
            run.font.bold = True
            run.font.color.rgb = RGBColor(0x1A, 0x1A, 0x2E)
            doc.add_paragraph()
    else:
        cover_title = doc.add_paragraph()
        cover_title.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = cover_title.add_run(title)
        run.font.size = Pt(28)
        run.font.bold = True
        run.font.color.rgb = RGBColor(0x1A, 0x1A, 0x2E)
        if author:
            author_para = doc.add_paragraph()
            author_para.alignment = WD_ALIGN_PARAGRAPH.CENTER
            arun = author_para.add_run(f"by {author}")
            arun.font.size = Pt(14)
            arun.font.color.rgb = RGBColor(0x66, 0x66, 0x66)
        doc.add_paragraph()

    doc.add_page_break()

    # ── COPYRIGHT PAGE ──────────────────────────────────────────────────
    doc.add_paragraph()
    doc.add_paragraph()
    copyright_p = doc.add_paragraph()
    copyright_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    crun = copyright_p.add_run(f"Copyright (c) {time.strftime('%Y')} {author or 'Author'}")
    crun.font.size = Pt(10)
    crun.font.color.rgb = RGBColor(0x99, 0x99, 0x99)

    cp2 = doc.add_paragraph()
    cp2.alignment = WD_ALIGN_PARAGRAPH.CENTER
    cp2.add_run("All rights reserved.\n").font.size = Pt(10)
    cp2.add_run("Generated by S.T.E.W Agent").font.size = Pt(9)

    doc.add_page_break()

    # ── TABLE OF CONTENTS ───────────────────────────────────────────────
    toc_heading = doc.add_heading("Table of Contents", level=1)
    toc_heading.alignment = WD_ALIGN_PARAGRAPH.CENTER
    doc.add_paragraph()

    for i, ch in enumerate(chapters, 1):
        toc_p = doc.add_paragraph()
        toc_p.paragraph_format.space_after = Pt(4)
        run = toc_p.add_run(f"Chapter {i}: {ch.get('title', f'Chapter {i}')}")
        run.font.size = Pt(12)
        # Page number placeholder
        toc_p.add_run(f"  ....  {i * (200 // max(len(chapters), 1))}").font.size = Pt(10)

    doc.add_page_break()

    # ── CHAPTERS ───────────────────────────────────────────────────────
    for i, ch in enumerate(chapters, 1):
        # Chapter title page
        ch_title_p = doc.add_paragraph()
        ch_title_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        ch_title_p.paragraph_format.space_before = Inches(2)
        ch_num_run = ch_title_p.add_run(f"Chapter {i}")
        ch_num_run.font.size = Pt(14)
        ch_num_run.font.color.rgb = RGBColor(0x99, 0x99, 0x99)

        ch_name_p = doc.add_paragraph()
        ch_name_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        ch_name_run = ch_name_p.add_run(ch.get("title", f"Chapter {i}"))
        ch_name_run.font.size = Pt(22)
        ch_name_run.font.bold = True
        ch_name_run.font.color.rgb = RGBColor(0x1A, 0x1A, 0x2E)

        doc.add_paragraph()

        # Chapter content
        content = ch.get("content", "")
        lines = content.split("\n")
        for line in lines:
            stripped = line.strip()
            if not stripped:
                doc.add_paragraph()
            elif stripped.startswith("## "):
                heading_text = stripped[3:].replace("**", "").replace("*", "")
                h = doc.add_heading(heading_text, level=2)
            elif stripped.startswith("### "):
                heading_text = stripped[4:].replace("**", "").replace("*", "")
                h = doc.add_heading(heading_text, level=3)
            elif stripped.startswith("# "):
                heading_text = stripped[2:].replace("**", "").replace("*", "")
                h = doc.add_heading(heading_text, level=1)
            elif stripped.startswith("- ") or stripped.startswith("* "):
                clean = stripped.lstrip("-* ").replace("**", "").replace("*", "")
                doc.add_paragraph(clean, style="List Bullet")
            else:
                p = doc.add_paragraph(stripped.replace("**", "").replace("*", ""))
                p.paragraph_format.first_line_indent = Inches(0.3)
                for run in p.runs:
                    run.font.size = Pt(11)
                    run.font.line_spacing = 1.15

        # Page break after each chapter (except last)
        if i < len(chapters):
            doc.add_page_break()

    # ── BACK COVER ──────────────────────────────────────────────────────
    doc.add_page_break()
    if back_cover_bytes:
        try:
            back_path = tempfile.mktemp(suffix=".jpg")
            with open(back_path, "wb") as f:
                f.write(back_cover_bytes)
            doc.add_picture(back_path, width=Inches(4.5))
            last_paragraph = doc.paragraphs[-1]
            last_paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
            os.unlink(back_path)
        except Exception as e:
            logger.warning(f"Could not embed back cover: {e}")
            blurb = doc.add_paragraph()
            blurb.alignment = WD_ALIGN_PARAGRAPH.CENTER
            blurb.add_run(f"About {author}\n").font.bold = True
            blurb.add_run(f"{author} is an author and creator. This book was generated with S.T.E.W Agent.")

    # ── SAVE ────────────────────────────────────────────────────────────
    buf = io.BytesIO()
    doc.save(buf)
    safe_title = re.sub(r'[^\w\- ]', '', title)[:60].strip().replace(" ", "_") or "book"
    filename = f"{safe_title}.docx"

    return {
        "file_bytes": buf.getvalue(),
        "filename": filename,
        "format": "docx",
        "pages_estimated": sum(len(ch.get("content", "")) // 3000 for ch in chapters),
        "chapters": len(chapters),
    }


# ── BOOK GENERATION ORCHESTRATOR ────────────────────────────────────────────

def generate_book(topic: str, author: str = "", pages: int = 100,
                   llm_chat_fn=None, llm_complete_fn=None) -> dict:
    """Full book generation pipeline.
    
    Args:
        topic: Book subject/title
        author: Author name
        pages: Target page count (up to 200)
        llm_chat_fn: async fn(messages, max_tokens) -> {"content": str}
        llm_complete_fn: sync fn(prompt, system) -> str
    
    Returns dict with file_bytes, filename, etc.
    """
    import json as _json

    pages = max(10, min(pages, 200))

    # Determine genre from topic
    topic_lower = topic.lower()
    genre = "nonfiction"
    genre_map = {
        "business": "business", "entrepreneur": "business", "startup": "business",
        "money": "business", "finance": "business", "invest": "business",
        "church": "religion", "god": "religion", "faith": "religion",
        "christian": "religion", "prayer": "religion", "spiritual": "religion",
        "love": "romance", "romance": "romance", "relationship": "romance",
        "child": "children", "kids": "children",
        "biography": "biography", "memoir": "biography", "life story": "biography",
        "novel": "fiction", "story": "fiction", "fiction": "fiction",
        "fantasy": "fantasy", "magic": "fantasy",
        "science fiction": "scifi", "sci-fi": "scifi", "space": "scifi",
        "thriller": "thriller", "mystery": "thriller", "crime": "thriller",
        "study": "academic", "research": "academic", "textbook": "academic",
        "self help": "self_help", "motivation": "self_help", "success": "self_help",
    }
    for keyword, g in genre_map.items():
        if keyword in topic_lower:
            genre = g
            break

    # Calculate chapters
    num_chapters = max(5, min(pages // 10, 20))
    pages_per_chapter = pages // num_chapters

    # Step 1: Generate outline
    logger.info(f"Generating book outline: {num_chapters} chapters, {pages} pages, genre={genre}")
    outline_prompt = build_book_outline_prompt(topic, num_chapters, pages)
    outline_msg = [{"role": "system", "content": outline_prompt},
                   {"role": "user", "content": f"Book topic: {topic}"}]

    outline_raw = None
    if llm_chat_fn:
        result = llm_chat_fn(outline_msg, max_tokens=4000)
        outline_raw = result.get("content", "")
    elif llm_complete_fn:
        outline_raw = llm_complete_fn(f"Book topic: {topic}", system=outline_prompt)

    chapters = []
    if outline_raw:
        json_match = re.search(r'\[.*\]', outline_raw, re.DOTALL)
        if json_match:
            try:
                chapters = _json.loads(json_match.group())
            except Exception:
                pass

    if not chapters:
        # Fallback: generate generic chapters
        chapters = [{"title": f"Chapter {i}: {topic}", "summary": f"Content about {topic}"} 
                     for i in range(1, num_chapters + 1)]

    # Step 2: Generate cover images (non-blocking, done first)
    logger.info("Generating book cover images...")
    front_prompt = _generate_cover_prompt(topic, genre, is_back=False)
    back_prompt = _generate_cover_prompt(topic, genre, is_back=True)
    
    front_cover = _fetch_cover_image(front_prompt, width=768, height=1152)
    back_cover = _fetch_cover_image(back_prompt, width=768, height=1152)

    # Step 3: Generate each chapter's content
    for i, ch in enumerate(chapters):
        ch_title = ch.get("title", f"Chapter {i+1}")
        ch_summary = ch.get("summary", "")
        
        logger.info(f"Generating chapter {i+1}/{len(chapters)}: {ch_title}")
        chapter_sys = build_chapter_content_prompt(
            topic, ch_title, ch_summary, i + 1, len(chapters), pages_per_chapter
        )
        
        chapter_content = None
        if llm_chat_fn:
            result = llm_chat_fn(
                [{"role": "system", "content": chapter_sys},
                 {"role": "user", "content": f"Write chapter {i+1} now."}],
                max_tokens=8000
            )
            chapter_content = result.get("content", "")
        elif llm_complete_fn:
            chapter_content = llm_complete_fn(f"Write chapter {i+1} now.", system=chapter_sys)

        if not chapter_content:
            chapter_content = f"## {ch_title}\n\nContent for this chapter about {topic}."

        ch["content"] = chapter_content

    # Step 4: Compile the book
    logger.info("Compiling book DOCX...")
    return generate_book_docx(chapters, topic, author, genre, front_cover, back_cover)


# ── SONG / MUSIC GENERATION ──────────────────────────────────────────────────

def generate_song(prompt: str, llm_complete_fn=None, llm_chat_fn=None,
                   duration_seconds: int = 30) -> dict:
    """Generate a song with lyrics, album art, and audio.
    
    Uses HuggingFace MusicGen for instrumental audio (free inference API),
    falls back to Pollinations TTS for spoken-word version.
    Also generates lyrics via LLM and album cover via Pollinations.
    
    Returns dict with audio_bytes, lyrics, cover_bytes, filename.
    """
    import json as _json

    # Step 1: Generate lyrics
    lyrics = ""
    lyrics_prompt = (
        "You are a professional songwriter. Write original song lyrics based on the user's request. "
        "Include verse(s), chorus, and bridge. Format clearly with [Verse], [Chorus], [Bridge] labels. "
        "Make it catchy, emotional, and memorable. Do not use markdown. Plain text only."
    )
    if llm_chat_fn:
        result = llm_chat_fn(
            [{"role": "system", "content": lyrics_prompt},
             {"role": "user", "content": f"Write a song about: {prompt}"}],
            max_tokens=2000
        )
        lyrics = result.get("content", "")
    elif llm_complete_fn:
        lyrics = llm_complete_fn(f"Write a song about: {prompt}", system=lyrics_prompt)

    if not lyrics:
        lyrics = f"[Verse 1]\n{prompt}\n\n[Chorus]\n{prompt}\n\n[Verse 2]\n{prompt}\n\n[Outro]\n{prompt}"

    # Step 2: Generate album cover
    cover_prompt = (
        f"Professional album cover art for a song about '{prompt[:100]}', "
        f"musical theme, vibrant colors, high quality, square format, 4k"
    )
    cover_bytes = _fetch_cover_image(cover_prompt, width=1024, height=1024)

    # Step 3: Generate audio via HuggingFace MusicGen (free inference API)
    audio_bytes = None
    audio_format = "wav"

    # Try HuggingFace Inference API
    hf_token = os.environ.get("HF_TOKEN") or os.environ.get("HUGGINGFACE_API_KEY") or ""
    if hf_token:
        try:
            logger.info("Attempting music generation via HuggingFace MusicGen...")
            hf_url = "https://api-inference.huggingface.co/models/facebook/musicgen-small"
            headers = {"Authorization": f"Bearer {hf_token}"}
            payload = {
                "inputs": f"{prompt}, high quality music, professional production",
                "parameters": {
                    "do_sample": True,
                    "max_new_tokens": min(duration_seconds * 50, 1500),
                    "temperature": 1.0,
                    "guidance_scale": 3.0,
                }
            }
            for attempt in range(3):
                resp = requests.post(hf_url, headers=headers, json=payload, timeout=120)
                if resp.status_code == 200:
                    audio_bytes = resp.content
                    logger.info(f"MusicGen returned {len(audio_bytes)} bytes of audio")
                    break
                elif resp.status_code == 503:
                    # Model loading, wait and retry
                    logger.info(f"MusicGen loading (503), retry {attempt+1}...")
                    time.sleep(15)
                else:
                    logger.warning(f"MusicGen API error: {resp.status_code} - {resp.text[:200]}")
                    break
        except Exception as e:
            logger.warning(f"MusicGen generation failed: {e}")

    # Fallback: Try Pollinations TTS for spoken lyrics
    if not audio_bytes:
        logger.info("Falling back to Pollinations TTS for audio...")
        try:
            # Use pollinations openai-audio for TTS
            tts_text = lyrics[:2000]  # TTS has limits
            tts_url = f"https://text.pollinations.ai/{quote(tts_text)}?model=openai-audio&voice=nova"
            for attempt in range(3):
                resp = requests.get(tts_url, timeout=90)
                if resp.status_code == 200 and len(resp.content) > 1000:
                    audio_bytes = resp.content
                    audio_format = "mp3"
                    logger.info(f"TTS returned {len(audio_bytes)} bytes")
                    break
                logger.warning(f"TTS attempt {attempt+1}: status={resp.status_code}")
                time.sleep(3 * (attempt + 1))
        except Exception as e:
            logger.warning(f"TTS fallback failed: {e}")

    safe_name = re.sub(r'[^\w\- ]', '', prompt)[:40].strip().replace(" ", "_") or "song"

    return {
        "lyrics": lyrics,
        "cover_bytes": cover_bytes,
        "audio_bytes": audio_bytes,
        "audio_format": audio_format,
        "filename": f"{safe_name}.{audio_format}",
    }
