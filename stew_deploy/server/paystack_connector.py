"""User-side Paystack connector — Stew acts on the USER's own Paystack
account (their key, their money) to create payment links/invoices, verify
payments, and list transactions. Different from payments.py, which handles
subscriptions TO Stew.

The user's Paystack secret key is stored encrypted (UserSecret model) and
set via /setpaystack in chat. Creating a payment link is an instant private
write (no money moves until their customer pays), so it doesn't pause for
approval — but the gateway still governs anything downstream.
"""
from __future__ import annotations

import base64
import hashlib
import logging
import os
from typing import Optional

import httpx
from sqlalchemy import select

from server.database import AsyncSessionLocal
from server.models import UserSecret

logger = logging.getLogger(__name__)
PS_BASE = "https://api.paystack.co"


def _fernet():
    from cryptography.fernet import Fernet
    seed = os.environ.get("USER_SECRET_FERNET_KEY") or (
        os.environ.get("STEW_ADMIN_SECRET", "stew") + "|user-secret-vault")
    key = base64.urlsafe_b64encode(hashlib.sha256(seed.encode()).digest())
    return Fernet(key)


async def set_user_key(user_id: str, key: str) -> dict:
    key = (key or "").strip()
    if not key.startswith("sk_"):
        return {"ok": False, "error": "That doesn't look like a Paystack secret key (they start with sk_). Find it at paystack.com → Settings → API Keys."}
    # validate against the real API before storing
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(f"{PS_BASE}/transaction?page=1",
                                    headers={"Authorization": f"Bearer {key}"})
        if resp.status_code == 401:
            return {"ok": False, "error": "Paystack rejected that key (401). Double-check you copied the SECRET key, not the public one."}
    except Exception as exc:
        return {"ok": False, "error": f"Could not reach Paystack to validate the key: {exc}"}
    f = _fernet()
    enc = f.encrypt(key.encode()).decode()
    async with AsyncSessionLocal() as db:
        row = (await db.execute(select(UserSecret).where(
            UserSecret.telegram_user_id == str(user_id),
            UserSecret.name == "paystack_secret_key"))).scalar()
        if row:
            row.value_encrypted = enc
        else:
            db.add(UserSecret(telegram_user_id=str(user_id),
                             name="paystack_secret_key", value_encrypted=enc))
        await db.commit()
    return {"ok": True, "note": "Paystack key verified and stored encrypted. Tools create_invoice / check_payment / my_transactions are now live."}


async def _get_key(user_id: str) -> Optional[str]:
    async with AsyncSessionLocal() as db:
        row = (await db.execute(select(UserSecret).where(
            UserSecret.telegram_user_id == str(user_id),
            UserSecret.name == "paystack_secret_key"))).scalar()
    if not row:
        return None
    try:
        return _fernet().decrypt(row.value_encrypted.encode()).decode()
    except Exception:
        return None


async def create_invoice(user_id: str, email: str, amount_ngn: float,
                         description: str) -> dict:
    key = await _get_key(user_id)
    if not key:
        return {"ok": False, "error": "No Paystack key set yet. Tell the user: send /setpaystack sk_xxx (their Paystack secret key) — I verify it and store it encrypted."}
    if amount_ngn <= 0 or amount_ngn > 100_000_000:
        return {"ok": False, "error": "amount_ngn must be between 1 and 100,000,000."}
    if not email or "@" not in email:
        return {"ok": False, "error": "a valid customer email is required"}
    try:
        async with httpx.AsyncClient(timeout=25) as client:
            resp = await client.post(f"{PS_BASE}/transaction/initialize",
                headers={"Authorization": f"Bearer {key}"},
                json={"email": email.strip(),
                      "amount": int(round(amount_ngn * 100)),
                      "description": (description or "Payment")[:100]})
        data = resp.json()
    except Exception as exc:
        return {"ok": False, "error": f"Paystack request failed: {exc}"}
    if resp.status_code not in (200, 201):
        return {"ok": False, "error": f"Paystack error: {data.get('message', resp.status_code)}"}
    d = (data.get("data") or {})
    return {"ok": True,
            "payment_link": d.get("authorization_url"),
            "reference": d.get("reference"),
            "amount_ngn": amount_ngn,
            "note": "Payment link created on the user's own Paystack account — share it with their customer. No approval needed: no money moves until someone pays."}


async def check_payment(user_id: str, reference: str) -> dict:
    key = await _get_key(user_id)
    if not key:
        return {"ok": False, "error": "No Paystack key set — /setpaystack sk_xxx first."}
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(f"{PS_BASE}/transaction/verify/{reference.strip()}",
                                    headers={"Authorization": f"Bearer {key}"})
        data = resp.json()
    except Exception as exc:
        return {"ok": False, "error": f"Paystack request failed: {exc}"}
    d = (data.get("data") or {})
    return {"ok": resp.status_code == 200, "status": d.get("status"),
            "amount_ngn": (d.get("amount") or 0) / 100,
            "paid_at": d.get("paid_at"), "customer": (d.get("customer") or {}).get("email"),
            "raw": data if resp.status_code != 200 else None}


async def my_transactions(user_id: str) -> dict:
    key = await _get_key(user_id)
    if not key:
        return {"ok": False, "error": "No Paystack key set — /setpaystack sk_xxx first."}
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(f"{PS_BASE}/transaction?perPage=10",
                                    headers={"Authorization": f"Bearer {key}"})
        data = resp.json()
    except Exception as exc:
        return {"ok": False, "error": f"Paystack request failed: {exc}"}
    rows = []
    for t in (data.get("data") or [])[:10]:
        rows.append({"status": t.get("status"), "amount_ngn": (t.get("amount") or 0) / 100,
                     "email": (t.get("customer") or {}).get("email"),
                     "paid_at": t.get("paid_at")})
    return {"ok": resp.status_code == 200, "transactions": rows,
            "total": data.get("meta", {}).get("total")}
