"""Tests for heartbeat and basic API structure."""
import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_heartbeat(client: AsyncClient):
    resp = await client.get("/heartbeat")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["version"] == "5.0.0"
    assert "timestamp" in data
    assert "providers" in data


@pytest.mark.asyncio
async def test_unknown_endpoint_returns_404(client: AsyncClient):
    resp = await client.get("/this-does-not-exist")
    assert resp.status_code == 404
    data = resp.json()
    assert data["success"] is False


@pytest.mark.asyncio
async def test_protected_endpoint_without_api_key(client: AsyncClient):
    resp = await client.post("/task", json={"task": "test", "api_key": ""})
    assert resp.status_code in (401, 422)


@pytest.mark.asyncio
async def test_generate_pdf_without_auth(client: AsyncClient):
    resp = await client.post("/generate/pdf", json={
        "content": "test", "title": "test", "api_key": "not-a-real-key",
    })
    assert resp.status_code == 401
