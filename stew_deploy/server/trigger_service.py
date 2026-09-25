"""Event-driven triggers — "when this happens, do that."

Two sources:
  • webhook — a personal URL per trigger; any service POSTs JSON and the
    agent instruction runs with that payload.
  • gmail   — polled every ~2 minutes via the user's connected Gmail
    (Composio). Optional filters: from sender / subject keyword.
Firing runs the standard agent loop, so the same approval gateway protects
external actions. Polling is throttled and does NOT burn the user's daily
connector-action quota (only fired runs meter like normal agent activity).
"""
from __future__ import annotations

import json
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select

from server.database import AsyncSessionLocal
from server.models import TriggerRule

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def create_trigger(user_id: str, chat_id: str, name: str, source: str,
                        instruction: str, config: Optional[dict] = None) -> dict:
    source = (source or "webhook").lower().strip()
    if source not in ("webhook", "gmail", "email"):
        raise ValueError("source must be 'webhook' or 'gmail'")
    if source in ("gmail", "email"):
        source = "gmail"
    instruction = (instruction or "").strip()
    if not instruction:
        raise ValueError("an instruction is required, e.g. 'summarize it and message me'")
    async with AsyncSessionLocal() as db:
        # cap: 10 active triggers per user
        active = (await db.execute(
            select(TriggerRule).where(TriggerRule.telegram_user_id == user_id,
                                       TriggerRule.is_active == True))).scalars().all()  # noqa: E712
        if len(active) >= 10:
            raise ValueError("You already have 10 active triggers. /trigger off <id> one first.")
        rule = TriggerRule(
            telegram_user_id=str(user_id), chat_id=str(chat_id),
            name=(name or "My trigger")[:120], source=source,
            config=dict(config or {}), instruction=instruction[:2000],
            webhook_token=secrets.token_urlsafe(24) if source == "webhook" else None,
        )
        db.add(rule)
        await db.commit()
        return {"id": rule.id, "name": rule.name, "source": rule.source,
                "webhook_token": rule.webhook_token}


async def list_triggers(user_id: str) -> list[dict]:
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(
            select(TriggerRule).where(TriggerRule.telegram_user_id == str(user_id),
                                       TriggerRule.is_active == True)  # noqa: E712
            .order_by(TriggerRule.created_at))).scalars().all()
        out = []
        for r in rows:
            item = {"id": r.id, "name": r.name, "source": r.source,
                    "instruction": r.instruction, "fires": r.fire_count}
            if r.source == "webhook" and r.webhook_token:
                item["webhook_url"] = f"/api/triggers/hook/{r.webhook_token}"
            out.append(item)
        return out


async def cancel_trigger(user_id: str, trigger_id: str) -> bool:
    async with AsyncSessionLocal() as db:
        row = (await db.execute(
            select(TriggerRule).where(TriggerRule.id == trigger_id,
                                      TriggerRule.telegram_user_id == str(user_id)))).scalar()
        if not row:
            return False
        row.is_active = False
        await db.commit()
        return True


async def get_by_token(token: str) -> Optional[TriggerRule]:
    async with AsyncSessionLocal() as db:
        return (await db.execute(
            select(TriggerRule).where(TriggerRule.webhook_token == token,
                                      TriggerRule.is_active == True))).scalar()  # noqa: E712


async def fire(rule: TriggerRule, payload: dict) -> None:
    """Wake the agent: run the stored instruction with the event payload.
    Runs the normal tool-agent loop, so approvals and quotas apply."""
    import asyncio
    from server.config import get_settings
    from server.telegram_bot import TelegramBot
    chat_id = int(rule.chat_id) if str(rule.chat_id).lstrip("-").isdigit() else rule.chat_id
    try:
        bot = TelegramBot(get_settings().TELEGRAM_BOT_TOKEN)
    except Exception:
        bot = None

    async def _run():
        from server.tool_agent import run_agent_loop
        from server.live_motion import LiveActivityStream
        payload_txt = json.dumps(payload, ensure_ascii=False)[:3000] if payload else "(no payload)"
        goal = (f"⚡ TRIGGER FIRED: '{rule.name}' ({rule.source} event).\n"
                f"User's standing instruction: \"{rule.instruction}\"\n"
                f"Event payload:\n{payload_txt}\n"
                "Execute the user's instruction on this event now.")
        try:
            stream = None
            if bot is not None:
                try:
                    stream = LiveActivityStream(bot, chat_id)
                    await stream.start()
                except Exception:
                    stream = None
            result = await run_agent_loop(goal, bot=bot, chat_id=chat_id,
                                         max_iterations=8, tg_user_id=rule.telegram_user_id,
                                         progress_cb=stream.record if stream else None)
            if stream:
                await stream.finish()
            response = result.get("response") or "⚡ Trigger ran, but produced no summary."
            import re
            response = re.sub(r"TOOL_CALL:\s*\{.*?\}", "", response, flags=re.DOTALL).strip()
            response = re.sub(r"TOOL_RESULT[\s\S]*", "", response).strip() or response
            for i in range(0, len(response), 3800):
                if bot is not None:
                    await bot.send_message(chat_id, response[i:i+3800])
        except Exception as exc:
            logger.error(f"trigger fire failed: {exc}", exc_info=True)
            try:
                if bot is not None:
                    await bot.send_message(chat_id, f"⚡ Your trigger '{rule.name}' fired but hit an error: {exc}")
            except Exception:
                pass
        finally:
            async with AsyncSessionLocal() as db:
                row = (await db.execute(select(TriggerRule)
                        .where(TriggerRule.id == rule.id))).scalar()
                if row:
                    row.last_fired_at = _now()
                    row.fire_count = (row.fire_count or 0) + 1
                    await db.commit()

    asyncio.create_task(_run())


async def poll_gmail_triggers() -> int:
    """Called from the scheduler every ~2 minutes. For each active gmail
    trigger, look for new messages since last check and fire if matched.
    Read polls are not metered against the daily quota; fired runs are."""
    from server.composio_service import search_tools, execute_action
    now = _now()
    async with AsyncSessionLocal() as db:
        rules = (await db.execute(
            select(TriggerRule).where(TriggerRule.source == "gmail",
                                      TriggerRule.is_active == True,  # noqa: E712
                                      TriggerRule.last_check_at != None))).scalars().all()
        fresh = [r for r in rules if (now - r.last_check_at).total_seconds() >= 110]
        if not fresh:
            return 0
        fired = 0
        for rule in fresh:
            cfg = rule.config or {}
            since = rule.last_check_at - timedelta(minutes=1)
            since_iso = since.strftime("%Y/%m/%d")
            try:
                found = await search_tools(rule.telegram_user_id,
                                          "gmail search or find messages received after a date")
                results = found.get("results") or []
                primary = (results[0].get("primary_tool_slugs") or [None])[0] if results else None
                statuses = found.get("toolkit_connection_statuses") or []
                toolkit = (results[0].get("toolkits") or [None])[0] if results else None
                st = next((x for x in statuses if isinstance(x, dict) and x.get("toolkit") == toolkit), None)
                if not (primary and st and st.get("has_active_connection")):
                    rule.last_check_at = now
                    continue
                args = {}
                params = found.get("tool_schemas") or {}
                schema = params.get(primary) if isinstance(params, dict) else None
                if isinstance(schema, dict):
                    names = {p.get("name") for p in (schema.get("parameters") or [])}
                    for cand, val in (("query", f"after:{since_iso}"
                                                + (f" from:{cfg['from']}" if cfg.get("from") else "")
                                                + (f" subject:{cfg['subject']}" if cfg.get("subject") else "")),
                                      ("search_query", f"after:{since_iso}"), ("q", f"after:{since_iso}")):
                        if cand in names:
                            args[cand] = val
                            break
                data = await execute_action(rule.telegram_user_id, primary, args or {"query": f"after:{since_iso}"})
            except Exception as exc:
                logger.debug(f"gmail poll failed for trigger {rule.id}: {exc}")
                data = None
            rule.last_check_at = now
            if not data:
                continue
            msgs = []
            if isinstance(data, dict):
                msgs = data.get("data") or data.get("messages") or data.get("results") or []
            if isinstance(msgs, dict):
                msgs = msgs.get("messages") or msgs.get("data") or []
            if not isinstance(msgs, list):
                msgs = []
            if msgs:
                sample = msgs[:5]
                summary = []
                for m in sample:
                    if isinstance(m, dict):
                        summary.append({k: m.get(k) for k in ("subject", "from", "sender", "date", "snippet", "body", "text") if m.get(k)})
                await db.commit()
                await fire(rule, {"event": "new_email", "messages": summary, "count": len(msgs)})
                fired += 1
        await db.commit()
        return fired


async def mark_initial_checks() -> None:
    """New gmail triggers start polling from creation moment — set last_check_at
    so the first poll doesn't replay the whole inbox."""
    async with AsyncSessionLocal() as db:
        rules = (await db.execute(
            select(TriggerRule).where(TriggerRule.source == "gmail",
                                      TriggerRule.is_active == True,  # noqa: E712
                                      TriggerRule.last_check_at == None))).scalars().all()  # noqa: E711
        for r in rules:
            r.last_check_at = _now()
        await db.commit()
