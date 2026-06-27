"""Tests for authentication endpoints."""
import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_register_success(client: AsyncClient):
    resp = await client.post("/auth/register", json={
        "name": "Alice",
        "email": "alice@test.com",
        "password": "password123",
        "plan": "free",
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["success"] is True
    assert data["api_key"].startswith("stew_")
    assert len(data["api_key"]) == 64
    assert data["plan"] == "free"
    assert data["calls_limit"] == 3000


@pytest.mark.asyncio
async def test_register_duplicate_email(client: AsyncClient):
    payload = {"name": "Bob", "email": "bob@test.com", "password": "pw", "plan": "free"}
    await client.post("/auth/register", json=payload)
    resp = await client.post("/auth/register", json=payload)
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_register_invalid_plan(client: AsyncClient):
    resp = await client.post("/auth/register", json={
        "name": "Charlie", "email": "charlie@test.com",
        "password": "pw", "plan": "diamond",
    })
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_login_success(client: AsyncClient):
    email = "logintest@test.com"
    await client.post("/auth/register", json={
        "name": "Login Test", "email": email,
        "password": "mypassword", "plan": "free",
    })
    resp = await client.post("/auth/login", json={"email": email, "password": "mypassword"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["access_token"].startswith("eyJ")
    assert data["token_type"] == "bearer"
    assert data["success"] is True


@pytest.mark.asyncio
async def test_login_wrong_password(client: AsyncClient):
    email = "wrongpw@test.com"
    await client.post("/auth/register", json={
        "name": "Wrong PW", "email": email,
        "password": "correct", "plan": "free",
    })
    resp = await client.post("/auth/login", json={"email": email, "password": "wrong"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_get_me_with_valid_token(client: AsyncClient):
    email = "mee@test.com"
    await client.post("/auth/register", json={
        "name": "Me User", "email": email,
        "password": "pass", "plan": "pro",
    })
    login = await client.post("/auth/login", json={"email": email, "password": "pass"})
    token = login.json()["access_token"]
    resp = await client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["email"] == email
    assert data["plan"] == "pro"


@pytest.mark.asyncio
async def test_api_key_format(client: AsyncClient):
    """API key must be exactly 64 chars and start with stew_"""
    resp = await client.post("/auth/register", json={
        "name": "Key Test", "email": "keytest@test.com",
        "password": "pw", "plan": "free",
    })
    key = resp.json()["api_key"]
    assert key.startswith("stew_")
    assert len(key) == 64
    assert key[5:].isalnum()
