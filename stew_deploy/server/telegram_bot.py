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
        """Send a file to a Telegram chat with proper MIME type."""
        import mimetypes
        mime_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                f"{self.base}/sendDocument",
                data={"chat_id": str(chat_id), "caption": caption[:1024]},
                files={"document": (filename, file_bytes, mime_type)},
            )
            result = resp.json()
            if not result.get("ok"):
                logger.error(f"Telegram sendDocument failed: {result}")
            return result

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


    async def get_file_path(self, file_id: str) -> str | None:
        """Get the file path for a Telegram file_id."""
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(f"{self.base}/getFile", params={"file_id": file_id})
            data = resp.json()
            if data.get("ok"):
                return data["result"]["file_path"]
            return None

    async def download_file(self, file_id: str) -> bytes | None:
        """Download a file from Telegram by file_id. Returns raw bytes."""
        file_path = await self.get_file_path(file_id)
        if not file_path:
            return None
        download_url = f"https://api.telegram.org/file/bot{self.token}/{file_path}"
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.get(download_url)
            if resp.status_code == 200:
                return resp.content
            return None


    async def send_inline_keyboard(self, chat_id: int, text: str, keyboard: list) -> dict:
        """Send a message with an inline keyboard (buttons).

        keyboard = [[{"text": "Button 1", "callback_data": "action1"}], ...]
        """
        import json as _json
        inline_keyboard = {"inline_keyboard": keyboard}
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{self.base}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": text,
                    "reply_markup": _json.dumps(inline_keyboard),
                },
            )
            return resp.json()

    async def send_voice(self, chat_id: int, audio_bytes: bytes, caption: str = "") -> dict:
        """Send a voice message (OGG format)."""
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{self.base}/sendVoice",
                data={"chat_id": str(chat_id), "caption": caption[:1024]},
                files={"voice": ("voice.ogg", audio_bytes, "audio/ogg")},
            )
            return resp.json()

    def parse_update(self, data: dict) -> Optional[dict]:
        """Extract message info from Telegram update.
        Handles text, photos, documents, voice, audio, and callback queries."""
        # Handle callback queries (inline button presses)
        callback = data.get("callback_query")
        if callback:
            return {
                "update_id": data.get("update_id"),
                "chat_id": callback["message"]["chat"]["id"],
                "user_id": callback["from"]["id"],
                "username": callback["from"].get("username", ""),
                "first_name": callback["from"].get("first_name", ""),
                "text": "",
                "caption": "",
                "message_id": callback["message"]["message_id"],
                "is_bot": callback["from"].get("is_bot", False),
                "has_photo": False,
                "has_document": False,
                "has_voice": False,
                "has_audio": False,
                "file_id": None,
                "file_name": None,
                "file_type": None,
                "file_size": 0,
                "is_callback": True,
                "callback_data": callback.get("data", ""),
                "callback_id": callback.get("id", ""),
            }

        msg = data.get("message") or data.get("edited_message")
        if not msg:
            return None

        text = msg.get("text", "")
        caption = msg.get("caption", "")
        has_photo = "photo" in msg and msg["photo"]
        has_document = "document" in msg and msg["document"]
        has_voice = "voice" in msg and msg["voice"]
        has_audio = "audio" in msg and msg["audio"]

        # Determine file info
        file_id = None
        file_name = None
        file_type = None
        file_size = None

        if has_photo:
            photo = msg["photo"][-1]
            file_id = photo.get("file_id")
            file_name = f"photo_{msg['message_id']}.jpg"
            file_type = "image"
            file_size = photo.get("file_size", 0)
        elif has_document:
            doc = msg["document"]
            file_id = doc.get("file_id")
            file_name = doc.get("file_name", "document")
            file_type = "document"
            file_size = doc.get("file_size", 0)
        elif has_voice:
            voice = msg["voice"]
            file_id = voice.get("file_id")
            file_name = f"voice_{msg['message_id']}.ogg"
            file_type = "voice"
            file_size = voice.get("file_size", 0)
        elif has_audio:
            audio = msg["audio"]
            file_id = audio.get("file_id")
            file_name = audio.get("file_name", f"audio_{msg['message_id']}.mp3")
            file_type = "audio"
            file_size = audio.get("file_size", 0)

        return {
            "update_id": data.get("update_id"),
            "chat_id": msg["chat"]["id"],
            "user_id": msg["from"]["id"],
            "username": msg["from"].get("username", ""),
            "first_name": msg["from"].get("first_name", ""),
            "text": text,
            "caption": caption,
            "message_id": msg["message_id"],
            "is_bot": msg["from"].get("is_bot", False),
            "is_callback": False,
            "callback_data": None,
            "callback_id": None,
            "has_photo": has_photo,
            "has_document": has_document,
            "has_voice": has_voice,
            "has_audio": has_audio,
            "file_id": file_id,
            "file_name": file_name,
            "file_type": file_type,
            "file_size": file_size,
        }
