"""
S.T.E.W Morning Motivation - wakes every Telegram user each morning with a
fresh motivational image quote. Runs as a background loop inside the FastAPI
app (same pattern as keepalive / bot_stats). New Telegram users are picked up
automatically because the roster is queried from the DB every morning.

Schedule: 6:30 AM Africa/Lagos (UTC+1) daily. One shared quote per day for the
whole fleet - 1 LLM call + 1 AI background render, so it costs almost nothing
even with thousands of users. The morning boost is a gift: it does NOT consume
any user's image quota.
"""
import asyncio
import json
import logging
import re
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from server.database import AsyncSessionLocal
from server.models import User

logger = logging.getLogger("stew.morning_quotes")

LAGOS = timezone(timedelta(hours=1))
FIRE_HOUR, FIRE_MINUTE = 6, 30  # 6:30 AM Lagos time

_task = None
_last_sent_date = None

# Offline rotation used if every LLM provider is down
_OFFLINE_QUOTES = [
    ("Your future is built by what you do today, not tomorrow. Start now.", "Stew Daily Boost"),
    ("Small daily wins compound into an unstoppable life. Win today.", "Stew Daily Boost"),
    ("Discipline is choosing what you want most over what you want now.", "Stew Daily Boost"),
    ("The master has failed more times than the beginner has even tried.", "African Proverb"),
    ("However long the night, the dawn will break.", "African Proverb"),
    ("If you want to go fast, go alone. If you want to go far, go together.", "African Proverb"),
    ("Skills are cheaper to build today than to beg with tomorrow.", "Stew Daily Boost"),
    ("Every expert was once a beginner who refused to quit.", "Stew Daily Boost"),
    ("The best time to plant a tree was twenty years ago. The second best time is now.", "African Proverb"),
    ("Do not watch the clock. Do what it does - keep moving.", "Stew Daily Boost"),
]


def _daily_quote():
    """Generate today's unique motivational quote (sync, run in a thread).
    Returns (quote, author)."""
    date_key = datetime.now(LAGOS).strftime("%A, %d %B %Y")
    try:
        from server.llm_client import get_llm_client
        llm = get_llm_client()
        r = llm.chat([
            {"role": "system", "content":
                "You are a motivational writer for ambitious young Africans. "
                "Create ONE original motivational quote for today. Reply with ONLY a JSON object: "
                '{"quote": "<max 22 words, powerful, specific, no cliches, no emoji>", '
                '"author": "<short attribution: \'Stew Daily Boost\' or \'African Proverb\' or the real author name>"}'},
            {"role": "user", "content":
                f"Today is {date_key}. Themes rotate between: starting strong, building skills, "
                f"business growth, consistency, self-belief, serving people excellently. "
                f"Never use overused quotes."},
        ])
        m = re.search(r"\{.*\}", r.get("content", ""), re.S)
        if m:
            d = json.loads(m.group(0))
            q = (d.get("quote") or "").strip().strip('"')
            if q:
                return q[:160], ((d.get("author") or "Stew Daily Boost").strip()[:40])
    except Exception as e:
        logger.warning(f"Quote LLM failed, using offline quote: {e}")
    idx = datetime.now(LAGOS).timetuple().tm_yday % len(_OFFLINE_QUOTES)
    return _OFFLINE_QUOTES[idx]


async def _tg_chat_ids() -> list:
    """All active Telegram users - existing AND new (queried fresh each morning)."""
    async with AsyncSessionLocal() as db:
        rows = await db.execute(
            select(User.email).where(
                User.email.like("tg_%@telegram.stew"),
                User.is_active == True,  # noqa: E712
            )
        )
        ids = []
        for (email,) in rows.fetchall():
            try:
                ids.append(int(email[3:].split("@")[0]))
            except (ValueError, IndexError):
                continue
        return ids


async def send_morning_quotes(chat_ids=None) -> dict:
    """Generate today's quote card and broadcast it. Returns a summary dict.
    chat_ids=None -> send to every active Telegram user."""
    from server.telegram_bot import TelegramBot
    from server.config import get_settings
    from server.image_studio import render_quote_image

    quote, author = await asyncio.to_thread(_daily_quote)
    try:
        img = await asyncio.to_thread(render_quote_image, quote, author)
    except Exception as e:
        logger.error(f"Quote card render failed: {e}")
        return {"ok": False, "error": f"render failed: {e}"}

    if chat_ids is None:
        try:
            chat_ids = await _tg_chat_ids()
        except Exception as e:
            logger.error(f"Could not load Telegram roster: {e}")
            return {"ok": False, "error": f"roster failed: {e}"}

    if not chat_ids:
        return {"ok": True, "quote": quote, "author": author, "sent": 0, "failed": 0}

    bot = TelegramBot(get_settings().TELEGRAM_BOT_TOKEN)
    caption = "Good morning! Your daily boost from S.T.E.W - have a powerful day."
    sent, failed = 0, 0
    for cid in chat_ids:
        try:
            await bot.send_photo(cid, img, caption=caption)
            sent += 1
        except Exception as e:
            failed += 1
            logger.warning(f"Quote send failed to {cid}: {e}")
        await asyncio.sleep(0.5)  # stay well under Telegram rate limits
    logger.info(f"Morning boost delivered: {sent} sent, {failed} failed - '{quote[:50]}'")
    return {"ok": True, "quote": quote, "author": author, "sent": sent, "failed": failed}


async def _loop():
    global _last_sent_date
    await asyncio.sleep(180)  # let the server settle after boot
    while True:
        try:
            now = datetime.now(LAGOS)
            today = now.strftime("%Y-%m-%d")
            if (now.hour == FIRE_HOUR and now.minute >= FIRE_MINUTE
                    and _last_sent_date != today):
                _last_sent_date = today  # claim before sending so a retry can't double-fire
                await send_morning_quotes()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(f"Morning quote loop error: {e}")
        await asyncio.sleep(45)


def start_morning_quotes():
    global _task
    if _task is None or _task.done():
        _task = asyncio.create_task(_loop())
        logger.info("Morning motivation active - waking users at 6:30 AM daily (Lagos time)")


def stop_morning_quotes():
    global _task
    if _task is not None:
        _task.cancel()
        _task = None
