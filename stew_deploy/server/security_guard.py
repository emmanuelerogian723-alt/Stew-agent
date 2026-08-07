"""
S.T.E.W Security Guard — Anti-Abuse Layer
==========================================
Device fingerprinting, VPN/proxy detection, risk scoring,
multi-account prevention, and registration guards.

Built by MUTYINT — protecting Africa's AI backbone.
"""
import hashlib
import logging
import asyncio
from datetime import datetime, timezone, timedelta
from typing import Optional, Tuple, Dict
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from server.models import DeviceFingerprint, SecurityEvent, User

logger = logging.getLogger(__name__)

# ── Configuration ────────────────────────────────────────────────────────────

MAX_FREE_ACCOUNTS_PER_IP = 3          # Max free accounts from one IP per 24h
MAX_FREE_ACCOUNTS_PER_FINGERPRINT = 2 # Max free accounts per device fingerprint
VPN_RISK_PENALTY = 30
PROXY_RISK_PENALTY = 20
TOR_RISK_PENALTY = 50
MULTI_ACCOUNT_RISK_PENALTY = 25
RISK_THRESHOLD_BLOCK = 70              # Block registration if risk >= this
RISK_THRESHOLD_FLAG = 40               # Flag but allow if risk >= this


def detect_device_type(user_agent: str) -> str:
    """Detect device type from user agent."""
    ua = (user_agent or "").lower()
    if any(k in ua for k in ["mobile", "android", "iphone", "ipad", "opera mini"]):
        if "ipad" in ua or ("android" in ua and "tablet" in ua):
            return "tablet"
        return "mobile"
    return "desktop"


def detect_os(user_agent: str) -> str:
    """Detect OS from user agent."""
    ua = (user_agent or "").lower()
    if "windows" in ua: return "Windows"
    if "mac os" in ua or "macintosh" in ua: return "macOS"
    if "android" in ua: return "Android"
    if "iphone" in ua or "ipad" in ua: return "iOS"
    if "linux" in ua: return "Linux"
    return "Unknown"


def detect_browser(user_agent: str) -> str:
    """Detect browser from user agent."""
    ua = (user_agent or "").lower()
    if "edg" in ua: return "Edge"
    if "chrome" in ua: return "Chrome"
    if "firefox" in ua: return "Firefox"
    if "safari" in ua: return "Safari"
    if "opera" in ua or "opr" in ua: return "Opera"
    return "Unknown"


def compute_fingerprint(
    user_agent: str,
    screen_resolution: str = "",
    timezone: str = "",
    language: str = "",
    canvas_hash: str = "",
    webgl_hash: str = "",
) -> str:
    """
    Compute a stable device fingerprint hash.
    Combines multiple browser characteristics for uniqueness.
    """
    raw = "|".join([
        user_agent or "",
        screen_resolution or "",
        timezone or "",
        language or "",
        canvas_hash or "",
        webgl_hash or "",
    ])
    return hashlib.sha256(raw.encode()).hexdigest()


async def check_vpn_proxy(ip: str) -> Dict:
    """
    Check if IP is a VPN/proxy/Tor using ip-api.com (free, no key, 45 req/min).
    Returns dict with is_vpn, is_proxy, is_tor, country, isp.
    """
    result = {
        "is_vpn": False, "is_proxy": False, "is_tor": False,
        "country": "", "isp": "", "org": "", "as_number": ""
    }
    try:
        import aiohttp
        async with aiohttp.ClientSession() as session:
            # ip-api.com free endpoint with fields for proxy detection
            url = f"http://ip-api.com/json/{ip}?fields=status,country,isp,org,as,proxy,hosting"
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data.get("status") == "success":
                        result["country"] = data.get("country", "")
                        result["isp"] = data.get("isp", "")
                        result["org"] = data.get("org", "")
                        result["as_number"] = data.get("as", "")
                        result["is_proxy"] = data.get("proxy", False)
                        result["is_vpn"] = data.get("hosting", False)  # hosting = datacenter/VPN
    except Exception as e:
        logger.debug(f"VPN check failed for {ip}: {e}")
    return result


async def count_free_accounts_by_ip(ip: str, db: AsyncSession) -> int:
    """Count free-tier accounts created from this IP in the last 24h."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    result = await db.execute(
        select(func.count(DeviceFingerprint.id)).where(
            DeviceFingerprint.ip_address == ip,
            DeviceFingerprint.created_at >= cutoff
        )
    )
    return result.scalar() or 0


async def count_free_accounts_by_fingerprint(fingerprint_hash: str, db: AsyncSession) -> int:
    """Count free-tier accounts with this device fingerprint."""
    result = await db.execute(
        select(func.count(DeviceFingerprint.id)).where(
            DeviceFingerprint.fingerprint_hash == fingerprint_hash
        )
    )
    return result.scalar() or 0


async def assess_registration_risk(
    ip: str,
    fingerprint_hash: str,
    user_agent: str,
    db: AsyncSession,
) -> Tuple[int, list, dict]:
    """
    Assess risk for a new registration.
    Returns (risk_score 0-100, list_of_reasons, vpn_info_dict).
    """
    risk = 0
    reasons = []

    # 1. VPN/Proxy/Tor detection
    vpn_info = await check_vpn_proxy(ip)
    if vpn_info["is_tor"]:
        risk += TOR_RISK_PENALTY
        reasons.append(f"Tor exit node detected (AS: {vpn_info.get('as_number','')})")
    if vpn_info["is_vpn"]:
        risk += VPN_RISK_PENALTY
        reasons.append(f"VPN/datacenter IP detected (ISP: {vpn_info.get('isp','')})")
    if vpn_info["is_proxy"]:
        risk += PROXY_RISK_PENALTY
        reasons.append(f"Proxy detected (ISP: {vpn_info.get('isp','')})")

    # 2. Multiple accounts from same IP
    ip_count = await count_free_accounts_by_ip(ip, db)
    if ip_count >= MAX_FREE_ACCOUNTS_PER_IP:
        risk += MULTI_ACCOUNT_RISK_PENALTY
        reasons.append(f"{ip_count} accounts already created from this IP (max {MAX_FREE_ACCOUNTS_PER_IP})")
    elif ip_count >= 1:
        risk += 10
        reasons.append(f"{ip_count} existing account(s) from this IP")

    # 3. Same device fingerprint
    fp_count = await count_free_accounts_by_fingerprint(fingerprint_hash, db)
    if fp_count >= MAX_FREE_ACCOUNTS_PER_FINGERPRINT:
        risk += MULTI_ACCOUNT_RISK_PENALTY
        reasons.append(f"{fp_count} accounts on this device fingerprint (max {MAX_FREE_ACCOUNTS_PER_FINGERPRINT})")

    # 4. Suspicious user agent (bots, scrapers)
    ua = (user_agent or "").lower()
    if not ua or len(ua) < 20:
        risk += 15
        reasons.append("Missing or suspicious user agent")
    if any(k in ua for k in ["bot", "crawler", "spider", "scrape", "curl", "wget", "python", "httpx"]):
        risk += 20
        reasons.append("Automated client detected in user agent")

    risk = min(risk, 100)

    # Log security event
    event = SecurityEvent(
        event_type="risk_assessment",
        ip_address=ip,
        fingerprint_hash=fingerprint_hash,
        risk_score=risk,
        details="; ".join(reasons) if reasons else "No risk factors",
    )
    db.add(event)

    return risk, reasons, vpn_info


async def record_device_fingerprint(
    user_id: str,
    fingerprint_hash: str,
    ip_address: str,
    user_agent: str,
    vpn_info: dict,
    risk_score: int,
    screen_resolution: str = "",
    timezone: str = "",
    language: str = "",
    db: AsyncSession = None,
) -> DeviceFingerprint:
    """Record a device fingerprint for a user."""
    fp = DeviceFingerprint(
        user_id=user_id,
        fingerprint_hash=fingerprint_hash,
        ip_address=ip_address,
        user_agent=user_agent,
        device_type=detect_device_type(user_agent),
        os_name=detect_os(user_agent),
        browser_name=detect_browser(user_agent),
        screen_resolution=screen_resolution,
        timezone=timezone,
        language=language,
        is_vpn=vpn_info.get("is_vpn", False),
        is_proxy=vpn_info.get("is_proxy", False),
        is_tor=vpn_info.get("is_tor", False),
        risk_score=risk_score,
    )
    if db:
        db.add(fp)
    return fp


async def log_security_event(
    event_type: str,
    ip_address: str,
    user_id: str = None,
    fingerprint_hash: str = None,
    risk_score: int = 0,
    details: str = "",
    db: AsyncSession = None,
):
    """Log a security event."""
    event = SecurityEvent(
        event_type=event_type,
        ip_address=ip_address,
        user_id=user_id,
        fingerprint_hash=fingerprint_hash,
        risk_score=risk_score,
        details=details,
    )
    if db:
        db.add(event)
        await db.flush()





# ── Known VPN/datacenter AS numbers (fallback when ip-api.com is unavailable) ──
KNOWN_VPN_AS_NUMBERS = {
    "AS14061",   # DigitalOcean
    "AS16509",   # Amazon AWS
    "AS15169",   # Google Cloud
    "AS8075",    # Microsoft Azure
    "AS13335",   # Cloudflare
    "AS24940",   # Hetzner
    "AS49505",   # Selectel (common VPN provider)
    "AS60068",   # Datacamp (VPN)
    "AS396982",  # Google Cloud
    "AS14618",   # Amazon AES
}

# ── Disposable email domains (block free account creation) ──
DISPOSABLE_EMAIL_DOMAINS = {
    "10minutemail.com", "guerrillamail.com", "mailinator.com", "tempmail.net",
    "throwaway.email", "temp-mail.org", "fakeinbox.com", "sharklasers.com",
    "guerrillamailblock.com", "spam4.me", "dispostable.com", "maildrop.cc",
    "getnada.com", "tempmailo.com", "yopmail.com", "mohmal.com",
    "tempinbox.com", "emailondeck.com", "mintemail.com", "dudmail.com",
}


def is_disposable_email(email: str) -> bool:
    """Check if email is from a disposable email provider."""
    if not email or "@" not in email:
        return False
    domain = email.split("@")[-1].lower()
    return domain in DISPOSABLE_EMAIL_DOMAINS


def check_as_number_vpn(as_number: str) -> bool:
    """Fallback: check if AS number is a known VPN/datacenter."""
    return as_number.upper() in KNOWN_VPN_AS_NUMBERS if as_number else False


async def is_ip_blocked(ip: str, db: AsyncSession) -> bool:
    """
    Check if an IP should be completely blocked from registration.
    Returns True if the IP has been flagged for abuse (3+ blocked registrations).
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    result = await db.execute(
        select(func.count(SecurityEvent.id)).where(
            SecurityEvent.ip_address == ip,
            SecurityEvent.event_type == "registration_blocked",
            SecurityEvent.created_at >= cutoff
        )
    )
    block_count = result.scalar() or 0
    return block_count >= 3  # 3 blocked attempts in 24h = permanent block for the day


async def get_security_dashboard(db: AsyncSession) -> dict:
    """Get security statistics for admin dashboard."""
    # Total registrations
    total_users = (await db.execute(select(func.count(User.id)))).scalar() or 0

    # VPN/proxy detections
    vpn_count = (await db.execute(
        select(func.count(DeviceFingerprint.id)).where(DeviceFingerprint.is_vpn == True)
    )).scalar() or 0

    # High risk accounts
    high_risk = (await db.execute(
        select(func.count(DeviceFingerprint.id)).where(DeviceFingerprint.risk_score >= RISK_THRESHOLD_FLAG)
    )).scalar() or 0

    # Recent security events
    recent_events = (await db.execute(
        select(SecurityEvent).order_by(SecurityEvent.created_at.desc()).limit(10)
    )).scalars().all()

    # Top IPs by account count
    top_ips = (await db.execute(
        select(
            DeviceFingerprint.ip_address,
            func.count(DeviceFingerprint.id).label("count")
        ).group_by(DeviceFingerprint.ip_address)
        .order_by(func.count(DeviceFingerprint.id).desc())
        .limit(5)
    )).all()

    return {
        "total_users": total_users,
        "vpn_detected": vpn_count,
        "high_risk_accounts": high_risk,
        "recent_events": [
            {
                "type": e.event_type,
                "ip": e.ip_address,
                "risk": e.risk_score,
                "details": e.details or "",
                "time": e.created_at.isoformat() if e.created_at else "",
            }
            for e in recent_events
        ],
        "top_ips": [{"ip": ip, "accounts": count} for ip, count in top_ips],
    }
