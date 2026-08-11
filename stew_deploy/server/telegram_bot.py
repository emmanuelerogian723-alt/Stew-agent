"""
S.T.E.W Telegram Bot Integration.
Receives messages via webhook, processes them through the S.T.E.W engine,
and sends replies back via Telegram Bot API.
"""
import asyncio
import logging
import httpx
from server.clean_output import clean_response
from typing import Optional

logger = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"


class TelegramBot:
    def __init__(self, token: str):
        self.token = token
        self.base = f"https://api.telegram.org/bot{token}"

    async def send_message(self, chat_id: int, text: str, parse_mode: str = "") -> dict:
        """Send a message to a Telegram chat. Clean markdown before sending."""
        text = clean_response(text)
        chunks = [text[i:i+4000] for i in range(0, len(text), 4000)]
        results = []
        async with httpx.AsyncClient(timeout=30) as client:
            for chunk in chunks:
                payload = {
                    "chat_id": chat_id,
                    "text": chunk,
                    "disable_web_page_preview": False,
                }
                if parse_mode:
                    payload["parse_mode"] = parse_mode
                resp = await client.post(
                    f"{self.base}/sendMessage",
                    json=payload,
                )
                results.append(resp.json())
        return results[-1] if results else {}

    async def send_document(self, chat_id: int, file_bytes: bytes,
                            filename: str, caption: str = "") -> dict:
        """Send a file to a Telegram chat."""
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{self.base}/sendDocument",
                data={"chat_id": str(chat_id), "caption": caption[:1024]},
                files={"document": (filename, file_bytes)},
            )
            return resp.json()

    async def send_photo(self, chat_id: int, photo_bytes: bytes,
                         caption: str = "", filename: str = "image.jpg") -> dict:
        """Send a photo (raw bytes) to a Telegram chat."""
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{self.base}/sendPhoto",
                data={"chat_id": str(chat_id), "caption": caption[:1024]},
                files={"photo": (filename, photo_bytes, "image/jpeg")},
            )
            return resp.json()

    async def send_photo_url(self, chat_id: int, photo_url: str,
                             caption: str = "") -> dict:
        """Send a photo by URL to a Telegram chat."""
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{self.base}/sendPhoto",
                json={"chat_id": chat_id, "photo": photo_url, "caption": caption[:1024]},
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

    async def send_chat_action(self, chat_id: int, action: str = "typing"):
        """Send a chat action (typing, upload_photo, upload_document)."""
        async with httpx.AsyncClient(timeout=5) as client:
            await client.post(
                f"{self.base}/sendChatAction",
                json={"chat_id": chat_id, "action": action},
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
