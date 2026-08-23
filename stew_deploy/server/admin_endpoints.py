"""
S.T.E.W Admin API - Comprehensive multi-admin management system.
Handles: users, payments, Telegram bot, ads, features, security, broadcasts, admin roles.
"""
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select, func, update, text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import datetime, timezone, timedelta
from typing import Optional
import os
import json
import hashlib
import secrets
import logging

from server.database import get_db, AsyncSessionLocal
from server.models import (
    User, Conversation, APICall, Document, PaymentTransaction,
    DeviceFingerprint, SecurityEvent, AdCampaign, FeatureRequest, UserMemory
)
from server.config import settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin/api", tags=["Admin"])

ADMIN_ROLES = {
    "super_admin": "Full access - manage admins, settings, everything",
    "admin": "Manage users, payments, ads, features, broadcasts",
    "moderator": "View-only + moderate features and user bans",
    "finance": "View payments and revenue analytics only",
}

_admin_sessions = {}

def _get_admin_secret():
    return os.environ.get("STEW_ADMIN_SECRET", settings.STEW_ADMIN_SECRET or "")

def _verify_admin(token):
    if not token:
        raise HTTPException(status_code=401, detail="Admin token required")
    if token in _admin_sessions:
        return _admin_sessions[token]
    if token == _get_admin_secret():
        return {"role": "super_admin", "name": "Emmanuel (Owner)", "id": "owner"}
    raise HTTPException(status_code=401, detail="Invalid or expired admin session")

def _require_role(admin, min_role="moderator"):
    role_hierarchy = {"super_admin": 4, "admin": 3, "moderator": 2, "finance": 1}
    if role_hierarchy.get(admin["role"], 0) < role_hierarchy.get(min_role, 0):
        raise HTTPException(status_code=403, detail=f"Requires {min_role} or higher")

@router.post("/login")
async def admin_login(request: Request):
    body = await request.json()
    code = body.get("code", "").strip()
    mfa_pin = body.get("mfa", "").strip()
    name = body.get("name", "").strip()
    admin_secret = _get_admin_secret()
    if not admin_secret:
        raise HTTPException(status_code=503, detail="Admin system not configured. Set STEW_ADMIN_SECRET env var.")
    if code != admin_secret:
        raise HTTPException(status_code=401, detail="Invalid admin code")
    mfa_required = os.environ.get("STEW_ADMIN_MFA", "")
    if mfa_required and mfa_pin != mfa_required:
        raise HTTPException(status_code=401, detail="Invalid MFA pin")
    token = secrets.token_urlsafe(32)
    admin_info = {"role": "super_admin", "name": name or "Super Admin", "id": "owner", "login_time": datetime.now(timezone.utc).isoformat()}
    _admin_sessions[token] = admin_info
    return {"token": token, "admin": admin_info, "roles": ADMIN_ROLES}

@router.get("/verify")
async def admin_verify(token: str):
    admin = _verify_admin(token)
    return {"valid": True, "admin": admin}

@router.post("/logout")
async def admin_logout(request: Request):
    body = await request.json()
    token = body.get("token", "")
    if token in _admin_sessions:
        del _admin_sessions[token]
    return {"ok": True}

@router.get("/dashboard")
async def admin_dashboard(token: str, db: AsyncSession = Depends(get_db)):
    admin = _verify_admin(token)
    total_users = (await db.execute(select(func.count(User.id)))).scalar() or 0
    tg_users = (await db.execute(select(func.count(User.id)).where(User.email.like("tg_%@telegram.stew")))).scalar() or 0
    web_users = total_users - tg_users
    plan_result = await db.execute(select(User.plan, func.count(User.id)).group_by(User.plan))
    users_by_plan = {row[0]: row[1] for row in plan_result}
    total_revenue = (await db.execute(select(func.sum(PaymentTransaction.amount)).where(PaymentTransaction.status == "success"))).scalar() or 0
    month_start = datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    month_revenue = (await db.execute(select(func.sum(PaymentTransaction.amount)).where(PaymentTransaction.status == "success", PaymentTransaction.created_at >= month_start))).scalar() or 0
    tx_count = (await db.execute(select(func.count(PaymentTransaction.id)))).scalar() or 0
    calls_this_month = (await db.execute(select(func.count(APICall.id)).where(APICall.timestamp >= month_start))).scalar() or 0
    week_ago = datetime.now(timezone.utc) - timedelta(days=7)
    active_users = (await db.execute(select(func.count(func.distinct(APICall.user_id))).where(APICall.timestamp >= week_ago))).scalar() or 0
    fr_pending = (await db.execute(select(func.count(FeatureRequest.id)).where(FeatureRequest.status == "pending"))).scalar() or 0
    fr_total = (await db.execute(select(func.count(FeatureRequest.id)))).scalar() or 0
    active_ads = (await db.execute(select(func.count(AdCampaign.id)).where(AdCampaign.status == "active"))).scalar() or 0
    doc_count = (await db.execute(select(func.count(Document.id)))).scalar() or 0
    security_events = (await db.execute(select(func.count(SecurityEvent.id)).where(SecurityEvent.created_at >= week_ago))).scalar() or 0
    new_users_week = (await db.execute(select(func.count(User.id)).where(User.created_at >= week_ago))).scalar() or 0
    revenue_chart = []
    for i in range(6, -1, -1):
        ds = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=i)
        de = ds + timedelta(days=1)
        dr = (await db.execute(select(func.sum(PaymentTransaction.amount)).where(PaymentTransaction.status == "success", PaymentTransaction.created_at >= ds, PaymentTransaction.created_at < de))).scalar() or 0
        revenue_chart.append({"date": ds.strftime("%Y-%m-%d"), "day": ds.strftime("%a"), "revenue": dr})
    signup_chart = []
    for i in range(6, -1, -1):
        ds = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=i)
        de = ds + timedelta(days=1)
        ds_count = (await db.execute(select(func.count(User.id)).where(User.created_at >= ds, User.created_at < de))).scalar() or 0
        signup_chart.append({"date": ds.strftime("%Y-%m-%d"), "day": ds.strftime("%a"), "signups": ds_count})
    return {
        "total_users": total_users, "telegram_users": tg_users, "web_users": web_users,
        "users_by_plan": users_by_plan, "total_revenue": total_revenue, "month_revenue": month_revenue,
        "transactions": tx_count, "calls_this_month": calls_this_month,
        "active_users_7d": active_users, "new_users_7d": new_users_week,
        "feature_requests_pending": fr_pending, "feature_requests_total": fr_total,
        "active_ads": active_ads, "documents_generated": doc_count,
        "security_events_7d": security_events, "revenue_chart": revenue_chart,
        "signup_chart": signup_chart,
        "plan_prices": {"free": 0, "student": 2000, "pro": 9900, "business": 29000, "enterprise": 49000},
        "plan_limits": {"free": 50, "student": 400, "pro": 10000, "business": 100000, "enterprise": 50000},
    }

@router.get("/users")
async def admin_list_users(token: str, page: int = 1, limit: int = 50, search: str = "", plan: str = "", status: str = "", db: AsyncSession = Depends(get_db)):
    admin = _verify_admin(token)
    query = select(User)
    if search:
        query = query.where((User.name.ilike(f"%{search}%")) | (User.email.ilike(f"%{search}%")))
    if plan:
        query = query.where(User.plan == plan)
    if status == "active":
        query = query.where(User.is_active == True)
    elif status == "banned":
        query = query.where(User.is_active == False)
    count_query = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_query)).scalar() or 0
    offset = (page - 1) * limit
    result = await db.execute(query.order_by(User.created_at.desc()).offset(offset).limit(limit))
    users = result.scalars().all()
    user_ids = [u.id for u in users]
    call_map = {}
    if user_ids:
        call_counts = await db.execute(select(APICall.user_id, func.count(APICall.id)).where(APICall.user_id.in_(user_ids)).group_by(APICall.user_id))
        call_map = {row[0]: row[1] for row in call_counts}
    return {"users": [{"id": u.id, "name": u.name, "email": u.email, "plan": u.plan, "is_active": u.is_active, "voice_enabled": getattr(u, "voice_enabled", False), "preferred_voice": getattr(u, "preferred_voice", None), "created_at": u.created_at.isoformat() if u.created_at else None, "api_calls": call_map.get(u.id, 0)} for u in users], "total": total, "page": page, "pages": (total + limit - 1) // limit}

@router.get("/users/{user_id}")
async def admin_get_user(user_id: str, token: str, db: AsyncSession = Depends(get_db)):
    admin = _verify_admin(token)
    user = (await db.execute(select(User).where(User.id == user_id))).scalars().first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    convs = (await db.execute(select(Conversation).where(Conversation.user_id == user_id).order_by(Conversation.updated_at.desc()).limit(10))).scalars().all()
    payments = (await db.execute(select(PaymentTransaction).where(PaymentTransaction.user_id == user_id).order_by(PaymentTransaction.created_at.desc()))).scalars().all()
    call_count = (await db.execute(select(func.count(APICall.id)).where(APICall.user_id == user_id))).scalar() or 0
    docs = (await db.execute(select(Document).where(Document.user_id == user_id).order_by(Document.created_at.desc()).limit(20))).scalars().all()
    memories = (await db.execute(select(UserMemory).where(UserMemory.user_id == user_id).order_by(UserMemory.created_at.desc()).limit(20))).scalars().all()
    sec_events = (await db.execute(select(SecurityEvent).where(SecurityEvent.user_id == user_id).order_by(SecurityEvent.created_at.desc()).limit(20))).scalars().all()
    return {"user": {"id": user.id, "name": user.name, "email": user.email, "plan": user.plan, "is_active": user.is_active, "voice_enabled": getattr(user, "voice_enabled", False), "preferred_voice": getattr(user, "preferred_voice", None), "language": getattr(user, "language", "en"), "persona": getattr(user, "persona", "general"), "created_at": user.created_at.isoformat() if user.created_at else None, "api_key": user.api_key[:10] + "..." if user.api_key else None}, "stats": {"total_calls": call_count, "total_conversations": len(convs), "total_documents": len(docs), "total_payments": len(payments), "total_memories": len(memories)}, "conversations": [{"id": c.id, "title": c.title, "message_count": len(c.messages) if c.messages else 0, "created_at": c.created_at.isoformat() if c.created_at else None, "updated_at": c.updated_at.isoformat() if c.updated_at else None} for c in convs], "payments": [{"id": p.id, "reference": p.reference, "plan": p.plan, "amount": p.amount, "status": p.status, "created_at": p.created_at.isoformat() if p.created_at else None} for p in payments], "documents": [{"id": d.id, "filename": d.filename, "file_type": d.file_type, "file_size": d.file_size, "created_at": d.created_at.isoformat() if d.created_at else None} for d in docs], "memories": [{"id": m.id, "category": m.category, "content": m.content[:200], "importance": m.importance, "is_active": m.is_active, "created_at": m.created_at.isoformat() if m.created_at else None} for m in memories], "security_events": [{"id": s.id, "event_type": s.event_type, "ip_address": s.ip_address, "risk_score": s.risk_score, "details": s.details, "created_at": s.created_at.isoformat() if s.created_at else None} for s in sec_events]}

@router.post("/users/{user_id}/plan")
async def admin_update_user_plan(user_id: str, request: Request, token: str, db: AsyncSession = Depends(get_db)):
    admin = _verify_admin(token)
    _require_role(admin, "admin")
    body = await request.json()
    new_plan = body.get("plan", "").strip()
    if new_plan not in ("free", "student", "pro", "business", "enterprise", "owner"):
        raise HTTPException(status_code=400, detail="Invalid plan")
    result = await db.execute(update(User).where(User.id == user_id).values(plan=new_plan))
    await db.commit()
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="User not found")
    logger.info(f"Admin {admin['name']} changed user {user_id} to plan {new_plan}")
    return {"ok": True, "plan": new_plan}

@router.post("/users/{user_id}/ban")
async def admin_ban_user(user_id: str, request: Request, token: str, db: AsyncSession = Depends(get_db)):
    admin = _verify_admin(token)
    _require_role(admin, "admin")
    body = await request.json()
    banned = body.get("banned", True)
    result = await db.execute(update(User).where(User.id == user_id).values(is_active=not banned))
    await db.commit()
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="User not found")
    logger.info(f"Admin {admin['name']} {'banned' if banned else 'unbanned'} user {user_id}")
    return {"ok": True, "is_active": not banned}

@router.delete("/users/{user_id}")
async def admin_delete_user(user_id: str, token: str, db: AsyncSession = Depends(get_db)):
    admin = _verify_admin(token)
    _require_role(admin, "super_admin")
    user = (await db.execute(select(User).where(User.id == user_id))).scalars().first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    await db.delete(user)
    await db.commit()
    logger.info(f"Admin {admin['name']} deleted user {user_id}")
    return {"ok": True}

@router.get("/payments")
async def admin_list_payments(token: str, page: int = 1, limit: int = 50, status: str = "", db: AsyncSession = Depends(get_db)):
    admin = _verify_admin(token)
    query = select(PaymentTransaction)
    if status:
        query = query.where(PaymentTransaction.status == status)
    count_query = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_query)).scalar() or 0
    offset = (page - 1) * limit
    result = await db.execute(query.order_by(PaymentTransaction.created_at.desc()).offset(offset).limit(limit))
    txns = result.scalars().all()
    user_ids = [t.user_id for t in txns]
    user_map = {}
    if user_ids:
        users = await db.execute(select(User.id, User.name, User.email).where(User.id.in_(user_ids)))
        user_map = {u[0]: {"name": u[1], "email": u[2]} for u in users}
    return {"payments": [{"id": t.id, "user_id": t.user_id, "user_name": user_map.get(t.user_id, {}).get("name", "Unknown"), "user_email": user_map.get(t.user_id, {}).get("email", ""), "reference": t.reference, "plan": t.plan, "amount": t.amount, "status": t.status, "created_at": t.created_at.isoformat() if t.created_at else None} for t in txns], "total": total, "page": page, "pages": (total + limit - 1) // limit}

@router.get("/payments/analytics")
async def admin_payment_analytics(token: str, db: AsyncSession = Depends(get_db)):
    admin = _verify_admin(token)
    plan_rev = await db.execute(select(PaymentTransaction.plan, func.sum(PaymentTransaction.amount), func.count(PaymentTransaction.id)).where(PaymentTransaction.status == "success").group_by(PaymentTransaction.plan))
    revenue_by_plan = {row[0]: {"revenue": row[1], "count": row[2]} for row in plan_rev}
    monthly = []
    for i in range(5, -1, -1):
        ms = datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0) - timedelta(days=i * 30)
        me = ms + timedelta(days=30)
        rev = (await db.execute(select(func.sum(PaymentTransaction.amount)).where(PaymentTransaction.status == "success", PaymentTransaction.created_at >= ms, PaymentTransaction.created_at < me))).scalar() or 0
        cnt = (await db.execute(select(func.count(PaymentTransaction.id)).where(PaymentTransaction.status == "success", PaymentTransaction.created_at >= ms, PaymentTransaction.created_at < me))).scalar() or 0
        monthly.append({"month": ms.strftime("%b %Y"), "revenue": rev, "count": cnt})
    status_breakdown = await db.execute(select(PaymentTransaction.status, func.count(PaymentTransaction.id), func.sum(PaymentTransaction.amount)).group_by(PaymentTransaction.status))
    by_status = {row[0]: {"count": row[1], "amount": row[2]} for row in status_breakdown}
    return {"revenue_by_plan": revenue_by_plan, "monthly_revenue": monthly, "status_breakdown": by_status, "total_revenue": sum(v["revenue"] for v in revenue_by_plan.values())}

@router.get("/telegram/stats")
async def admin_telegram_stats(token: str, db: AsyncSession = Depends(get_db)):
    admin = _verify_admin(token)
    tg_count = (await db.execute(select(func.count(User.id)).where(User.email.like("tg_%@telegram.stew")))).scalar() or 0
    tg_plans = await db.execute(select(User.plan, func.count(User.id)).where(User.email.like("tg_%@telegram.stew")).group_by(User.plan))
    plans = {row[0]: row[1] for row in tg_plans}
    voice_users = (await db.execute(select(func.count(User.id)).where(User.email.like("tg_%@telegram.stew"), User.voice_enabled == True))).scalar() or 0
    conv_count = (await db.execute(select(func.count(Conversation.id)))).scalar() or 0
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    calls_today = (await db.execute(select(func.count(APICall.id)).where(APICall.timestamp >= today_start))).scalar() or 0
    return {"telegram_users": tg_count, "users_by_plan": plans, "voice_enabled_users": voice_users, "total_conversations": conv_count, "messages_today": calls_today, "bot_configured": bool(os.environ.get("TELEGRAM_BOT_TOKEN", ""))}

@router.post("/telegram/broadcast")
async def admin_broadcast(token: str, request: Request, db: AsyncSession = Depends(get_db)):
    admin = _verify_admin(token)
    _require_role(admin, "admin")
    body = await request.json()
    message = body.get("message", "").strip()
    target = body.get("target", "all")
    if not message:
        raise HTTPException(status_code=400, detail="Message is required")
    if len(message) > 4000:
        raise HTTPException(status_code=400, detail="Message too long (max 4000 chars)")
    query = select(User).where(User.email.like("tg_%@telegram.stew"))
    if target != "all":
        query = query.where(User.plan == target)
    users = (await db.execute(query)).scalars().all()
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not bot_token:
        raise HTTPException(status_code=503, detail="Telegram bot not configured")
    import httpx
    sent = 0
    failed = 0
    errors = []
    async with httpx.AsyncClient(timeout=30) as client:
        for user in users:
            tg_id = user.email.replace("tg_", "").replace("@telegram.stew", "")
            try:
                resp = await client.post(f"https://api.telegram.org/bot{bot_token}/sendMessage", json={"chat_id": int(tg_id), "text": f"[STEW Announcement]\n\n{message}", "parse_mode": ""})
                if resp.status_code == 200:
                    sent += 1
                else:
                    failed += 1
                    if len(errors) < 5:
                        errors.append(f"{tg_id}: {resp.text[:100]}")
            except Exception as e:
                failed += 1
                if len(errors) < 5:
                    errors.append(f"{tg_id}: {str(e)[:100]}")
    logger.info(f"Admin {admin['name']} broadcast to {sent} users ({failed} failed)")
    return {"sent": sent, "failed": failed, "total": len(users), "errors": errors[:5]}

@router.get("/features")
async def admin_list_features(token: str, status: str = "", page: int = 1, limit: int = 50, db: AsyncSession = Depends(get_db)):
    admin = _verify_admin(token)
    query = select(FeatureRequest)
    if status:
        query = query.where(FeatureRequest.status == status)
    count_query = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_query)).scalar() or 0
    offset = (page - 1) * limit
    result = await db.execute(query.order_by(FeatureRequest.created_at.desc()).offset(offset).limit(limit))
    features = result.scalars().all()
    return {"features": [{"id": f.id, "feature_text": f.feature_text, "category": f.category, "votes": len(f.voter_ids) if f.voter_ids else 0, "status": f.status, "telegram_user_id": f.telegram_user_id, "created_at": f.created_at.isoformat() if f.created_at else None} for f in features], "total": total, "page": page, "pages": (total + limit - 1) // limit}

@router.post("/features/{feature_id}/status")
async def admin_update_feature_status(feature_id: str, request: Request, token: str, db: AsyncSession = Depends(get_db)):
    admin = _verify_admin(token)
    _require_role(admin, "moderator")
    body = await request.json()
    new_status = body.get("status", "").strip()
    if new_status not in ("pending", "approved", "rejected", "in_progress", "completed"):
        raise HTTPException(status_code=400, detail="Invalid status")
    result = await db.execute(update(FeatureRequest).where(FeatureRequest.id == feature_id).values(status=new_status))
    await db.commit()
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Feature not found")
    return {"ok": True, "status": new_status}

@router.get("/ads")
async def admin_list_ads(token: str, status: str = "", db: AsyncSession = Depends(get_db)):
    admin = _verify_admin(token)
    query = select(AdCampaign)
    if status:
        query = query.where(AdCampaign.status == status)
    result = await db.execute(query.order_by(AdCampaign.created_at.desc()))
    ads = result.scalars().all()
    return {"ads": [{"id": a.id, "advertiser_name": a.advertiser_name, "ad_text": a.ad_text, "ad_link": a.ad_link, "button_text": a.button_text, "target_audience": a.target_audience, "frequency": a.frequency, "impressions": a.impressions, "clicks": a.clicks, "budget_impressions": a.budget_impressions, "status": a.status, "created_at": a.created_at.isoformat() if a.created_at else None} for a in ads]}

@router.post("/ads")
async def admin_create_ad(request: Request, token: str, db: AsyncSession = Depends(get_db)):
    admin = _verify_admin(token)
    _require_role(admin, "admin")
    body = await request.json()
    ad = AdCampaign(advertiser_name=body.get("advertiser_name", ""), ad_text=body.get("ad_text", ""), ad_link=body.get("ad_link", ""), button_text=body.get("button_text", "Learn More"), target_audience=body.get("target_audience", "free"), frequency=body.get("frequency", 5), budget_impressions=body.get("budget_impressions", 10000), status="active")
    db.add(ad)
    await db.commit()
    return {"ok": True, "id": ad.id}

@router.post("/ads/{ad_id}/status")
async def admin_update_ad_status(ad_id: str, request: Request, token: str, db: AsyncSession = Depends(get_db)):
    admin = _verify_admin(token)
    _require_role(admin, "admin")
    body = await request.json()
    new_status = body.get("status", "").strip()
    if new_status not in ("active", "paused", "ended"):
        raise HTTPException(status_code=400, detail="Invalid status")
    result = await db.execute(update(AdCampaign).where(AdCampaign.id == ad_id).values(status=new_status))
    await db.commit()
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Ad not found")
    return {"ok": True, "status": new_status}

@router.delete("/ads/{ad_id}")
async def admin_delete_ad(ad_id: str, token: str, db: AsyncSession = Depends(get_db)):
    admin = _verify_admin(token)
    _require_role(admin, "admin")
    ad = (await db.execute(select(AdCampaign).where(AdCampaign.id == ad_id))).scalars().first()
    if not ad:
        raise HTTPException(status_code=404, detail="Ad not found")
    await db.delete(ad)
    await db.commit()
    return {"ok": True}

@router.get("/security")
async def admin_security_events(token: str, page: int = 1, limit: int = 50, db: AsyncSession = Depends(get_db)):
    admin = _verify_admin(token)
    total = (await db.execute(select(func.count(SecurityEvent.id)))).scalar() or 0
    offset = (page - 1) * limit
    result = await db.execute(select(SecurityEvent).order_by(SecurityEvent.created_at.desc()).offset(offset).limit(limit))
    events = result.scalars().all()
    return {"events": [{"id": s.id, "event_type": s.event_type, "ip_address": s.ip_address, "user_id": s.user_id, "risk_score": s.risk_score, "details": s.details, "created_at": s.created_at.isoformat() if s.created_at else None} for s in events], "total": total, "page": page, "pages": (total + limit - 1) // limit}

@router.get("/security/devices")
async def admin_device_fingerprints(token: str, page: int = 1, limit: int = 50, db: AsyncSession = Depends(get_db)):
    admin = _verify_admin(token)
    total = (await db.execute(select(func.count(DeviceFingerprint.id)))).scalar() or 0
    offset = (page - 1) * limit
    result = await db.execute(select(DeviceFingerprint).order_by(DeviceFingerprint.created_at.desc()).offset(offset).limit(limit))
    devices = result.scalars().all()
    return {"devices": [{"id": d.id, "user_id": d.user_id, "fingerprint_hash": d.fingerprint_hash[:16] + "...", "ip_address": d.ip_address, "device_type": d.device_type, "os_name": d.os_name, "browser_name": d.browser_name, "is_vpn": d.is_vpn, "is_proxy": d.is_proxy, "risk_score": d.risk_score, "created_at": d.created_at.isoformat() if d.created_at else None} for d in devices], "total": total, "page": page, "pages": (total + limit - 1) // limit}

@router.get("/system/health")
async def admin_system_health(token: str, db: AsyncSession = Depends(get_db)):
    admin = _verify_admin(token)
    try:
        await db.execute(select(func.count(User.id)).limit(1))
        db_status = "healthy"
    except Exception as e:
        db_status = f"error: {str(e)[:100]}"
    from server.llm_client import get_llm_client
    try:
        llm = get_llm_client()
        providers = llm.fallback_order
        llm_status = {"providers": providers, "count": len(providers)}
    except Exception as e:
        llm_status = {"providers": [], "error": str(e)[:100]}
    env_vars = {"GROQ_API_KEY": bool(os.environ.get("GROQ_API_KEY")), "OPENAI_API_KEY": bool(os.environ.get("OPENAI_API_KEY")), "TELEGRAM_BOT_TOKEN": bool(os.environ.get("TELEGRAM_BOT_TOKEN")), "PAYSTACK_SECRET_KEY": bool(os.environ.get("PAYSTACK_SECRET_KEY")), "SUPABASE_URL": bool(os.environ.get("SUPABASE_URL")), "HUGGINGFACE_API_KEY": bool(os.environ.get("HUGGINGFACE_API_KEY")), "STEW_ADMIN_SECRET": bool(os.environ.get("STEW_ADMIN_SECRET"))}
    return {"database": db_status, "llm": llm_status, "telegram_bot": "configured" if os.environ.get("TELEGRAM_BOT_TOKEN") else "not_configured", "env_vars": env_vars, "server_time": datetime.now(timezone.utc).isoformat(), "uptime": "active"}

@router.get("/audit-log")
async def admin_audit_log(token: str, limit: int = 50):
    admin = _verify_admin(token)
    return {"active_sessions": list(_admin_sessions.values()), "note": "Full audit log requires a dedicated DB table. Active sessions shown for now."}

@router.get("/memories/{user_id}")
async def admin_get_memories(user_id: str, token: str, db: AsyncSession = Depends(get_db)):
    admin = _verify_admin(token)
    result = await db.execute(select(UserMemory).where(UserMemory.user_id == user_id).order_by(UserMemory.created_at.desc()))
    memories = result.scalars().all()
    return {"memories": [{"id": m.id, "category": m.category, "content": m.content, "importance": m.importance, "is_active": m.is_active, "created_at": m.created_at.isoformat() if m.created_at else None} for m in memories], "total": len(memories)}

@router.delete("/memories/{memory_id}")
async def admin_delete_memory(memory_id: str, token: str, db: AsyncSession = Depends(get_db)):
    admin = _verify_admin(token)
    _require_role(admin, "admin")
    mem = (await db.execute(select(UserMemory).where(UserMemory.id == memory_id))).scalars().first()
    if not mem:
        raise HTTPException(status_code=404, detail="Memory not found")
    await db.delete(mem)
    await db.commit()
    return {"ok": True}
