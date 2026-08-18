"""
S.T.E.W Telegram Bot Stats — keeps the bot's public "About" description on
Telegram updated with a live user count (mirrors how consumer AI apps like
Manus show a persistent "X,XXX users" badge). Runs as a lightweight periodic
background task, same pattern as keepalive.py.
"""
import asyncio
import logging
import os
import httpx

logger = logging.getLogger(__name__)

# How often to refresh the bot's description (seconds). 30 minutes is plenty —
# this is a vanity counter, not a real-time metric.
STATS_INTERVAL = int(os.environ.get("BOT_STATS_INTERVAL", "1800"))

_stats_task = None


async def _get_user_count() -> int:
    """Query the DB directly (own session) for the current Telegram user count."""
    from server.database import AsyncSessionLocal
    from server.models import User
    from sqlalchemy import select, func
    try:
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(func.count(User.id)).where(User.email.like("tg_%@telegram.stew"))
            )
            return result.scalar() or 0
    except Exception as e:
        logger.warning(f"Bot stats: failed to count users: {e}")
        return -1


async def _update_description_loop():
    from server.config import get_settings
    settings = get_settings()
    token = settings.TELEGRAM_BOT_TOKEN
    if not token:
        logger.info("Bot stats: no TELEGRAM_BOT_TOKEN, skipping description updater")
        return

    await asyncio.sleep(45)  # let the app finish booting first
    while True:
        try:
            count = await _get_user_count()
            if count >= 0:
                short_desc = f"AI Agent for Africa | 👥 {count:,} users | 59 skills, Naira billing"
                full_desc = (
                    f"S.T.E.W — AI Agent for Africa\n\n"
                    f"👥 {count:,} users\n"
                    f"Web research, documents, OCR, code execution, images, songs, and more.\n"
                    f"Type /start to begin. Free tier included."
                )
                async with httpx.AsyncClient(timeout=15) as client:
                    await client.post(
                        f"https://api.telegram.org/bot{token}/setMyShortDescription",
                        json={"short_description": short_desc[:120]},
                    )
                    await client.post(
                        f"https://api.telegram.org/bot{token}/setMyDescription",
                        json={"description": full_desc[:512]},
                    )
                logger.info(f"Bot stats: updated Telegram profile with {count:,} users")
        except Exception as e:
            logger.warning(f"Bot stats update failed: {e}")
        await asyncio.sleep(STATS_INTERVAL)


def start_bot_stats():
    global _stats_task
    _stats_task = asyncio.create_task(_update_description_loop())
    logger.info(f"Bot stats updater started — refreshing every {STATS_INTERVAL}s")


def stop_bot_stats():
    global _stats_task
    if _stats_task:
        _stats_task.cancel()
        _stats_task = None
