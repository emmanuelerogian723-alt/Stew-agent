"""
S.T.E.W Keepalive — Prevents cold starts on free hosting tiers.
Pings the /heartbeat endpoint on a schedule so the server stays warm.
Works on Render, Railway, Fly.io, Hugging Face Spaces, Koyeb.
"""
import asyncio
import logging
import os
import httpx
from datetime import datetime

logger = logging.getLogger(__name__)

# How often to self-ping (seconds). Free tier servers sleep after 15 min.
PING_INTERVAL = int(os.environ.get("KEEPALIVE_INTERVAL", "600"))  # 10 minutes default
SELF_URL = os.environ.get("RENDER_EXTERNAL_URL") or \
           os.environ.get("RAILWAY_PUBLIC_DOMAIN") or \
           os.environ.get("KOYEB_PUBLIC_DOMAIN") or \
           os.environ.get("FLY_APP_NAME") or \
           os.environ.get("APP_BASE_URL", "")

_keepalive_task = None


async def _ping_loop():
    """Background loop that pings /heartbeat to prevent sleep."""
    await asyncio.sleep(60)  # Wait 1 minute after startup before first ping
    while True:
        try:
            base = SELF_URL
            if base:
                if not base.startswith("http"):
                    base = f"https://{base}"
                url = f"{base.rstrip('/')}/heartbeat"
                async with httpx.AsyncClient(timeout=10) as client:
                    resp = await client.get(url)
                    logger.info(f"Keepalive ping → {url} [{resp.status_code}] at {datetime.utcnow().isoformat()}")
            else:
                logger.debug("Keepalive: no APP_BASE_URL set, skipping ping")
        except Exception as e:
            logger.warning(f"Keepalive ping failed: {e}")
        await asyncio.sleep(PING_INTERVAL)


def start_keepalive():
    """Start the keepalive background task. Call from app lifespan."""
    global _keepalive_task
    if SELF_URL:
        _keepalive_task = asyncio.create_task(_ping_loop())
        logger.info(f"Keepalive started — pinging every {PING_INTERVAL}s → {SELF_URL}")
    else:
        logger.info("Keepalive: set APP_BASE_URL env var to enable self-pinging")


def stop_keepalive():
    """Cancel the keepalive task on shutdown."""
    global _keepalive_task
    if _keepalive_task:
        _keepalive_task.cancel()
        _keepalive_task = None
