"""
S.T.E.W Payments — Paystack integration for plan upgrades.
"""
import hashlib
import hmac
import logging
from typing import Optional

import requests
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from server.config import get_settings
from server.models import User, PaymentTransaction

logger = logging.getLogger(__name__)
settings = get_settings()

PAYSTACK_BASE = "https://api.paystack.co"


def _headers() -> dict:
    if not settings.PAYSTACK_SECRET_KEY:
        raise HTTPException(503, "Paystack not configured (PAYSTACK_SECRET_KEY missing)")
    return {
        "Authorization": f"Bearer {settings.PAYSTACK_SECRET_KEY}",
        "Content-Type": "application/json",
    }


def _paystack_safe_email(email: str) -> str:
    """
    Paystack validates the email domain against real-world TLDs and rejects
    fake/internal domains (e.g. our internal '@telegram.stew' placeholder emails
    for Telegram-only users — '.stew' is not a registered TLD).
    Swap any non-standard internal domain for a valid-looking one so Paystack
    accepts the transaction. This does NOT touch the user's real DB email —
    only the value sent to Paystack for this specific charge.
    """
    if not email:
        return "stewagent.user@gmail.com"
    if email.endswith("@telegram.stew"):
        local = email.split("@")[0]
        return f"{local}@telegramuser.com"
    return email


def initialize_payment(email: str, amount_kobo: int, plan: str, metadata: dict = None) -> dict:
    """Initialize a Paystack transaction. Returns authorization_url."""
    payload = {
        "email": _paystack_safe_email(email),
        "amount": amount_kobo,
        "currency": "NGN",
        "metadata": metadata or {},
        "callback_url": f"{settings.APP_BASE_URL or 'https://stew-agent.onrender.com'}/payments/callback",
        "channels": ["card", "bank", "ussd", "bank_transfer"],
    }
    try:
        resp = requests.post(
            f"{PAYSTACK_BASE}/transaction/initialize",
            json=payload,
            headers=_headers(),
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        if not data.get("status"):
            raise HTTPException(400, data.get("message", "Paystack init failed"))
        return {
            "authorization_url": data["data"]["authorization_url"],
            "access_code": data["data"]["access_code"],
            "reference": data["data"]["reference"],
        }
    except requests.RequestException as e:
        logger.error(f"Paystack init error: {e}")
        raise HTTPException(502, f"Payment initialization failed: {str(e)[:200]}")


def verify_payment(reference: str) -> dict:
    """Verify a Paystack transaction by reference."""
    try:
        resp = requests.get(
            f"{PAYSTACK_BASE}/transaction/verify/{reference}",
            headers=_headers(),
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        if not data.get("status"):
            raise HTTPException(400, "Verification failed")
        tx = data["data"]
        return {
            "status": tx["status"],
            "amount": tx["amount"],
            "currency": tx["currency"],
            "paid_at": tx.get("paid_at"),
            "customer_email": tx["customer"]["email"],
            "metadata": tx.get("metadata", {}),
        }
    except requests.RequestException as e:
        logger.error(f"Paystack verify error: {e}")
        raise HTTPException(502, "Payment verification failed")


def validate_webhook_signature(payload_bytes: bytes, signature: str) -> bool:
    """Validate the Paystack webhook HMAC-SHA512 signature."""
    if not settings.PAYSTACK_SECRET_KEY:
        return False
    expected = hmac.new(
        settings.PAYSTACK_SECRET_KEY.encode(),
        payload_bytes,
        hashlib.sha512,
    ).hexdigest()
    return hmac.compare_digest(expected, signature or "")


async def upgrade_user_plan(db: AsyncSession, user_id: str, plan: str) -> User:
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(404, "User not found")
    user.plan = plan
    await db.flush()
    return user
