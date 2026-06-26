"""
pytest configuration and shared fixtures for S.T.E.W tests.
Uses SQLite in-memory — no PostgreSQL needed for testing.
"""
import asyncio
import os
import random

import pytest
import pytest_asyncio

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./test_stew.db"
os.environ["JWT_SECRET_KEY"] = "test-secret-key-for-testing-only-do-not-use-prod"
os.environ["GROQ_API_KEY"] = ""
os.environ["OPENROUTER_API_KEY"] = ""
os.environ["OPENAI_API_KEY"] = ""
os.environ["SERPER_API_KEY"] = ""
os.environ["ENVIRONMENT"] = "test"

from httpx import AsyncClient, ASGITransport
from server.main import app
from server.database import engine, Base


@pytest_asyncio.fixture(scope="session", autouse=True)
async def setup_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    if os.path.exists("./test_stew.db"):
        try:
            os.remove("./test_stew.db")
        except Exception:
            pass


@pytest_asyncio.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest_asyncio.fixture
async def registered_user(client: AsyncClient):
    suffix = random.randint(100000, 999999)
    # Use example.com — universally accepted by email validators
    email = f"testuser{suffix}@example.com"
    resp = await client.post("/auth/register", json={
        "name": "Test User",
        "email": email,
        "password": "SecurePass123!",
        "plan": "free",
    })
    assert resp.status_code == 201, f"Registration failed: {resp.text}"
    data = resp.json()
    return {"api_key": data["api_key"], "user_id": data["user_id"], "email": email}
