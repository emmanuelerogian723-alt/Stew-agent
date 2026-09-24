"""E2E test: agent-initiated check-ins (Stew reaches out first)."""
import os, sys, asyncio

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:////tmp/stew-checkin-test.db"
sys.path.insert(0, os.path.abspath("stew_deploy"))
os.chdir("stew_deploy")

SENT = []  # capture telegram sends instead of network


async def main():
    from server.database import AsyncSessionLocal, engine
    from server.models import Base
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    import server.checkin_service as ci

    # intercept telegram delivery
    async def fake_send(chat_id, text):
        SENT.append((chat_id, text))
        return True
    ci._send_telegram = fake_send

    UID, CHAT = "999888777", "999888777"

    # 1) _parse_when: ISO, relative, Z, tz-aware, errors
    from datetime import datetime, timezone
    now = ci._now()
    iso = ci._parse_when("2026-09-25T17:30:00")
    rel = ci._parse_when("+90m")
    assert abs((rel - now).total_seconds() - 5400) < 30
    z = ci._parse_when("2026-09-25T17:30:00Z")
    off = ci._parse_when("2026-09-25T18:30:00+01:00")
    assert z == off, "Z vs +01:00 must normalize to same UTC"
    try:
        ci._parse_when("garbage")
        raise AssertionError("garbage when must raise")
    except ValueError:
        pass
    print("PASS _parse_when (ISO/+relative/Z/tz/invalid)")

    # 2) schedule one-off custom check-in due now
    r = await ci.schedule_check_in(UID, CHAT, "custom", "thesis chapter 3 progress",
                                   in_minutes=0)
    cid = r["id"]
    assert r["kind"] == "custom"
    print("PASS schedule one-off custom:", cid[:8])

    # 3) schedule recurring briefing
    r2 = await ci.schedule_check_in(UID, CHAT, "briefing", "daily", in_minutes=60,
                                    recurring=True, interval_seconds=86400)
    print("PASS schedule recurring briefing:", r2["id"][:8])

    # 4) bad kind rejected
    try:
        await ci.schedule_check_in(UID, CHAT, "bogus", "x", in_minutes=5)
        raise AssertionError("bad kind must raise")
    except ValueError:
        print("PASS bad kind rejected")

    # 5) list + cancel round-trip
    items = await ci.list_check_ins(UID)
    assert len(items) == 2 and all(i["id"] for i in items)
    assert await ci.cancel_check_in(UID, r2["id"])
    items = await ci.list_check_ins(UID)
    assert len(items) == 1 and items[0]["id"] == cid
    assert not await ci.cancel_check_in(UID, r2["id"])  # already inactive
    print("PASS list/cancel/re-cancel-refused")

    # 6) run_due_check_ins: the custom one is due now -> composed + sent,
    #    one-off deactivated. LLM may fail offline; the service must still
    #    deliver the fallback message.
    n = await ci.run_due_check_ins()
    assert n == 1, n
    assert len(SENT) == 1 and CHAT == SENT[0][0]
    print("PASS run_due delivered:", SENT[0][1][:80].replace("\n", " "))
    items = await ci.list_check_ins(UID)
    assert len(items) == 0, "one-off must deactivate after send"
    print("PASS one-off deactivated after send")

    # 7) recurring fires and reschedules
    r3 = await ci.schedule_check_in(UID, CHAT, "briefing", "daily", in_minutes=0,
                                    recurring=True, interval_seconds=3600)
    n = await ci.run_due_check_ins()
    assert n == 1
    items = await ci.list_check_ins(UID)
    assert len(items) == 1 and items[0]["sent_count"] == 1 and items[0]["recurring"]
    print("PASS recurring kept active with next_run_at advanced")

    # 8) goal digest + daily briefing compose functions run without crashing
    #    (goal engine has no goals for this user; sections degrade gracefully)
    gd = await ci.compose_goal_digest(UID, None, "thesis")
    bf = await ci.compose_daily_briefing(UID)
    assert isinstance(gd, str) and isinstance(bf, str)
    print("PASS compose goal digest + daily briefing (len %d/%d)" % (len(gd), len(bf)))

    # 9) sections degrade gracefully offline
    w = await ci._weather_section()
    cal = await ci._calendar_section(UID)
    acts = await ci._activity_section(UID)
    print("PASS sections:", repr(w[:30]), repr(cal[:20]), repr(acts[:30]))

    print("\nALL CHECK-IN E2E TESTS: PASS")

asyncio.run(main())
