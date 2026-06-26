"""
S.T.E.W Telegram Bot Integration.
Receives messages via webhook, processes them through the S.T.E.W engine,
and sends replies back via Telegram Bot API.
"""
import asyncio
import logging
import httpx
from typing import Optional

logger = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"


class TelegramBot:
    def __init__(self, token: str):
        self.token = token
        self.base = f"https://api.telegram.org/bot{token}"

    async def send_message(self, chat_id: int, text: str, parse_mode: str = "Markdown") -> dict:
        """Send a message to a Telegram chat."""
        # Telegram message limit is 4096 chars
        chunks = [text[i:i+4000] for i in range(0, len(text), 4000)]
        results = []
        async with httpx.AsyncClient(timeout=15) as client:
            for chunk in chunks:
                resp = await client.post(
                    f"{self.base}/sendMessage",
                    json={
                        "chat_id": chat_id,
                        "text": chunk,
                        "parse_mode": parse_mode,
                        "disable_web_page_preview": False,
                    },
                )
                results.append(resp.json())
        return results[-1] if results else {}

    async def send_document(self, chat_id: int, file_bytes: bytes,
                            filename: str, caption: str = "") -> dict:
        """Send a file to a Telegram chat."""
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{self.base}/sendDocument",
                data={"chat_id": str(chat_id), "caption": caption},
                files={"document": (filename, file_bytes)},
            )
            return resp.json()

    async def set_webhook(self, webhook_url: str) -> dict:
        """Register webhook URL with Telegram."""
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{self.base}/setWebhook",
                json={"url": webhook_url, "allowed_updates": ["message", "callback_query"]},
            )
            return resp.json()

    async def delete_webhook(self) -> dict:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(f"{self.base}/deleteWebhook")
            return resp.json()

    async def get_me(self) -> dict:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{self.base}/getMe")
            return resp.json()

    async def send_typing(self, chat_id: int):
        """Show typing indicator."""
        async with httpx.AsyncClient(timeout=5) as client:
            await client.post(
                f"{self.base}/sendChatAction",
                json={"chat_id": chat_id, "action": "typing"},
            )

    def parse_update(self, data: dict) -> Optional[dict]:
        """Extract message info from Telegram update."""
        msg = data.get("message") or data.get("edited_message")
        if not msg:
            return None
        return {
            "update_id": data.get("update_id"),
            "chat_id": msg["chat"]["id"],
            "user_id": msg["from"]["id"],
            "username": msg["from"].get("username", ""),
            "first_name": msg["from"].get("first_name", ""),
            "text": msg.get("text", ""),
            "message_id": msg["message_id"],
            "is_bot": msg["from"].get("is_bot", False),
        }
