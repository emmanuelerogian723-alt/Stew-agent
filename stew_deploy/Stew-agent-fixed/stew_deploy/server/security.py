"""
S.T.E.W Security Layer v1.0
============================
Rate limiting, IP blocking, input sanitization,
request validation, DDoS protection, abuse detection.
Built by MUTYINT — protecting Africa's AI backbone.
"""

import time
import hashlib
import re
import os
from collections import defaultdict, deque
from typing import Optional, Tuple
from fastapi import Request, HTTPException
from fastapi.responses import JSONResponse
import logging
logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════

# Rate limits per IP (requests per window)
RATE_LIMITS = {
    "default":    {"requests": 60,  "window": 60},    # 60 req/min per IP
    "chat":       {"requests": 30,  "window": 60},    # 30 req/min for chat
    "task":       {"requests": 20,  "window": 60},    # 20 req/min for tasks
    "research":   {"requests": 10,  "window": 60},    # 10 req/min for deep research
    "browse":     {"requests": 10,  "window": 60},    # 10 req/min for browser
    "code":       {"requests": 20,  "window": 60},    # 20 req/min for code gen
    "agents/all": {"requests": 3,   "window": 60},    # 3 req/min — very expensive
    "burst":      {"requests": 200, "window": 60},    # 200 req/min burst = block
}

# Max input sizes (characters)
MAX_INPUT_SIZES = {
    "message":   8000,
    "task":      8000,
    "text":      20000,
    "query":     1000,
    "topic":     500,
    "url":       2000,
    "code":      50000,
    "default":   10000,
}

# Blocked forever after this many violations
BLOCK_THRESHOLD = 10

# Known bad patterns (injection, abuse, etc.)
MALICIOUS_PATTERNS = [
    # Prompt injection
    r"ignore (all |previous |above |prior )(instructions?|rules?|prompts?)",
    r"you are now (a |an )?(different|new|evil|unrestricted)",
    r"jailbreak",
    r"dan mode",
    r"developer mode",
    # System abuse
    r"rm -rf",
    r"sudo ",
    r"exec\s*\(",
    r"eval\s*\(",
    r"__import__",
    r"os\.system",
    r"subprocess",
    # SQL injection
    r"(union|select|insert|update|delete|drop|create)\s+(all\s+)?(from|into|table|database)",
    # XSS
    r"<script[^>]*>",
    r"javascript:",
    r"on(load|error|click)\s*=",
]

COMPILED_PATTERNS = [re.compile(p, re.IGNORECASE) for p in MALICIOUS_PATTERNS]

# Allowed origins for CORS
ALLOWED_ORIGINS = [
    "https://slimeai-frontend.vercel.app",
    "https://slime-ai.vercel.app",
    "https://mutyint.com",
    "https://www.mutyint.com",
    "https://stew-agent.onrender.com",
    "http://localhost:3000",
    "http://localhost:8000",
    "http://127.0.0.1:5500",
    # Allow all vercel preview deployments for Slime
    "https://slimeai",
]

# ═══════════════════════════════════════════
# IN-MEMORY STORES
# ═══════════════════════════════════════════

# {ip: deque of timestamps}
request_history: dict = defaultdict(lambda: defaultdict(deque))

# {ip: violation_count}
violation_counts: dict = defaultdict(int)

# {ip: blocked_until_timestamp}
blocked_ips: dict = {}

# {ip: first_seen, total_requests}
ip_stats: dict = defaultdict(lambda: {"first_seen": time.time(), "total": 0, "blocked_count": 0})

# Request counter for stats
total_requests = 0
blocked_requests = 0
malicious_attempts = 0


# ═══════════════════════════════════════════
# CORE SECURITY FUNCTIONS
# ═══════════════════════════════════════════

def get_client_ip(request: Request) -> str:
    """Get real IP even behind Cloudflare/proxies."""
    # Cloudflare real IP
    cf_ip = request.headers.get("CF-Connecting-IP")
    if cf_ip:
        return cf_ip.strip()
    # Standard proxy headers
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    real_ip = request.headers.get("X-Real-IP")
    if real_ip:
        return real_ip.strip()
    return request.client.host if request.client else "unknown"


def is_ip_blocked(ip: str) -> Tuple[bool, int]:
    """Check if IP is permanently or temporarily blocked."""
    if ip in blocked_ips:
        blocked_until = blocked_ips[ip]
        if blocked_until == -1:
            return True, -1  # Permanent block
        if time.time() < blocked_until:
            remaining = int(blocked_until - time.time())
            return True, remaining
        else:
            del blocked_ips[ip]  # Block expired
    return False, 0


def block_ip(ip: str, duration: int = 3600, reason: str = "abuse"):
    """Block an IP for duration seconds. -1 = permanent."""
    global blocked_requests
    if duration == -1:
        blocked_ips[ip] = -1
        logger.warning(f"🚫 PERMANENT BLOCK: {ip} — {reason}")
    else:
        blocked_ips[ip] = time.time() + duration
        logger.warning(f"🔒 TEMP BLOCK: {ip} for {duration}s — {reason}")
    ip_stats[ip]["blocked_count"] += 1
    blocked_requests += 1


def check_rate_limit(ip: str, endpoint: str) -> Tuple[bool, dict]:
    """
    Returns (is_allowed, info_dict).
    Checks both endpoint-specific and burst limits.
    """
    now = time.time()

    # Determine which limit to use
    ep_key = "default"
    for key in RATE_LIMITS:
        if key in endpoint:
            ep_key = key
            break

    limit_config = RATE_LIMITS[ep_key]
    burst_config = RATE_LIMITS["burst"]

    # Clean old timestamps for this endpoint
    history = request_history[ip][ep_key]
    burst_history = request_history[ip]["burst"]

    cutoff = now - limit_config["window"]
    burst_cutoff = now - burst_config["window"]

    while history and history[0] < cutoff:
        history.popleft()
    while burst_history and burst_history[0] < burst_cutoff:
        burst_history.popleft()

    # Check burst limit first (DDoS protection)
    if len(burst_history) >= burst_config["requests"]:
        block_ip(ip, 1800, "DDoS burst detected")
        return False, {
            "error": "Too many requests — DDoS protection triggered",
            "retry_after": 1800,
            "code": "DDOS_BLOCKED"
        }

    # Check endpoint-specific rate limit
    if len(history) >= limit_config["requests"]:
        violation_counts[ip] += 1
        if violation_counts[ip] >= BLOCK_THRESHOLD:
            block_ip(ip, 86400, f"repeated rate limit violations on {ep_key}")
        remaining_window = int(history[0] + limit_config["window"] - now)
        return False, {
            "error": f"Rate limit exceeded for /{ep_key}",
            "limit": limit_config["requests"],
            "window_seconds": limit_config["window"],
            "retry_after": max(1, remaining_window),
            "violations": violation_counts[ip],
            "code": "RATE_LIMITED"
        }

    # Record this request
    history.append(now)
    burst_history.append(now)
    return True, {"remaining": limit_config["requests"] - len(history)}


def sanitize_input(text: str, field: str = "default") -> Tuple[bool, str]:
    """
    Check input for malicious patterns.
    Returns (is_safe, cleaned_or_error).
    """
    if not text or not isinstance(text, str):
        return True, text or ""

    # Size check
    max_size = MAX_INPUT_SIZES.get(field, MAX_INPUT_SIZES["default"])
    if len(text) > max_size:
        return False, f"Input too large. Maximum {max_size} characters for field '{field}'."

    # Null byte check
    if "\x00" in text:
        return False, "Invalid input detected."

    # Malicious pattern check
    for pattern in COMPILED_PATTERNS:
        if pattern.search(text):
            return False, f"Potentially harmful input detected. If this is a legitimate request, please rephrase."

    return True, text


def validate_url(url: str) -> Tuple[bool, str]:
    """Validate URLs for browsing/scraping endpoints."""
    if not url:
        return False, "URL is required."

    url = url.strip()

    # Must start with http/https
    if not url.startswith(("http://", "https://")):
        return False, "URL must start with http:// or https://"

    # Block internal/private networks
    blocked_hosts = [
        "localhost", "127.0.0.1", "0.0.0.0",
        "192.168.", "10.", "172.16.", "172.17.",
        "169.254.",  # link-local
        "::1",       # IPv6 localhost
        "metadata.google.internal",  # GCP metadata
        "169.254.169.254",           # AWS metadata
    ]
    url_lower = url.lower()
    for blocked in blocked_hosts:
        if blocked in url_lower:
            return False, "Access to internal/private networks is not allowed."

    # Max URL length
    if len(url) > 2000:
        return False, "URL too long."

    return True, url


def check_origin(request: Request) -> bool:
    """Check if request origin is allowed."""
    origin = request.headers.get("origin", "")
    if not origin:
        return True  # Direct API calls (no browser)

    for allowed in ALLOWED_ORIGINS:
        if origin.startswith(allowed) or allowed in origin:
            return True

    return False


# ═══════════════════════════════════════════
# FASTAPI MIDDLEWARE
# ═══════════════════════════════════════════

async def security_middleware(request: Request, call_next):
    """Main security middleware — runs on EVERY request."""
    global total_requests, blocked_requests, malicious_attempts

    total_requests += 1
    ip = get_client_ip(request)
    path = request.url.path
    method = request.method

    # Skip security for health checks (Render needs these)
    if path in ["/heartbeat", "/health", "/"]:
        response = await call_next(request)
        return response

    # 1. Check if IP is already blocked
    is_blocked, remaining = is_ip_blocked(ip)
    if is_blocked:
        blocked_requests += 1
        msg = "Your IP is permanently blocked due to abuse." if remaining == -1 else f"Your IP is blocked. Try again in {remaining} seconds."
        logger.info(f"🚫 Blocked IP attempted access: {ip} → {path}")
        return JSONResponse(
            status_code=429,
            content={"error": msg, "code": "IP_BLOCKED", "retry_after": remaining}
        )

    # 2. Rate limiting
    allowed, rate_info = check_rate_limit(ip, path)
    if not allowed:
        malicious_attempts += 1
        logger.warning(f"⚠️ Rate limited: {ip} → {path} | {rate_info}")
        return JSONResponse(
            status_code=429,
            content=rate_info,
            headers={"Retry-After": str(rate_info.get("retry_after", 60))}
        )

    # 3. Block obviously bad user agents
    ua = request.headers.get("user-agent", "").lower()
    bad_agents = ["sqlmap", "nikto", "nmap", "masscan", "zgrab", "curl/7.1", "python-requests/2.1"]
    if any(bad in ua for bad in bad_agents):
        block_ip(ip, 86400, f"malicious user agent: {ua[:50]}")
        return JSONResponse(status_code=403, content={"error": "Forbidden", "code": "BAD_AGENT"})

    # 4. Log request (lightweight)
    logger.info(f"📡 {method} {path} | IP: {ip} | UA: {ua[:40]}")

    # 5. Process request — let FastAPI handle its own errors naturally
    response = await call_next(request)

    # Add security headers to every response
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["X-Powered-By"] = "S.T.E.W by MUTYINT"

    return response


# ═══════════════════════════════════════════
# SECURITY STATS (for /security/stats endpoint)
# ═══════════════════════════════════════════

def get_security_stats() -> dict:
    """Return current security statistics."""
    return {
        "total_requests": total_requests,
        "blocked_requests": blocked_requests,
        "malicious_attempts": malicious_attempts,
        "currently_blocked_ips": len(blocked_ips),
        "ips_with_violations": len([ip for ip, v in violation_counts.items() if v > 0]),
        "block_rate_percent": round((blocked_requests / max(total_requests, 1)) * 100, 2),
        "top_violators": sorted(
            [(ip, v) for ip, v in violation_counts.items() if v > 0],
            key=lambda x: x[1], reverse=True
        )[:5],
    }


def manual_block(ip: str, duration: int = -1, reason: str = "manual"):
    """Manually block an IP from admin endpoint."""
    block_ip(ip, duration, reason)


def manual_unblock(ip: str):
    """Unblock an IP."""
    if ip in blocked_ips:
        del blocked_ips[ip]
    if ip in violation_counts:
        del violation_counts[ip]
    logger.info(f"✅ IP unblocked: {ip}")



