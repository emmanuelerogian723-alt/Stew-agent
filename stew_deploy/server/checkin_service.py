"""Agent-initiated check-ins — Stew reaches out first.

Base44-superagent parity feature. A check-in is a scheduled WAKE-UP for the
agent itself: at the due time Stew pulls live state (goal progress, recent
approved-action outcomes, calendar events, weather, headlines), composes a
fresh message with the LLM, and sends it to the user proactively.

Kinds:
  goal     — progress digest for one AutomationGoal
  briefing — personal daily "For You" digest
  custom   — agent-authored follow-up on any topic the user asked to be
             checked on ("check on me Friday about the thesis")

Static reminders already exist (reminder.py); check-ins differ because the
message is composed AT send time from real data, not stored text.
"""
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select

from server.database import AsyncSessionLocal
from server.models import AgentCheckIn

logger = logging.getLogger(__name__)

WAT_OFFSET = timedelta(hours=1)  # Africa/Lagos, UTC+1


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _parse_when(when: Any, in_minutes: Optional[float] = None) -> datetime:
    """Accept ISO datetime ('2026-09-25T17:30:00' or with Z / +01:00),
    relative '+90m' / 'in 2h' style strings, or an in_minutes number."""
    if in_minutes is not None:
        return _now() + timedelta(minutes=float(in_minutes))
    if not str(when or "").strip():
        raise ValueError("No time given")
    raw = str(when or "").strip()
    if raw.startswith("+") or raw.lower().startswith("in "):
        body = raw.lstrip("+").lower().replace("in ", "", 1).strip()
        total = 0.0
        import re
        for num, unit in re.findall(r"(\d+(?:\.\d+)?)\s*([smhd])", body):
            total += float(num) * {"s": 1 / 60, "m": 1, "h": 60, "d": 1440}[unit]
        if total:
            return _now() + timedelta(minutes=total)
    iso = raw.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(iso)
        if dt.tzinfo:
            dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt
    except ValueError:
        pass
    raise ValueError(f"Could not understand the time '{when}'")


async def schedule_check_in(
    telegram_user_id: str,
    chat_id: str,
    kind: str,
    message: str = "",
    *,
    when: Any = None,
    in_minutes: Optional[float] = None,
    recurring: bool = False,
    interval_seconds: Optional[int] = None,
    goal_id: Optional[str] = None,
) -> Dict[str, Any]:
    if kind not in ("goal", "briefing", "custom"):
        raise ValueError("kind must be goal, briefing or custom")
    due_at = _parse_when(when, in_minutes)
    row = AgentCheckIn(
        telegram_user_id=str(telegram_user_id),
        chat_id=str(chat_id),
        kind=kind,
        message=(message or "")[:4000],
        goal_id=goal_id,
        recurring=bool(recurring),
        interval_seconds=int(interval_seconds) if interval_seconds else None,
        next_run_at=due_at,
    )
    async with AsyncSessionLocal() as db:
        db.add(row)
        await db.commit()
        return {"id": row.id, "kind": kind, "due_at": due_at.isoformat() + "Z",
                "recurring": row.recurring}


async def list_check_ins(telegram_user_id: str) -> List[Dict[str, Any]]:
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(select(AgentCheckIn)
                .where(AgentCheckIn.telegram_user_id == str(telegram_user_id),
                       AgentCheckIn.is_active == True)   # noqa: E712
                .order_by(AgentCheckIn.next_run_at))).scalars().all()
        return [{"id": r.id, "kind": r.kind, "message": r.message[:120],
                 "goal_id": r.goal_id, "recurring": r.recurring,
                 "next_run_at": r.next_run_at.isoformat() + "Z" if r.next_run_at else None,
                 "sent_count": r.sent_count} for r in rows]


async def cancel_check_in(telegram_user_id: str, checkin_id: str) -> bool:
    async with AsyncSessionLocal() as db:
        row = (await db.execute(select(AgentCheckIn).where(
            AgentCheckIn.id == checkin_id,
            AgentCheckIn.telegram_user_id == str(telegram_user_id),
            AgentCheckIn.is_active == True))).scalar_one_or_none()   # noqa: E712
        if not row:
            return False
        row.is_active = False
        await db.commit()
        return True


# ── message composition ───────────────────────────────────────────────────

def _llm_complete(prompt: str) -> str:
    from server.llm_client import get_llm_client
    try:
        out = get_llm_client().complete(
            "You are S.T.E.W (Secret Task Execution Worker), a proactive AI coworker on Telegram. "
            "Write a warm, concise, useful message for this user based on the live data below. "
            "Use WhatsApp-safe formatting (bold with single *), max ~180 words, no headings. "
            "Open with a one-line greeting tied to the time of day, then the substance, "
            "then ONE concrete suggested next step. Never invent data that isn't provided.\n\n"
            + prompt, temperature=0.5, max_tokens=600)
        return (out or "").strip() or "Stew here — I woke up to check on you but couldn't build the update. Ping me and I'll sort it."
    except Exception as exc:
        logger.warning("Check-in LLM compose failed: %s", exc)
        return ""


async def _goal_section(telegram_user_id: str) -> str:
    try:
        from server.automation_engine import list_goals
        goals = await list_goals(telegram_user_id, limit=10)
        if not goals:
            return "No active goals."
        lines = []
        for g in goals:
            steps = g.get("steps") or []
            done = sum(1 for s in steps if (s.get("status") or "").lower() in ("done", "succeeded", "completed"))
            lines.append(f"- {g.get('objective', g.get('goal', 'goal'))[:90]} — status {g.get('status')}, steps {done}/{len(steps) or '?'}")
        return "\n".join(lines)
    except Exception as exc:
        logger.warning("goal section failed: %s", exc)
        return "Goal data unavailable."


async def _activity_section(telegram_user_id: str) -> str:
    try:
        from server.agent_activity import activity_dashboard
        dash = await activity_dashboard(telegram_user_id, limit=10)
        items = dash.get("items") or dash.get("activities") or []
        if not items:
            return "No recent app actions."
        return "\n".join(f"- {str(i.get('tool_slug', 'action'))[:60]} — {i.get('status')}" for i in items[:10])
    except Exception as exc:
        logger.warning("activity section failed: %s", exc)
        return "Recent action data unavailable."


async def _calendar_section(telegram_user_id: str) -> str:
    """Today's events via Composio — only if connected; metered normally so
    the freemium model is respected. Returns a 'skip' marker on paywall."""
    try:
        from server.composio_service import execute_action
        start = datetime.now(timezone.utc).isoformat(timespec="seconds")
        end = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(timespec="seconds")
        res = await execute_action(telegram_user_id, "GOOGLECALENDAR_FIND_EVENT",
                                   {"startDate": start, "endDate": end, "limit": 5})
        if res.get("paywall"):
            return "__SKIP__"
        if not res.get("success"):
            return "__SKIP__"
        data = res.get("data") or res.get("result") or []
        text = data if isinstance(data, str) else str(data)[:1500]
        return f"Calendar today: {text}"
    except Exception:
        return "__SKIP__"


async def _weather_section() -> str:
    try:
        from server.skills_engine import weather
        w = await weather("Lagos")
        if isinstance(w, dict) and w.get("temp_c"):
            return (f"{w.get('resolved_location', 'Lagos')}: {w.get('temp_c')}°C, "
                    f"{w.get('description')}, feels like {w.get('feels_like_c')}°C, "
                    f"humidity {w.get('humidity')}%")
    except Exception:
        pass
    return "__SKIP__"


async def _news_section() -> str:
    try:
        from server.news_engine import fetch_topic_news, format_news_for_llm
        stories = await fetch_topic_news("world", days=1, max_items=4)
        if stories:
            return "Top headlines:\n" + format_news_for_llm(stories)[:1200]
    except Exception:
        pass
    return "__SKIP__"


async def compose_goal_digest(telegram_user_id: str, goal_id: Optional[str], note: str = "") -> str:
    goal_part = "Requested goal id: %s" % goal_id if goal_id else "all goals"
    goals = await _goal_section(telegram_user_id)
    activity = await _activity_section(telegram_user_id)
    prompt = (f"Write a goal progress check-in. {goal_part}. Note from user: {note or 'none'}.\n"
              f"Goal state:\n{goals}\n\nRecent approved actions:\n{activity}\n")
    return _llm_complete(prompt)


async def compose_daily_briefing(telegram_user_id: str) -> str:
    cal = await _calendar_section(telegram_user_id)
    goals = await _goal_section(telegram_user_id)
    weather = await _weather_section()
    news = await _news_section()
    parts = [f"Goals:\n{goals}"]
    if cal != "__SKIP__":
        parts.append(cal)
    if weather != "__SKIP__":
        parts.append(f"Weather: {weather}")
    if news != "__SKIP__":
        parts.append(news)
    parts.append("(If calendar data is missing, do not mention calendars at all.)")
    text = _llm_complete("Write the user's personal daily briefing from this live data:\n" + "\n\n".join(parts))
    if not text or not text.strip():
        # All AI providers rate-limited/down: still deliver the real live data
        # instead of silently skipping (the scheduler would count 0 sends and
        # the user wakes up to nothing).
        fallback = ["🌅 *Your daily briefing*"]
        for part in parts:
            if part and not part.startswith("(If calendar"):
                fallback.append(str(part))
        fallback.append("Have a great day — S.T.E.W 🫡")
        return "\n\n".join(fallback)
    return text


async def compose_custom_followup(telegram_user_id: str, message: str) -> str:
    recall = ""
    try:
        from server.memory_gateway import build_recall_context
        recall = await build_recall_context(telegram_user_id, message, top_k=5) or ""
    except Exception:
        recall = ""
    prompt = (f"The user earlier asked Stew to check in on this: '{message}'.\n"
              f"Relevant memory:\n{str(recall)[:1200] or 'none'}\n"
              "Write the check-in message.")
    return _llm_complete(prompt)


# ── delivery + loop ───────────────────────────────────────────────────────

async def _send_telegram(chat_id: str, text: str) -> bool:
    import httpx
    from server.config import get_settings
    token = get_settings().TELEGRAM_BOT_TOKEN
    if not token or not chat_id:
        return False
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(f"https://api.telegram.org/bot{token}/sendMessage",
                                  json={"chat_id": chat_id, "text": text[:4000]})
            return r.status_code == 200
    except Exception as exc:
        logger.warning("Check-in Telegram send failed: %s", exc)
        return False


async def run_due_check_ins() -> int:
    """Called from the scheduler loop. Returns number of check-ins sent."""
    now = _now()
    sent = 0
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(select(AgentCheckIn)
                .where(AgentCheckIn.is_active == True,   # noqa: E712
                       AgentCheckIn.next_run_at != None,  # noqa: E711
                       AgentCheckIn.next_run_at <= now))).scalars().all()
        for row in rows:
            try:
                if row.kind == "goal":
                    text = await compose_goal_digest(row.telegram_user_id, row.goal_id, row.message)
                elif row.kind == "briefing":
                    text = await compose_daily_briefing(row.telegram_user_id)
                else:
                    text = await compose_custom_followup(row.telegram_user_id, row.message)
                if text and await _send_telegram(row.chat_id, text):
                    sent += 1
                    row.sent_count = (row.sent_count or 0) + 1
                    row.last_run_at = now
                    row.last_result = text[:2000]
                if row.recurring and row.interval_seconds:
                    row.next_run_at = now + timedelta(seconds=row.interval_seconds)
                else:
                    row.is_active = False
                    row.next_run_at = None
            except Exception as exc:
                logger.error("Check-in %s failed: %s", row.id, exc)
                row.last_result = f"error: {exc}"[:2000]
                if row.recurring and row.interval_seconds:
                    row.next_run_at = now + timedelta(seconds=row.interval_seconds)
                else:
                    row.is_active = False
        await db.commit()
    return sent
