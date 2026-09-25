"""
S.T.E.W Brevo Service — email marketing, product updates, and campaigns.

Uses the Brevo (ex-Sendinblue) v3 REST API with BREVO_API_KEY.
Powers:
- Contact capture: every user who shares an email in chat is synced to a
  Brevo contact list (STEW_USERS_LIST_ID) with plan/name attributes.
- Transactional product-update emails (simple HTML send, no SMTP).
- Campaign sending to all captured emails (owner-triggered from the admin
  panel only — Stew never mass-emails on its own).
- Account status probe so the admin panel can show Brevo connectivity.

API reference: https://developers.brevo.com/reference
"""
import logging
import os
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger("stew.brevo")

BASE = "https://api.brevo.com/v3"

# Owner-editable list id (integer) for marketing contacts.
def _list_id() -> Optional[int]:
    raw = (os.environ.get("BREVO_USERS_LIST_ID") or "").strip()
    try:
        return int(raw) if raw else None
    except ValueError:
        return None


def _headers() -> Dict[str, str]:
    key = (os.environ.get("BREVO_API_KEY") or "").strip()
    return {
        "api-key": key,
        "content-type": "application/json",
        "accept": "application/json",
    }


def configured() -> bool:
    return bool((os.environ.get("BREVO_API_KEY") or "").strip())


def _sanitize_html(html: str) -> str:
    """Marketing emails still need to be tidy: strip script/iframe tags and
    add a light default wrapper if the owner sent bare text."""
    import re
    clean = re.sub(r"<(script|iframe)[^>]*>.*?</\1>", "", html or "", flags=re.I | re.S)
    if clean.strip() and "<" not in clean.strip()[:60]:
        # plain text → wrap in basic HTML
        body = clean.replace("\n", "<br/>")
        clean = (
            "<div style='font-family:Segoe UI,Arial,sans-serif;font-size:15px;"
            "color:#111;max-width:600px;margin:0 auto'>"
            f"{body}<br/><br/><span style='color:#888;font-size:12px'>"
            "You receive this because you use S.T.E.W on Telegram. "
            "Reply STOP to unsubscribe.</span></div>"
        )
    return clean


async def get_status() -> Dict[str, Any]:
    """Admin: is the Brevo key valid + how many contacts/emails do we have."""
    if not configured():
        return {"configured": False, "success": False,
                "error": "BREVO_API_KEY not set on the server"}
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(f"{BASE}/account", headers=_headers())
            if resp.status_code != 200:
                return {"configured": True, "success": False,
                        "error": f"Brevo API returned {resp.status_code}: {resp.text[:200]}"}
            acct = resp.json()
            plans = acct.get("plan", []) or [{}]
            return {
                "configured": True, "success": True,
                "email": (acct.get("company") or {}).get("email"),
                "plan_type": plans[0].get("type") if plans else None,
                "credits": acct.get("plan", [{}])[0].get("credits", 0) if plans else None,
                "list_id": _list_id(),
            }
    except Exception as exc:
        logger.warning(f"Brevo status failed: {exc}")
        return {"configured": True, "success": False, "error": str(exc)[:200]}


async def upsert_contact(email: str, attrs: Optional[Dict[str, Any]] = None,
                         list_id: Optional[int] = None) -> Dict[str, Any]:
    """Create-or-update a Brevo contact; adds them to the marketing list.
    Called when a chat user shares their email (opt-in) or during a bulk sync."""
    if not configured():
        return {"success": False, "error": "Brevo not configured"}
    if not list_id:
        list_id = _list_id()
    payload: Dict[str, Any] = {
        "email": email,
        "updateEnabled": True,
        "attributes": {
            "FIRSTNAME": (attrs or {}).get("name", ""),
            "PLAN": (attrs or {}).get("plan", "free"),
            "SOURCE": "stew-telegram-bot",
        },
    }
    if list_id:
        payload["listIds"] = [list_id]
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(f"{BASE}/contacts", headers=_headers(), json=payload)
            if resp.status_code in (200, 201, 204):
                return {"success": True}
            # 400 with code "duplicate_parameter" → contact exists; still fine.
            try:
                body = resp.json()
            except Exception:
                body = {"message": resp.text[:200]}
            if body.get("code") == "duplicate_parameter" or "already" in str(body.get("message", "")).lower():
                return {"success": True, "note": "contact already exists"}
            logger.warning(f"Brevo upsert failed {resp.status_code}: {body}")
            return {"success": False, "error": str(body)[:250]}
    except Exception as exc:
        return {"success": False, "error": str(exc)[:250]}


async def send_email(to_email: str, subject: str, html: str,
                     sender_name: str = "S.T.E.W") -> Dict[str, Any]:
    """Send one transactional HTML email (product updates, confirmations)."""
    if not configured():
        return {"success": False, "error": "Brevo not configured"}
    payload = {
        "sender": {"name": sender_name, "email": (os.environ.get("BREVO_SENDER_EMAIL") or "noreply@stew-agent.onrender.com")},
        "to": [{"email": to_email}],
        "subject": subject,
        "htmlContent": _sanitize_html(html),
    }
    try:
        async with httpx.AsyncClient(timeout=25) as client:
            resp = await client.post(f"{BASE}/smtp/email", headers=_headers(), json=payload)
            if resp.status_code in (200, 201):
                return {"success": True, "message_id": resp.json().get("messageId")}
            logger.warning(f"Brevo send failed {resp.status_code}: {resp.text[:200]}")
            return {"success": False, "error": f"Brevo {resp.status_code}: {resp.text[:200]}"}
    except Exception as exc:
        return {"success": False, "error": str(exc)[:250]}


async def send_bulk(to_emails: List[str], subject: str, html: str,
                    batch_size: int = 50) -> Dict[str, Any]:
    """Sequential small-batch campaign send via the transactional endpoint.
    Brevo SMTP batches recipients, but we chunk to stay inside rate limits
    and to give the admin per-batch progress."""
    results = {"success": True, "sent": 0, "failed": 0, "errors": []}
    html = _sanitize_html(html)
    for i in range(0, len(to_emails), batch_size):
        chunk = [e for e in to_emails[i:i + batch_size] if e]
        if not chunk:
            continue
        payload = {
            "sender": {"name": "S.T.E.W", "email": (os.environ.get("BREVO_SENDER_EMAIL") or "noreply@stew-agent.onrender.com")},
            "to": [{"email": e} for e in chunk],
            "subject": subject,
            "htmlContent": html,
        }
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(f"{BASE}/smtp/email", headers=_headers(), json=payload)
            if resp.status_code in (200, 201):
                results["sent"] += len(chunk)
            else:
                results["failed"] += len(chunk)
                results["errors"].append(f"batch {i//batch_size}: {resp.status_code} {resp.text[:120]}")
        except Exception as exc:
            results["failed"] += len(chunk)
            results["errors"].append(f"batch {i//batch_size}: {str(exc)[:120]}")
    results["success"] = results["failed"] == 0 or results["sent"] > 0
    return results


async def create_list(name: str) -> Dict[str, Any]:
    """Admin: create the STEW users marketing list once; returns its id."""
    if not configured():
        return {"success": False, "error": "Brevo not configured"}
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(f"{BASE}/contacts/lists", headers=_headers(),
                                     json={"name": name, "folderId": 1})
            if resp.status_code in (200, 201):
                return {"success": True, "list_id": resp.json().get("id")}
            return {"success": False, "error": f"{resp.status_code}: {resp.text[:200]}"}
    except Exception as exc:
        return {"success": False, "error": str(exc)[:250]}
