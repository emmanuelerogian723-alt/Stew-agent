"""
S.T.E.W — Structured Task Execution Workflow
FastAPI Backend v5.0
"""
import json
import logging
import os
import requests as http_requests
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import (
    Depends, FastAPI, File, Form, HTTPException, Header,
    Request, UploadFile, BackgroundTasks
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse, HTMLResponse
from pydantic import BaseModel, EmailStr, field_validator
import asyncio
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from server.auth import (
    create_access_token, generate_api_key, get_current_user_jwt,
    get_user_by_api_key, hash_password, verify_password,
)
from server.config import get_settings
from server.database import get_db, init_db
from server.document_generator import (
    generate_docx, generate_html, generate_pdf, generate_pptx, generate_xlsx,
)
from server.document_processor import extract_text
from server.llm_client import get_llm_client
from server.orchestrator import orchestrate_text, orchestrate_image
from server.memory import (
    append_message, build_llm_messages, get_or_create_conversation, get_relevant_context,
)
from server.middleware import RateLimitMiddleware, SecurityHeadersMiddleware
from server.models import APICall, Conversation, DeviceFingerprint, Document, PaymentTransaction, SecurityEvent, User
from server.security_guard import (
    compute_fingerprint, check_vpn_proxy, assess_registration_risk,
    record_device_fingerprint, log_security_event, get_security_dashboard,
    RISK_THRESHOLD_BLOCK, RISK_THRESHOLD_FLAG
)
from server.payments import initialize_payment, validate_webhook_signature, verify_payment, upgrade_user_plan
from server.search import get_searcher
from server.ocr_engine import ocr_file, ocr_and_reason, SUPPORTED_LANGS

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)
settings = get_settings()

from server.system_prompt import STEW_MASTER_PROMPT
from server.clean_output import clean_response
from server.email_service import send_welcome_email, send_password_reset_email, send_password_changed_email
from server.auth import create_reset_token, consume_reset_token
from server.keepalive import start_keepalive, stop_keepalive
from server.skills_engine import run_skill, list_skills as get_skills_list



# ── Lifespan ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("S.T.E.W API v5.0 starting up…")
    await init_db()
    os.makedirs("logs", exist_ok=True)
    os.makedirs("output", exist_ok=True)
    start_keepalive()
    yield
    stop_keepalive()
    logger.info("S.T.E.W API shutting down.")


# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="S.T.E.W Agent API",
    description="Structured Task Execution Workflow — AI Agent Backend v5.0",
    version="6.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(RateLimitMiddleware)


# ── Background: log API call ──────────────────────────────────────────────────

async def _log_call(_db: AsyncSession, user_id: Optional[str], endpoint: str,
                    method: str, tokens: int, status: int):
    """Log an API call using its own fresh DB session (background-safe)."""
    from server.database import AsyncSessionLocal
    try:
        async with AsyncSessionLocal() as session:
            call = APICall(
                user_id=user_id,
                endpoint=endpoint,
                method=method,
                tokens_used=tokens,
                status_code=status,
            )
            session.add(call)
            await session.commit()
    except Exception as e:
        logger.warning(f"Failed to log API call: {e}")

async def _check_quota(user: User, db: AsyncSession) -> tuple[bool, int, int]:
    """Check if user has remaining quota. Returns (allowed, calls_used, limit)."""
    month_start = datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    result = await db.execute(
        select(func.count(APICall.id)).where(
            APICall.user_id == user.id,
            APICall.timestamp >= month_start
        )
    )
    calls_used = result.scalar() or 0
    plan_limit = settings.PLAN_CALL_LIMITS.get(user.plan, 1500)
    return (calls_used < plan_limit, calls_used, plan_limit)




# ═══════════════════════════════════════════════════════════════════════════════
# PYDANTIC SCHEMAS
# ═══════════════════════════════════════════════════════════════════════════════

class RegisterRequest(BaseModel):
    name: str
    email: EmailStr
    password: Optional[str] = None
    plan: str = "free"

    @field_validator("plan")
    @classmethod
    def validate_plan(cls, v):
        if v not in ("free", "pro", "business", "enterprise"):
            raise ValueError("plan must be free, pro, business, or enterprise")
        return v


class LoginRequest(BaseModel):
    email: EmailStr
    password: str

class GenerateKeyRequest(BaseModel):
    email: EmailStr
    password: Optional[str] = None


class ChatRequest(BaseModel):
    message: str
    conversation_id: Optional[str] = None
    api_key: Optional[str] = None
    web_search: bool = True
    fusion_mode: bool = False


class TaskRequest(BaseModel):
    task: str
    api_key: str = ""
    context: Optional[str] = None


class BrowseRequest(BaseModel):
    url: str
    question: Optional[str] = None
    api_key: str


class GeneratePDFRequest(BaseModel):
    content: str
    title: str = "Document"
    api_key: str = ""


class GenerateDOCXRequest(BaseModel):
    content: str
    title: str = "Document"
    api_key: str = ""


class GenerateXLSXRequest(BaseModel):
    data: list[dict]
    sheet_name: str = "Sheet1"
    title: str = "Spreadsheet"
    api_key: str = ""


class GeneratePPTXRequest(BaseModel):
    slides: list[dict]
    title: str = "Presentation"
    api_key: str = ""


class GenerateHTMLRequest(BaseModel):
    content: str
    title: str = "Report"
    api_key: str = ""


class APICallRequest(BaseModel):
    url: str
    method: str = "GET"
    headers: dict = {}
    body: Optional[dict] = None
    api_key: str


class FingerprintRequest(BaseModel):
    """Device fingerprint data sent from the frontend."""
    canvas_hash: Optional[str] = ""
    webgl_hash: Optional[str] = ""
    screen_resolution: Optional[str] = ""
    timezone: Optional[str] = ""
    language: Optional[str] = ""


class InitPaymentRequest(BaseModel):
    plan: str
    api_key: str


class VerifyPaymentRequest(BaseModel):
    reference: str
    api_key: str


# ═══════════════════════════════════════════════════════════════════════════════
# ROUTES
# ═══════════════════════════════════════════════════════════════════════════════

# ── Health ─────────────────────────────────────────────────────────────────────


async def count_free_accounts_by_ip_secured(ip: str, db: AsyncSession) -> int:
    """Count free-tier accounts from this IP in the last 24h."""
    from datetime import timedelta
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    result = await db.execute(
        select(func.count(DeviceFingerprint.id)).where(
            DeviceFingerprint.ip_address == ip,
            DeviceFingerprint.created_at >= cutoff,
        )
    )
    return result.scalar() or 0

async def _safe_get_user(api_key: str, db: AsyncSession) -> Optional[User]:
    """Safely look up a user by API key. Returns None if not found or inactive."""
    if not api_key:
        return None
    try:
        result = await db.execute(select(User).where(User.api_key == api_key))
        user = result.scalar_one_or_none()
        if not user or not user.is_active:
            return None
        return user
    except Exception:
        return None


@app.get("/heartbeat")
async def heartbeat():
    # Sanitized status — no provider names exposed
    ai_ready = bool(settings.GROQ_API_KEY or settings.OPENROUTER_API_KEY or settings.OPENAI_API_KEY or settings.MISTRAL_API_KEY)
    return {
        "status": "ok",
        "version": "6.0.0",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "services": {
            "ai_engine": "operational" if ai_ready else "degraded",
            "web_search": "operational" if settings.SERPER_API_KEY else "unavailable",
            "payments": "operational" if settings.PAYSTACK_SECRET_KEY else "unavailable",
            "agent_pool": "operational",
            "image_generation": "operational",
        },
    }


# ── Auth ───────────────────────────────────────────────────────────────────────



@app.get("/reset-password", response_class=HTMLResponse, include_in_schema=False)
async def reset_password_page(token: str = ""):
    """Redirect /reset-password?token=xxx to landing page which handles the reset UI."""
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url=f"/?token={token}", status_code=302)


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def landing_page():
    """Serve the S.T.E.W landing page."""
    import os
    # Look for landing.html in several locations
    candidates = [
        "/app/landing.html",
        "/app/stew_deploy/landing.html",
        os.path.join(os.path.dirname(__file__), "..", "landing.html"),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "landing.html"),
        "landing.html",
    ]
    for path in candidates:
        path = os.path.normpath(path)
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return HTMLResponse(content=f.read())
    # Fallback inline landing
    return HTMLResponse(content="""<!DOCTYPE html>
<html><head><title>S.T.E.W Agent</title>
<meta name='viewport' content='width=device-width,initial-scale=1'>
<style>body{font-family:system-ui;background:#0d0d1a;color:#fff;text-align:center;padding:60px 20px}
h1{font-size:3em;background:linear-gradient(90deg,#7B2FBE,#00d4ff);-webkit-background-clip:text;-webkit-text-fill-color:transparent}
p{color:#aaa;font-size:1.2em}.btn{display:inline-block;margin:10px;padding:14px 30px;border-radius:8px;text-decoration:none;font-weight:bold}
.btn-primary{background:#7B2FBE;color:#fff}.btn-secondary{border:2px solid #7B2FBE;color:#7B2FBE}</style></head>
<body><h1>S.T.E.W 3.0 ULTRA</h1><p>Smart Thinking Executive Worker</p>
<p>Africa's Most Powerful AI Agent API</p>
<a class="btn btn-primary" href="/docs">API Docs</a>
<a class="btn btn-secondary" href="/heartbeat">Status</a>
</body></html>""")



@app.get("/robots.txt", include_in_schema=False)
async def robots():
    import os
    p = os.path.join(os.path.dirname(__file__), "..", "robots.txt")
    if os.path.exists(p):
        return FileResponse(p, media_type="text/plain")
    return PlainTextResponse("User-agent: *\nAllow: /\nSitemap: https://stew-agent.onrender.com/sitemap.xml")

@app.get("/sitemap.xml", include_in_schema=False)
async def sitemap():
    import os
    p = os.path.join(os.path.dirname(__file__), "..", "sitemap.xml")
    if os.path.exists(p):
        return FileResponse(p, media_type="application/xml")
    return PlainTextResponse("<?xml version=\"1.0\"?><urlset xmlns=\"http://www.sitemaps.org/schemas/sitemap/0.9\"><url><loc>https://stew-agent.onrender.com/</loc></url></urlset>")

@app.get("/playground", response_class=HTMLResponse, include_in_schema=False)
async def playground_page():
    """Serve the S.T.E.W Playground."""
    import os
    candidates = [
        "/app/stew_playground.html",
        os.path.join(os.path.dirname(__file__), "..", "stew_playground.html"),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "stew_playground.html"),
        "stew_playground.html",
    ]
    for path in candidates:
        path = os.path.normpath(path)
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return HTMLResponse(content=f.read())
    return HTMLResponse(content="<h1>Playground not found</h1><p>stew_playground.html missing</p>")


@app.get("/dashboard", response_class=HTMLResponse, include_in_schema=False)
async def dashboard_page():
    """Serve the S.T.E.W user dashboard."""
    for path in [
        "/app/dashboard.html",
        "/app/stew_deploy/dashboard.html",
        os.path.join(os.path.dirname(__file__), "..", "dashboard.html"),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "dashboard.html"),
        "dashboard.html",
    ]:
        if os.path.exists(path):
            with open(path, "r") as f:
                return HTMLResponse(content=f.read())
    return HTMLResponse(content="<h1>Dashboard not found</h1>", status_code=404)



@app.post("/auth/register", status_code=201)
async def register(
    body: RegisterRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    # ── Security: Get client IP and user agent ──
    from server.security import get_client_ip
    client_ip = get_client_ip(request)
    user_agent = request.headers.get("User-Agent", "")

    # ── Security: Check if IP is already rate-limited for registrations ──
    # Prevent rapid-fire account creation
    ip_reg_count = await count_free_accounts_by_ip_secured(client_ip, db)
    if body.plan == "free" and ip_reg_count >= 3:
        await log_security_event(
            "registration_blocked", client_ip,
            risk_score=80, details=f"IP already has {ip_reg_count} accounts",
            db=db,
        )
        raise HTTPException(429, "Too many accounts created from this network. Please upgrade to a paid plan or try again later.")

    # ── Security: Compute device fingerprint ──
    fp_hash = compute_fingerprint(
        user_agent=user_agent,
        canvas_hash=body.__dict__.get("canvas_hash", ""),
        screen_resolution=body.__dict__.get("screen_resolution", ""),
        timezone=body.__dict__.get("timezone", ""),
        language=body.__dict__.get("language", ""),
    )

    # ── Security: Assess registration risk ──
    risk_score, risk_reasons, vpn_info = await assess_registration_risk(
        client_ip, fp_hash, user_agent, db
    )

    if risk_score >= RISK_THRESHOLD_BLOCK:
        await log_security_event(
            "registration_blocked", client_ip,
            fingerprint_hash=fp_hash, risk_score=risk_score,
            details="; ".join(risk_reasons), db=db,
        )
        raise HTTPException(403, f"Registration blocked: suspicious activity detected. If this is an error, contact support@mutyint.com")

    # Check email
    result = await db.execute(select(User).where(User.email == body.email))
    if result.scalar_one_or_none():
        raise HTTPException(409, "Email already registered")

    user = User(
        name=body.name,
        email=body.email,
        password_hash=hash_password(body.password) if body.password else None,
        plan=body.plan,
        api_key=generate_api_key(),
    )
    db.add(user)
    await db.flush()
    await db.refresh(user)

    # ── Security: Record device fingerprint ──
    await record_device_fingerprint(
        user_id=user.id,
        fingerprint_hash=fp_hash,
        ip_address=client_ip,
        user_agent=user_agent,
        vpn_info=vpn_info,
        risk_score=risk_score,
        db=db,
    )

    # Log the security event
    await log_security_event(
        "register", client_ip,
        user_id=user.id, fingerprint_hash=fp_hash,
        risk_score=risk_score,
        details=f"New registration. Risk: {risk_score}. {'Flagged: ' + '; '.join(risk_reasons) if risk_reasons else 'Clean'}",
        db=db,
    )

    # Send welcome email in background (non-blocking)
    import asyncio
    asyncio.create_task(send_welcome_email(user.email, user.name, user.api_key, user.plan))

    token = create_access_token(user.id, user.email)
    return {
        "api_key": user.api_key,
        "user_id": user.id,
        "plan": user.plan,
        "calls_limit": settings.PLAN_CALL_LIMITS[user.plan],
        "access_token": token,
        "token_type": "bearer",
        "name": user.name,
        "success": True,
        "message": "Account created! Your API key is ready in the dashboard.",
        "risk_score": risk_score,
        "security_flags": risk_reasons if risk_reasons else [],
    }


@app.post("/auth/login")
async def login(body: LoginRequest, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.email == body.email))
    user = result.scalar_one_or_none()

    if not user or not user.password_hash or not verify_password(body.password, user.password_hash):
        raise HTTPException(401, "Invalid email or password")

    token = create_access_token(user.id, user.email)
    return {
        "access_token": token,
        "token_type": "bearer",
        "user_id": user.id,
        "plan": user.plan,
        "api_key": user.api_key,
        "name": user.name,
        "success": True,
    }



@app.post("/auth/firebase")
async def firebase_auth(body: dict, request: Request, db: AsyncSession = Depends(get_db)):
    """
    Authenticate or register a user via Firebase ID token.
    If the user doesn't exist in our DB, create them. If they do, log them in.
    Returns the same shape as /auth/register and /auth/login.
    Now includes device fingerprinting and VPN/risk assessment.
    """
    from server.security import get_client_ip
    id_token = body.get("id_token")
    email = body.get("email", "")
    name = body.get("name", "")
    # Device fingerprint data from frontend
    canvas_hash = body.get("canvas_hash", "")
    webgl_hash = body.get("webgl_hash", "")
    screen_resolution = body.get("screen_resolution", "")
    fp_timezone = body.get("timezone", "")
    fp_language = body.get("language", "")

    if not id_token:
        raise HTTPException(400, "Missing id_token")

    client_ip = get_client_ip(request)
    user_agent = request.headers.get("User-Agent", "")

    # Try to verify the Firebase token using Firebase Admin SDK if available
    firebase_uid = None
    fb_verified = False
    try:
        import firebase_admin
        from firebase_admin import credentials, auth as fb_auth_admin
        if not firebase_admin._apps:
            import json as _json
            fb_creds = os.environ.get("FIREBASE_CREDENTIALS")
            if fb_creds:
                cred_dict = _json.loads(fb_creds)
                cred = credentials.Certificate(cred_dict)
                firebase_admin.initialize_app(cred)
            else:
                # Try Application Default Credentials (works on Render/GCP)
                firebase_admin.initialize_app()
        decoded = fb_auth_admin.verify_id_token(id_token)
        firebase_uid = decoded.get("uid")
        email = decoded.get("email", email)
        name = decoded.get("name", name)
        fb_verified = True
        logger.info(f"Firebase token verified for uid={firebase_uid}, email={email}")
    except ImportError:
        logger.warning("firebase_admin not installed — trusting frontend Firebase token")
    except Exception as fb_err:
        logger.warning(f"Firebase token verification failed: {fb_err} — trusting frontend token")
        # Still proceed — the frontend has already verified the Firebase token via Firebase client SDK

    # Compute device fingerprint
    fp_hash = compute_fingerprint(
        user_agent=user_agent,
        canvas_hash=canvas_hash,
        webgl_hash=webgl_hash,
        screen_resolution=screen_resolution,
        timezone=fp_timezone,
        language=fp_language,
    )

    # Check if user exists
    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()

    if user:
        # Existing user — log them in
        if not user.is_active:
            raise HTTPException(403, "Account is deactivated")

        # Record this login's device fingerprint
        try:
            risk_score, risk_reasons, vpn_info = await assess_registration_risk(
                client_ip, fp_hash, user_agent, db
            )
            await record_device_fingerprint(
                user_id=user.id,
                fingerprint_hash=fp_hash,
                ip_address=client_ip,
                user_agent=user_agent,
                vpn_info=vpn_info,
                risk_score=risk_score,
                screen_resolution=screen_resolution,
                timezone=fp_timezone,
                language=fp_language,
                db=db,
            )
            await log_security_event(
                "login", client_ip,
                user_id=user.id, fingerprint_hash=fp_hash,
                risk_score=risk_score,
                details=f"Login. Risk: {risk_score}. {'; '.join(risk_reasons) if risk_reasons else 'Clean'}",
                db=db,
            )
        except Exception as e:
            logger.warning(f"Security logging failed during login: {e}")

        token = create_access_token(user.id, user.email)
        return {
            "success": True,
            "access_token": token,
            "token_type": "bearer",
            "user_id": user.id,
            "plan": user.plan,
            "api_key": user.api_key,
            "name": user.name,
        }
    else:
        # New user — security checks before creating
        ip_reg_count = await count_free_accounts_by_ip_secured(client_ip, db)
        if ip_reg_count >= 3:
            await log_security_event(
                "registration_blocked", client_ip,
                risk_score=80, details=f"Firebase auth: IP already has {ip_reg_count} accounts",
                db=db,
            )
            raise HTTPException(429, "Too many accounts created from this network. Please upgrade to a paid plan or try again later.")

        risk_score, risk_reasons, vpn_info = await assess_registration_risk(
            client_ip, fp_hash, user_agent, db
        )
        if risk_score >= RISK_THRESHOLD_BLOCK:
            await log_security_event(
                "registration_blocked", client_ip,
                fingerprint_hash=fp_hash, risk_score=risk_score,
                details="; ".join(risk_reasons), db=db,
            )
            raise HTTPException(403, "Registration blocked: suspicious activity detected. If this is an error, contact support@mutyint.com")

        user = User(
            name=name or email.split("@")[0],
            email=email,
            password_hash=None,
            plan="free",
            api_key=generate_api_key(),
        )
        db.add(user)
        await db.flush()
        await db.refresh(user)

        # Record device fingerprint
        await record_device_fingerprint(
            user_id=user.id,
            fingerprint_hash=fp_hash,
            ip_address=client_ip,
            user_agent=user_agent,
            vpn_info=vpn_info,
            risk_score=risk_score,
            screen_resolution=screen_resolution,
            timezone=fp_timezone,
            language=fp_language,
            db=db,
        )
        await log_security_event(
            "register", client_ip,
            user_id=user.id, fingerprint_hash=fp_hash,
            risk_score=risk_score,
            details=f"Firebase registration. Risk: {risk_score}. {'; '.join(risk_reasons) if risk_reasons else 'Clean'}",
            db=db,
        )

        token = create_access_token(user.id, user.email)
        return {
            "success": True,
            "access_token": token,
            "token_type": "bearer",
            "user_id": user.id,
            "plan": user.plan,
            "api_key": user.api_key,
            "name": user.name,
            "message": "Account created via Firebase!",
            "risk_score": risk_score,
            "security_flags": risk_reasons if risk_reasons else [],
        }


@app.post("/auth/generate-key")
async def generate_api_key_endpoint(body: GenerateKeyRequest, db: AsyncSession = Depends(get_db)):
    """Generate or retrieve the API key for a user."""
    result = await db.execute(select(User).where(User.email == body.email))
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(404, "User not found. Please register first.")

    # If user has password, verify it
    if user.password_hash and body.password:
        if not verify_password(body.password, user.password_hash):
            raise HTTPException(401, "Invalid password")

    # Generate new key if none exists
    if not user.api_key:
        user.api_key = generate_api_key()
        await db.flush()
        await db.refresh(user)

    token = create_access_token(user.id, user.email)
    return {
        "api_key": user.api_key,
        "user_id": user.id,
        "plan": user.plan,
        "calls_limit": settings.PLAN_CALL_LIMITS[user.plan],
        "access_token": token,
        "token_type": "bearer",
        "name": user.name,
        "success": True,
    }


@app.get("/auth/me")
async def get_me(current_user: User = Depends(get_current_user_jwt), db: AsyncSession = Depends(get_db)):
    # Count actual API calls this month from the APICall table
    from datetime import timedelta
    month_start = datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    try:
        result = await db.execute(
            select(func.count(APICall.id)).where(
                APICall.user_id == current_user.id,
                APICall.timestamp >= month_start
            )
        )
        calls_used = result.scalar() or 0
    except Exception:
        calls_used = 0
    return {
        "id": current_user.id,
        "name": current_user.name,
        "email": current_user.email,
        "plan": current_user.plan,
        "api_key": current_user.api_key,
        "calls_used": calls_used,
        "calls_limit": settings.PLAN_CALL_LIMITS.get(current_user.plan, 1500),
        "created_at": current_user.created_at.isoformat(),
    }




@app.get("/security/dashboard")
async def security_dashboard(api_key: str, db: AsyncSession = Depends(get_db)):
    """Get security statistics. Requires admin API key."""
    if api_key != settings.STEW_ADMIN_SECRET and api_key != os.environ.get("STEW_ADMIN_SECRET", ""):
        user = await _safe_get_user(api_key, db)
        if not user or user.plan not in ("enterprise",):
            raise HTTPException(403, "Admin access required")
    return await get_security_dashboard(db)


@app.post("/security/fingerprint")
async def submit_fingerprint(body: dict, request: Request, db: AsyncSession = Depends(get_db)):
    """Record device fingerprint for an existing user (called after login)."""
    from server.security import get_client_ip
    api_key = body.get("api_key", "")
    user = await _safe_get_user(api_key, db)
    if not user:
        raise HTTPException(401, "Invalid API key")

    client_ip = get_client_ip(request)
    user_agent = request.headers.get("User-Agent", "")
    fp_hash = compute_fingerprint(
        user_agent=user_agent,
        canvas_hash=body.get("canvas_hash", ""),
        webgl_hash=body.get("webgl_hash", ""),
        screen_resolution=body.get("screen_resolution", ""),
        timezone=body.get("timezone", ""),
        language=body.get("language", ""),
    )

    # Check if this fingerprint already exists for this user
    existing = await db.execute(
        select(DeviceFingerprint).where(
            DeviceFingerprint.user_id == user.id,
            DeviceFingerprint.fingerprint_hash == fp_hash,
        )
    )
    if existing.scalar_one_or_none():
        return {"success": True, "message": "Fingerprint already recorded"}

    vpn_info = await check_vpn_proxy(client_ip)
    risk_score = vpn_info.get("is_vpn", False) and 30 or vpn_info.get("is_proxy", False) and 20 or 0

    await record_device_fingerprint(
        user_id=user.id,
        fingerprint_hash=fp_hash,
        ip_address=client_ip,
        user_agent=user_agent,
        vpn_info=vpn_info,
        risk_score=risk_score,
        screen_resolution=body.get("screen_resolution", ""),
        timezone=body.get("timezone", ""),
        language=body.get("language", ""),
        db=db,
    )
    return {"success": True, "message": "Fingerprint recorded", "risk_score": risk_score}


# ── Chat ───────────────────────────────────────────────────────────────────────


@app.get("/auth/usage")
async def auth_usage(api_key: str, db: AsyncSession = Depends(get_db)):
    """Return the user's current plan, calls used, remaining quota, and device info."""
    user = await get_user_by_api_key(api_key, db)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid API key")

    from datetime import timedelta
    month_start = datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    result = await db.execute(
        select(func.count(APICall.id)).where(
            APICall.user_id == user.id,
            APICall.timestamp >= month_start
        )
    )
    calls_used = result.scalar() or 0
    plan_limit = settings.PLAN_CALL_LIMITS.get(user.plan, 1500)
    calls_remaining = max(0, plan_limit - calls_used)

    # Get latest device fingerprint
    fp_result = await db.execute(
        select(DeviceFingerprint).where(
            DeviceFingerprint.user_id == user.id
        ).order_by(DeviceFingerprint.created_at.desc()).limit(1)
    )
    device = fp_result.scalar_one_or_none()

    return {
        "success": True,
        "plan": user.plan,
        "calls_used": calls_used,
        "calls_limit": plan_limit,
        "calls_remaining": calls_remaining,
        "reset_date": (month_start.replace(month=month_start.month + 1) if month_start.month < 12 else month_start.replace(year=month_start.year + 1, month=1)).isoformat(),
        "device_info": {
            "type": device.device_type if device else "unknown",
            "os": device.os_name if device else "unknown",
            "browser": device.browser_name if device else "unknown",
            "is_vpn": device.is_vpn if device else False,
            "risk_score": device.risk_score if device else 0,
        } if device else None,
    }




@app.post("/auth/regenerate-key")
async def regenerate_api_key(
    body: dict,
    db: AsyncSession = Depends(get_db),
):
    """Regenerate the user's API key. The old key stops working immediately."""
    user = await _safe_get_user(body.get("api_key", ""), db)
    if not user:
        raise HTTPException(401, "Invalid API key")
    
    import secrets
    new_key = "stew_" + secrets.token_urlsafe(32)
    user.api_key = new_key
    await db.commit()
    
    return {"api_key": new_key, "success": True, "message": "API key regenerated. Update your applications with the new key."}



@app.post("/chat")
async def chat(
    body: ChatRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    llm = get_llm_client()
    searcher = get_searcher()

    user = None
    if body.api_key:
        try:
            import asyncio as _asyncio
            user = await _asyncio.wait_for(get_user_by_api_key(body.api_key, db), timeout=5.0)
        except (HTTPException, _asyncio.TimeoutError, Exception):
            user = None  # Invalid/unknown key — treat as anonymous

    # Enforce quota — block calls when limit exceeded
    if user:
        allowed, used, limit = await _check_quota(user, db)
        if not allowed:
            raise HTTPException(
                status_code=429,
                detail=f"API call limit reached ({used}/{limit} this month). Upgrade your plan to continue."
            )

    # Web search grounding
    search_results = None
    sources = []
    web_grounded = False

    msg_lower = body.message.lower()
    should_search = False

    if body.web_search and searcher._is_available():
        # Decide if query needs fresh data — broadened keyword set
        needs_search_keywords = [
            "latest", "current", "today", "news", "score", "price",
            "weather", "stock", "who won", "when is", "what is the",
            "now", "recent", "update", "happened", "2024", "2025", "2026",
            "bitcoin", "crypto", "naira", "dollar", "exchange", "rate",
            "result", "match", "game", "election", "release", "launch",
            "announce", "dead", "born", "happen", "live",
        ]
        should_search = any(kw in msg_lower for kw in needs_search_keywords)
        # Also search if the message looks like a question about real-world facts
        if not should_search and any(q in msg_lower for q in ["who is", "where is", "how much", "how many"]):
            should_search = True
        if should_search:
            try:
                search_results = await asyncio.to_thread(searcher.search, body.message, 5)
                if not search_results.get("grounded"):
                    # Try Stew Browser Extension as fallback
                    logger.info("Serper/SearXNG failed, trying Stew Browser Extension...")
                    search_results = await asyncio.to_thread(searcher.stew_extension_search, body.message, 5)
                if search_results.get("grounded"):
                    web_grounded = True
                    sources = [
                        {"title": r["title"], "url": r["link"], "snippet": r["snippet"]}
                        for r in search_results.get("organic", [])
                    ]
            except Exception as e:
                logger.warning(f"Search failed, trying Stew Browser Extension: {e}")
                try:
                    search_results = await asyncio.to_thread(searcher.stew_extension_search, body.message, 5)
                    if search_results.get("grounded"):
                        web_grounded = True
                        sources = [
                            {"title": r["title"], "url": r["link"], "snippet": r["snippet"]}
                            for r in search_results.get("organic", [])
                        ]
                except Exception as e2:
                    logger.warning(f"All search methods failed: {e2}")

    # Detect explicit research requests
    research_keywords = ["research", "investigate", "deep dive", "analyze this", "study on", "report on", "look into"]
    is_research = any(kw in msg_lower for kw in research_keywords)

    if is_research and not should_search:
        should_search = True

    # For research requests, use the browser extension for deeper results
    if is_research:
        try:
            research_results = await asyncio.to_thread(searcher.stew_extension_research, body.message, 3)
            if research_results.get("grounded") and research_results.get("report"):
                # Use the research report as context
                context = f"[S.T.E.W Research Report for: {body.message}]\n\n{research_results['report']}\n\nSources: {', '.join([s['link'] for s in research_results.get('organic', [])[:5]])}"
                system += f"\n\nRESEARCH CONTEXT (from Stew Browser Extension):\n{context}"
                web_grounded = True
                sources = [
                    {"title": r["title"], "url": r["link"], "snippet": ""}
                    for r in research_results.get("organic", [])
                ]
        except Exception as e:
            logger.warning(f"Research via browser extension failed: {e}")

    # Build messages
    # Build persona-aware system prompt
    settings_obj = get_settings()
    persona = getattr(user, 'persona', 'general') if user else 'general'
    persona_prompts = settings_obj.PERSONA_PROMPTS
    system = persona_prompts.get(persona, persona_prompts['general'])
    
    # Append custom instructions if user has set them
    if user and getattr(user, 'custom_instructions', None):
        style = getattr(user, 'response_style', 'balanced')
        style_note = ""
        if style == "concise":
            style_note = "\n\nRESPONSE STYLE: Be concise and direct. Keep responses short and to the point."
        elif style == "detailed":
            style_note = "\n\nRESPONSE STYLE: Be comprehensive and detailed. Explain thoroughly."
        system = system + f"\n\nUSER CUSTOM INSTRUCTIONS:\n{user.custom_instructions}" + style_note
    
    # Use user's Mistral key if they have one set
    if user and getattr(user, 'mistral_api_key', None) and settings.MISTRAL_API_KEY == "":
        import os as _os
        _os.environ["MISTRAL_API_KEY"] = user.mistral_api_key
    if search_results and web_grounded:
        context = searcher.format_results_for_llm(search_results)
        system += f"\n\nWEB SEARCH CONTEXT (use ONLY this for factual claims):\n{context}"

    if user:
        conv = await get_or_create_conversation(db, user.id, body.conversation_id)
        # Retrieve relevant past memories across all sessions
        recalled = await get_relevant_context(user.id, body.message, platform="api")
        await append_message(db, conv, "user", body.message, platform="api")
        messages = build_llm_messages(conv, system, recalled)
    else:
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": body.message},
        ]

    # ── Multi-Model Fusion (Sakana Fugu-style) ──────────────────────────────
    # When fusion_mode is enabled, dispatch the prompt to multiple LLM providers
    # in parallel, collect independent answers, then synthesize one best-of-all
    # response — richer, more nuanced output than any single model alone.
    if body.fusion_mode and len(llm.fallback_order) >= 2:
        try:
            from server.orchestrator import orchestrate_text
            # Build the user message for fusion
            user_msg = body.message
            if search_results and web_grounded:
                context = searcher.format_results_for_llm(search_results)
                user_msg = f"{body.message}\n\n[Context from web search:]\n{context}"

            fusion_result = await orchestrate_text(
                prompt=user_msg,
                system=system,
                workers=llm.fallback_order[:3],  # up to 3 providers
                temperature=0.7,
            )
            result = {
                "content": fusion_result.get("answer", ""),
                "provider": "stew_fusion",
                "model": "mixture-of-agents",
                "tokens": {"total": sum(r.get("tokens", {}).get("total", 0) for r in fusion_result.get("raw_worker_outputs", []))},
            }
            response_text = clean_response(result["content"])
            tokens = result["tokens"].get("total", 0)
        except Exception as fusion_err:
            logger.warning(f"Fusion failed, falling back to single model: {fusion_err}")
            result = await asyncio.to_thread(llm.chat, messages)
            response_text = clean_response(result["content"])
            tokens = result["tokens"].get("total", 0)
    else:
        result = await asyncio.to_thread(llm.chat, messages)
        response_text = clean_response(result["content"])
        tokens = result["tokens"].get("total", 0)

    if user:
        await append_message(db, conv, "assistant", response_text, platform="api")

    if user:
        background_tasks.add_task(_log_call, db, user.id if user else None, "/chat", "POST", tokens, 200)

    return {
        "response": response_text,
        "web_grounded": web_grounded,
        "sources": sources,
        "provider": "stew_fusion" if body.fusion_mode and len(llm.fallback_order) >= 2 else "stew_engine",
        "model": result.get("model", ""),
        "fusion_workers": [r.get("worker") for r in result.get("raw_worker_outputs", [])] if body.fusion_mode else None,
        "conversation_id": conv.id if user and 'conv' in dir() else None,
        "success": True,
    }


# ── Orchestrator (Fugu-style mixture-of-agents) ─────────────────────────────

class OrchestrateTextRequest(BaseModel):
    prompt: str
    system: Optional[str] = None
    workers: Optional[list[str]] = None
    temperature: float = 0.7


@app.post("/orchestrate/text")
async def orchestrate_text_endpoint(body: OrchestrateTextRequest):
    """
    Mixture-of-agents endpoint (Fugu-style): fans your prompt out to multiple
    LLM workers in parallel (Groq, NVIDIA NIM, OpenRouter, HuggingFace, OpenAI —
    whichever are configured), then synthesizes their independent answers into
    one best-of-all-worlds response through a single call.
    """
    try:
        result = await orchestrate_text(
            body.prompt, system=body.system, workers=body.workers, temperature=body.temperature
        )
        return {"success": True, **result}
    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e))


class OrchestrateImageRequest(BaseModel):
    prompt: str
    mode: str = "first"  # "first" = fastest worker wins, "all" = return every worker's output


@app.post("/orchestrate/image")
async def orchestrate_image_endpoint(body: OrchestrateImageRequest):
    """
    Multi-worker image generation: dispatches your prompt to multiple free
    image-generation models in parallel (pollinations.ai, HuggingFace FLUX,
    more to come) and returns the fastest result, or all of them for comparison.
    """
    try:
        result = await orchestrate_image(body.prompt, mode=body.mode)
        return {"success": True, **result}
    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e))


# ── Image Generation (API-accessible) ────────────────────────────────────────

class GenerateImageRequest(BaseModel):
    prompt: str
    api_key: Optional[str] = None
    width: int = 1024
    height: int = 1024
    model: str = "flux"  # flux | turbo


@app.post("/generate/image")
async def generate_image_endpoint(body: GenerateImageRequest, db: AsyncSession = Depends(get_db)):
    """
    Generate an image from a text prompt.
    Uses pollinations.ai (free, no key required) with FLUX model.
    Downloads and verifies the image, returns as both a direct URL and base64 data URL.
    """
    import httpx
    import urllib.parse
    import base64
    import random

    user = await _safe_get_user(body.api_key, db) if body.api_key else None

    if user:
        allowed, used, limit = await _check_quota(user, db)
        if not allowed:
            raise HTTPException(status_code=429, detail=f"API call limit reached ({used}/{limit} this month). Upgrade your plan to continue.")

    model_map = {"flux": "flux", "turbo": "turbo", "flux-realism": "flux-realism"}
    model_name = model_map.get(body.model, "flux")

    encoded_prompt = urllib.parse.quote(body.prompt, safe='')

    # Try up to 3 times with different seeds — pollinations sometimes returns 0 bytes
    image_bytes = None
    content_type = "image/jpeg"
    final_url = None

    async with httpx.AsyncClient(timeout=60, follow_redirects=True) as http:
        for attempt in range(3):
            seed = random.randint(1, 999999)
            url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?width={body.width}&height={body.height}&model={model_name}&nologo=true&seed={seed}"
            try:
                resp = await http.get(url)
                if resp.status_code == 200 and len(resp.content) > 1000:
                    image_bytes = resp.content
                    content_type = resp.headers.get("content-type", "image/jpeg")
                    final_url = url
                    break
                else:
                    logger.warning(f"Image gen attempt {attempt+1}: status={resp.status_code} size={len(resp.content)} — retrying")
            except Exception as e:
                logger.warning(f"Image gen attempt {attempt+1} error: {e} — retrying")

    if image_bytes is None:
        raise HTTPException(503, "Image generation failed after 3 attempts. The free image service may be overloaded — please try again in a moment.")

    # Convert to base64 data URL for reliable embedding
    b64 = base64.b64encode(image_bytes).decode('utf-8')
    data_url = f"data:{content_type};base64,{b64}"

    # Log the call
    if user:
        from server.database import AsyncSessionLocal
        async with AsyncSessionLocal() as session:
            call = APICall(
                user_id=user.id, endpoint="/generate/image", method="POST", tokens_used=0, status_code=200
            )
            session.add(call)
            await session.commit()

    return {
        "success": True,
        "image_url": final_url,
        "image_data": data_url,
        "prompt": body.prompt,
        "model": model_name,
        "dimensions": f"{body.width}x{body.height}",
        "provider": "pollinations.ai",
        "image_size_bytes": len(image_bytes),
    }


# ── 100-Agent Swarm ────────────────────────────────────────────────────────────

class AgentRunRequest(BaseModel):
    task: str
    api_key: Optional[str] = None
    num_agents: int = 5
    synthesize: bool = True


@app.post("/agents/run")
async def agents_run(body: AgentRunRequest, db: AsyncSession = Depends(get_db)):
    """
    Dispatch a task to S.T.E.W's 100-agent pool.
    Selects num_agents specialists, runs them in parallel, and optionally
    synthesizes their outputs into a single best-of-all-worlds response.
    """
    from agents.agent_pool import AgentPool

    user = await _safe_get_user(body.api_key, db) if body.api_key else None
    if user:
        allowed, used, limit = await _check_quota(user, db)
        if not allowed:
            raise HTTPException(status_code=429, detail=f"API call limit reached ({used}/{limit} this month). Upgrade your plan to continue.")

    pool = AgentPool()

    class BrainAdapter:
        async def call_llm(self, prompt: str, system: str = "", max_tokens: int = 2048) -> str:
            llm = get_llm_client()
            messages = [
                {"role": "system", "content": system or "You are a helpful AI agent."},
                {"role": "user", "content": prompt},
            ]
            try:
                # llm.chat() is a blocking sync call — run it in a thread so
                # asyncio.gather() in agent_pool actually parallelizes agents
                # instead of serializing them on the event loop (was causing
                # /agents/run to time out with >2 agents).
                result = await asyncio.to_thread(llm.chat, messages)
                return result.get("content", "")
            except Exception as e:
                logger.warning(f"Agent brain call failed: {e}")
                return f"Agent could not complete: {e}"

    brain = BrainAdapter()

    try:
        result = await pool.execute_task(
            task=body.task,
            brain=brain,
            num_agents=min(body.num_agents, 10),
            synthesize=body.synthesize,
        )
        return {
            "success": True,
            "task": body.task,
            "agents_used": result.get("agents_used", body.num_agents),
            "results": result.get("agent_results", []),
            "synthesis": result.get("synthesis", ""),
            "execution_time": result.get("execution_time", 0),
        }
    except Exception as e:
        logger.error(f"Agent pool execution failed: {e}")
        raise HTTPException(status_code=503, detail=f"Agent execution failed: {e}")




@app.get("/agents/status")
async def agents_status():
    """Get the status of the 100-agent pool."""
    from agents.agent_pool import AgentPool
    pool = AgentPool()
    return {"success": True, **pool.get_pool_status()}

# ── Task ───────────────────────────────────────────────────────────────────────

@app.post("/task")
async def task(
    body: TaskRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    user = await _safe_get_user(body.api_key, db)
    if user:
        allowed, used, limit = await _check_quota(user, db)
        if not allowed:
            raise HTTPException(status_code=429, detail=f"API call limit reached ({used}/{limit} this month). Upgrade your plan to continue.")

    llm = get_llm_client()
    searcher = get_searcher()

    # Always try to search for task context
    search_context = ""
    sources = []
    web_grounded = False

    if searcher._is_available():
        try:
            sr = await asyncio.to_thread(searcher.search, body.task, 5)
            if sr.get("grounded"):
                search_context = searcher.format_results_for_llm(sr)
                sources = [
                    {"title": r["title"], "url": r["link"]}
                    for r in sr.get("organic", [])
                ]
                web_grounded = True
        except Exception as e:
            logger.warning(f"Task search failed: {e}")

    system = STEW_MASTER_PROMPT
    if search_context:
        system += f"\n\nWEB CONTEXT:\n{search_context}"
    if body.context:
        system += f"\n\nADDITIONAL CONTEXT:\n{body.context}"

    result = llm.chat([
        {"role": "system", "content": system},
        {"role": "user", "content": f"Complete this task:\n{body.task}"},
    ])

    background_tasks.add_task(
        _log_call, db, user.id if user else None, "/task", "POST", result["tokens"].get("total", 0), 200
    )

    return {
        "output": result["content"],
        "web_grounded": web_grounded,
        "sources": sources,
        "provider": result.get("provider"),
        "success": True,
    }




@app.post("/search")
async def search_web(body: dict, background_tasks: BackgroundTasks, db: AsyncSession = Depends(get_db)):
    """Web search endpoint with DuckDuckGo fallback."""
    user = await _safe_get_user(body.get("api_key", ""), db)
    searcher = get_searcher()
    query = body.get("query", "")
    if not query:
        raise HTTPException(400, "Query required")
    try:
        results = await asyncio.to_thread(searcher.search, query, 5)
        if user:
            background_tasks.add_task(_log_call, db, user.id if user else None, "/search", "POST", 0, 200)
        return {"results": results, "success": True}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Search endpoint error: {e}")
        raise HTTPException(500, f"Search failed: {e}")


# ── Browse ─────────────────────────────────────────────────────────────────────

@app.post("/browse/navigate")
async def browse_navigate(
    body: BrowseRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    user = await _safe_get_user(body.api_key, db)
    llm = get_llm_client()

    try:
        from server.browser import StewBrowser
        browser = StewBrowser()
        result = await browser.fetch(body.url)

        visual_analysis = ""
        if body.question and result.get("content"):
            result_llm = await asyncio.to_thread(
                llm.complete,
                f"Page content:\n{result['content']}\n\nQuestion: {body.question}",
                system="You are analyzing a webpage. Answer the question based ONLY on the page content provided.",
            )
            visual_analysis = result_llm

        background_tasks.add_task(_log_call, db, user.id if user else None, "/browse/navigate", "POST", 0, 200)

        return {
            "url": body.url,
            "title": result.get("title", ""),
            "content": result.get("content", ""),
            "visual_analysis": visual_analysis,
            "success": True,
            **{k: v for k, v in result.items() if k not in ("title", "content", "url")},
        }
    except Exception as e:
        logger.error(f"Browse error: {e}")
        raise HTTPException(502, f"Could not fetch URL: {e}")


# ── Document Generation ────────────────────────────────────────────────────────

@app.post("/generate/pdf")
async def gen_pdf(
    body: GeneratePDFRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    user = await _safe_get_user(body.api_key, db)
    if not user:
        raise HTTPException(401, "Valid API key required. Register at /auth/register to get a free key.")
    allowed, used, limit = await _check_quota(user, db)
    if not allowed:
        raise HTTPException(429, f"API call limit reached ({used}/{limit}). Upgrade to continue.")
    result = generate_pdf(body.content, body.title)
    background_tasks.add_task(_log_call, db, user.id, "/generate/pdf", "POST", 0, 200)
    return result


@app.post("/generate/docx")
async def gen_docx(
    body: GenerateDOCXRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    user = await _safe_get_user(body.api_key, db)
    if not user:
        raise HTTPException(401, "Valid API key required. Register at /auth/register to get a free key.")
    allowed, used, limit = await _check_quota(user, db)
    if not allowed:
        raise HTTPException(429, f"API call limit reached ({used}/{limit}). Upgrade to continue.")
    result = generate_docx(body.content, body.title)
    background_tasks.add_task(_log_call, db, user.id, "/generate/docx", "POST", 0, 200)
    return result


@app.post("/generate/xlsx")
async def gen_xlsx(
    body: GenerateXLSXRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    user = await _safe_get_user(body.api_key, db)
    if not user:
        raise HTTPException(401, "Valid API key required. Register at /auth/register to get a free key.")
    allowed, used, limit = await _check_quota(user, db)
    if not allowed:
        raise HTTPException(429, f"API call limit reached ({used}/{limit}). Upgrade to continue.")
    result = generate_xlsx(body.data, body.sheet_name, body.title)
    background_tasks.add_task(_log_call, db, user.id, "/generate/xlsx", "POST", 0, 200)
    return result


@app.post("/generate/pptx")
async def gen_pptx(
    body: GeneratePPTXRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    user = await _safe_get_user(body.api_key, db)
    if not user:
        raise HTTPException(401, "Valid API key required. Register at /auth/register to get a free key.")
    allowed, used, limit = await _check_quota(user, db)
    if not allowed:
        raise HTTPException(429, f"API call limit reached ({used}/{limit}). Upgrade to continue.")
    result = generate_pptx(body.slides, body.title)
    background_tasks.add_task(_log_call, db, user.id, "/generate/pptx", "POST", 0, 200)
    return result


@app.post("/generate/html")
async def gen_html(
    body: GenerateHTMLRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    user = await _safe_get_user(body.api_key, db)
    if not user:
        raise HTTPException(401, "Valid API key required. Register at /auth/register to get a free key.")
    allowed, used, limit = await _check_quota(user, db)
    if not allowed:
        raise HTTPException(429, f"API call limit reached ({used}/{limit}). Upgrade to continue.")
    result = generate_html(body.content, body.title)
    background_tasks.add_task(_log_call, db, user.id, "/generate/html", "POST", 0, 200)
    return result


# ── Document Upload ────────────────────────────────────────────────────────────

@app.post("/upload/document")
async def upload_document(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    question: Optional[str] = Form(None),
    api_key: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    user = await _safe_get_user(api_key, db)
    llm = get_llm_client()

    extracted = await extract_text(file)
    text = extracted["text"]

    answer = ""
    if question and text:
        answer = llm.complete(
            f"Document:\n{text[:8000]}\n\nQuestion: {question}",
            system="Answer the question based on the document. Be concise and accurate.",
        )

    # Save document record
    doc = Document(
        user_id=user.id,
        filename=extracted["filename"],
        file_type=extracted["file_type"],
        content=text[:50000],  # Store up to 50K chars
        file_size=len(text),
    )
    db.add(doc)
    await db.flush()

    background_tasks.add_task(_log_call, db, user.id if user else None, "/upload/document", "POST", 0, 200)

    return {
        "filename": extracted["filename"],
        "file_type": extracted["file_type"],
        "text": text,
        "answer": answer,
        "document_id": doc.id,
        "success": True,
    }


# ── OCR & Vision ──────────────────────────────────────────────────────────────

@app.post("/api/ocr")
async def ocr_endpoint(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    lang: str = Form("eng"),
    include_boxes: bool = Form(False),
    include_confidence: bool = Form(True),
    api_key: str = Form(None),
    db: AsyncSession = Depends(get_db),
):
    """
    Run OCR on an uploaded image or PDF document.

    Supported formats: PNG, JPG, JPEG, WEBP, BMP, TIFF, GIF, PDF

    Returns extracted text, document structure, confidence scores,
    detected language, and word-level bounding boxes (when include_boxes=true).

    Use lang to specify Tesseract language code (e.g. 'eng', 'fra', 'eng+fra').
    Multiple languages can be combined with '+' (e.g. 'eng+fra+deu').
    """
    user = await _safe_get_user(api_key, db) if api_key else None

    if user:
        allowed, used, limit = await _check_quota(user, db)
        if not allowed:
            raise HTTPException(status_code=429, detail=f"API call limit reached ({used}/{limit} this month). Upgrade your plan to continue.")

    content_bytes = await file.read()

    try:
        result = await asyncio.to_thread(
            ocr_file,
            content_bytes,
            file.filename or "upload",
            lang,
            include_boxes,
            include_confidence,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    except RuntimeError as e:
        raise HTTPException(503, str(e))
    except Exception as e:
        logger.error(f"OCR error: {e}", exc_info=True)
        raise HTTPException(500, f"OCR processing failed: {str(e)}")

    # Log the call
    if user:
        background_tasks.add_task(_log_call, db, user.id, "/api/ocr", "POST", 0, 200)

    return {
        "success": True,
        "filename": result["filename"],
        "file_type": result["file_type"],
        "page_count": result["page_count"],
        "text": result["text"],
        "word_count": result["word_count"],
        "char_count": result["char_count"],
        "avg_confidence": result["avg_confidence"],
        "detected_language": result.get("detected_language", "unknown"),
        "lines": result["lines"],
        "paragraphs": result["paragraphs"],
        "pages": result["pages"],
        "words": result.get("words", []),
        "provider": "tesseract",
        "engine": "S.T.E.W OCR v1.0",
    }


@app.post("/api/ocr/analyze")
async def ocr_analyze_endpoint(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    task: str = Form("summarize"),
    question: str = Form(None),
    lang: str = Form("eng"),
    api_key: str = Form(None),
    db: AsyncSession = Depends(get_db),
):
    """
    Run OCR on a file, then use S.T.E.W AI reasoning to analyze the content.

    Tasks:
      - "answer": Answer a specific question about the document (requires question)
      - "summarize": Generate a concise summary
      - "extract": Extract key information (names, dates, amounts, etc.)
      - "analyze": General analysis of the document content

    The OCR text is extracted first, then fed to the S.T.E.W reasoning engine
    for intelligent analysis, Q&A, or summarization.
    """
    user = await _safe_get_user(api_key, db) if api_key else None

    if user:
        allowed, used, limit = await _check_quota(user, db)
        if not allowed:
            raise HTTPException(status_code=429, detail=f"API call limit reached ({used}/{limit} this month). Upgrade your plan to continue.")

    valid_tasks = {"answer", "summarize", "extract", "analyze"}
    if task not in valid_tasks:
        raise HTTPException(400, f"Invalid task '{task}'. Must be one of: {', '.join(sorted(valid_tasks))}")

    if task == "answer" and not question:
        raise HTTPException(400, "The 'question' field is required when task='answer'")

    content_bytes = await file.read()

    try:
        result = await ocr_and_reason(
            content=content_bytes,
            filename=file.filename or "upload",
            question=question or "",
            lang=lang,
            task=task,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    except RuntimeError as e:
        raise HTTPException(503, str(e))
    except Exception as e:
        logger.error(f"OCR analyze error: {e}", exc_info=True)
        raise HTTPException(500, f"OCR analysis failed: {str(e)}")

    if user:
        background_tasks.add_task(_log_call, db, user.id, "/api/ocr/analyze", "POST", 0, 200)

    return result


@app.get("/api/ocr/languages")
async def ocr_languages_endpoint():
    """List all supported OCR languages."""
    return {
        "supported_languages": SUPPORTED_LANGS,
        "total": len(SUPPORTED_LANGS),
        "note": "Combine multiple languages with '+' (e.g. 'eng+fra+deu')",
    }


@app.get("/api/ocr/info")
async def ocr_info_endpoint():
    """Get OCR service information and supported formats."""
    return {
        "service": "S.T.E.W OCR Engine",
        "version": "1.0.0",
        "engine": "Tesseract OCR via pytesseract",
        "pdf_backend": "PyMuPDF (fitz)",
        "supported_formats": ["PNG", "JPG", "JPEG", "WEBP", "BMP", "TIFF", "GIF", "PDF"],
        "max_file_size_mb": 25,
        "max_pdf_pages": 50,
        "features": [
            "Text extraction from images and PDFs",
            "Multi-page PDF processing",
            "Word-level bounding boxes",
            "Confidence scores per word",
            "Automatic language detection",
            "Document structure (lines, paragraphs)",
            "AI-powered analysis (summarize, Q&A, extract, analyze)",
        ],
        "endpoints": {
            "POST /api/ocr": "Extract text from image/PDF",
            "POST /api/ocr/analyze": "OCR + AI reasoning (summarize, answer, extract, analyze)",
            "GET /api/ocr/languages": "List supported languages",
            "GET /api/ocr/info": "This endpoint",
        },
    }


# ── API Proxy ──────────────────────────────────────────────────────────────────

@app.post("/api/call")
async def api_proxy_call(
    body: APICallRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    user = await _safe_get_user(body.api_key, db)

    # Block calls to internal/private IPs
    blocked_prefixes = ("localhost", "127.", "10.", "192.168.", "172.16.", "0.0.0.0")
    if any(body.url.startswith(f"http://{p}") or body.url.startswith(f"https://{p}")
           or body.url.replace("http://", "").replace("https://", "").startswith(p)
           for p in blocked_prefixes):
        raise HTTPException(403, "Calls to internal/private addresses are not allowed")

    method = body.method.upper()
    if method not in ("GET", "POST", "PUT", "PATCH", "DELETE"):
        raise HTTPException(400, f"Unsupported HTTP method: {method}")

    try:
        resp = http_requests.request(
            method=method,
            url=body.url,
            headers=body.headers,
            json=body.body if body.body else None,
            timeout=30,
        )
        background_tasks.add_task(_log_call, db, user.id if user else None, "/api/call", "POST", 0, resp.status_code)
        return {
            "status_code": resp.status_code,
            "body": resp.text,
            "headers": dict(resp.headers),
            "success": resp.ok,
        }
    except http_requests.Timeout:
        raise HTTPException(504, "Request timed out")
    except Exception as e:
        raise HTTPException(502, f"Request failed: {e}")


# ── Payments ───────────────────────────────────────────────────────────────────

@app.post("/payments/initialize")
async def init_payment(
    body: InitPaymentRequest,
    db: AsyncSession = Depends(get_db),
):
    user = await _safe_get_user(body.api_key, db)
    if not user:
        raise HTTPException(401, "Invalid API key")
    if body.plan not in settings.PLAN_PRICES:
        raise HTTPException(400, "Invalid plan")
    if body.plan == "free":
        raise HTTPException(400, "Free plan requires no payment")

    amount_kobo = settings.PLAN_PRICES[body.plan] * 100
    result = initialize_payment(
        email=user.email,
        amount_kobo=amount_kobo,
        plan=body.plan,
        metadata={"user_id": user.id, "plan": body.plan},
    )
    return {**result, "success": True}


@app.post("/payments/verify")
async def verify_payment_endpoint(
    body: VerifyPaymentRequest,
    db: AsyncSession = Depends(get_db),
):
    user = await _safe_get_user(body.api_key, db)
    tx_data = verify_payment(body.reference)

    if tx_data["status"] == "success":
        plan = tx_data.get("metadata", {}).get("plan", "pro")
        await upgrade_user_plan(db, user.id, plan)

        # Record transaction
        t = PaymentTransaction(
            user_id=user.id,
            reference=body.reference,
            plan=plan,
            amount=tx_data["amount"],
            status="success",
        )
        db.add(t)
        await db.flush()

        return {"message": f"Plan upgraded to {plan}", "plan": plan, "success": True}
    else:
        return {"message": "Payment not yet completed", "status": tx_data["status"], "success": False}


@app.post("/payments/webhook")
async def paystack_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    body_bytes = await request.body()
    signature = request.headers.get("x-paystack-signature", "")

    if not validate_webhook_signature(body_bytes, signature):
        raise HTTPException(400, "Invalid webhook signature")

    event = json.loads(body_bytes)
    if event.get("event") == "charge.success":
        data = event["data"]
        metadata = data.get("metadata", {})
        user_id = metadata.get("user_id")
        plan = metadata.get("plan", "pro")

        if user_id:
            await upgrade_user_plan(db, user_id, plan)
            t = PaymentTransaction(
                user_id=user_id,
                reference=data["reference"],
                plan=plan,
                amount=data["amount"],
                status="success",
            )
            db.add(t)
            await db.flush()
            logger.info(f"Webhook: upgraded user {user_id} to {plan}")

    return {"status": "ok"}


@app.get("/payments/callback")
async def payment_callback(request: Request, reference: str = ""):
    """Paystack redirects here after payment. Redirect to dashboard with reference."""
    if not reference:
        return HTMLResponse('<script>window.location.href="/dashboard.html?payment=unknown";</script>')
    return HTMLResponse('<script>window.location.href="/dashboard.html?payment=success&reference=' + reference + '";</script>')


@app.get("/payments/status/{reference}")
async def payment_status(reference: str, api_key: str, db: AsyncSession = Depends(get_db)):
    """Check payment status by reference (for polling)."""
    user = await _safe_get_user(api_key, db)
    if not user:
        raise HTTPException(401, "Invalid API key")
    try:
        tx_data = verify_payment(reference)
        if tx_data["status"] == "success":
            plan = tx_data.get("metadata", {}).get("plan", "pro")
            await upgrade_user_plan(db, user.id, plan)
            t = PaymentTransaction(
                user_id=user.id,
                reference=reference,
                plan=plan,
                amount=tx_data["amount"],
                status="success",
            )
            db.add(t)
            await db.flush()
            return {"success": True, "plan": plan, "status": "success", "message": "Plan upgraded to " + plan}
        return {"success": False, "status": tx_data["status"], "message": "Payment not yet completed"}
    except HTTPException as e:
        return {"success": False, "status": "error", "message": str(e.detail)}


# ── Conversations ──────────────────────────────────────────────────────────────

@app.get("/conversations")
async def list_conversations(current_user: User = Depends(get_current_user_jwt),
                              db: AsyncSession = Depends(get_db)):
    from server.memory import list_conversations as _list
    convs = await _list(db, current_user.id)
    return {
        "conversations": [
            {
                "id": c.id,
                "title": c.title,
                "message_count": len(c.messages or []),
                "created_at": c.created_at.isoformat(),
                "updated_at": c.updated_at.isoformat(),
            }
            for c in convs
        ],
        "success": True,
    }


# ── Error handlers ─────────────────────────────────────────────────────────────



# ── Fine-tune / Persona System ─────────────────────────────────────────────────

PERSONA_OPTIONS = {
    "general": {"label": "General Assistant", "icon": "🤖", "desc": "Powerful all-purpose AI for any task"},
    "doctor": {"label": "Medical Doctor", "icon": "🩺", "desc": "Clinical AI for healthcare professionals & patients"},
    "health": {"label": "Health & Wellness", "icon": "💚", "desc": "Nutrition, fitness, mental wellness & preventive care"},
    "startup": {"label": "Startup Co-founder", "icon": "🚀", "desc": "Strategy, fundraising, product & growth hacking"},
    "legal": {"label": "Legal Assistant", "icon": "⚖️", "desc": "Contracts, compliance, legal research & documentation"},
    "finance": {"label": "Finance Advisor", "icon": "📈", "desc": "Financial modeling, investment analysis & budgeting"},
    "education": {"label": "AI Tutor", "icon": "🎓", "desc": "Learning plans, explanations & educational content"},
    "ecommerce": {"label": "E-Commerce Expert", "icon": "🛒", "desc": "Product listings, marketing copy & store optimization"},
    "developer": {"label": "Software Engineer", "icon": "💻", "desc": "Code review, architecture & production-quality development"},
    "marketing": {"label": "Growth Marketer", "icon": "📣", "desc": "Copywriting, SEO, campaigns & conversion optimization"},
    "hr": {"label": "HR & People Ops", "icon": "👥", "desc": "Job descriptions, interviews, onboarding & culture"},
    "customer_support": {"label": "Customer Support", "icon": "💬", "desc": "Empathetic, efficient customer query resolution"},
}


class FineTuneRequest(BaseModel):
    api_key: str
    persona: Optional[str] = "general"
    custom_instructions: Optional[str] = None
    persona_name: Optional[str] = None
    response_style: Optional[str] = "balanced"  # concise | balanced | detailed
    language: Optional[str] = "en"
    preferred_model: Optional[str] = None
    mistral_api_key: Optional[str] = None


class TestKeyRequest(BaseModel):
    api_key: str
    mistral_api_key: Optional[str] = None


@app.get("/personas")
async def list_personas():
    """List all available persona fine-tune options."""
    return {
        "personas": [
            {"id": k, "label": v["label"], "icon": v["icon"], "desc": v["desc"]}
            for k, v in PERSONA_OPTIONS.items()
        ],
        "total": len(PERSONA_OPTIONS),
    }


@app.post("/finetune")
async def fine_tune_key(body: FineTuneRequest, db: AsyncSession = Depends(get_db)):
    """Fine-tune your S.T.E.W API key for a specific domain/persona."""
    user = await _safe_get_user(body.api_key, db)
    if not user:
        raise HTTPException(401, "Invalid API key")

    updates = {}
    if body.persona and body.persona in PERSONA_OPTIONS:
        updates["persona"] = body.persona
    if body.custom_instructions is not None:
        updates["custom_instructions"] = body.custom_instructions[:2000]  # cap at 2000 chars
    if body.persona_name is not None:
        updates["persona_name"] = body.persona_name[:100]
    if body.response_style in ["concise", "balanced", "detailed"]:
        updates["response_style"] = body.response_style
    if body.language:
        updates["language"] = body.language[:10]
    if body.preferred_model:
        updates["preferred_model"] = body.preferred_model[:50]
    if body.mistral_api_key is not None:
        updates["mistral_api_key"] = body.mistral_api_key

    if updates:
        from sqlalchemy import update as sql_update
        await db.execute(sql_update(User).where(User.id == user.id).values(**updates))
        await db.commit()

    # Re-fetch the user to get updated values
    result = await db.execute(select(User).where(User.id == user.id))
    updated_user = result.scalar_one_or_none()

    return {
        "success": True,
        "message": f"API key fine-tuned to {body.persona or 'general'} persona",
        "settings": {
            "persona": getattr(updated_user, 'persona', 'general'),
            "persona_label": PERSONA_OPTIONS.get(body.persona or 'general', {}).get('label', 'General'),
            "response_style": body.response_style or "balanced",
            "language": body.language or "en",
            "preferred_model": body.preferred_model,
            "custom_instructions_set": bool(body.custom_instructions),
        }
    }


@app.get("/finetune/{api_key}")
async def get_fine_tune_settings(api_key: str, db: AsyncSession = Depends(get_db)):
    """Get current fine-tune settings for an API key."""
    user = await _safe_get_user(api_key, db)
    if not user:
        raise HTTPException(401, "Invalid API key")
    return {
        "success": True,
        "persona": getattr(user, 'persona', 'general'),
        "persona_label": PERSONA_OPTIONS.get(getattr(user, 'persona', 'general'), {}).get('label', 'General'),
        "custom_instructions": getattr(user, 'custom_instructions', None),
        "persona_name": getattr(user, 'persona_name', None),
        "response_style": getattr(user, 'response_style', 'balanced'),
        "language": getattr(user, 'language', 'en'),
        "preferred_model": getattr(user, 'preferred_model', None),
        "has_mistral_key": bool(getattr(user, 'mistral_api_key', None)),
    }


@app.post("/test-key")
async def test_api_key(body: TestKeyRequest, db: AsyncSession = Depends(get_db)):
    """Test that an API key works and optionally verify a Mistral key."""
    user = await _safe_get_user(body.api_key, db)
    if not user:
        raise HTTPException(401, "Invalid or inactive API key")

    result = {
        "valid": True,
        "plan": user.plan,
        "persona": getattr(user, 'persona', 'general'),
        "name": user.name,
        "email": user.email,
        "key_preview": user.api_key[:12] + "..." + user.api_key[-4:],
    }

    # Test Mistral key if provided
    if body.mistral_api_key:
        try:
            from mistralai import Mistral as _Mistral
            _mc = _Mistral(api_key=body.mistral_api_key)
            _resp = _mc.chat.complete(
                model="mistral-small-latest",
                messages=[{"role":"user","content":"reply with the word: ok"}],
            )
            result["mistral_key_valid"] = True
            result["mistral_test_response"] = _resp.choices[0].message.content
        except Exception as e:
            result["mistral_key_valid"] = False
            result["mistral_key_error"] = str(e)[:100]

    return result


@app.exception_handler(404)
async def not_found(request: Request, exc):
    return JSONResponse(
        status_code=404,
        content={"detail": f"Endpoint {request.url.path} not found", "success": False},
    )


@app.exception_handler(500)
async def internal_error(request: Request, exc):
    import traceback as _tb
    logger.error(f"Internal error on {request.url.path}: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"detail": str(exc), "traceback": _tb.format_exc()[-800:], "success": False},
    )


# ── Skills ─────────────────────────────────────────────────────────────────────

class SkillRequest(BaseModel):
    skill: str
    params: dict = {}
    api_key: str


@app.get("/skills")
async def list_available_skills(category: str = ""):
    """List all 60+ S.T.E.W skills."""
    from server.skills_engine import list_skills
    skills = list_skills(category if category else None)
    categories = list(set(s["category"] for s in skills))
    return {"total": len(skills), "categories": sorted(categories), "skills": skills}


@app.post("/skills/run")
async def run_skill_endpoint(body: SkillRequest, db: AsyncSession = Depends(get_db)):
    """Execute any S.T.E.W skill by name."""
    user = await _safe_get_user(body.api_key, db)
    from server.skills_engine import run_skill
    result = await run_skill(body.skill, **body.params)
    return {"skill": body.skill, "result": result, "success": "error" not in result}





@app.post("/browse")
async def browse_url(body: BrowseRequest, db: AsyncSession = Depends(get_db)):
    """Browse any URL and extract content. Uses Serper for search queries, httpx for direct URLs."""
    user = await _safe_get_user(body.api_key, db)
    from server.browser import StewBrowser
    browser = StewBrowser()
    
    url = body.url.strip()
    
    # Detect search queries (Google URLs or plain text queries)
    is_google_search = "google.com/search" in url or "google.com/search?" in url
    is_search_query = not url.startswith("http") or is_google_search
    
    if is_search_query:
        # Extract query from Google URL or use as-is
        if is_google_search:
            from urllib.parse import urlparse, parse_qs
            parsed = urlparse(url)
            params = parse_qs(parsed.query)
            query = params.get("q", [""])[0]
        else:
            query = url  # plain text query
        
        # Use Serper API (real Google results) first
        searcher = get_searcher()
        try:
            search_results = await asyncio.to_thread(searcher.search, query, 8)
            if search_results.get("grounded"):
                organic = search_results.get("organic", [])
                return {
                    "success": True,
                    "question": body.question,
                    "title": f"Search: {query}",
                    "url": url if url.startswith("http") else f"https://google.com/search?q={query}",
                    "content": "\n".join([f"{r['title']}\n{r['link']}\n{r['snippet']}\n" for r in organic]),
                    "links": [{"text": r["title"][:80], "url": r["link"]} for r in organic[:10]],
                    "word_count": sum(len(r.get("snippet", "").split()) for r in organic),
                    "rendered": True,
                    "source": "serper_google_search",
                    "search_results": organic,
                    "answer_box": search_results.get("answer_box", {}),
                    "knowledge_graph": search_results.get("knowledge_graph", {}),
                    "grounded": True,
                }
        except Exception as e:
            logger.warning(f"Browse Serper search failed: {e}")
        
        # Fallback: DuckDuckGo HTML search (no JS needed)
        result = await browser.search_web_fallback(query)
        result["success"] = True
        result["question"] = body.question
        result["source"] = "duckduckgo_fallback"
        return result
    else:
        # Direct URL fetch
        result = await browser.fetch(url)
        # Detect Google JS-required page (httpx can't render JS)
        if "enable" in result.get("title", "").lower() if isinstance(result.get("title"), str) else False:
            # Google returned a JS-required page, try Serper instead
            logger.warning(f"Google returned JS-required page for {url}, trying Serper")
            searcher = get_searcher()
            try:
                search_results = await asyncio.to_thread(searcher.search, url, 8)
                if search_results.get("grounded"):
                    return {
                        "success": True,
                        "question": body.question,
                        "title": f"Search results",
                        "url": url,
                        "content": "\n".join([f"{r['title']}\n{r['link']}\n{r['snippet']}\n" for r in search_results.get("organic", [])]),
                        "links": [{"text": r["title"][:80], "url": r["link"]} for r in search_results.get("organic", [])[:10]],
                        "word_count": sum(len(r.get("snippet", "").split()) for r in search_results.get("organic", [])),
                        "rendered": False,
                        "source": "serper_fallback",
                        "grounded": True,
                    }
            except Exception as e:
                logger.warning(f"Serper fallback failed: {e}")
        return {"success": True, "question": body.question, **result}


# ── Memory Management Endpoints ──────────────────────────────────────────────
@app.get("/memory/{user_id}")
async def get_memory_summary(
    user_id: str,
    api_key: str = Header(None, alias="X-API-Key"),
    db: AsyncSession = Depends(get_db),
):
    """Get summary of stored vector memories for a user."""
    user = await _safe_get_user(api_key, db)
    if not user or user.id != user_id:
        raise HTTPException(403, "Access denied")
    from server.vector_memory import get_user_memory_summary
    return get_user_memory_summary(user_id)


@app.delete("/memory/{user_id}")
async def clear_memory(
    user_id: str,
    api_key: str = Header(None, alias="X-API-Key"),
    db: AsyncSession = Depends(get_db),
):
    """Clear all vector memories for a user."""
    user = await _safe_get_user(api_key, db)
    if not user or user.id != user_id:
        raise HTTPException(403, "Access denied")
    from server.vector_memory import clear_user_memories
    count = clear_user_memories(user_id)
    return {"cleared": count, "message": f"Cleared {count} memories"}


@app.post("/memory/search")
async def search_memory(
    body: dict,
    api_key: str = Header(None, alias="X-API-Key"),
    db: AsyncSession = Depends(get_db),
):
    """Search past memories semantically."""
    user = await _safe_get_user(api_key, db)
    if not user:
        raise HTTPException(401, "Invalid API key")
    from server.vector_memory import recall_relevant, format_memories_for_prompt
    query = body.get("query", "")
    platform = body.get("platform")
    n = body.get("n_results", 8)
    memories = recall_relevant(user.id, query, platform=platform, n_results=n)
    return {
        "query": query,
        "memories": memories,
        "formatted": format_memories_for_prompt(memories),
        "count": len(memories),
    }


# ── Deep Research Endpoint ──────────────────────────────────────────────────
class ResearchRequest(BaseModel):
    query: str
    depth: int = 3  # 1=quick, 2=standard, 3=deep
    api_key: str = Header(None, alias="X-API-Key")

@app.post("/research")
async def deep_research(
    body: ResearchRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Deep research endpoint — searches multiple sources, fetches page content,
    and synthesizes a comprehensive answer with citations.
    Returns ACTUAL research output, not instructions.
    """
    user = await _safe_get_user(body.api_key, db)
    if not user:
        raise HTTPException(401, "Invalid API key")

    if user.plan == "free":
        # Check API quota
        allowed, used, limit = await _check_quota(user, db)
        if not allowed:
            raise HTTPException(429, "Daily free-tier limit reached (500 calls)")

    searcher = get_searcher()
    llm = get_llm_client()

    # Step 1: Multi-query search
    queries = [body.query]
    if body.depth >= 2:
        # Generate sub-queries for broader coverage
        sub_query_prompt = [
            {"role": "system", "content": "Generate 2-3 alternative search queries for this research topic. Return ONLY the queries, one per line, no numbering or explanation."},
            {"role": "user", "content": body.query},
        ]
        try:
            sub_result = llm.chat(sub_query_prompt)
            sub_queries = [q.strip() for q in sub_result["content"].strip().split("\n") if q.strip()][:3]
            queries.extend(sub_queries)
        except Exception:
            pass

    all_sources = []
    all_snippets = []
    search_raw = []

    for q in queries[:4]:
        try:
            results = await asyncio.to_thread(searcher.search, q, 5)
            if results.get("grounded"):
                for r in results.get("organic", []):
                    if r not in all_sources:
                        all_sources.append(r)
                        all_snippets.append(f"[{r['title']}]({r['link']}): {r['snippet']}")
                search_raw.append(results)
        except Exception as e:
            logger.warning(f"Research search error for query '{q}': {e}")

    # Step 2: Fetch top pages for deeper content (depth >= 2)
    page_contents = []
    if body.depth >= 2 and all_sources:
        for source in all_sources[:5]:
            try:
                page = await asyncio.to_thread(searcher.fetch_page_content, source["link"], 3000)
                if page.get("content"):
                    page_contents.append({
                        "title": source["title"],
                        "url": source["link"],
                        "content": page["content"],
                        "extractor": page.get("extractor", "unknown"),
                    })
            except Exception as e:
                logger.warning(f"Failed to fetch {source['link']}: {e}")

    # Step 3: Synthesize with LLM
    context_parts = []
    context_parts.append(f"[Search Results for: {body.query}]")
    for i, s in enumerate(all_snippets[:15], 1):
        context_parts.append(f"{i}. {s}")

    if page_contents:
        context_parts.append("\n[Deep page content:]")
        for pc in page_contents:
            context_parts.append(f"\n--- {pc['title']} ({pc['url']}) ---\n{pc['content'][:2000]}")

    full_context = "\n".join(context_parts)

    research_prompt = f"""You are S.T.E.W Research Agent. Conduct thorough research on: "{body.query}"

Use the web search results and page content below. Synthesize a comprehensive, well-structured research report.

Requirements:
- Start with a direct answer / summary
- Include key findings with citations [source name]
- Note any conflicting information
- Include data points, statistics, and specific numbers where available
- End with a "Sources" section listing all URLs
- Be factual — only use information from the provided search results
- If information is insufficient, say what's missing
- DO NOT use ## or ### markdown headers — use plain text section titles
- DO NOT use **bold** or *italic* markers — use plain text
- Use numbered lists (1. 2. 3.) for structured content
- Keep output clean, professional, and readable on any platform

SEARCH CONTEXT:
{full_context}
"""

    try:
        research_messages = [{"role": "system", "content": "You are S.T.E.W Research Agent. Produce a comprehensive research report with citations. Use clean plain text — NO ## markdown headers, NO **bold** markers. Use numbered lists and plain section titles."}, {"role": "user", "content": research_prompt}]
        result = llm.chat(research_messages)
        report = clean_response(result["content"])
    except Exception as e:
        raise HTTPException(500, f"Research synthesis failed: {e}")

    # Store the research in memory
    try:
        from server.vector_memory import store_memory
        store_memory(
            user_id=user.id,
            role="user",
            content=f"Research request: {body.query}",
            platform="api",
        )
        store_memory(
            user_id=user.id,
            role="assistant",
            content=report[:2000],
            platform="api",
        )
    except Exception:
        pass

    return {
        "query": body.query,
        "depth": body.depth,
        "report": report,
        "sources": [{"title": s["title"], "url": s["link"]} for s in all_sources],
        "pages_fetched": len(page_contents),
        "queries_used": queries,
        "grounded": len(all_sources) > 0,
        "provider": "stew_research",
    }



# ── Telegram Webhook ───────────────────────────────────────────────────────────

# ── Telegram Webhook ───────────────────────────────────────────────────────────


@app.get("/search/test")
async def search_test():
    """Test if web search is working. Returns provider status."""
    try:
        searcher = get_searcher()
        result = await asyncio.to_thread(searcher.search, "test query python", 3)
        organic = result.get("organic", [])
        return {
            "success": True,
            "grounded": result.get("grounded", False),
            "source": result.get("source", "unknown"),
            "num_results": len(organic),
            "first_result": organic[0] if organic else None,
            "has_allorigins": "duckduckgo_proxy" in str(result.get("source", "")),
        }
    except Exception as e:
        return {"success": False, "error": str(e)[:300]}

@app.post("/telegram/webhook")
async def telegram_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    """Receive Telegram messages and reply via S.T.E.W."""
    if not settings.TELEGRAM_BOT_TOKEN:
        raise HTTPException(503, "Telegram bot not configured")

    data = await request.json()
    from server.telegram_bot import TelegramBot
    bot = TelegramBot(settings.TELEGRAM_BOT_TOKEN)
    msg = bot.parse_update(data)

    if not msg or not msg["text"] or msg["is_bot"]:
        return {"ok": True}

    chat_id = msg["chat_id"]
    user_text = msg["text"]
    username = msg.get("username") or msg.get("first_name", "User")
    user_lower = user_text.lower()

    # Show typing
    await bot.send_typing(chat_id)

    # Get or create a stew user for this telegram user (with retry for SQLite locks)
    tg_email = f"tg_{msg['user_id']}@telegram.stew"
    tg_user = None
    for _attempt in range(3):
        try:
            result_q = await db.execute(select(User).where(User.email == tg_email))
            tg_user = result_q.scalar_one_or_none()
            if not tg_user:
                from server.auth import generate_api_key
                tg_user = User(
                    name=username, email=tg_email,
                    plan="free", api_key=generate_api_key()
                )
                db.add(tg_user)
                await db.flush()
                await db.refresh(tg_user)
            break
        except Exception as db_err:
            logger.warning(f"Telegram DB attempt {_attempt+1} failed: {db_err}")
            await db.rollback()
            if _attempt == 2:
                await bot.send_message(chat_id, "I'm experiencing high traffic. Please try again in a moment.")
                return {"ok": True}
            await asyncio.sleep(0.5)

    # Handle /start command
    if user_text.startswith("/start"):
        welcome = (
            f"Hello {username}! I'm S.T.E.W — Smart Thinking Executive Worker.\n\n"
            "I can:\n"
            "1. Search the web (real Google results)\n"
            "2. Generate images (just say 'generate image of...')\n"
            "3. Create PDF, Word, Excel, PowerPoint documents\n"
            "4. Write and review code\n"
            "5. Browse any URL and summarize it\n"
            "6. Analyze data\n\n"
            f"Your API key: {tg_user.api_key}\n\n"
            "Just send me any message or question to get started!"
        )
        await bot.send_message(chat_id, welcome)
        return {"ok": True}

    # Handle /help command
    if user_text.startswith("/help"):
        help_text = (
            "S.T.E.W Commands:\n\n"
            "1. Search: just ask any question\n"
            "2. Generate image: 'generate image of a sunset over Lagos'\n"
            "3. Create PDF: 'make a PDF about business plan for bakery'\n"
            "4. Create Word: 'create a Word document about marketing strategy'\n"
            "5. Create Excel: 'make a spreadsheet of monthly expenses'\n"
            "6. Create PowerPoint: 'create a presentation about AI trends'\n"
            "7. Browse URL: 'browse https://example.com'\n"
            "8. Research: 'research the latest AI news'\n\n"
            "Just talk to me naturally — I understand what you need!"
        )
        await bot.send_message(chat_id, help_text)
        return {"ok": True}

    # ── IMAGE GENERATION ──────────────────────────────────────────────────────
    image_keywords = ["generate image", "create image", "draw", "make an image",
                      "generate an image", "create an image", "make a picture",
                      "generate a picture", "create a picture", "ai image",
                      "image of", "picture of"]
    is_image_request = any(kw in user_lower for kw in image_keywords)

    if is_image_request:
        # Extract the actual prompt from the message
        image_prompt = user_text
        for kw in image_keywords:
            if kw in user_lower:
                idx = user_lower.index(kw) + len(kw)
                image_prompt = user_text[idx:].strip()
                break
        if not image_prompt or len(image_prompt) < 3:
            image_prompt = user_text  # fallback to full text

        await bot.send_message(chat_id, f"Generating image: {image_prompt[:100]}...")
        await bot.send_chat_action(chat_id, "upload_photo")

        try:
            import httpx as _httpx
            import urllib.parse as _urlparse
            import random as _random
            encoded = _urlparse.quote(image_prompt, safe='')
            image_bytes = None
            async with _httpx.AsyncClient(timeout=90, follow_redirects=True) as http:
                for attempt in range(3):
                    seed = _random.randint(1, 999999)
                    url = f"https://image.pollinations.ai/prompt/{encoded}?width=1024&height=1024&model=flux&nologo=true&seed={seed}"
                    try:
                        resp = await http.get(url)
                        if resp.status_code == 200 and len(resp.content) > 2000:
                            image_bytes = resp.content
                            break
                        else:
                            logger.warning(f"TG image gen attempt {attempt+1}: status={resp.status_code} size={len(resp.content)}")
                    except Exception as e:
                        logger.warning(f"TG image gen attempt {attempt+1} error: {e}")

            if image_bytes:
                await bot.send_photo(chat_id, image_bytes,
                    caption=f"Generated by S.T.E.W\nPrompt: {image_prompt[:200]}")
            else:
                await bot.send_message(chat_id, "Sorry, image generation failed after 3 attempts. The free image service may be busy. Please try again in a moment.")
        except Exception as e:
            logger.error(f"Telegram image generation error: {e}")
            await bot.send_message(chat_id, f"Image generation error: {str(e)[:200]}")
        return {"ok": True}

    # ── DOCUMENT GENERATION ───────────────────────────────────────────────────
    doc_keywords = {
        "pdf": ["make a pdf", "create a pdf", "generate a pdf", "make pdf", "create pdf",
                "generate pdf", "pdf of", "pdf about", "pdf for", "convert to pdf"],
        "docx": ["make a word", "create a word", "generate a word", "make word",
                 "create word", "generate word", "word document", "word doc",
                 "docx of", "docx about", "make a docx", "create a docx"],
        "xlsx": ["make a spreadsheet", "create a spreadsheet", "generate a spreadsheet",
                 "make an excel", "create an excel", "generate an excel",
                 "make excel", "create excel", "xlsx of", "make a xlsx",
                 "create a xlsx", "spreadsheet of"],
        "pptx": ["make a powerpoint", "create a powerpoint", "generate a powerpoint",
                 "make a presentation", "create a presentation", "generate a presentation",
                 "make presentation", "create presentation", "pptx of",
                 "slides about", "slides for"],
    }

    doc_type = None
    doc_topic = ""
    for dtype, keywords in doc_keywords.items():
        for kw in keywords:
            if kw in user_lower:
                doc_type = dtype
                idx = user_lower.index(kw) + len(kw)
                doc_topic = user_text[idx:].strip().rstrip(".")
                if not doc_topic:
                    # Try extracting "about X" or "of X" pattern
                    for prefix in ["about ", "of ", "for ", "on "]:
                        if user_lower.startswith(prefix):
                            doc_topic = user_text[len(prefix):].strip()
                break
        if doc_type:
            break

    if doc_type:
        if not doc_topic or len(doc_topic) < 3:
            doc_topic = user_text  # fallback

        await bot.send_message(chat_id, f"Creating {doc_type.upper()} about: {doc_topic[:100]}...")
        await bot.send_chat_action(chat_id, "upload_document")

        try:
            llm = get_llm_client()
            # Generate content with LLM first
            if doc_type == "xlsx":
                # For spreadsheets, ask LLM for structured data
                system_prompt = "You are a data analyst. Generate structured spreadsheet data as a JSON array of objects. Return ONLY valid JSON, no explanation."
                user_msg = f"Create spreadsheet data about: {doc_topic}. Return an array of 5-15 row objects with appropriate column names. Return ONLY the JSON array."
                messages = [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_msg},
                ]
                result = await asyncio.to_thread(llm.chat, messages)
                content = clean_response(result["content"])

                # Parse JSON from response
                import json as _json
                import re as _re
                json_match = _re.search(r'\[.*\]', content, _re.DOTALL)
                if json_match:
                    try:
                        data = _json.loads(json_match.group())
                    except:
                        data = [{"Info": "Could not parse data", "Topic": doc_topic}]
                else:
                    data = [{"Topic": doc_topic, "Status": "Generated by S.T.E.W", "Date": datetime.now().strftime("%Y-%m-%d")}]

                doc_result = generate_xlsx(data, "Sheet1", doc_topic)
            elif doc_type == "pptx":
                # For presentations, ask LLM for slide structure
                system_prompt = "You are a presentation designer. Generate slides as a JSON array. Each slide has 'title' and 'content' fields. Return ONLY valid JSON."
                user_msg = f"Create a 5-8 slide presentation about: {doc_topic}. Return as JSON array of slides with title and content fields."
                messages = [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_msg},
                ]
                result = await asyncio.to_thread(llm.chat, messages)
                content = clean_response(result["content"])

                import json as _json
                import re as _re
                json_match = _re.search(r'\[.*\]', content, _re.DOTALL)
                if json_match:
                    try:
                        slides = _json.loads(json_match.group())
                    except:
                        slides = [{"title": doc_topic, "content": "Generated by S.T.E.W"}]
                else:
                    slides = [{"title": doc_topic, "content": "Generated by S.T.E.W Agent"}]

                doc_result = generate_pptx(slides, doc_topic)
            else:
                # For PDF and DOCX, generate text content
                system_prompt = f"You are a professional writer. Create a well-structured, detailed document about: {doc_topic}. Use markdown formatting with # for headings, ## for subheadings, - for bullet points. Make it comprehensive and professional."
                user_msg = f"Write a complete, detailed document about: {doc_topic}. Include an introduction, main sections with headings, and a conclusion."
                messages = [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_msg},
                ]
                result = await asyncio.to_thread(llm.chat, messages)
                content = clean_response(result["content"])

                if doc_type == "pdf":
                    doc_result = generate_pdf(content, doc_topic)
                elif doc_type == "docx":
                    doc_result = generate_docx(content, doc_topic)
                else:
                    doc_result = generate_html(content, doc_topic)

            # Decode base64 and send the file
            if doc_result.get("success") and doc_result.get("file"):
                import base64 as _b64
                file_bytes = _b64.b64decode(doc_result["file"])
                filename = doc_result.get("filename", f"stew_{doc_type}_{datetime.now().strftime('%Y%m%d')}.{doc_type}")
                caption = f"S.T.E.W generated {doc_type.upper()}\nTopic: {doc_topic[:200]}"
                await bot.send_document(chat_id, file_bytes, filename, caption)
            else:
                await bot.send_message(chat_id, f"Failed to generate {doc_type.upper()}. Please try again.")
        except Exception as e:
            logger.error(f"Telegram document generation error: {e}")
            await bot.send_message(chat_id, f"Document generation error: {str(e)[:200]}")
        return {"ok": True}

    # ── BROWSE URL REQUEST ─────────────────────────────────────────────────────
    browse_keywords = ["browse ", "open ", "read ", "visit ", "summarize this url", "check this site"]
    is_browse_request = any(user_lower.startswith(kw) for kw in browse_keywords) and "http" in user_lower

    if is_browse_request:
        # Extract URL
        import re as _re
        url_match = _re.search(r'https?://[^\s]+', user_text)
        if url_match:
            target_url = url_match.group()
            await bot.send_message(chat_id, f"Reading: {target_url}")
            await bot.send_typing(chat_id)
            try:
                from server.browser import get_browser
                browser = get_browser()
                page = await browser.fetch(target_url, timeout=25)
                if page.get("content"):
                    # Summarize the page
                    llm = get_llm_client()
                    system = STEW_MASTER_PROMPT + "\n\nYou are responding via Telegram. Summarize the browsed page concisely for mobile."
                    page_content = page.get("content", "")[:6000]
                    page_title = page.get("title", "Unknown")
                    messages = [
                        {"role": "system", "content": system},
                        {"role": "user", "content": f"Summarize this page:\n\nTitle: {page_title}\nURL: {target_url}\n\nContent:\n{page_content}"},
                    ]
                    result = await asyncio.to_thread(llm.chat, messages)
                    reply = clean_response(result["content"])
                    await bot.send_message(chat_id, f"Page: {page_title}\n\n{reply}")
                else:
                    await bot.send_message(chat_id, f"Could not read {target_url}. The site may be blocking automated access.")
            except Exception as e:
                logger.error(f"Telegram browse error: {e}")
                await bot.send_message(chat_id, f"Error browsing: {str(e)[:200]}")
            return {"ok": True}

    # ── REGULAR CHAT WITH SEARCH + RESEARCH ────────────────────────────────────
    llm = get_llm_client()
    searcher = get_searcher()

    web_grounded = False
    system = STEW_MASTER_PROMPT + "\n\nYou are responding via Telegram. Keep answers concise and well-formatted for mobile. Use plain text, avoid complex markdown."

    # Detect if search is needed
    needs_search = any(kw in user_lower for kw in [
        "latest", "current", "today", "news", "score", "price",
        "weather", "stock", "search", "find", "what is", "who is",
        "best", "top", "how to", "when", "where", "which", "compare",
        "happened", "update", "recent", "2024", "2025", "2026",
        "naira", "dollar", "bitcoin", "crypto", "exchange rate",
    ])
    # Detect research requests
    tg_research_kw = ["research", "investigate", "look into", "report on", "study", "analyze", "deep dive"]
    needs_research = any(kw in user_lower for kw in tg_research_kw)

    if needs_research:
        await bot.send_message(chat_id, f"Starting deep research on: {user_text[:100]}")
        await bot.send_typing(chat_id)
        try:
            await bot.send_message(chat_id, "Searching multiple sources...")
            await bot.send_typing(chat_id)
            research_results = await asyncio.to_thread(searcher.stew_extension_research, user_text, 3)
            if research_results.get("grounded") and research_results.get("report"):
                num_sources = len(research_results.get("organic", []))
                await bot.send_message(chat_id, f"Found {num_sources} sources. Analyzing content...")
                await bot.send_typing(chat_id)
                pages = research_results.get("pages", [])
                if pages:
                    for i, page in enumerate(pages[:3], 1):
                        title = page.get("title", "Source")[:60]
                        await bot.send_message(chat_id, f"Reading source {i}/{min(len(pages),3)}: {title}")
                        await bot.send_typing(chat_id)
                system += f"\n\nRESEARCH CONTEXT:\n{research_results['report']}"
                web_grounded = True
                await bot.send_message(chat_id, "Generating comprehensive response...")
                await bot.send_typing(chat_id)
        except Exception as e:
            logger.warning(f"Telegram research failed: {e}")
            await bot.send_message(chat_id, "Research encountered an issue, proceeding with available data...")
            await bot.send_typing(chat_id)
    elif needs_search:
        await bot.send_message(chat_id, f"Searching the web for: {user_text[:80]}")
        await bot.send_typing(chat_id)
        try:
            search_results = await asyncio.to_thread(searcher.search, user_text, 5)
            if not search_results.get("grounded"):
                await bot.send_message(chat_id, "Trying alternative search...")
                await bot.send_typing(chat_id)
                search_results = await asyncio.to_thread(searcher.stew_extension_search, user_text, 5)
            if search_results.get("grounded"):
                num_results = len(search_results.get("organic", []))
                await bot.send_message(chat_id, f"Found {num_results} results. Analyzing...")
                await bot.send_typing(chat_id)
                context = searcher.format_results_for_llm(search_results)
                system += f"\n\nWEB SEARCH CONTEXT:\n{context}"
                web_grounded = True
                await bot.send_message(chat_id, "Generating response...")
                await bot.send_typing(chat_id)
        except Exception as e:
            logger.warning(f"Telegram search failed: {e}")
            try:
                search_results = await asyncio.to_thread(searcher.stew_extension_search, user_text, 5)
                if search_results.get("grounded"):
                    context = searcher.format_results_for_llm(search_results)
                    system += f"\n\nWEB SEARCH CONTEXT:\n{context}"
                    web_grounded = True
            except Exception as e2:
                logger.warning(f"Telegram browser extension search failed: {e2}")

    # Reuse the most recent conversation for this Telegram user
    from sqlalchemy import select as _sel
    _conv_q = await db.execute(
        _sel(Conversation).where(Conversation.user_id == tg_user.id)
        .order_by(Conversation.updated_at.desc()).limit(1)
    )
    _existing_conv = _conv_q.scalar_one_or_none()
    conv = _existing_conv if _existing_conv else await get_or_create_conversation(db, tg_user.id, None)

    recalled_tg = await get_relevant_context(tg_user.id, user_text, platform="telegram")
    await append_message(db, conv, "user", user_text, platform="telegram")
    messages = build_llm_messages(conv, system, recalled_tg)

    try:
        result = await asyncio.to_thread(llm.chat, messages)
        reply = clean_response(result["content"])
        await append_message(db, conv, "assistant", reply, platform="telegram")
        await bot.send_message(chat_id, reply, parse_mode="")
    except Exception as e:
        logger.error(f"Telegram LLM error: {e}")
        await bot.send_message(chat_id, "I encountered an error. Please try again in a moment.")

    return {"ok": True}


@app.get("/telegram/status")
async def telegram_status():
    """Check if Telegram bot is configured and get bot info."""
    if not settings.TELEGRAM_BOT_TOKEN:
        return {
            "configured": False,
            "error": "TELEGRAM_BOT_TOKEN not set",
            "instructions": "Add TELEGRAM_BOT_TOKEN to your environment variables",
            "how_to_get_token": "Message @BotFather on Telegram, type /newbot, follow steps",
            "success": False
        }
    try:
        from server.telegram_bot import TelegramBot
        bot = TelegramBot(settings.TELEGRAM_BOT_TOKEN)
        info = await bot.get_me()
        if info.get("ok"):
            b = info["result"]
            return {
                "configured": True,
                "bot_name": b.get("first_name"),
                "bot_username": f"@{b.get('username')}",
                "bot_id": b.get("id"),
                "direct_link": f"https://t.me/{b.get('username')}",
                "success": True
            }
        return {"configured": False, "error": info.get("description"), "success": False}
    except Exception as e:
        return {"configured": False, "error": str(e), "success": False}


@app.post("/telegram/setup")
async def setup_telegram(request: Request):
    """Register your deployment URL as the Telegram webhook."""
    if not settings.TELEGRAM_BOT_TOKEN:
        raise HTTPException(503, "Set TELEGRAM_BOT_TOKEN environment variable first")
    data = await request.json()
    webhook_url = data.get("webhook_url")
    if not webhook_url:
        raise HTTPException(400, "webhook_url required")
    from server.telegram_bot import TelegramBot
    bot = TelegramBot(settings.TELEGRAM_BOT_TOKEN)
    info = await bot.get_me()
    result = await bot.set_webhook(webhook_url + "/telegram/webhook")
    return {"bot": info, "webhook_result": result, "success": True}


# ── Password Reset ─────────────────────────────────────────────────────────────

class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str


@app.post("/auth/forgot-password")
async def forgot_password(body: ForgotPasswordRequest, db: AsyncSession = Depends(get_db)):
    """
    Request a password reset link. Always returns 200 to prevent email enumeration.
    Sends reset email if account exists.
    """
    import asyncio
    result = await db.execute(select(User).where(User.email == body.email))
    user = result.scalar_one_or_none()
    if user and user.is_active:
        token = create_reset_token(user.id)
        asyncio.create_task(send_password_reset_email(user.email, user.name, token))
    # Always return 200 — never reveal if email exists
    return {
        "success": True,
        "message": "If that email is registered, a reset link has been sent.",
    }


@app.post("/auth/reset-password")
async def reset_password(body: ResetPasswordRequest, db: AsyncSession = Depends(get_db)):
    """Reset password using a token from the reset email."""
    import asyncio
    if len(body.new_password) < 8:
        raise HTTPException(400, "Password must be at least 8 characters")

    user_id = consume_reset_token(body.token)
    if not user_id:
        raise HTTPException(400, "Reset link is invalid or has expired. Please request a new one.")

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(404, "User not found")

    user.password_hash = hash_password(body.new_password)
    await db.commit()

    # Send confirmation email
    asyncio.create_task(send_password_changed_email(user.email, user.name))

    return {"success": True, "message": "Password changed successfully. You can now log in."}


@app.get("/auth/verify-reset-token")
async def verify_reset_token(token: str):
    """Check if a reset token is still valid (used by frontend before showing reset form)."""
    from server.auth import validate_reset_token
    user_id = validate_reset_token(token)
    if not user_id:
        raise HTTPException(400, "Token is invalid or expired")
    return {"valid": True}


# ── Integrations / Third-Party API Proxy ──────────────────────────────────────

class IntegrationRequest(BaseModel):
    api_key: str
    service: str          # e.g. "openai", "stripe", "custom"
    endpoint: str         # full URL
    method: str = "POST"
    headers: dict = {}
    payload: dict = {}


@app.post("/integrations/call")
async def integration_call(body: IntegrationRequest, db: AsyncSession = Depends(get_db)):
    """
    Proxy any external API call through S.T.E.W.
    Useful for integrating Stripe, SendGrid, Twilio, etc.
    """
    user = await _safe_get_user(body.api_key, db)
    import httpx
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            method = body.method.upper()
            if method == "GET":
                resp = await client.get(body.endpoint, headers=body.headers, params=body.payload)
            elif method == "POST":
                resp = await client.post(body.endpoint, headers=body.headers, json=body.payload)
            elif method == "PUT":
                resp = await client.put(body.endpoint, headers=body.headers, json=body.payload)
            elif method == "DELETE":
                resp = await client.delete(body.endpoint, headers=body.headers)
            else:
                raise HTTPException(400, f"Unsupported method: {method}")

            ct = resp.headers.get("content-type", "")
            body_data = resp.json() if "json" in ct else resp.text
            return {
                "success": resp.status_code < 400,
                "status": resp.status_code,
                "service": body.service,
                "response": body_data,
            }
    except httpx.TimeoutException:
        raise HTTPException(504, f"Timeout calling {body.service}")
    except Exception as e:
        raise HTTPException(500, str(e))

