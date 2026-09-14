"""
S.T.E.W Reminder parser — turns natural language into a scheduled task.

'remind me to call mum at 5pm' / 'in 20 minutes' / 'tomorrow 9:30' →
a {'prompt','schedule_config','when_str'} dict for the existing
ScheduledTask system (schedule_type='once', ISO datetime, Africa/Lagos).
"""
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

LAGOS = ZoneInfo("Africa/Lagos")


def parse_reminder(text: str) -> dict:
    """Returns {'prompt', 'schedule_config' (ISO dt str), 'when_str'} or None."""
    t = (text or "").strip()
    if not t:
        return None
    low = t.lower()
    if low.startswith("/remind"):
        t = t[len("/remind"):].strip()
        if t.startswith("me"):
            t = t[2:].strip()
    m = re.match(r"^(?:to\s+)?remind\s+me\s+(?:to\s+)?", t, re.IGNORECASE)
    if m:
        t = t[m.end():].strip()
    low = t.lower()
    now = datetime.now(LAGOS)
    when: datetime = None
    task: str = None

    # in N minutes/hours/seconds
    m = re.search(r"\bin\s+(\d+)\s*(second|sec|minute|min|hour|hr|day)s?\b", low)
    if m:
        n = int(m.group(1))
        unit = {"s": "s", "sec": "s", "second": "s", "m": "m", "min": "m",
                "minute": "m", "h": "h", "hr": "h", "hour": "h",
                "d": "d", "day": "d"}[m.group(2)]
        when = now + timedelta(seconds=n if unit == "s" else 0,
                                minutes=n if unit == "m" else 0,
                                hours=n if unit == "h" else 0,
                                days=n if unit == "d" else 0)
        task = t[:m.start()].strip() or t[m.end():].strip()
    else:
        # at HH:MM (am/pm) with optional day prefix
        m = re.search(r"\b(today|tomorrow|tonight)?\s*,?\s*(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b",
                      low)
        if m:
            day_word = m.group(1)
            hour = int(m.group(2))
            minute = int(m.group(3) or 0)
            ampm = m.group(4)
            if ampm == "pm" and hour < 12:
                hour += 12
            if ampm == "am" and hour == 12:
                hour = 0
            if not ampm and hour < 8:      # "at 5" in the evening usually means pm
                hour += 12
            when = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if day_word == "tomorrow":
                when += timedelta(days=1)
            if when <= now:
                when += timedelta(days=1)  # past time today → tomorrow
            task = (t[:m.start()].strip() + " " + t[m.end():].strip()).strip()
            task = re.sub(r"\b(at|on|by)\s*$", "", task).strip()

    if when is None or not task or len(task) < 3:
        return None
    prompt = f"Send me my reminder now verbatim, in a friendly Stew voice: {task}"
    when_str = when.strftime("%I:%M %p %d %b").lstrip("0")
    return {"prompt": prompt,
            "schedule_config": when.isoformat(),
            "when_str": when_str,
            "task": task}
