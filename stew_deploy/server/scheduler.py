"""
S.T.E.W Scheduler Engine — Background task runner for scheduled tasks.
Runs inside the FastAPI app as an async background task.
Supports: interval, daily, weekly, and one-time schedules.
Delivers results via Telegram, email, or webhook.
"""
import asyncio
import logging
import json
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from server.database import AsyncSessionLocal
from server.models import ScheduledTask, User

logger = logging.getLogger("stew.scheduler")

# ── Schedule Parsing ────────────────────────────────────────────────────────

WEEKDAYS = {
    "mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6,
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
}


def parse_interval(config: str) -> int:
    """Parse '300s', '10m', '2h', '1d' into seconds."""
    config = config.strip().lower()
    if config.endswith("s"):
        return int(config[:-1])
    if config.endswith("m"):
        return int(config[:-1]) * 60
    if config.endswith("h"):
        return int(config[:-1]) * 3600
    if config.endswith("d"):
        return int(config[:-1]) * 86400
    return int(config)  # assume seconds


def compute_next_run(schedule_type: str, schedule_config: str, from_time: datetime) -> Optional[datetime]:
    """Compute the next run time based on schedule type and config."""
    now = from_time

    if schedule_type == "interval":
        seconds = parse_interval(schedule_config)
        return now + timedelta(seconds=seconds)

    if schedule_type == "daily":
        hour, minute = map(int, schedule_config.split(":"))
        next_run = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if next_run <= now:
            next_run += timedelta(days=1)
        return next_run

    if schedule_type == "weekly":
        parts = schedule_config.split(":")
        day_str = parts[0].lower()
        hour = int(parts[1])
        minute = int(parts[2]) if len(parts) > 2 else 0
        target_day = WEEKDAYS.get(day_str, 0)
        next_run = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        days_ahead = target_day - now.weekday()
        if days_ahead < 0 or (days_ahead == 0 and next_run <= now):
            days_ahead += 7
        next_run += timedelta(days=days_ahead)
        return next_run

    if schedule_type == "once":
        try:
            return datetime.fromisoformat(schedule_config)
        except Exception:
            return None

    return None


# ── Task Execution ──────────────────────────────────────────────────────────

async def execute_scheduled_task(task: ScheduledTask, user: User) -> dict:
    """
    Execute a scheduled task as an AUTONOMOUS AGENT (Manus-style): the prompt
    runs through the full tool-calling loop, so scheduled tasks can search
    the web, run Python, generate charts, and produce real document files —
    not just one plain LLM call. Falls back to the old plain chat call if
    the agent loop fails for any reason.

    Returns {"response": str, "files": [{"base64", "filename", "doc_type"}]}.
    """
    files = []
    try:
        from server.tool_agent import run_agent_loop
        from server.telegram_bot import TelegramBot
        from server.config import get_settings
        settings = get_settings()

        # If delivery is Telegram, pass the live bot so figures (charts/QR)
        # stream to the user as they're generated mid-agent-run.
        bot = None
        chat_id = None
        if task.delivery_method == "telegram" and task.delivery_target:
            token = settings.TELEGRAM_BOT_TOKEN
            if token:
                bot = TelegramBot(token)
                try:
                    chat_id = int(task.delivery_target)
                except (TypeError, ValueError):
                    chat_id = task.delivery_target

        agent_result = await run_agent_loop(
            task.prompt, bot=bot, chat_id=chat_id, max_iterations=6,
        )
        return {
            "response": agent_result.get("response") or "Task completed.",
            "files": agent_result.get("files", []),
        }
    except Exception as e:
        logger.error(f"Agent-mode execution failed, falling back to plain chat: {e}", exc_info=True)

    # ── Legacy fallback: single plain chat call ─────────────────────────────
    import httpx
    system_msg = "You are S.T.E.W executing a scheduled task. Be concise and deliver results directly."
    try:
        base_url = "http://localhost:8000"
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                f"{base_url}/chat",
                json={
                    "message": task.prompt,
                    "api_key": user.api_key,
                    "system_prompt": system_msg,
                },
            )
            if resp.status_code == 200:
                data = resp.json()
                result = data.get("response", "Task completed with no output.")
            else:
                result = f"Task failed with status {resp.status_code}: {resp.text[:200]}"
    except Exception as e:
        result = f"Task execution error: {str(e)}"

    return {"response": result, "files": files}


async def deliver_files(task: ScheduledTask, files: list):
    """Deliver generated document files (PDF/DOCX/XLSX/PPTX) via Telegram."""
    if not files or task.delivery_method != "telegram" or not task.delivery_target:
        return
    import base64 as _b64
    from server.telegram_bot import TelegramBot
    from server.config import get_settings
    settings = get_settings()
    token = settings.TELEGRAM_BOT_TOKEN
    if not token:
        return
    bot = TelegramBot(token)
    for f in files[:5]:  # cap at 5 files per run
        try:
            file_bytes = _b64.b64decode(f.get("base64", ""))
            if not file_bytes:
                continue
            filename = f.get("filename") or f"stew_document.{f.get('doc_type', 'pdf')}"
            await bot.send_document(
                int(task.delivery_target) if str(task.delivery_target).lstrip('-').isdigit() else task.delivery_target,
                file_bytes, filename,
                f"📎 Generated by scheduled task: {task.name}",
            )
        except Exception as e:
            logger.error(f"File delivery failed for task {task.id}: {e}")


async def deliver_result(task: ScheduledTask, result: str):
    """Deliver the task result via the configured delivery method."""
    import httpx

    delivery = task.delivery_method
    target = task.delivery_target or ""
    truncated = result[:4000] if len(result) > 4000 else result

    if delivery == "telegram" and target:
        from server.config import get_settings
        settings = get_settings()
        token = settings.TELEGRAM_BOT_TOKEN
        if not token:
            logger.warning("No Telegram bot token configured for delivery")
            return
        try:
            msg = f"📋 Scheduled Task: {task.name}\n\n{truncated}"
            async with httpx.AsyncClient(timeout=30) as client:
                await client.post(
                    f"https://api.telegram.org/bot{token}/sendMessage",
                    json={
                        "chat_id": target,
                        "text": msg,
                    },
                )
        except Exception as e:
            logger.error(f"Telegram delivery failed: {e}")

    elif delivery == "webhook" and target:
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                await client.post(target, json={
                    "task_id": task.id,
                    "task_name": task.name,
                    "result": result,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })
        except Exception as e:
            logger.error(f"Webhook delivery failed: {e}")

    elif delivery == "email" and target:
        logger.info(f"Email delivery to {target} (not yet implemented)")

    elif delivery == "dashboard":
        logger.info(f"Dashboard delivery for task {task.name}")

    else:
        logger.info(f"Unknown delivery method: {delivery}")


# ── Main Scheduler Loop ─────────────────────────────────────────────────────

async def scheduler_loop():
    """Main background loop that checks for due tasks every 30 seconds."""
    logger.info("Stew Scheduler engine started — checking every 30s")
    while True:
        try:
            await asyncio.sleep(30)
            await check_and_run_due_tasks()
        except asyncio.CancelledError:
            logger.info("Scheduler engine stopping")
            break
        except Exception as e:
            logger.error(f"Scheduler loop error: {e}")
            await asyncio.sleep(10)


async def check_and_run_due_tasks():
    """Find all active tasks that are due and execute them."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(ScheduledTask, User)
            .join(User, ScheduledTask.user_id == User.id)
            .where(
                ScheduledTask.is_active == True,
                ScheduledTask.next_run_at != None,
                ScheduledTask.next_run_at <= now,
            )
        )
        due_tasks = result.all()

        if not due_tasks:
            return

        logger.info(f"Scheduler: {len(due_tasks)} task(s) due")

        for task, user in due_tasks:
            if task.max_runs is not None and task.run_count >= task.max_runs:
                await db.execute(
                    update(ScheduledTask)
                    .where(ScheduledTask.id == task.id)
                    .values(is_active=False)
                )
                await db.commit()
                continue

            logger.info(f"Executing scheduled task: '{task.name}' for user {user.email}")
            try:
                exec_result = await execute_scheduled_task(task, user)
                result_text = exec_result.get("response", "Task completed.")
                result_files = exec_result.get("files", [])
            except Exception as e:
                result_text = f"Execution failed: {str(e)}"
                result_files = []
                logger.error(f"Task execution failed: {e}")

            try:
                await deliver_result(task, result_text)
            except Exception as e:
                logger.error(f"Delivery failed: {e}")

            try:
                if result_files:
                    await deliver_files(task, result_files)
            except Exception as e:
                logger.error(f"File delivery failed: {e}")

            # 'once' tasks run exactly ONE time, then deactivate — previously
            # they kept re-firing every 30s forever because compute_next_run
            # returned the same (now past) datetime.
            if task.schedule_type == "once":
                next_run = None
                await db.execute(
                    update(ScheduledTask)
                    .where(ScheduledTask.id == task.id)
                    .values(
                        last_run_at=now,
                        next_run_at=None,
                        last_result=result_text[:2000],
                        run_count=task.run_count + 1,
                        is_active=False,
                    )
                )
                await db.commit()
                logger.info(f"One-time task '{task.name}' completed and deactivated.")
                continue

            next_run = compute_next_run(task.schedule_type, task.schedule_config, now)

            await db.execute(
                update(ScheduledTask)
                .where(ScheduledTask.id == task.id)
                .values(
                    last_run_at=now,
                    next_run_at=next_run,
                    last_result=result_text[:2000],
                    run_count=task.run_count + 1,
                )
            )
            await db.commit()
            logger.info(f"Task '{task.name}' completed. Next run: {next_run}")


# ── Startup ──────────────────────────────────────────────────────────────────

_scheduler_task: Optional[asyncio.Task] = None

async def start_scheduler():
    """Start the scheduler background task. Call on app startup."""
    global _scheduler_task
    if _scheduler_task is None or _scheduler_task.done():
        _scheduler_task = asyncio.create_task(scheduler_loop())
        logger.info("Scheduler background task created")


async def stop_scheduler():
    """Stop the scheduler background task. Call on app shutdown."""
    global _scheduler_task
    if _scheduler_task and not _scheduler_task.done():
        _scheduler_task.cancel()
        try:
            await _scheduler_task
        except asyncio.CancelledError:
            pass
    logger.info("Scheduler background task stopped")
