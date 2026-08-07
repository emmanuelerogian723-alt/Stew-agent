"""
S.T.E.W OCR Engine — Free, open-source OCR for images, PDFs, and screenshots.

Uses Tesseract OCR (via pytesseract) as the primary engine with
pdf2image/pymupdf for PDF page-to-image conversion.

Supports: PNG, JPG, JPEG, WEBP, BMP, TIFF, GIF, PDF

Features:
  - Multi-page PDF processing (each page -> image -> OCR)
  - Bounding boxes and text positions
  - Confidence scores per word
  - Automatic language detection
  - Document structure detection (paragraphs, lines)
  - Connects to S.T.E.W reasoning for Q&A / summarization
"""
import io
import re
import logging
import asyncio
from typing import Any, Optional

import pytesseract
from PIL import Image, ImageEnhance, ImageFilter

def _get_available_langs():
    """Get available Tesseract languages, falling back to eng."""
    try:
        langs = pytesseract.get_languages()
        return set(langs) if langs else {"eng"}
    except Exception:
        return {"eng"}

def _resolve_lang(lang: str) -> str:
    """Resolve requested language to available Tesseract language(s)."""
    available = _get_available_langs()
    requested = [l.strip() for l in lang.split("+") if l.strip()]
    resolved = [l for l in requested if l in available]
    if not resolved:
        return "eng"
    return "+".join(resolved)

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────

MAX_FILE_SIZE = 25 * 1024 * 1024  # 25 MB
ALLOWED_IMAGE_EXTS = {"png", "jpg", "jpeg", "webp", "bmp", "tiff", "tif", "gif"}
ALLOWED_EXTS = ALLOWED_IMAGE_EXTS | {"pdf"}

# Languages supported by installed Tesseract packs
SUPPORTED_LANGS = {
    "eng": "English",
    "fra": "French",
    "spa": "Spanish",
    "deu": "German",
    "ita": "Italian",
    "por": "Portuguese",
    "nld": "Dutch",
    "rus": "Russian",
    "chi_sim": "Chinese (Simplified)",
    "chi_tra": "Chinese (Traditional)",
    "jpn": "Japanese",
    "kor": "Korean",
    "ara": "Arabic",
    "hin": "Hindi",
    "tur": "Turkish",
    "vie": "Vietnamese",
    "tha": "Thai",
}


# ── Security ─────────────────────────────────────────────────────────────────

def validate_file(filename: str, content: bytes) -> str:
    """Validate uploaded file. Returns the lowercase extension."""
    if len(content) > MAX_FILE_SIZE:
        raise ValueError(f"File too large (max {MAX_FILE_SIZE // (1024*1024)}MB)")

    if not filename:
        raise ValueError("Filename is required")

    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

    if ext not in ALLOWED_EXTS:
        raise ValueError(
            f"Unsupported file type '.{ext}'. "
            f"Allowed: {', '.join(sorted(ALLOWED_EXTS))}"
        )

    # Basic magic-byte validation for images
    if ext in ALLOWED_IMAGE_EXTS:
        _validate_image_bytes(content, ext)

    return ext


def _validate_image_bytes(content: bytes, ext: str):
    """Check magic bytes to prevent disguised uploads."""
    if len(content) < 8:
        raise ValueError("File too small to be a valid image")

    signatures = {
        "png": b"\x89PNG\r\n\x1a\n",
        "jpg": b"\xff\xd8\xff",
        "jpeg": b"\xff\xd8\xff",
        "gif": b"GIF8",
        "bmp": b"BM",
        "webp": b"RIFF",
    }

    if ext in ("tiff", "tif"):
        if not (content[:4] == b"II*\x00" or content[:4] == b"MM\x00*"):
            raise ValueError("File does not appear to be a valid TIFF image")
    elif ext in signatures:
        sig = signatures[ext]
        if not content.startswith(sig):
            raise ValueError(f"File does not appear to be a valid {ext.upper()} image")


# ── Image Preprocessing ─────────────────────────────────────────────────────

def _preprocess_image(img: Image.Image) -> Image.Image:
    """Enhance image for better OCR accuracy."""
    # Convert to RGB if needed (handles RGBA, P, L modes)
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")

    # Convert to grayscale for OCR
    gray = img.convert("L")

    # Upscale small images
    w, h = gray.size
    if w < 1000:
        scale = max(2, 1000 // w)
        gray = gray.resize((w * scale, h * scale), Image.Resampling.LANCZOS)

    # Slight sharpening
    gray = gray.filter(ImageFilter.SHARPEN)

    # Contrast enhancement
    enhancer = ImageEnhance.Contrast(gray)
    gray = enhancer.enhance(1.5)

    return gray


# ── OCR Core ─────────────────────────────────────────────────────────────────

def _detect_language(text: str) -> str:
    """Detect the dominant language from OCR output."""
    if not text.strip():
        return "unknown"

    cjk_chars = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
    if cjk_chars > len(text) * 0.2:
        return "chi_sim"
    japanese_chars = sum(1 for c in text if '\u3040' <= c <= '\u30ff')
    if japanese_chars > len(text) * 0.1:
        return "jpn"
    korean_chars = sum(1 for c in text if '\uac00' <= c <= '\ud7af')
    if korean_chars > len(text) * 0.1:
        return "kor"
    arabic_chars = sum(1 for c in text if '\u0600' <= c <= '\u06ff')
    if arabic_chars > len(text) * 0.1:
        return "ara"
    cyrillic_chars = sum(1 for c in text if '\u0400' <= c <= '\u04ff')
    if cyrillic_chars > len(text) * 0.1:
        return "rus"

    return "eng"


def _ocr_image(img: Image.Image, lang: str = "eng",
               include_boxes: bool = True, include_confidence: bool = True) -> dict:
    """
    Run Tesseract OCR on a single PIL Image.
    Returns text, structured data, and metadata.
    """
    processed = _preprocess_image(img)

    # Get the raw text
    text = pytesseract.image_to_string(processed, lang=lang).strip()

    # Get detailed data with bounding boxes and confidence
    data = pytesseract.image_to_data(
        processed, lang=lang, output_type=pytesseract.Output.DICT
    )

    # Build structured output
    words = []
    confidences = []
    lines = []
    current_line_words = []
    current_line_num = -1

    for i, word_text in enumerate(data["text"]):
        word = word_text.strip()
        if not word:
            continue

        conf = data["conf"][i]
        try:
            conf_val = float(conf)
        except (ValueError, TypeError):
            conf_val = 0.0

        word_entry = {
            "text": word,
            "confidence": round(conf_val, 2),
            "bbox": {
                "x": data["left"][i],
                "y": data["top"][i],
                "width": data["width"][i],
                "height": data["height"][i],
            },
        }
        words.append(word_entry)
        if conf_val > 0:
            confidences.append(conf_val)

        # Group words into lines
        line_num = data["line_num"][i]
        if line_num != current_line_num:
            if current_line_words:
                lines.append(" ".join(current_line_words))
            current_line_words = [word]
            current_line_num = line_num
        else:
            current_line_words.append(word)

    if current_line_words:
        lines.append(" ".join(current_line_words))

    # Group lines into paragraphs
    paragraphs = _group_paragraphs(lines)

    avg_conf = round(sum(confidences) / len(confidences), 2) if confidences else 0.0

    detected_lang = _detect_language(text)

    result = {
        "text": text,
        "word_count": len(words),
        "char_count": len(text),
        "avg_confidence": avg_conf,
        "detected_language": detected_lang,
        "lines": lines,
        "paragraphs": paragraphs,
    }

    if include_boxes:
        result["words"] = words

    return result


def _group_paragraphs(lines: list) -> list:
    """Group consecutive lines into paragraphs based on indentation and spacing."""
    if not lines:
        return []

    paragraphs = []
    current = lines[0]

    for i in range(1, len(lines)):
        prev = lines[i-1]
        curr = lines[i]

        starts_new = (
            (curr and curr[0].isupper() and prev and not prev.endswith(('.', '!', '?', ':', ';', ',')))
            or (len(curr.strip()) == 0)
        )

        if starts_new and current:
            paragraphs.append(current.strip())
            current = curr
        else:
            current = current + " " + curr

    if current:
        paragraphs.append(current.strip())

    return [p for p in paragraphs if p]


# ── PDF Processing ──────────────────────────────────────────────────────────

def _pdf_to_images(content: bytes) -> list:
    """Convert PDF pages to PIL Images using pymupdf (fitz) or pdf2image."""
    # Try PyMuPDF first (no system deps)
    try:
        import fitz  # PyMuPDF
        images = []
        doc = fitz.open(stream=content, filetype="pdf")
        for page_num in range(len(doc)):
            page = doc[page_num]
            mat = fitz.Matrix(200/72, 200/72)
            pix = page.get_pixmap(matrix=mat)
            img_data = pix.tobytes("png")
            images.append(Image.open(io.BytesIO(img_data)))
        doc.close()
        return images
    except ImportError:
        pass

    # Fallback: pdf2image (needs poppler)
    try:
        from pdf2image import convert_from_bytes
        images = convert_from_bytes(content, dpi=200, fmt="png")
        return images
    except ImportError:
        raise RuntimeError(
            "PDF processing requires PyMuPDF (pip install pymupdf) or pdf2image+poppler. "
            "Neither is available."
        )


# ── Public API ───────────────────────────────────────────────────────────────

def ocr_file(
    content: bytes,
    filename: str,
    lang: str = "eng",
    include_boxes: bool = True,
    include_confidence: bool = True,
    max_pages: int = 50,
) -> dict:
    """
    Run OCR on an uploaded image or PDF.

    Args:
        content: Raw file bytes
        filename: Original filename
        lang: Tesseract language code (e.g. 'eng', 'fra', 'eng+fra')
        include_boxes: Include word-level bounding boxes
        include_confidence: Include per-word confidence scores
        max_pages: Max PDF pages to process

    Returns:
        dict with: text, pages, confidence, language, structure, words
    """
    ext = validate_file(filename, content)

    lang = _resolve_lang(lang)
    if ext == "pdf":
        return _ocr_pdf(content, filename, lang, include_boxes, include_confidence, max_pages)
    else:
        return _ocr_single_image(content, filename, lang, include_boxes, include_confidence)


def _ocr_single_image(content: bytes, filename: str, lang: str,
                      include_boxes: bool, include_confidence: bool) -> dict:
    """OCR a single image file."""
    img = Image.open(io.BytesIO(content))
    result = _ocr_image(img, lang, include_boxes, include_confidence)
    result["filename"] = filename
    result["file_type"] = "image"
    result["page_count"] = 1
    result["pages"] = [
        {k: v for k, v in result.items() if k != "filename"}
    ]
    return result


def _ocr_pdf(content: bytes, filename: str, lang: str,
             include_boxes: bool, include_confidence: bool, max_pages: int) -> dict:
    """OCR every page of a PDF."""
    images = _pdf_to_images(content)

    if len(images) > max_pages:
        images = images[:max_pages]

    all_pages = []
    full_text_parts = []
    all_words = []
    all_lines = []
    confidences = []

    for i, img in enumerate(images):
        page_result = _ocr_image(img, lang, include_boxes, include_confidence)

        page_data = {
            "page_number": i + 1,
            "text": page_result["text"],
            "word_count": page_result["word_count"],
            "avg_confidence": page_result["avg_confidence"],
            "lines": page_result["lines"],
            "paragraphs": page_result["paragraphs"],
        }

        if include_boxes:
            page_data["words"] = page_result.get("words", [])

        all_pages.append(page_data)
        full_text_parts.append(page_result["text"])
        all_lines.extend(page_result["lines"])

        if page_result["avg_confidence"] > 0:
            confidences.append(page_result["avg_confidence"])
        if include_boxes:
            all_words.extend(page_result.get("words", []))

    full_text = "\n\n--- Page Break ---\n\n".join(full_text_parts)
    overall_conf = round(sum(confidences) / len(confidences), 2) if confidences else 0.0
    detected_lang = _detect_language(full_text)

    return {
        "filename": filename,
        "file_type": "pdf",
        "page_count": len(all_pages),
        "text": full_text,
        "pages": all_pages,
        "words": all_words if include_boxes else [],
        "lines": all_lines,
        "avg_confidence": overall_conf,
        "detected_language": detected_lang,
        "word_count": sum(p["word_count"] for p in all_pages),
        "char_count": len(full_text),
    }


# ── Reasoning Integration ────────────────────────────────────────────────────

async def ocr_and_reason(
    content: bytes,
    filename: str,
    question: str,
    lang: str = "eng",
    task: str = "answer",
    api_key: Optional[str] = None,
) -> dict:
    """
    Run OCR on a file, then feed the extracted text to the S.T.E.W reasoning
    system to answer questions, summarize, or extract info.

    Tasks:
      - "answer": Answer a specific question about the document
      - "summarize": Generate a concise summary
      - "extract": Extract key information (dates, names, amounts, etc.)
      - "analyze": General analysis of the content
    """
    from server.llm_client import get_llm_client

    # Step 1: OCR
    ocr_result = ocr_file(content, filename, lang=lang, include_boxes=False, include_confidence=True)

    if not ocr_result["text"].strip():
        return {
            "success": False,
            "error": "No text could be extracted from the document. "
                     "The image may be too low quality or contain no readable text.",
            "ocr_result": ocr_result,
        }

    text = ocr_result["text"]
    # Cap text to avoid token limits
    text_preview = text[:12000]

    # Step 2: Build reasoning prompt
    task_prompts = {
        "answer": f"The user asks this question about the document:\n{question}\n\nAnswer based ONLY on the document content. If the answer is not in the document, say so clearly.",
        "summarize": f"Provide a clear, concise summary of this document. Include key points, important data, and the main purpose of the document.",
        "extract": f"Extract the following key information from this document: {question or 'names, dates, amounts, addresses, phone numbers, and email addresses'}. Format as a structured list.",
        "analyze": f"Analyze this document and provide insights about its content, structure, and purpose. {('Focus on: ' + question) if question else ''}",
    }

    system_prompt = (
        "You are S.T.E.W, an AI document analysis agent. "
        "You can read and understand documents through OCR-extracted text. "
        "Be accurate, concise, and only state what is in the document."
    )

    user_prompt = f"""
Document: {filename}
Type: {ocr_result['file_type']}
Pages: {ocr_result.get('page_count', 1)}
OCR Confidence: {ocr_result['avg_confidence']}%
Detected Language: {ocr_result.get('detected_language', 'unknown')}

--- EXTRACTED TEXT ---
{text_preview}
--- END TEXT ---

{task_prompts.get(task, task_prompts['answer'])}
"""

    # Step 3: Run through LLM reasoning
    llm = get_llm_client()
    reasoning = await asyncio.to_thread(
        llm.complete,
        user_prompt,
        system=system_prompt,
    )

    return {
        "success": True,
        "task": task,
        "filename": filename,
        "question": question,
        "ocr_text": text,
        "ocr_confidence": ocr_result["avg_confidence"],
        "detected_language": ocr_result.get("detected_language", "unknown"),
        "page_count": ocr_result.get("page_count", 1),
        "word_count": ocr_result["word_count"],
        "answer": reasoning,
    }
