"""
S.T.E.W Live Motion — the "sharp moves" working animation.

While Stew works, a status message with an animated spinner is sent and
live-edited so users SEE the magic happening in real time, right up until
the final answer arrives.
"""
import asyncio
import logging
from typing import Optional

logger = logging.getLogger(__name__)

_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
_TICKS = ["🌕", "🌖", "🌗", "🌘", "🌑", "🌒", "🌓", "🌔"]


class WorkingBanner:
    """Animated live status banner for Telegram.

        banner = WorkingBanner(bot, chat_id, "⚡ Stew is on it")
        await banner.start()
        await banner.update("🔍 Searching the web…")
        await banner.finish("✅ Reply ready")

    start() sends a message and runs a spinner that live-edits it every
    ~1.4s; update() swaps the stage line; finish() stops the spinner and
    finalizes the message.
    """

    def __init__(self, bot, chat_id: int, title: str = "🧠 Stew is working"):
        self.bot = bot
        self.chat_id = int(chat_id)
        self.title = title
        self.stage: str = "Starting…"
        self.stages_done: list = []
        self.message_id: Optional[int] = None
        self._task: Optional[asyncio.Task] = None
        self._frame = 0
        self._done = False

    def _render(self, frame: str) -> str:
        lines = [self.title]
        lines += [f"✅ {s}" for s in self.stages_done]
        if not self._done:
            lines.append(f"{frame} {self.stage}")
        return "\n".join(lines)

    async def _spin(self) -> None:
        while not self._done:
            try:
                self._frame = (self._frame + 1) % len(_FRAMES)
                if self.message_id:
                    await self.bot.edit_message(self.chat_id, self.message_id,
                                                self._render(_FRAMES[self._frame]))
                else:
                    await self.bot.send_chat_action(self.chat_id, "typing")
            except asyncio.CancelledError:
                return
            except Exception:
                pass
            await asyncio.sleep(1.4)

    async def start(self) -> None:
        try:
            await self.bot.send_chat_action(self.chat_id, "typing")
            res = await self.bot.send_message(self.chat_id, self._render(_FRAMES[0]))
            self.message_id = (res or {}).get("message_id")
        except Exception as e:
            logger.debug(f"banner start skipped: {e}")
        try:
            self._task = asyncio.get_event_loop().create_task(self._spin())
        except Exception:
            pass

    async def update(self, stage: str) -> None:
        """Move to the next stage — previous stage is ticked off as done."""
        if self.stage and self.stage != "Starting…" and self.stage not in self.stages_done:
            self.stages_done.append(self.stage)
        self.stage = stage[:120]

    async def finish(self, final: str = "✅ Done") -> None:
        self._done = True
        if self._task:
            try:
                self._task.cancel()
            except Exception:
                pass
        if self.stage and self.stage not in self.stages_done and not self.stages_done.count(self.stage):
            self.stages_done.append(self.stage)
        try:
            if self.message_id:
                lines = [self.title] + [f"✅ {s}" for s in self.stages_done] + [final[:200]]
                await self.bot.edit_message(self.chat_id, self.message_id, "\n".join(lines))
        except Exception as e:
            logger.debug(f"banner finish edit skipped: {e}")
