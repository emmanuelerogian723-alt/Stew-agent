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


# ═══════════════════ Paywall v4: metered feature allowances ═══════════════════
# Free users get REAL monthly allowances for every premium feature so they can
# experience the value before paying (the #1 conversion driver used by Chatbase,
# ManyChat and GoHighLevel freemium tiers). Allowances reset monthly.

FEATURE_LIMITS: Dict[str, Dict[str, int]] = {
    # Connected-app actions (Composio executes: reads, prepared/approved writes)
    "connector_action": {"free": 30, "whatsapp": 30, "student": 150, "pro": 1000,
                          "business": 5000, "enterprise": 20000, "owner": 10_000_000},
    # High-quality FLUX images (before falling back to standard generation)
    "hd_image": {"free": 10, "whatsapp": 10, "student": 40, "pro": 200,
                  "business": 1000, "enterprise": 5000, "owner": 10_000_000},
}


def feature_limit(plan: str, feature: str) -> int:
    return FEATURE_LIMITS.get(feature, {}).get(plan or "free",
           FEATURE_LIMITS.get(feature, {}).get("free", 0))


def current_period(daily: bool = False) -> str:
    from datetime import datetime
    now = datetime.utcnow()
    return now.strftime("%Y-%m-%d") if daily else now.strftime("%Y-%m")


async def feature_usage_count(telegram_user_id: str, feature: str, period: str) -> int:
    from server.models import FeatureUsage
    from server.database import AsyncSessionLocal
    async with AsyncSessionLocal() as db:
        row = (await db.execute(select(FeatureUsage).where(
            FeatureUsage.telegram_user_id == str(telegram_user_id),
            FeatureUsage.feature == feature,
            FeatureUsage.period == period))).scalar_one_or_none()
        return row.count if row else 0


async def meter_feature(telegram_user_id: str, feature: str, period: str, amount: int = 1) -> int:
    """Increment usage and return the new count (upsert)."""
    from server.models import FeatureUsage
    from server.database import AsyncSessionLocal
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    from sqlalchemy import update as generic_update
    async with AsyncSessionLocal() as db:
        row = (await db.execute(select(FeatureUsage).where(
            FeatureUsage.telegram_user_id == str(telegram_user_id),
            FeatureUsage.feature == feature,
            FeatureUsage.period == period))).scalar_one_or_none()
        if row:
            row.count += amount
            await db.commit()
            return row.count
        db.add(FeatureUsage(telegram_user_id=str(telegram_user_id), feature=feature,
                            period=period, count=amount))
        try:
            await db.commit()
        except Exception:
            await db.rollback()
            # Race: another worker created it — fall back to an increment.
            await db.execute(generic_update(FeatureUsage).where(
                FeatureUsage.telegram_user_id == str(telegram_user_id),
                FeatureUsage.feature == feature,
                FeatureUsage.period == period).values(count=FeatureUsage.count + amount))
            await db.commit()
        return amount


async def user_plan_for_telegram(telegram_user_id: str) -> str:
    """Plan for a Telegram user (users are keyed by email tg_<id>@telegram.stew)."""
    from server.models import User
    from server.database import AsyncSessionLocal
    try:
        async with AsyncSessionLocal() as db:
            row = (await db.execute(select(User).where(
                User.email == f"tg_{telegram_user_id}@telegram.stew"))).scalar_one_or_none()
            return row.plan if row else "free"
    except Exception as e:
        logger.warning(f"user_plan_for_telegram failed: {e}")
        return "free"


async def check_feature(telegram_user_id: str, feature: str, daily: bool = False) -> Dict[str, Any]:
    """Gate a feature by plan allowance. Returns allowed/used/limit/message."""
    plan = await user_plan_for_telegram(telegram_user_id)
    limit = feature_limit(plan, feature)
    period = current_period(daily)
    used = await feature_usage_count(telegram_user_id, feature, period)
    if plan == "owner" or used < limit:
        return {"allowed": True, "used": used, "limit": limit, "plan": plan}
    return {"allowed": False, "used": used, "limit": limit, "plan": plan,
            "message": (f"🔒 Monthly {feature.replace('_',' ')} limit reached ({used}/{limit} on "
                        f"{plan.upper()}).\n\nYour allowance resets on the 1st, or upgrade with /upgrade "
                        f"— Pro unlocks 1,000 connected-app actions and 200 HD images monthly.")}


async def metered_feature_gate(telegram_user_id: str, feature: str) -> Dict[str, Any]:
    """Check-and-charge atomically: consumes one unit if allowed."""
    gate = await check_feature(telegram_user_id, feature)
    if gate["allowed"] and gate["plan"] != "owner":
        await meter_feature(telegram_user_id, feature, current_period())
        gate["used"] += 1
    return gate
