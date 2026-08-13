"""
S.T.E.W Auth — JWT tokens, bcrypt passwords, API key generation,
password reset tokens.
"""
import secrets
import string
from datetime import datetime, timedelta
from typing import Optional

import bcrypt
import jwt
from fastapi import Depends, HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from server.config import get_settings
from server.database import get_db
from server.models import User

settings = get_settings()
bearer_scheme = HTTPBearer(auto_error=False)

# In-memory reset token store: {token: {user_id, expires}}
# In production with Redis, swap this for Redis SET with TTL
_reset_tokens: dict = {}


# ── Password helpers ─────────────────────────────────────────────────────────

def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode(), hashed.encode())


# ── API key generation ───────────────────────────────────────────────────────

def generate_api_key() -> str:
    """64-char random string prefixed with stew_"""
    alphabet = string.ascii_letters + string.digits
    random_part = "".join(secrets.choice(alphabet) for _ in range(59))
    return f"stew_{random_part}"


# ── JWT helpers ──────────────────────────────────────────────────────────────

def create_access_token(user_id: str, email: str) -> str:
    expire = datetime.utcnow() + timedelta(hours=settings.JWT_EXPIRE_HOURS)
    payload = {
        "sub": user_id,
        "email": email,
        "exp": expire,
        "iat": datetime.utcnow(),
    }
    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token has expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")


# ── Password Reset ────────────────────────────────────────────────────────────

def create_reset_token(user_id: str) -> str:
    """Generate a 1-hour password reset token."""
    token = secrets.token_urlsafe(48)
    _reset_tokens[token] = {
        "user_id": user_id,
        "expires": datetime.utcnow() + timedelta(hours=1),
    }
    return token


def validate_reset_token(token: str) -> Optional[str]:
    """Return user_id if token is valid, else None."""
    entry = _reset_tokens.get(token)
    if not entry:
        return None
    if datetime.utcnow() > entry["expires"]:
        _reset_tokens.pop(token, None)
        return None
    return entry["user_id"]


def consume_reset_token(token: str) -> Optional[str]:
    """Validate and delete token (single use). Returns user_id or None."""
    user_id = validate_reset_token(token)
    if user_id:
        _reset_tokens.pop(token, None)
    return user_id


# ── FastAPI dependencies ─────────────────────────────────────────────────────

async def get_current_user_jwt(
    credentials: Optional[HTTPAuthorizationCredentials] = Security(bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    if not credentials:
        raise HTTPException(status_code=401, detail="Authorization header missing")
    payload = decode_token(credentials.credentials)
    result = await db.execute(select(User).where(User.id == payload["sub"]))
    user = result.scalar_one_or_none()
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="User not found or inactive")
    return user


async def get_user_by_api_key(api_key: str, db: AsyncSession) -> User:
    result = await db.execute(select(User).where(User.api_key == api_key))
    user = result.scalar_one_or_none()
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="Invalid or inactive API key")
    return user
