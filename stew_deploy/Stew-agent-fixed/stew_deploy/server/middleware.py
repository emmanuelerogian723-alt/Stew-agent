"""
S.T.E.W Middleware — rate limiting, request logging, CORS.
"""
import logging
import time
from collections import defaultdict
from typing import Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from server.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

RATE_LIMITS = {
    "free": settings.RATE_LIMIT_FREE,
    "pro": settings.RATE_LIMIT_PRO,
    "business": settings.RATE_LIMIT_BUSINESS,
    "enterprise": settings.RATE_LIMIT_ENTERPRISE,
}

# In-memory rate limiter (use Redis in production for multi-instance)
_request_counts: dict = defaultdict(list)


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        start = time.time()

        # Skip rate limiting for health checks and static files
        if request.url.path in ("/heartbeat", "/", "/docs", "/openapi.json"):
            response = await call_next(request)
            return response

        # Extract identifier (API key from body is async — use IP for middleware)
        client_ip = request.client.host if request.client else "unknown"
        now = time.time()
        window = 60  # 1 minute

        _request_counts[client_ip] = [
            t for t in _request_counts[client_ip] if now - t < window
        ]

        # Default free tier limit for IP-based checking
        limit = settings.RATE_LIMIT_FREE
        if len(_request_counts[client_ip]) >= limit:
            return Response(
                content='{"detail":"Rate limit exceeded. Upgrade your plan for higher limits.","success":false}',
                status_code=429,
                media_type="application/json",
            )

        _request_counts[client_ip].append(now)

        response = await call_next(request)

        duration = time.time() - start
        logger.info(
            f"{request.method} {request.url.path} "
            f"status={response.status_code} "
            f"duration={duration:.3f}s "
            f"ip={client_ip}"
        )

        response.headers["X-Process-Time"] = f"{duration:.3f}"
        response.headers["X-STEW-Version"] = settings.VERSION
        return response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        return response
