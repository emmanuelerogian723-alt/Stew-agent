"""
S.T.E.W Live Motion — the "sharp moves" working animation.

While Stew works, a status message with an animated spinner is sent and
live-edited so users SEE the magic happening in real time, right up until
the final answer arrives.
"""
import asyncio
import logging
from datetime import timedelta
from typing import Optional

logger = logging.getLogger(__name__)

_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

# ── WIND MOTION ──────────────────────────────────────────────────────────────
# Claude's execution panel has flowing gradient "wind" lines while it works.
# In plain Telegram text we emulate that with a wave pattern that DRIFTS one
# character per tick — the strip visibly flows across edits.
_WIND_CHARS = ["≋", "≈", "∿", "≈"]


def _wind_strip(length: int, phase: int = 0) -> str:
    if length <= 0:
        return ""
    return "".join(_WIND_CHARS[(i + phase) % len(_WIND_CHARS)] for i in range(length))


def _esc(text) -> str:
    """HTML-escape dynamic content so bold markup can never break the edit."""
    import html as _html
    return _html.escape(str(text or ""))
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
            self.message_id = (res or {}).get("result", {}).get("message_id")
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


# ─────────────────────────────────────────────────────────────────────────
# LiveActivityStream — the "real digital worker" execution UI.
#
# Telegram is plain text, so "premium motion" is simulated the only way a
# chat medium allows: one message, edited every ~1.5s, whose content keeps
# growing and changing — a cycling spinner, a filling progress bar, a
# timeline of checked-off steps each with real evidence (never "Working…"),
# a live "tool card" for whatever connector/tool is active right now, and
# a rolling timestamped log console. Rendered in plain text (no Markdown)
# on purpose: tool evidence text is arbitrary and would break Telegram's
# markdown entity parser mid-run and silently freeze the animation.
# ─────────────────────────────────────────────────────────────────────────

WAT_OFFSET = timedelta(hours=1)  # Africa/Lagos, UTC+1, no DST


def _wat_now_str() -> str:
    from datetime import datetime as _dt, timezone as _tz
    return (_dt.now(_tz.utc) + WAT_OFFSET).strftime("%H:%M")


class LiveActivityStream:
    """Rich live-execution status message for the tool-calling agent.

        stream = LiveActivityStream(bot, chat_id)
        await stream.start()
        # tool_agent's progress_cb IS this — a plain sync callable:
        agent_result = await run_agent_loop(..., progress_cb=stream.record)
        await stream.finish("Done — see the answer below 👇")

    record() is a fast, synchronous, non-blocking state mutation (safe to
    call directly from the agent loop with no await). A background ticker
    task reads that state every ~1.5s and edits the single Telegram message,
    decoupling how fast tool events arrive from how fast Telegram lets a
    bot edit a message.
    """

    def __init__(self, bot, chat_id: int, title: str = "⚡ Stew is on it — Live Execution"):
        self.bot = bot
        self.chat_id = int(chat_id)
        self.title = title
        self.message_id: Optional[int] = None
        self.timeline: list = []       # finished step lines, e.g. "✅ Found 327 emails"
        self.logs: list = []           # (HH:MM, text) rolling log console lines
        self.tool_card: Optional[dict] = None
        self.current: Optional[dict] = None   # {"type": "thinking"|"tool", "label": ...}
        self.progress_pct: int = 0
        self._task: Optional[asyncio.Task] = None
        self._frame = 0
        self._done = False
        self._last_rendered = ""

    def _log(self, text: str) -> None:
        self.logs.append((_wat_now_str(), text[:90]))
        self.logs = self.logs[-8:]

    def note(self, label: str) -> None:
        """One-off ad-hoc status line (e.g. 'Fetching your generated video…')
        that isn't a structured tool event — still sync, still cheap."""
        self.current = {"type": "thinking", "label": label[:150]}

    def record(self, event: dict) -> None:
        """Sync state update — called directly as tool_agent's progress_cb."""
        try:
            kind = (event or {}).get("kind")
            if kind == "thinking":
                self.current = {"type": "thinking", "label": event.get("label") or "Thinking…"}
            elif kind == "tool_start":
                card = {
                    "type": "tool",
                    "icon": event.get("icon", "⚙️"),
                    "name": event.get("name") or event.get("tool", "Tool"),
                    "label": event.get("label") or "Working…",
                    "connector": bool(event.get("connector")),
                    "status": "running",
                    "evidence": None,
                }
                self.current = card
                self.tool_card = card
                self._log(f"{card['icon']} {card['name']}: {card['label']}")
            elif kind == "tool_done":
                evidence = str(event.get("evidence") or "Completed")[:150]
                ok = event.get("ok", True)
                icon = event.get("icon", "⚙️")
                name = event.get("name") or event.get("tool", "Tool")
                prefix = "✅" if ok else "⚠️"
                # Combine the action label + real evidence into ONE dense
                # timeline line (icon so it stays traceable to its
                # connector/tool even after the tool card moves on) — e.g.
                # "📧 ✅ Reading Inbox — Found 327 emails" — instead of a
                # bare "Working…" or a bare number with no context.
                action = ""
                if self.tool_card and self.tool_card.get("name") == name:
                    action = str(self.tool_card.get("label", "")).rstrip("…").strip()
                    self.tool_card["status"] = "done" if ok else "error"
                    self.tool_card["evidence"] = evidence
                if action and action.lower() not in evidence.lower():
                    log_line = f"{icon} {prefix} {action} — {evidence}"
                else:
                    log_line = f"{icon} {prefix} {evidence}"
                # Claude-style step group: gerund action title + real evidence.
                title = action or str(name)
                self.timeline.append({
                    "icon": icon, "title": title[:80], "evidence": evidence[:120],
                    "ok": ok,
                })
                self._log(log_line)
                self.current = None
                if self.timeline:
                    self.progress_pct = min(90, int(100 * len(self.timeline) / (len(self.timeline) + 2)))
        except Exception:
            logger.debug("LiveActivityStream.record swallowed a bad event", exc_info=True)

    def _render(self, final: bool = False) -> str:
        """Claude-style execution panel:
        - bold step titles, muted (·) context/evidence lines
        - a WIND strip that drifts every tick while work is in flight
        - the wind doubles as the progress bar (flows further as steps land)
        - rolling muted activity log at the bottom"""
        phase = self._frame
        out = []

        # Header — bold, Claude-panel style
        out.append(f"<b>{_esc('⚡ Stew is working' if not final else '⚡ Stew — finished')}</b>")

        # Wind strip: flows while running; full-width still wave when done
        if final:
            out.append(_wind_strip(24) + "‖")
        else:
            out.append(_wind_strip(22, phase) + "≫")

        # Step groups (Claude-style: bold title line, muted evidence line)
        shown = self.timeline[-5:]
        hidden = len(self.timeline) - len(shown)
        if hidden > 0:
            out.append(f"· … {_esc(hidden)} earlier step(s) done")
        for step in shown:
            mark = "✓" if step.get("ok", True) else "⚠"
            out.append(f"<b>{mark} {_esc(step['title'])}</b>")
            out.append(f"· {_esc(step['evidence'])}")

        # Current in-flight action — bold with a small trailing wind trail
        if not final and self.current:
            label = self.current.get("label", "Working…")
            trail = _wind_strip(3, phase)
            out.append(f"<b>▸ {_esc(label)} {_wind_strip(3, phase)}</b>")

        # Tool card (connector permission + status) — Claude tool-card feel
        tc = self.tool_card
        if tc and (not final or tc.get("status") != "running"):
            out.append("")
            out.append(f"{tc['icon']} <b>{_esc(tc['name'])}</b>")
            if tc.get("connector"):
                out.append("· Permission: ✅ Authorized")
            if tc.get("evidence"):
                out.append(f"· → {_esc(tc['evidence'])}")
            if not final and tc.get("status") == "running":
                out.append(f"· Still working on it… {_wind_strip(2, phase)}")
            else:
                status = {"done": "✅ Complete", "error": "⚠️ Error"}.get(tc.get("status"), "✅ Complete")
                out.append(f"· Status: {status}")

        # Wind progress bar — the same flowing wave, filled by real progress
        pct = 100 if final else self.progress_pct
        filled = int(round(20 * pct / 100.0))
        bar = _wind_strip(filled, phase) + "·" * (20 - filled)
        out.append("")
        out.append(f"{bar}  {pct}%")

        # Muted rolling activity log
        if self.logs:
            out.append("")
            out.append("<b>Activity</b>")
            for ts, txt in self.logs[-5:]:
                out.append(f"· {_esc(ts)} {_esc(txt)}")

        return "\n".join(out)[:3900]

    async def _tick(self) -> None:
        while not self._done:
            try:
                self._frame = (self._frame + 1) % len(_FRAMES)
                text = self._render()
                if self.message_id and text != self._last_rendered:
                    await self.bot.edit_message(self.chat_id, self.message_id, text, parse_mode="HTML")
                    self._last_rendered = text
            except asyncio.CancelledError:
                return
            except Exception:
                pass
            await asyncio.sleep(1.5)

    async def start(self) -> None:
        try:
            await self.bot.send_chat_action(self.chat_id, "typing")
            self.current = {"type": "thinking", "label": "Starting…"}
            res = await self.bot.send_message(self.chat_id, self._render(), parse_mode="HTML")
            self.message_id = (res or {}).get("result", {}).get("message_id")
        except Exception as e:
            logger.debug(f"LiveActivityStream start skipped: {e}")
        try:
            self._task = asyncio.get_event_loop().create_task(self._tick())
        except Exception:
            pass

    async def finish(self, final: str = "✅ Task completed") -> None:
        self._done = True
        if self._task:
            try:
                self._task.cancel()
            except Exception:
                pass
        try:
            if self.message_id:
                text = self._render(final=True) + f"\n\n<b>{_esc(final)}</b>"
                await self.bot.edit_message(self.chat_id, self.message_id, text[:3950], parse_mode="HTML")
        except Exception as e:
            logger.debug(f"LiveActivityStream finish edit skipped: {e}")
