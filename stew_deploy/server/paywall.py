"""
S.T.E.W Paywall v3 — connected-apps limits, pass codes, and gatekeeping.

Rules (per owner spec, 2026-09):
- Free users can connect at most 7 apps of their choosing.
- Paid tiers get progressively more apps + more usage.
- The admin/owner plan is exempt from every gate.
- Pass codes redeem instantly into a paid plan.
"""
import logging
import secrets
import string
from typing import Any, Dict, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from server.config import get_settings  # noqa: F401 (name is `get_settings`, not `settings`)

logger = logging.getLogger(__name__)

# Connected-app caps per plan. Free = 7 (spec: "between 5 to 7" — we give 7).
APP_LIMITS: Dict[str, int] = {
    "free": 7,
    "whatsapp": 7,
    "student": 15,
    "pro": 60,
    "business": 250,
    "enterprise": 1000,
    "owner": 100000,
}

_EXPIRY_DAYS = {"student": 30, "pro": 30, "business": 30, "enterprise": 365}


def app_limit_for(plan: str) -> int:
    return APP_LIMITS.get(plan or "free", 7)


async def count_connected_apps(user_id: str) -> int:
    """Count every live Composio toolkit, not just the first catalog page.

    Fail closed: a provider error cannot be interpreted as zero connections.
    """
    from server.composio_service import list_connections
    slugs = set()
    cursor = None
    seen_cursors = set()
    for _ in range(20):
        res = await list_connections(user_id, connected_only=True, limit=50, next_cursor=cursor)
        if not isinstance(res, dict) or not res.get("success", False):
            raise RuntimeError("Connected-app count is unavailable")
        for item in res.get("items", []):
            if item.get("slug") and (item.get("is_no_auth") or (item.get("connection") or {}).get("is_active")):
                slugs.add(str(item["slug"]).lower())
        cursor = res.get("next_cursor")
        if not cursor:
            return len(slugs)
        if cursor in seen_cursors:
            raise RuntimeError("Connected-app catalog repeated a page")
        seen_cursors.add(cursor)
    if cursor:
        raise RuntimeError("Connected-app count exceeded pagination safety limit")
    return len(slugs)


async def get_connected_apps(user_id: str) -> list:
    """Names of currently connected apps."""
    try:
        from server.composio_service import list_connections
        res = await list_connections(user_id, connected_only=True, limit=50)
        items = res.get("items", []) if isinstance(res, dict) else []
        out = []
        for i in items:
            name = i.get("name") or i.get("slug") or "app"
            slug = i.get("slug") or ""
            out.append({"name": name, "slug": slug})
        return out
    except Exception as e:
        logger.warning(f"get_connected_apps failed: {e}")
        return []


async def check_connect_allowed(plan: str, user_id: str) -> Tuple[bool, int, int, str]:
    """Returns (allowed, current_count, limit, message)."""
    if plan == "owner":
        current = await count_connected_apps(user_id)
        return (True, current, APP_LIMITS["owner"], "")
    limit = app_limit_for(plan)
    current = await count_connected_apps(user_id)
    if current >= limit:
        return (False, current, limit,
                f"🔒 App limit reached ({current}/{limit} connected apps on your "
                f"{plan.upper()} plan).\n\nDisconnect an app in the Mini App, or "
                f"upgrade with /upgrade to connect more (Pro = 60 apps, Business = 250).")
    return (True, current, limit, "")


# ───────────────── Monetization v3: daily free-tier quotas ─────────────────
# 2026-09 owner spec: free users get a real taste of premium capability,
# then hit a friendly upsell wall. Paid plans get generous daily allowances;
# the owner/admin plan is unlimited.

HQ_IMAGE_DAILY: Dict[str, int] = {
    "free": 5,       # 5 flagship FLUX-2 images/day, then Pollinations fallback
    "student": 20,
    "pro": 100,
    "business": 400,
    "enterprise": 1000,
    "owner": 1000000,
}

CONNECTOR_ACTIONS_DAILY: Dict[str, int] = {
    "free": 40,      # 40 connected-app actions/day (reads + writes)
    "student": 150,
    "pro": 500,
    "business": 2000,
    "enterprise": 10000,
    "owner": 1000000,
}


def _today() -> str:
    from datetime import datetime
    return datetime.utcnow().strftime("%Y-%m-%d")


async def _ensure_usage_window(user) -> None:
    """Reset daily counters when the UTC day rolls over. Caller commits."""
    today = _today()
    if getattr(user, "usage_day", None) != today:
        user.usage_day = today
        user.hq_images_used = 0
        user.connector_actions_used = 0


async def check_hq_image_quota(db, user) -> Tuple[bool, int, int, str]:
    """May the user get a premium FLUX-2 image right now?
    Returns (allowed, used_today, limit, message). Never raises."""
    try:
        if user is None:
            return (False, 0, 0, "")
        plan = getattr(user, "plan", "free") or "free"
        if plan == "owner":
            return (True, 0, 0, "")
        limit = HQ_IMAGE_DAILY.get(plan, 5)
        # Thank-you bonus: users who shared their email get +2 daily flagship
        # images (email capture drives product-update reach).
        if (getattr(user, "marketing_email", None) or "").strip():
            limit += 2
        await _ensure_usage_window(user)
        used = int(getattr(user, "hq_images_used", 0) or 0)
        if used < limit:
            return (True, used, limit, "")
        await db.commit()
        return (False, used, limit,
                f"🎨 You've used your {limit} daily premium images on the "
                f"{plan.upper()} plan — this one uses the free engine instead.\n\n"
                f"Upgrade with /upgrade for up to 100/day premium quality "
                f"(Pro) — first images look noticeably sharper, faster.")
    except Exception as exc:
        logger.warning(f"hq image quota check failed: {exc}")
        return (True, 0, 0, "")  # fail open — never block on counter errors


async def bump_hq_image_usage(db, user) -> None:
    try:
        await _ensure_usage_window(user)
        user.hq_images_used = int(getattr(user, "hq_images_used", 0) or 0) + 1
        await db.commit()
    except Exception as exc:
        logger.warning(f"hq image bump failed: {exc}")


async def check_connector_action_quota(db, user) -> Tuple[bool, int, int, str]:
    """May the user run another connected-app action right now?"""
    try:
        if user is None:
            return (True, 0, 0, "")
        plan = getattr(user, "plan", "free") or "free"
        if plan == "owner":
            return (True, 0, 0, "")
        limit = CONNECTOR_ACTIONS_DAILY.get(plan, 40)
        await _ensure_usage_window(user)
        used = int(getattr(user, "connector_actions_used", 0) or 0)
        if used < limit:
            return (True, used, limit, "")
        await db.commit()
        return (False, used, limit,
                f"🔐 Daily connected-app limit reached ({used}/{limit} on "
                f"{plan.upper()}).\n\nYour apps stay connected — upgrade "
                f"with /upgrade to keep the actions flowing today "
                f"(Pro = 500/day, Business = 2000/day).")
    except Exception as exc:
        logger.warning(f"connector quota check failed: {exc}")
        return (True, 0, 0, "")


async def bump_connector_action_usage(db, user) -> None:
    try:
        await _ensure_usage_window(user)
        user.connector_actions_used = int(getattr(user, "connector_actions_used", 0) or 0) + 1
        await db.commit()
    except Exception as exc:
        logger.warning(f"connector action bump failed: {exc}")


# ───────────────────────── Pass codes ─────────────────────────

def _gen_code() -> str:
    alphabet = string.ascii_uppercase + string.digits
    return "STEW-" + "-".join(
        "".join(secrets.choice(alphabet) for _ in range(4)) for _ in range(3)
    )


async def create_pass_code(db: AsyncSession, plan: str, created_by: str,
                           note: str = "") -> Dict[str, Any]:
    """Admin: mint a redeemable pass code. plan: student|pro|business|enterprise."""
    from server.models import PassCode
    plan = (plan or "pro").lower().strip()
    if plan not in _EXPIRY_DAYS:
        return {"success": False, "error": "Plan must be student, pro, business or enterprise"}
    code = _gen_code()
    row = PassCode(code=code, plan=plan, created_by=created_by, note=note[:200])
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return {"success": True, "code": code, "plan": plan,
            "message": f"Pass code {code} created for {plan.upper()} plan."}


async def redeem_pass_code(db: AsyncSession, code: str, user) -> Dict[str, Any]:
    """User: redeem a pass code → upgrade their plan instantly."""
    from server.models import PassCode
    from datetime import datetime, timedelta
    code = (code or "").strip().upper()
    if not code:
        return {"success": False, "error": "Send the code like this: /unlock STEW-XXXX-XXXX-XXXX"}
    q = await db.execute(select(PassCode).where(PassCode.code == code))
    row = q.scalar_one_or_none()
    if not row or row.is_used:
        return {"success": False, "error": "That pass code is invalid or already used."}
    row.is_used = True
    row.used_by = getattr(user, "email", "unknown")
    row.used_at = datetime.utcnow()
    user.plan = row.plan
    user.plan_expires_at = datetime.utcnow() + timedelta(days=_EXPIRY_DAYS.get(row.plan, 30))
    await db.commit()
    return {"success": True, "plan": row.plan,
            "message": f"🎉 Unlocked! Your account is now {row.plan.upper()} for "
                       f"{_EXPIRY_DAYS.get(row.plan, 30)} days. Enjoy the upgraded limits."}


# ───────────────────────── Live status ("the magic") ─────────────────────────

class LiveStatus:
    """Sharp motion while Stew works: sends a status message and live-edits it
    through stages so the user sees the magic happening in real time.

    Usage:
        async with LiveStatus(bot, chat_id) as st:
            await st.stage("⚡ Understanding your request…")
            await st.stage("🔍 Searching the web…")
        # on exit the message is finalized
    """

    _ARROWS = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

    def __init__(self, bot, chat_id: int, title: str = "🧠 Stew is working…"):
        self.bot = bot
        self.chat_id = chat_id
        self.title = title
        self.message_id: Optional[int] = None
        self.stages: list = []
        self._finished = False

    async def __aenter__(self):
        try:
            await self.bot.send_chat_action(self.chat_id, "typing")
            res = await self.bot.send_message(self.chat_id, f"{self.title}\n⠋ Starting…")
            self.message_id = (res or {}).get("message_id")
        except Exception:
            self.message_id = None
        return self

    async def stage(self, text: str) -> None:
        """Add a completed stage + show a spinner on the current one."""
        try:
            await self.bot.send_chat_action(self.chat_id, "typing")
            self.stages.append(text)
            if not self.message_id:
                return
            lines = [f"{self.title}"] + [f"✅ {s}" for s in self.stages[:-1]] + \
                    [f"⠿ {self.stages[-1]}"]
            await self.bot.edit_message(self.chat_id, self.message_id, "\n".join(lines))
        except Exception:
            pass

    async def finish(self, final: str = "") -> None:
        try:
            if self.message_id:
                lines = [f"{self.title}"] + [f"✅ {s}" for s in self.stages]
                if final:
                    lines.append(f"🏁 {final}")
                await self.bot.edit_message(self.chat_id, self.message_id, "\n".join(lines))
        except Exception:
            pass
        self._finished = True

    async def __aexit__(self, exc_type, exc, tb):
        if not self._finished:
            if exc is None:
                await self.finish("Done ✨")
            else:
                try:
                    if self.message_id:
                        await self.bot.edit_message(
                            self.chat_id, self.message_id,
                            "\n".join([f"{self.title}"] + [f"✅ {s}" for s in self.stages] +
                                      ["⚠️ Hit a snag — but I recovered."]))
                except Exception:
                    pass
        return False
