"""
Stew on WhatsApp — the consumer channel on Meta's WhatsApp Business Cloud API.

Economics (2026): Meta bills per delivered message (~₦5–10 each in Nigeria),
so this channel carries its own allowance and a coin surcharge:

  • Free users: WHATSAPP_FREE_TRIAL_MESSAGES (30) messages, then paywall.
  • WhatsApp plan (₦3,500 / 30 days): WHATSAPP_PLAN_MESSAGES (300) messages
    + premium features (research, search grounding) included.
  • Coins never let you run dry — but on WhatsApp they burn
    WHATSAPP_COIN_MULTIPLIER (10x) faster, which covers Meta's per-message fee.

Payment links are always one reply away: "BUY" → fresh Paystack checkout.
Setup: set WHATSAPP_TOKEN, WHATSAPP_PHONE_NUMBER_ID, WHATSAPP_VERIFY_TOKEN
(+ optional WHATSAPP_APP_SECRET) env vars, then point the Meta webhook at
https://<host>/whatsapp/webhook with the same verify token.
"""
import asyncio
import hashlib
import hmac
import logging
import re
from datetime import datetime, timedelta

import httpx
from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from server.auth import generate_api_key
from server.clean_output import clean_response
from server.config import get_settings

settings = get_settings()
from server.database import get_db
from server.llm_client import get_llm_client
from server.memory import append_message, build_llm_messages, get_or_create_conversation, get_relevant_context
from server.models import APICall, User
from server.payments import initialize_payment
from server.search import get_searcher

logger = logging.getLogger("stew.whatsapp")
router = APIRouter(tags=["WhatsApp"])

GRAPH_API = "https://graph.facebook.com/v21.0"
WA_SYSTEM = (
    "You are S.T.E.W (Special Task Execution Worker), chatting INSIDE WhatsApp with everyday users. "
    "You are warm, sharp, funny when it fits, and genuinely useful — the best AI agent on WhatsApp.\n"
    "WHATSAPP FORMATTING (strict): *single asterisks* for bold, _underscores_ for italics. "
    "NEVER use markdown headers (#), double asterisks, dash bullets, or tables. "
    "Use numbered lists (1. 2. 3.) for steps. No links in brackets — paste raw URLs.\n"
    "LENGTH: replies read like texts from a smart friend — short punchy paragraphs, "
    "~150 words max unless the user clearly wants depth (essays, plans, research reports). "
    "Use an occasional emoji, never emoji spam.\n"
    "When asked who built you: S.T.E.W was built by Emmanuel Ene Rejoice Gideon, CEO of MUTYINT, "
    "a Nigerian tech company serving users across Africa and beyond."
)

_seen_ids: set[str] = set()
_seen_max = 5000


# ── Meta plumbing ────────────────────────────────────────────────────────────

def _verify_signature(raw_body: bytes, signature: str) -> bool:
    """X-Hub-Signature-256 check. Skipped when WHATSAPP_APP_SECRET isn't set
    (test numbers in the Meta dashboard often run without it)."""
    secret = settings.WHATSAPP_APP_SECRET
    if not secret:
        return True
    if not signature or not signature.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature.removeprefix("sha256="), expected)


async def _wa_send(wa_id: str, text: str) -> bool:
    """Send a text message via the Cloud API. Long replies are chunked —
    WhatsApp hard-caps a message at 4096 characters."""
    token = settings.WHATSAPP_TOKEN
    phone_id = settings.WHATSAPP_PHONE_NUMBER_ID
    if not token or not phone_id:
        logger.warning("WHATSAPP_TOKEN / WHATSAPP_PHONE_NUMBER_ID not set — message not delivered")
        return False
    chunks = [text[i:i + 3800] for i in range(0, len(text), 3800)] or [""]
    ok = True
    async with httpx.AsyncClient(timeout=30) as client:
        for chunk in chunks:
            try:
                r = await client.post(
                    f"{GRAPH_API}/{phone_id}/messages",
                    headers={"Authorization": f"Bearer {token}"},
                    json={
                        "messaging_product": "whatsapp",
                        "recipient_type": "individual",
                        "to": wa_id,
                        "type": "text",
                        "text": {"preview_url": False, "body": chunk},
                    },
                )
                if r.status_code >= 300:
                    ok = False
                    logger.error(f"WA send failed {r.status_code}: {r.text[:200]}")
            except Exception as e:
                ok = False
                logger.error(f"WA send error: {e}")
    return ok


async def _wa_read_receipt(mid: str):
    """Mark the user's message as read (blue ticks) — fire and forget."""
    token, phone_id = settings.WHATSAPP_TOKEN, settings.WHATSAPP_PHONE_NUMBER_ID
    if not (token and phone_id) or not mid:
        return
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(
                f"{GRAPH_API}/{phone_id}/messages",
                headers={"Authorization": f"Bearer {token}"},
                json={"messaging_product": "whatsapp", "status": "read", "message_id": mid},
            )
    except Exception as e:
        logger.debug(f"read receipt skipped: {e}")


async def _wa_typing(wa_id: str):
    """Show the 'typing…' indicator while we think — fire and forget.
    (Older Graph API versions reject this silently; errors are ignored.)"""
    token, phone_id = settings.WHATSAPP_TOKEN, settings.WHATSAPP_PHONE_NUMBER_ID
    if not (token and phone_id):
        return
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(
                f"{GRAPH_API}/{phone_id}/messages",
                headers={"Authorization": f"Bearer {token}"},
                json={"messaging_product": "whatsapp", "status": "typing", "to": wa_id},
            )
    except Exception as e:
        logger.debug(f"typing indicator skipped: {e}")


async def _wa_download_media(media_id: str) -> tuple[bytes, str] | None:
    """Download media from Meta (voice notes, photos). Returns (bytes, mime)."""
    token = settings.WHATSAPP_TOKEN
    if not token or not media_id:
        return None
    try:
        async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
            r = await client.get(f"{GRAPH_API}/{media_id}", headers={"Authorization": f"Bearer {token}"})
            info = r.json()
            url, mime = info.get("url", ""), info.get("mime_type", "application/octet-stream")
            if not url:
                return None
            r2 = await client.get(url, headers={"Authorization": f"Bearer {token}"})
            return (r2.content, mime) if r2.status_code == 200 else None
    except Exception as e:
        logger.error(f"WA media download failed: {e}")
        return None


@router.get("/whatsapp/webhook")
async def whatsapp_verify(request: Request):
    """Meta webhook subscription handshake (hub.challenge echo)."""
    qp = request.query_params
    if qp.get("hub.mode") == "subscribe" and qp.get("hub.verify_token") == settings.WHATSAPP_VERIFY_TOKEN:
        return Response(content=qp.get("hub.challenge", ""), media_type="text/plain")
    return Response(content="verification failed", status_code=403, media_type="text/plain")


@router.post("/whatsapp/webhook")
async def whatsapp_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    raw = await request.body()
    if not _verify_signature(raw, request.headers.get("x-hub-signature-256", "")):
        logger.warning("WA webhook signature mismatch — rejected")
        return {"ok": False}
    try:
        payload = await request.json()
    except Exception:
        return {"ok": True}

    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            for msg in value.get("messages", []):
                mid = msg.get("id", "")
                if mid in _seen_ids:
                    continue
                _seen_ids.add(mid)
                if len(_seen_ids) > _seen_max:
                    _seen_ids.clear()
                    _seen_ids.add(mid)
                wa_id = msg.get("from", "")
                mtype = msg.get("type", "")
                profile = (value.get("contacts") or [{}])[0].get("profile", {}).get("name", "")
                if not wa_id:
                    continue
                try:
                    if mtype == "text":
                        text = (msg.get("text") or {}).get("body", "").strip()
                        if not text:
                            continue
                        await _wa_read_receipt(mid)
                        await _handle_message(db, wa_id, text, profile)
                    elif mtype == "audio":
                        await _wa_read_receipt(mid)
                        await _handle_audio(db, wa_id, profile, msg.get("audio", {}).get("id", ""), mid)
                    elif mtype == "image":
                        await _wa_read_receipt(mid)
                        img = msg.get("image", {})
                        await _handle_image(db, wa_id, profile, img.get("id", ""), (img.get("caption") or "").strip(), mid)
                except Exception:
                    logger.exception("WA handler crashed")
    return {"ok": True}


# ── User + quota ─────────────────────────────────────────────────────────────

async def _ensure_plan_valid(user: User, db: AsyncSession) -> None:
    """Paid plans expire after 30 days (mirrors main.py logic; kept local to
    avoid a circular import)."""
    if user.plan in ("free", "owner"):
        return
    exp = getattr(user, "plan_expires_at", None)
    now = datetime.utcnow()
    if exp is not None and exp.tzinfo is not None:
        exp = exp.replace(tzinfo=None)
    if exp is None:
        user.plan_expires_at = now + timedelta(days=settings.PLAN_DURATION_DAYS)
        await db.commit()
    elif now > exp:
        user.plan = "free"
        user.plan_expires_at = None
        await db.commit()


async def _get_or_create_wa_user(db: AsyncSession, wa_id: str, profile_name: str):
    """Get or create the S.T.E.W account for a WhatsApp number.
    Returns (user, created)."""
    email = f"wa_{wa_id}@whatsapp.stew"
    for attempt in range(3):
        try:
            q = await db.execute(select(User).where(User.email == email))
            user = q.scalar_one_or_none()
            if user is not None:
                return user, False
            user = User(
                name=profile_name or f"WA {wa_id[-4:]}",
                email=email,
                plan="free",
                api_key=generate_api_key(),
            )
            db.add(user)
            await db.flush()
            await db.refresh(user)
            return user, True
        except Exception as e:
            logger.warning(f"WA user DB attempt {attempt+1} failed: {e}")
            await db.rollback()
            if attempt == 2:
                raise
            await asyncio.sleep(0.4)
    raise RuntimeError("unreachable")


async def _wa_allowance(user: User) -> int:
    if user.plan == "owner":
        return 999_999_999
    if user.plan in ("free",):
        return settings.WHATSAPP_FREE_TRIAL_MESSAGES
    return settings.WHATSAPP_PLAN_MESSAGES


async def _wa_used(db: AsyncSession, user: User) -> int:
    month_start = datetime.utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    q = await db.execute(
        select(func.count(APICall.id)).where(
            APICall.user_id == user.id,
            APICall.endpoint.like("whatsapp:%"),
            APICall.timestamp >= month_start,
        )
    )
    return q.scalar() or 0


def _wa_coin_cost(feature: str) -> int:
    base = settings.FEATURE_COIN_COSTS.get(feature, 1)
    return base * settings.WHATSAPP_COIN_MULTIPLIER


async def _wa_charge(db: AsyncSession, user: User, feature: str):
    """Charge one WhatsApp message against the channel allowance, then coins.
    Returns (allowed, used, limit, feature)."""
    await _ensure_plan_valid(user, db)
    used = await _wa_used(db, user)
    limit = await _wa_allowance(user)
    if used < limit:
        db.add(APICall(user_id=user.id, endpoint=f"whatsapp:{feature}", method="POST", tokens_used=0, status_code=200))
        await db.commit()
        return True, used + 1, limit, feature
    # Allowance exhausted → burn coins at the WhatsApp-surcharge rate
    coins = getattr(user, "credits_balance", 0) or 0
    cost = _wa_coin_cost(feature)
    if coins >= cost:
        user.credits_balance = coins - cost
        db.add(APICall(user_id=user.id, endpoint=f"whatsapp:{feature}", method="POST", tokens_used=0, status_code=201))
        try:
            await db.commit()
        except Exception:
            await db.rollback()
            return False, used, limit, feature
        return True, used + 1, limit, feature
    return False, used, limit, feature


# ── Commerce messages (payment link always one reply away) ──────────────────

async def _payment_link(user: User, plan: str) -> str | None:
    try:
        result = initialize_payment(
            email=user.email,
            amount_kobo=settings.PLAN_PRICES[plan] * 100,
            plan=plan,
            metadata={"user_id": user.id, "plan": plan, "channel": "whatsapp"},
        )
        url = result.get("authorization_url") or result.get("data", {}).get("authorization_url")
        return url if url else None
    except Exception as e:
        logger.error(f"WA payment link failed: {e}")
        return None


async def _send_paywall(wa_id: str, user: User, used: int, limit: int):
    link = await _payment_link(user, "whatsapp")
    msg = (
        f"⚡ You've reached your {limit:,} message limit ({used:,} used).\n\n"
        f"💎 *Stew on WhatsApp plan* — ₦{settings.PLAN_PRICES['whatsapp']:,} for 30 days:\n"
        f"300 messages + premium features (research, document drafts, web-grounded answers).\n\n"
    )
    if link:
        msg += f"Tap to pay securely (Paystack — card, transfer or USSD):\n{link}\n\n"
    else:
        msg += "Reply BUY to get your secure payment link.\n\n"
    msg += "🪙 Or top up S.T.E.W Coins (they never expire): reply BUY COINS"
    await _wa_send(wa_id, msg)


async def _send_buy_link(wa_id: str, user: User, plan: str = "whatsapp"):
    link = await _payment_link(user, plan)
    if not link:
        await _wa_send(wa_id, "Payment service is briefly unavailable — please try again in a moment.")
        return
    if plan.startswith("coins_"):
        pack = plan.replace("coins_", "")
        coins = settings.CREDIT_PACKS[pack]["coins"]
        await _wa_send(wa_id, f"🪙 {pack.title()} pack — {coins:,} S.T.E.W Coins\nPay securely (Paystack):\n{link}\n\nCoins work on WhatsApp too (10x rate). They activate automatically after payment.")
    else:
        await _wa_send(
            wa_id,
            f"💎 {plan.title()} plan — ₦{settings.PLAN_PRICES[plan]:,} / 30 days\n"
            f"Tap to pay securely (Paystack — card, transfer or USSD):\n{link}\n\n"
            f"Your plan activates automatically the moment payment lands.",
        )


# ── Command + chat handling ─────────────────────────────────────────────────

async def _handle_message(db: AsyncSession, wa_id: str, text: str, profile_name: str):
    user, created = await _get_or_create_wa_user(db, wa_id, profile_name)
    lower = text.lower().strip()

    if created:
        await _wa_send(
            wa_id,
            f"👋 Hi {user.name.split(' ')[0] if user.name else ''}! I'm *S.T.E.W* — your AI agent on WhatsApp.\n\n"
            f"Ask me anything: research, business ideas, schoolwork, drafts, market info.\n\n"
            f"🎁 You get {settings.WHATSAPP_FREE_TRIAL_MESSAGES} free messages to start.\n"
            f"💎 Unlimited vibes after that: *Stew on WhatsApp* — ₦{settings.PLAN_PRICES['whatsapp']:,}/30 days, 300 messages + premium features.\n\n"
            f"Reply BUY anytime for your secure payment link.\n"
            f"Reply MENU for everything I can do.",
        )
        return

    # ── commands ──
    if lower in ("start", "menu", "help", "?"):
        await _wa_send(
            wa_id,
            "🤖 *S.T.E.W on WhatsApp*\n\n"
            "Just chat — I handle the rest:\n"
            "📚 Schoolwork, essays, summaries\n"
            "💼 Business plans, pitches, emails\n"
            "🎙 Send a *voice note* — I'll hear and answer it\n"
            "📷 Send a *photo* — I'll read any text in it (documents, receipts, notes)\n"
            "🔬 Deep research (premium)\n"
            "📊 Crypto/stock/forex answers (premium)\n\n"
            f"💎 Plan: {settings.WHATSAPP_PLAN_MESSAGES} messages/30 days for ₦{settings.PLAN_PRICES['whatsapp']:,}\n"
            "🪙 Or top up Coins: reply BUY COINS\n"
            "📊 Your usage: reply USAGE\n"
            "💳 Reply BUY for your secure payment link — anytime.",
        )
        return
    if lower.startswith("usage") or lower == "status":
        await _ensure_plan_valid(user, db)
        used = await _wa_used(db, user)
        limit = await _wa_allowance(user)
        bal = getattr(user, "credits_balance", 0) or 0
        exp = getattr(user, "plan_expires_at", None)
        expl = f"\n⏳ Renews/expires: {exp.strftime('%d %b %Y')}" if exp else ""
        status_line = "✅ You're good." if used < limit else "⚠️ Limit reached — reply BUY to top up instantly."
        msg = (
            f"Plan: {user.plan.title()}{expl}\n"
            f"Messages: {used:,} / {limit:,} this month\n"
            f"🪙 Coins: {bal:,}\n\n"
            f"{status_line}\n\n"
            f"💳 Reply BUY anytime for your secure payment link."
        )
        await _wa_send(wa_id, msg)
        return
    if lower in ("buy coins", "coins", "credits", "topup", "top up", "buy credit"):
        await _wa_send(
            wa_id,
            "🪙 S.T.E.W Coins — one-time top-ups, never expire:\n"
            f"⚡ Spark — {settings.CREDIT_PACKS['spark']['coins']:,} coins — ₦{settings.CREDIT_PACKS['spark']['price']:,}\n"
            f"🚀 Boost — {settings.CREDIT_PACKS['boost']['coins']:,} coins — ₦{settings.CREDIT_PACKS['boost']['price']:,}\n"
            f"🏆 Mega — {settings.CREDIT_PACKS['mega']['coins']:,} coins — ₦{settings.CREDIT_PACKS['mega']['price']:,}\n\n"
            "Reply BUY SPARK, BUY BOOST or BUY MEGA for your secure link.\n"
            "(On WhatsApp, coins burn at 10x rate — they cover Meta's per-message fee.)",
        )
        return
    for pack in ("spark", "boost", "mega"):
        if lower == f"buy {pack}" or lower == f"{pack}":
            await _send_buy_link(wa_id, user, f"coins_{pack}")
            return
    if lower in ("buy", "pay", "upgrade", "subscribe", "buy plan", "payment", "link", "paystack"):
        await _send_buy_link(wa_id, user, "whatsapp")
        return
    if lower.startswith(("plan", "pricing", "price")):
        await _wa_send(
            wa_id,
            "💎 *Stew on WhatsApp*\n"
            f"₦{settings.PLAN_PRICES['whatsapp']:,} / 30 days → {settings.WHATSAPP_PLAN_MESSAGES} messages + premium research & finance tools\n\n"
            f"🪙 Coins (no subscription): Spark {settings.CREDIT_PACKS['spark']['coins']:,} for ₦{settings.CREDIT_PACKS['spark']['price']:,} · "
            f"Boost {settings.CREDIT_PACKS['boost']['coins']:,} for ₦{settings.CREDIT_PACKS['boost']['price']:,} · "
            f"Mega {settings.CREDIT_PACKS['mega']['coins']:,} for ₦{settings.CREDIT_PACKS['mega']['price']:,}\n\n"
            "Reply BUY to pay & activate instantly.",
        )
        return

    # ── feature detection (weighted coin pricing) ──
    feature = _detect_feature(text)

    allowed, used, limit, _ = await _wa_charge(db, user, feature)
    if not allowed:
        await _send_paywall(wa_id, user, used, limit)
        return

    # ── generate the reply (same engine as Telegram/API) ──
    await _wa_typing(wa_id)
    await _wa_chat_reply(db, user, wa_id, text, feature, used, limit)


def _detect_feature(text: str) -> str:
    """Weighted feature detection — same coin economy as the API surface."""
    lower = text.lower()
    if re.search(r"\bresearch\b|\binvestigate\b|\bdeep dive\b|\breport on\b", lower):
        return "research"
    if re.search(r"\bsearch (for|about|up)\b|\bgoogle\b|\blook up\b", lower):
        return "search"
    if re.search(r"\b(btc|bitcoin|ethereum|crypto|stock price|share price|forex|naira rate|usd to ngn|exchange rate)\b", lower):
        return "finance"
    return "chat"


async def _wa_chat_reply(db: AsyncSession, user: User, wa_id: str, text: str,
                         feature: str, used: int, limit: int):
    """Shared reply engine: grounding (research/search) → memory recall → LLM →
    WhatsApp-native output. Used by text, voice-note and photo-OCR paths."""
    llm = get_llm_client()
    searcher = get_searcher()
    system = WA_SYSTEM
    try:
        if feature == "research":
            await _wa_typing(wa_id)
            await _wa_send(wa_id, "🔬 Deep research started — give me a moment…")
            results = await asyncio.to_thread(searcher.stew_extension_research, text, 3)
            if results.get("grounded") and results.get("report"):
                system += f"\n\nRESEARCH CONTEXT:\n{results['report']}"
        elif feature in ("search", "finance"):
            await _wa_typing(wa_id)
            results = await asyncio.to_thread(searcher.search, text, 5)
            if results.get("grounded"):
                system += f"\n\nWEB SEARCH CONTEXT:\n{searcher.format_results_for_llm(results)}"
    except Exception as e:
        logger.warning(f"WA grounding failed: {e}")

    conv = await get_or_create_conversation(db, user.id, None)
    recalled = await get_relevant_context(db, user.id, text, platform="whatsapp")
    await append_message(db, conv, "user", text, platform="whatsapp")
    messages = build_llm_messages(conv, system, recalled)

    try:
        result = await asyncio.to_thread(llm.chat, messages)
        reply = clean_response(result["content"])
    except Exception:
        await _wa_send(wa_id, "I hit a snag on that one — try again in a moment.")
        return
    await append_message(db, conv, "assistant", reply, platform="whatsapp")

    # Steady & showing: periodic payment nudge — never hidden, never nagging
    if used % 20 == 0 and user.plan == "free":
        reply += f"\n\n🎁 {limit - used} free messages left. Reply BUY to unlock {settings.WHATSAPP_PLAN_MESSAGES} + premium."
    await _wa_send(wa_id, reply)


async def _handle_audio(db: AsyncSession, wa_id: str, profile_name: str, media_id: str, mid: str):
    """Voice note in → Groq Whisper STT → same premium chat engine.
    Charged at the 'voice' weight (premium feature)."""
    user, created = await _get_or_create_wa_user(db, wa_id, profile_name)
    if created:
        await _wa_send(
            wa_id,
            "👋 Hi! I'm *S.T.E.W* — your AI agent on WhatsApp.\n\n"
            "I can even answer voice notes — send another one, or just type.\n\n"
            f"🎁 You get {settings.WHATSAPP_FREE_TRIAL_MESSAGES} free messages to start.\n"
            "Reply MENU for everything I can do.",
        )
        return
    await _wa_typing(wa_id)

    media = await _wa_download_media(media_id)
    if not media:
        await _wa_send(wa_id, "I couldn't grab that voice note — mind typing it instead?")
        return

    allowed, used, limit, _ = await _wa_charge(db, user, "voice")
    if not allowed:
        await _send_paywall(wa_id, user, used, limit)
        return
    audio_bytes, mime = media
    ext = "ogg" if "ogg" in (mime or "") else ("m4a" if "mp4" in (mime or "") else "ogg")

    # Lazy import avoids the circular main ↔ whatsapp_bot import at module load
    from server.main import _transcribe_audio_bytes
    transcript, err = await _transcribe_audio_bytes(audio_bytes, f"voice.{ext}")
    if err or not transcript.strip():
        await _wa_send(wa_id, "🎙 I couldn't quite hear that — try again or just type it out.")
        return

    await _wa_send(wa_id, f"🎙 I heard: _{transcript[:200]}_\n\nThinking…")
    await _wa_typing(wa_id)
    await _wa_chat_reply(db, user, wa_id, transcript, _detect_feature(transcript), used, limit)


async def _handle_image(db: AsyncSession, wa_id: str, profile_name: str, media_id: str,
                        caption: str, mid: str):
    """Photo in → Tesseract OCR (open-source, per house standard) → premium chat.
    Charged at the 'ocr' weight."""
    user, created = await _get_or_create_wa_user(db, wa_id, profile_name)
    if created:
        await _wa_send(
            wa_id,
            "👋 Hi! I'm *S.T.E.W* — your AI agent on WhatsApp.\n\n"
            "Send me that photo again and I'll read it — documents, receipts, notes, anything with text.\n\n"
            f"🎁 You get {settings.WHATSAPP_FREE_TRIAL_MESSAGES} free messages to start.",
        )
        return
    await _wa_typing(wa_id)

    media = await _wa_download_media(media_id)
    if not media:
        await _wa_send(wa_id, "I couldn't download that image — try sending it again.")
        return

    allowed, used, limit, _ = await _wa_charge(db, user, "ocr")
    if not allowed:
        await _send_paywall(wa_id, user, used, limit)
        return
    image_bytes, mime = media
    filename = "photo.jpg" if "png" not in (mime or "") else "photo.png"

    try:
        from server.ocr_engine import ocr_file
        ocr_result = await asyncio.to_thread(ocr_file, image_bytes, filename, "eng", False, False)
        extracted = (ocr_result.get("text") or "").strip()
    except Exception as e:
        logger.error(f"WA OCR failed: {e}")
        extracted = ""

    if not extracted:
        await _wa_send(wa_id, "I looked but couldn't find readable text in that image. Try a sharper photo with better lighting, or tell me what you need.")
        return

    # Feed the extracted text (+ any caption) into the same premium engine
    prompt = f"[Photo sent via WhatsApp — OCR extracted this text]\n{extracted[:3500]}"
    if caption:
        prompt = f"{caption}\n\n{prompt}"
    await _wa_send(wa_id, f"📄 Got it — I read *{len(extracted.split())} words* from your photo. Analyzing…")
    await _wa_typing(wa_id)
    await _wa_chat_reply(db, user, wa_id, prompt, _detect_feature(caption or extracted[:500]), used, limit)
