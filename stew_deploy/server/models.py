"""
S.T.E.W Database Models — SQLAlchemy ORM.
Uses String for plan field to support both PostgreSQL and SQLite.
"""
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    String, Text, Integer, DateTime, Boolean, JSON,
    ForeignKey, func
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from server.database import Base

VALID_PLANS = ("free", "student", "pro", "business", "enterprise")


def _uuid() -> str:
    return str(uuid.uuid4())


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    password_hash: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    plan: Mapped[str] = mapped_column(String(20), default="free", nullable=False)
    api_key: Mapped[str] = mapped_column(String(80), unique=True, nullable=False, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # Fine-tune / Persona system
    persona: Mapped[Optional[str]] = mapped_column(String(50), default="general", nullable=True)
    custom_instructions: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    persona_name: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    response_style: Mapped[Optional[str]] = mapped_column(String(20), default="balanced", nullable=True)  # concise|balanced|detailed
    # Voice reply settings
    voice_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)  # if True, Stew replies with voice notes
    preferred_voice: Mapped[Optional[str]] = mapped_column(String(50), default="en-US-AriaNeural", nullable=True)  # edge-tts voice name
    language: Mapped[Optional[str]] = mapped_column(String(10), default="en", nullable=True)
    preferred_model: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    # Mistral API key (user can bring their own)
    mistral_api_key: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    conversations: Mapped[list["Conversation"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    api_calls: Mapped[list["APICall"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    documents: Mapped[list["Document"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    device_fingerprints: Mapped[list["DeviceFingerprint"]] = relationship(back_populates="user", cascade="all, delete-orphan")


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    title: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    messages: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    user: Mapped["User"] = relationship(back_populates="conversations")


class APICall(Base):
    __tablename__ = "api_calls"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[Optional[str]] = mapped_column(String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    endpoint: Mapped[str] = mapped_column(String(255), nullable=False)
    method: Mapped[str] = mapped_column(String(10), default="POST")
    tokens_used: Mapped[int] = mapped_column(Integer, default=0)
    status_code: Mapped[int] = mapped_column(Integer, default=200)
    timestamp: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    user: Mapped[Optional["User"]] = relationship(back_populates="api_calls")


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    file_type: Mapped[str] = mapped_column(String(50), nullable=False)
    content: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    file_size: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    user: Mapped["User"] = relationship(back_populates="documents")


class PaymentTransaction(Base):
    __tablename__ = "payment_transactions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    reference: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    plan: Mapped[str] = mapped_column(String(50), nullable=False)
    amount: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(50), default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class DeviceFingerprint(Base):
    """Tracks device fingerprints to prevent multi-account abuse."""
    __tablename__ = "device_fingerprints"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    fingerprint_hash: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    ip_address: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    user_agent: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    device_type: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)  # desktop/mobile/tablet
    os_name: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    browser_name: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    screen_resolution: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    timezone: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    language: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    is_vpn: Mapped[bool] = mapped_column(Boolean, default=False)
    is_proxy: Mapped[bool] = mapped_column(Boolean, default=False)
    is_tor: Mapped[bool] = mapped_column(Boolean, default=False)
    risk_score: Mapped[int] = mapped_column(Integer, default=0)  # 0-100
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    user: Mapped["User"] = relationship(back_populates="device_fingerprints")


class SecurityEvent(Base):
    """Tracks security events for audit and abuse detection."""
    __tablename__ = "security_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)  # register, login, blocked, vpn_detected, multi_account
    ip_address: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    user_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    fingerprint_hash: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    risk_score: Mapped[int] = mapped_column(Integer, default=0)
    details: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class AdCampaign(Base):
    """Sponsored ad campaigns for the Telegram bot — revenue feature."""
    __tablename__ = "ad_campaigns"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    advertiser_name: Mapped[str] = mapped_column(String(200), nullable=False)
    ad_text: Mapped[str] = mapped_column(Text, nullable=False)  # the ad message text
    ad_link: Mapped[str] = mapped_column(String(500), nullable=True)  # URL to redirect to
    button_text: Mapped[str] = mapped_column(String(50), default="Learn More", nullable=True)  # CTA button text
    target_audience: Mapped[str] = mapped_column(String(50), default="free", nullable=False)  # free, all, pro
    frequency: Mapped[int] = mapped_column(Integer, default=5, nullable=False)  # show every N messages
    impressions: Mapped[int] = mapped_column(Integer, default=0, nullable=False)  # times shown
    clicks: Mapped[int] = mapped_column(Integer, default=0, nullable=False)  # times clicked
    budget_impressions: Mapped[int] = mapped_column(Integer, default=10000, nullable=False)  # max impressions
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False)  # active, paused, ended
    start_date: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    end_date: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class FeatureRequest(Base):
    """Tracks feature requests from Telegram users — what they want Stew to do."""
    __tablename__ = "feature_requests"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    telegram_user_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    feature_text: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(String(50), default="general", nullable=True)  # ai, document, creative, productivity, integration, other
    votes: Mapped[int] = mapped_column(Integer, default=1, nullable=False)  # vote count (starts at 1 for the requester)
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)  # pending, in_progress, done, rejected
    voter_ids: Mapped[list] = mapped_column(JSON, default=list)  # list of telegram user IDs who voted
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class AccessPass(Base):
    """Admin-issued free access passes for Telegram users.

    Admin can create passes with custom message limits, expiry dates,
    and personal notes. Each pass has a unique code. If a user turns
    out to be bad, admin can revoke the pass instantly.
    """
    __tablename__ = "access_passes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    code: Mapped[str] = mapped_column(String(20), unique=True, nullable=False, index=True)
    # Who created this pass
    created_by: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    # Who redeemed this pass (Telegram user ID as string, NULL if not yet redeemed)
    redeemed_by: Mapped[Optional[str]] = mapped_column(String(36), nullable=True, index=True)
    redeemed_by_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    redeemed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    # Pass settings
    message_limit: Mapped[int] = mapped_column(Integer, default=200, nullable=False)
    messages_used: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # Expiry (NULL = never expires)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    # Status: active, revoked, expired, fully_used
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False)
    # Admin's personal note (e.g., "For my cousin Chidi")
    note: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    # Plan this pass grants (free pass = "pro" so they get premium features)
    plan_level: Mapped[str] = mapped_column(String(20), default="pro", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class UserMemory(Base):
    """Persistent key-fact memory stored in PostgreSQL — survives Render restarts."""
    __tablename__ = "user_memories"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    category: Mapped[str] = mapped_column(String(50), nullable=False)  # fact, preference, instruction, context
    content: Mapped[str] = mapped_column(Text, nullable=False)
    importance: Mapped[int] = mapped_column(Integer, default=5)  # 1-10
    source_platform: Mapped[str] = mapped_column(String(20), default="telegram")  # telegram, api
    conversation_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class GeneratedWebsite(Base):
    """A motion-design website generated by /webbuild — hosted directly by Stew
    at /site/{id} so users get a real shareable live link without any external
    hosting dependency."""
    __tablename__ = "generated_websites"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    telegram_user_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=True)
    html: Mapped[str] = mapped_column(Text, nullable=False)
    style: Mapped[str] = mapped_column(String(30), default="premium-dark", nullable=True)
    views: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class MoodEntry(Base):
    """S.T.E.W Mood DNA — tracks user emotional patterns over time.
    No AI assistant does this. Stew learns when you're happiest, most stressed,
    most productive — and adapts its personality to match your mood."""
    __tablename__ = "mood_entries"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    mood: Mapped[str] = mapped_column(String(30), nullable=False)  # happy, excited, calm, neutral, stressed, sad, anxious, angry, tired, motivated
    mood_score: Mapped[int] = mapped_column(Integer, default=50)  # 0-100 (0=very negative, 100=very positive)
    energy_score: Mapped[int] = mapped_column(Integer, default=50)  # 0-100 (0=drained, 100=hyped)
    message_snippet: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)  # first 100 chars of the message
    day_of_week: Mapped[int] = mapped_column(Integer, default=0)  # 0=Mon, 6=Sun
    hour_of_day: Mapped[int] = mapped_column(Integer, default=12)  # 0-23
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
