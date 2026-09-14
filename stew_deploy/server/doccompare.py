"""
S.T.E.W Doc Compare — two documents, one clear side-by-side analysis.

Armed via 'compare documents' / /compare. The next two documents sent are
captured (PDF/DOCX/CSV/TXT via the existing extractor), then an LLM produces
a structured comparison: what matches, what differs, risks, recommendation.
"""
import asyncio
import logging
from typing import Callable, Optional

logger = logging.getLogger("stew.doccompare")

# chat_key -> {"doc1": {"name", "text"}, "ts": float}
PENDING: dict = {}


def arm(key: str) -> None:
    PENDING[key] = {"doc1": None, "ts": __import__("time").time()}


def is_armed(key: str) -> bool:
    p = PENDING.get(key)
    if not p:
        return False
    if __import__("time").time() - p.get("ts", 0) > 900:  # 15-min window
        PENDING.pop(key, None)
        return False
    return True


def store_doc(key: str, name: str, text: str) -> Optional[str]:
    """Returns a status message: needs second doc / ready / not armed."""
    p = PENDING.get(key)
    if not p:
        return None
    if not p.get("doc1"):
        p["doc1"] = {"name": name, "text": (text or "")[:60000]}
        return f"📄 Got *{name}*. Now send the second document to compare it against."
    second = {"name": name, "text": (text or "")[:60000]}
    PENDING.pop(key, None)
    p["doc2"] = second
    _LAST[key] = p
    return "__READY__"


_LAST: dict = {}


def pop_pair(key: str) -> Optional[dict]:
    return _LAST.pop(key, None)


def extract_bytes(file_bytes: bytes, file_name: str) -> str:
    """Text extraction by extension, reusing document_processor helpers."""
    ext = (file_name.rsplit(".", 1)[-1] if "." in file_name else "").lower()
    try:
        if ext == "pdf":
            from server.document_processor import _extract_pdf
            return _extract_pdf(file_bytes, file_name).get("text", "") or ""
        if ext in ("docx", "doc"):
            from server.document_processor import _extract_docx
            return _extract_docx(file_bytes, file_name).get("text", "") or ""
        if ext == "csv":
            from server.document_processor import _extract_csv
            return _extract_csv(file_bytes, file_name).get("text", "") or ""
        if ext == "json":
            from server.document_processor import _extract_json
            return _extract_json(file_bytes, file_name).get("text", "") or ""
        # txt / md / anything else
        try:
            return file_bytes.decode("utf-8", errors="ignore")
        except Exception:
            return ""
    except Exception as e:
        logger.warning(f"doccompare extract failed: {e}")
        return ""


def compare_prompt(pair: dict) -> list[dict]:
    return [
        {"role": "system", "content":
            "You are a meticulous document analyst. Compare the two documents and give a clear, "
            "structured comparison a busy person can act on. Use plain text sections. Be specific — "
            "quote exact differing numbers, dates, names and clauses."},
        {"role": "user", "content":
            f"DOCUMENT 1 — {pair['doc1']['name']}:\n{pair['doc1']['text'][:28000]}\n\n"
            f"DOCUMENT 2 — {pair['doc2']['name']}:\n{pair['doc2']['text'][:28000]}\n\n"
            "Produce:\n"
            "1. WHAT THEY ARE (one line each)\n"
            "2. KEY MATCHES (facts present in both)\n"
            "3. DIFFERENCES (numbered, most important first)\n"
            "4. RISKS / RED FLAGS in either document\n"
            "5. YOUR RECOMMENDATION"},
    ]


async def run_comparison(pair: dict, llm_chat_fn: Callable) -> Optional[str]:
    try:
        result = await asyncio.to_thread(llm_chat_fn, compare_prompt(pair), 2000)
        return result.get("content") if isinstance(result, dict) else str(result)
    except Exception as e:
        logger.warning(f"doccompare LLM failed: {e}")
        return None


def is_compare_intent(text: str) -> bool:
    t = (text or "").strip().lower()
    if t.startswith("/compare"):
        return True
    import re as _re
    return bool(_re.search(r"\bcompare\b.*\b(documents?|files?|contracts?|pdfs?|invoices?)\b", t)
                or _re.search(r"\b(documents?|contracts?)\b.*\bcomparison\b", t)
                or _re.match(r"^compare\b", t))
