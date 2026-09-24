"""S.T.E.W Brevo integration — email list growth + product-update campaigns.

Uses the account Brevo API key (BREVO_API_KEY env). Sender is discovered from
the account's verified senders, so campaigns work without extra configuration.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

BASE_URL = "https://api.brevo.com/v3"
STEW_LIST_NAME = os.environ.get("BREVO_LIST_NAME", "STEW Users")


def _api_key() -> Optional[str]:
    key = os.environ.get("BREVO_API_KEY") or os.environ.get("BREVO_SERVER_API_KEY") or ""
    return key.strip() or None


def _headers() -> Dict[str, str]:
    return {
        "api-key": _api_key() or "",
        "content-type": "application/json",
        "accept": "application/json",
    }


def brevo_enabled() -> bool:
    return bool(_api_key())


async def _get(path: str, params: Optional[dict] = None) -> Optional[dict]:
    async with httpx.AsyncClient(timeout=25) as client:
        r = await client.get(f"{BASE_URL}{path}", headers=_headers(), params=params)
        if r.status_code >= 400:
            logger.warning("Brevo GET %s -> %s %s", path, r.status_code, r.text[:200])
            return None
        return r.json()


async def _post(path: str, body: dict) -> Dict[str, Any]:
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(f"{BASE_URL}{path}", headers=_headers(), json=body)
        ok = r.status_code < 400
        if not ok:
            logger.warning("Brevo POST %s -> %s %s", path, r.status_code, r.text[:300])
        data: Dict[str, Any] = {}
        try:
            data = r.json()
        except Exception:
            data = {}
        return {"success": ok, "status": r.status_code, "data": data,
                "error": None if ok else r.text[:300]}


# ───────────────────────────── Contacts & lists ─────────────────────────────

_list_id_cache: Optional[int] = None


async def get_stew_list_id() -> Optional[int]:
    """Find (or create) the STEW contacts list."""
    global _list_id_cache
    if _list_id_cache:
        return _list_id_cache
    if not brevo_enabled():
        return None
    data = await _get("/contacts/lists", params={"limit": 50})
    if data:
        for item in data.get("lists", []):
            if str(item.get("name", "")).strip().lower() == STEW_LIST_NAME.lower():
                _list_id_cache = int(item["id"])
                return _list_id_cache
    result = await _post("/contacts/lists", {"name": STEW_LIST_NAME, "folderId": 1})
    if result["success"]:
        _list_id_cache = int(result["data"].get("id") or 0) or None
    return _list_id_cache


async def upsert_contact(email: str, name: str = "",
                         telegram_user_id: str = "") -> Dict[str, Any]:
    """Create or update a contact and add them to the STEW list."""
    if not brevo_enabled():
        return {"success": False, "error": "Brevo API key is not configured."}
    email = email.strip().lower()
    if "@" not in email:
        return {"success": False, "error": "Invalid email"}
    list_id = await get_stew_list_id()
    body: Dict[str, Any] = {
        "email": email,
        "updateEnabled": True,
        "attributes": {"FIRSTNAME": (name or "").split(" ")[0][:100],
                       "SOURCE": "stew-telegram"},
    }
    if telegram_user_id:
        body["attributes"]["TELEGRAM_ID"] = str(telegram_user_id)[:60]
    if list_id:
        body["listIds"] = [list_id]
    result = await _post("/contacts", body)
    return {"success": result["success"], "error": result.get("error")}


async def sync_all_contacts() -> Dict[str, Any]:
    """Push every captured ContactEmail to Brevo. Returns counts."""
    from server.database import AsyncSessionLocal
    from server.models import ContactEmail
    from sqlalchemy import select
    if not brevo_enabled():
        return {"success": False, "synced": 0, "failed": 0,
                "error": "Brevo API key is not configured."}
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(select(ContactEmail))).scalars().all()
    synced = failed = 0
    for row in rows:
        result = await upsert_contact(row.email, row.name or "", row.telegram_user_id)
        if result.get("success"):
            synced += 1
            row.brevo_synced = True
        else:
            failed += 1
    async with AsyncSessionLocal() as db:
        for row in rows:
            if row.brevo_synced:
                await db.merge(row)
        await db.commit()
    return {"success": True, "synced": synced, "failed": failed}


# ───────────────────────────── Sending email ─────────────────────────────

_sender_cache: Optional[dict] = None


async def get_sender() -> Optional[dict]:
    """First verified sender on the Brevo account."""
    global _sender_cache
    if _sender_cache:
        return _sender_cache
    data = await _get("/senders", params={"limit": 20})
    if not data:
        return None
    senders = data.get("senders", [])
    preferred = [s for s in senders if s.get("active", s.get("isActive", False))]
    if not preferred and senders:
        preferred = senders[:1]
    if not preferred:
        return None
    s = preferred[0]
    _sender_cache = {"name": s.get("name") or "S.T.E.W", "email": s.get("email")}
    return _sender_cache


async def send_email(to_email: str, subject: str, html_content: str,
                     to_name: str = "") -> Dict[str, Any]:
    """Transactional email via Brevo SMTP."""
    if not brevo_enabled():
        return {"success": False, "error": "Brevo API key is not configured."}
    sender = await get_sender()
    if not sender:
        return {"success": False, "error": "No verified sender on this Brevo account. Add one in Brevo → Senders."}
    body = {
        "sender": sender,
        "to": [{"email": to_email.strip().lower(), "name": to_name or to_email}],
        "subject": subject[:150],
        "htmlContent": html_content,
    }
    result = await _post("/smtp/email", body)
    return {"success": result["success"], "message_id": (result["data"] or {}).get("messageId"),
            "error": result.get("error")}


async def send_campaign(subject: str, html_content: str,
                        recipients: Optional[List[dict]] = None) -> Dict[str, Any]:
    """Broadcast a product update to captured contacts (transactional batch)."""
    from server.database import AsyncSessionLocal
    from server.models import ContactEmail
    from sqlalchemy import select
    if recipients is None:
        async with AsyncSessionLocal() as db:
            rows = (await db.execute(select(ContactEmail))).scalars().all()
        recipients = [{"email": r.email, "name": r.name or ""} for r in rows]
    sent = failed = 0
    failures: List[str] = []
    for r in recipients:
        result = await send_email(r["email"], subject, html_content, r.get("name", ""))
        if result.get("success"):
            sent += 1
        else:
            failed += 1
            failures.append(f"{r['email']}: {str(result.get('error'))[:120]}")
        # Stay gentle with the Brevo throughput limits.
        import asyncio
        await asyncio.sleep(0.25)
    return {"success": failed == 0, "sent": sent, "failed": failed,
            "failures": failures[:20]}
