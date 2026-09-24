"""S.T.E.W email capture — grows the Brevo marketing list from Telegram users.

Flow: STEW politely asks users for their email until we have one, stores it in
contact_emails, and syncs it to Brevo so the owner can send product updates and
campaigns from the STEW HQ admin site.
"""
from __future__ import annotations

import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)

EMAIL_RE = re.compile(
    r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"
)

_BANNED_DOMAINS = {"telegram.stew", "example.com", "test.com", "test.com.ng"}

_ASK_TEXT = (
    "📬 One quick thing — what's your *email*?\n\n"
    "I use it to send you product updates, premium feature drops and early access. "
    "No spam, ever. Just reply with your email (or /skip to hide this for now)."
)


def _is_real_email(email: str) -> bool:
    email = (email or "").strip().lower()
    return bool(EMAIL_RE.fullmatch(email)) and email.split("@")[-1] not in _BANNED_DOMAINS


def telegram_id_from_user(user) -> str:
    """Telegram numeric id from the tg_<id>@telegram.stew user record."""
    email = getattr(user, "email", "") or ""
    if email.startswith("tg_") and "@telegram.stew" in email:
        return email[3:].split("@")[0]
    return getattr(user, "id", "") or ""


async def get_contact_email(telegram_user_id: str) -> Optional[str]:
    from server.database import AsyncSessionLocal
    from server.models import ContactEmail
    from sqlalchemy import select
    async with AsyncSessionLocal() as db:
        row = (await db.execute(select(ContactEmail).where(
            ContactEmail.telegram_user_id == str(telegram_user_id)))).scalar_one_or_none()
        return row.email if row else None


async def save_contact_email(telegram_user_id: str, email: str, name: str = "") -> dict:
    """Store the email and kick off a Brevo sync in the background."""
    from server.database import AsyncSessionLocal
    from server.models import ContactEmail
    from sqlalchemy import select
    telegram_user_id = str(telegram_user_id)
    email = email.strip().lower()
    if not _is_real_email(email):
        return {"success": False, "error": "That doesn't look like a valid email."}
    async with AsyncSessionLocal() as db:
        row = (await db.execute(select(ContactEmail).where(
            ContactEmail.telegram_user_id == telegram_user_id))).scalar_one_or_none()
        if row:
            row.email = email
            row.name = (name or row.name or "")[:255]
            row.brevo_synced = False
        else:
            db.add(ContactEmail(telegram_user_id=telegram_user_id,
                                email=email, name=(name or "")[:255]))
        await db.commit()
    # Background Brevo sync — never blocks the chat reply.
    try:
        import asyncio
        from server import brevo_service
        asyncio.get_running_loop().create_task(
            brevo_service.upsert_contact(email, name, telegram_user_id))
    except Exception as exc:
        logger.warning("Brevo background sync skipped: %s", exc)
    return {"success": True, "email": email}


async def capture_and_confirm(user, chat_id, bot, text: str):
    """Handle explicit email intake. Returns True when the message was consumed."""
    telegram_user_id = telegram_id_from_user(user)
    if not telegram_user_id or not text:
        return None
    low = text.strip().lower()
    if low in ("/skip", "skip"):
        from server.paywall import meter_feature, current_period
        await meter_feature(telegram_user_id, "email_prompt", current_period(daily=True))
        await bot.send_message(chat_id, "No worries — I'll ask another time. You can always send /setemail your@email.com")
        return True
    if low.startswith("/setemail") or low.startswith("/email"):
        raw = text.split(" ", 1)[1].strip() if " " in text else ""
        match = EMAIL_RE.search(raw)
        if not match:
            await bot.send_message(
                chat_id,
                "Send it like this: `/setemail you@example.com`",
            )
            return True
        result = await save_contact_email(telegram_user_id, match.group(0), getattr(user, "name", ""))
        if result.get("success"):
            await bot.send_message(
                chat_id,
                f"✅ Saved *{result['email']}* — you'll get STEW product updates, premium drops and early access. "
                "Thanks! 🚀",
            )
            return True
        await bot.send_message(chat_id, f"⚠️ {result.get('error')}")
        return True
    # A bare email in chat = giving us their address.
    match = EMAIL_RE.search(text)
    if match and _is_real_email(match.group(0)) and len(text) < 120 and " " not in text.strip().rstrip(".,!"):
        result = await save_contact_email(telegram_user_id, match.group(0), getattr(user, "name", ""))
        if result.get("success"):
            await bot.send_message(
                chat_id,
                f"✅ Got it — *{result['email']}* saved for STEW updates & premium drops. Thanks! 🚀",
            )
            return True
    return None


async def maybe_ask_email(user, chat_id, text: str, bot=None):
    """Politely ask once a day (max) until we have the user's email."""
    try:
        import asyncio
        from server.paywall import current_period, feature_usage_count, meter_feature
        telegram_user_id = telegram_id_from_user(user)
        if not telegram_user_id:
            return
        if not text or text.startswith("/"):
            return
        if await get_contact_email(telegram_user_id):
            return
        asked = await feature_usage_count(telegram_user_id, "email_prompt",
                                          current_period(daily=True))
        if asked >= 1:
            return
        msgs_today = await meter_feature(telegram_user_id, "msg_day",
                                         current_period(daily=True))
        if msgs_today < 3:
            return
        await meter_feature(telegram_user_id, "email_prompt", current_period(daily=True))
        await asyncio.sleep(6)  # Ask AFTER the user's main reply, never before.
        _bot = bot
        if _bot is None:
            from server.telegram_bot import TelegramBot
            from server.config import get_settings
            _bot = TelegramBot(get_settings().TELEGRAM_BOT_TOKEN)
        await _bot.send_message(chat_id, _ASK_TEXT)
    except Exception as exc:
        logger.warning("Email ask skipped: %s", exc)
