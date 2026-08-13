"""Tests for rate limiting — verify 429 returned when limit is exceeded."""
import time
import pytest
from httpx import AsyncClient

from server.middleware import _request_counts
from server.config import get_settings

settings = get_settings()


@pytest.mark.asyncio
async def test_rate_limit_returns_429(client: AsyncClient):
    """Fill bucket for test IP then verify 429 is returned."""
    _request_counts.clear()

    # httpx ASGI transport resolves to 127.0.0.1
    test_ip = "127.0.0.1"
    limit = settings.RATE_LIMIT_FREE
    now = time.time()
    _request_counts[test_ip] = [now] * limit

    # Use /heartbeat won't be rate-limited, so hit a real endpoint
    # Use /auth/login which doesn't need LLM but IS rate-limited
    resp = await client.post("/auth/login", json={"email": "nobody@example.com", "password": "x"})
    assert resp.status_code == 429, f"Expected 429, got {resp.status_code}"
    data = resp.json()
    assert "Rate limit exceeded" in data["detail"]
    assert data["success"] is False

    _request_counts.clear()


@pytest.mark.asyncio
async def test_heartbeat_not_rate_limited(client: AsyncClient):
    """Health check must never be rate limited regardless of request count."""
    _request_counts.clear()
    for ip in ("127.0.0.1", "testclient"):
        _request_counts[ip] = [time.time()] * (settings.RATE_LIMIT_FREE * 10)

    resp = await client.get("/heartbeat")
    assert resp.status_code == 200

    _request_counts.clear()
