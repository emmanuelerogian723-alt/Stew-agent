"""
S.T.E.W Document Processor — read uploaded files and extract text.
Supports: PDF, DOCX, TXT, CSV, JSON
"""
import csv
import io
import json
import logging
from typing import Optional

from fastapi import HTTPException, UploadFile

logger = logging.getLogger(__name__)

SUPPORTED_TYPES = {
    "application/pdf": "pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "text/plain": "txt",
    "text/csv": "csv",
    "application/json": "json",
    "application/octet-stream": "auto",
}

MAX_FILE_SIZE = 20 * 1024 * 1024  # 20MB


async def extract_text(file: UploadFile) -> dict:
    """Read an uploaded file and return its text content."""
    content = await file.read()

    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(413, "File too large (max 20MB)")

    filename = file.filename or "upload"
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "txt"
    content_type = file.content_type or ""

    file_type = SUPPORTED_TYPES.get(content_type, ext)

    if ext == "pdf" or content_type == "application/pdf":
        return _extract_pdf(content, filename)
    elif ext == "docx" or "wordprocessingml" in content_type:
        return _extract_docx(content, filename)
    elif ext == "csv" or content_type in ("text/csv",):
        return _extract_csv(content, filename)
    elif ext == "json" or content_type == "application/json":
        return _extract_json(content, filename)
    else:
        # Default: treat as plain text
        return _extract_txt(content, filename)


def _extract_pdf(content: bytes, filename: str) -> dict:
    try:
        import pdfplumber

        with pdfplumber.open(io.BytesIO(content)) as pdf:
            pages = []
            for page in pdf.pages:
                text = page.extract_text() or ""
                pages.append(text)
        full_text = "\n\n".join(pages)
        return {"filename": filename, "file_type": "pdf", "text": full_text, "pages": len(pages)}
    except ImportError:
        # Fallback to pypdf
        try:
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(content))
            text = "\n\n".join(p.extract_text() or "" for p in reader.pages)
            return {"filename": filename, "file_type": "pdf", "text": text, "pages": len(reader.pages)}
        except ImportError:
            raise HTTPException(500, "PDF reading library not available (install pdfplumber or pypdf)")


def _extract_docx(content: bytes, filename: str) -> dict:
    try:
        from docx import Document
        doc = Document(io.BytesIO(content))
        paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
        text = "\n".join(paragraphs)
        return {"filename": filename, "file_type": "docx", "text": text}
    except ImportError:
        raise HTTPException(500, "python-docx not available")


def _extract_csv(content: bytes, filename: str) -> dict:
    try:
        decoded = content.decode("utf-8", errors="replace")
        reader = csv.DictReader(io.StringIO(decoded))
        rows = list(reader)
        # Format as readable text
        if rows:
            headers = list(rows[0].keys())
            lines = [", ".join(headers)]
            for row in rows[:100]:  # cap at 100 rows for context
                lines.append(", ".join(str(v) for v in row.values()))
            text = "\n".join(lines)
        else:
            text = decoded
        return {"filename": filename, "file_type": "csv", "text": text, "row_count": len(rows)}
    except Exception as e:
        raise HTTPException(500, f"CSV parse error: {e}")


def _extract_json(content: bytes, filename: str) -> dict:
    try:
        data = json.loads(content.decode("utf-8"))
        text = json.dumps(data, indent=2)
        return {"filename": filename, "file_type": "json", "text": text}
    except Exception as e:
        raise HTTPException(400, f"Invalid JSON: {e}")


def _extract_txt(content: bytes, filename: str) -> dict:
    text = content.decode("utf-8", errors="replace")
    return {"filename": filename, "file_type": "txt", "text": text}
