"""
S.T.E.W — Structured Task Execution Workflow
FastAPI Backend v5.0
"""
import json
import logging
import os
import re
import requests as http_requests
import httpx
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import (
    Depends, FastAPI, File, Form, HTTPException, Header,
    Request, UploadFile, BackgroundTasks
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse, HTMLResponse, PlainTextResponse
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
from server.video_tools import clip_video, create_video, smart_clips, generate_ai_video, generate_ai_video_with_narration
from server.webbuilder import build_motion_website
from server.persistent_memory import (
    is_configured as supabase_configured,
    save_memory as supa_save_memory,
    recall_memories as supa_recall,
    search_memories as supa_search,
    delete_memory as supa_delete,
    clear_all_memories as supa_clear,
    save_conversation as supa_save_conv,
    get_conversation_history as supa_get_conv,
    upload_file as supa_upload_file,
)
from server.document_generator import (
    generate_docx, generate_html, generate_pdf, generate_pptx, generate_xlsx,
)
from server.document_processor import extract_text
from server.llm_client import get_llm_client
from server.orchestrator import orchestrate_text, orchestrate_image
from server.memory import (
    append_message, build_llm_messages, get_or_create_conversation, get_relevant_context,
    store_user_memory, get_user_memories, search_user_memories, extract_and_store_memories,
)
from server.middleware import RateLimitMiddleware, SecurityHeadersMiddleware
from server.models import APICall, Conversation, DeviceFingerprint, Document, PaymentTransaction, SecurityEvent, User, UserMemory, FeatureRequest, AdCampaign, GeneratedWebsite
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

# In-memory dedup for Telegram webhook retries (Telegram re-sends the same
# update if our response is slow — this was causing duplicate bot replies,
# e.g. weather answers appearing to "repeat" with different fake numbers).
from collections import deque as _deque
_TG_SEEN_UPDATES = _deque(maxlen=500)
_TG_SEEN_SET = set()

def _tg_already_processed(update_id) -> bool:
    global _TG_SEEN_SET
    if update_id is None:
        return False
    if update_id in _TG_SEEN_SET:
        return True
    if len(_TG_SEEN_UPDATES) == _TG_SEEN_UPDATES.maxlen:
        # deque is about to evict its oldest item on next append — drop it from the set too
        _TG_SEEN_SET.discard(_TG_SEEN_UPDATES[0])
    _TG_SEEN_UPDATES.append(update_id)
    _TG_SEEN_SET.add(update_id)
    return False

from server.system_prompt import STEW_MASTER_PROMPT
from server.clean_output import clean_response
from server.email_service import send_welcome_email, send_password_reset_email, send_password_changed_email
from server.auth import create_reset_token, consume_reset_token
from server.keepalive import start_keepalive, stop_keepalive
from server.bot_stats import start_bot_stats, stop_bot_stats
from server.skills_engine import run_skill, list_skills as get_skills_list



# ── Lifespan ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("S.T.E.W API v5.0 starting up…")
    await init_db()
    os.makedirs("logs", exist_ok=True)
    os.makedirs("output", exist_ok=True)
    start_keepalive()
    start_bot_stats()

    # Register bot commands on Telegram (includes new /webbuild, /meme, /caption)
    try:
        import httpx as _httpx
        _cmds = [
            {"command": "start", "description": "Start using Stew Agent"},
            {"command": "menu", "description": "Show all commands and features"},
            {"command": "help", "description": "Get help and usage info"},
            {"command": "upgrade", "description": "Upgrade your plan (Student, Pro, Business)"},
            {"command": "usage", "description": "Check your usage and quota"},
            {"command": "plan", "description": "View pricing plans"},
            {"command": "voice", "description": "Toggle voice note replies"},
            {"command": "clip", "description": "Clip a video segment from URL"},
            {"command": "smartclip", "description": "AI smart clips with captions"},
            {"command": "createvideo", "description": "AI video with images + voiceover"},
            {"command": "aivideo", "description": "REAL AI video from text (LTX-Video)"},
            {"command": "aivideos", "description": "Multi-scene AI video with narration"},
            {"command": "webbuild", "description": "Build a motion-design website (Kimi style)"},
            {"command": "meme", "description": "Generate an AI meme image"},
            {"command": "caption", "description": "Generate viral social media captions"},
            {"command": "feature", "description": "Request a new feature"},
            {"command": "features", "description": "View feature requests"},
            {"command": "vote", "description": "Vote for a feature request"},
            {"command": "sponsor", "description": "Sponsor an ad on Stew"},
            {"command": "admin", "description": "Admin access (owner only)"},
        ]
        async with _httpx.AsyncClient(timeout=10) as _client:
            await _client.post(
                f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/setMyCommands",
                json={"commands": _cmds},
            )
        logger.info(f"Registered {len(_cmds)} Telegram bot commands")
    except Exception as _cmd_err:
        logger.warning(f"Failed to register bot commands: {_cmd_err}")

    yield
    stop_keepalive()
    stop_bot_stats()
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


def _plan_tier(plan: str) -> int:
    """Numeric rank of a plan: free=0, student=1, pro=2, business=3, enterprise=4, owner=5.
    Use for feature gating (e.g. video scene counts, webbuild access) that scales
    smoothly with plan level instead of a flat free-vs-paid check."""
    return settings.PLAN_TIER_ORDER.get(plan, 0)


def _tiered_limit(plan: str, by_tier: dict) -> int:
    """Pick a limit from a {tier_rank: value} map for the user's plan tier,
    falling back to the highest defined tier at or below the user's rank."""
    tier = _plan_tier(plan)
    available = sorted(by_tier.keys())
    chosen = available[0]
    for t in available:
        if tier >= t:
            chosen = t
    return by_tier[chosen]


async def _get_telegram_user_count(db: AsyncSession) -> int:
    """Count all registered Telegram users (for the live user counter shown in
    /start, /menu, /users, and the bot's public profile description)."""
    result = await db.execute(
        select(func.count(User.id)).where(User.email.like("tg_%@telegram.stew"))
    )
    return result.scalar() or 0




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
    theme: str = ""
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


@app.get("/site/{site_id}", response_class=HTMLResponse, include_in_schema=False)
async def serve_generated_website(site_id: str, db: AsyncSession = Depends(get_db)):
    """Serve a /webbuild-generated motion-design website — publicly viewable, no auth needed."""
    result = await db.execute(select(GeneratedWebsite).where(GeneratedWebsite.id == site_id))
    site = result.scalars().first()
    if not site:
        return HTMLResponse(
            content="<html><body style='font-family:sans-serif;text-align:center;padding:80px;"
                    "background:#0a0a0f;color:#fff;'><h1>404</h1><p>This site doesn't exist or was removed."
                    "</p><p style='opacity:0.6'>Built with S.T.E.W — Telegram: @StewAgent_bot</p></body></html>",
            status_code=404,
        )
    try:
        site.views = (site.views or 0) + 1
        await db.commit()
    except Exception:
        pass
    return HTMLResponse(content=site.html)


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
    return PlainTextResponse("""User-agent: *
Allow: /
Allow: /llms.txt
Allow: /llms-full.txt
Allow: /docs
Allow: /faq
Allow: /.well-known/ai-plugin.json
Allow: /.well-known/ai-manifest.json
Disallow: /v1/
Disallow: /dashboard
Disallow: /playground
Sitemap: https://stew-agent.onrender.com/sitemap.xml
LLM-Sitemap: https://stew-agent.onrender.com/llms.txt""")

@app.get("/sitemap.xml", include_in_schema=False)
async def sitemap():
    import os
    p = os.path.join(os.path.dirname(__file__), "..", "sitemap.xml")
    if os.path.exists(p):
        return FileResponse(p, media_type="application/xml")
    return PlainTextResponse("<?xml version=\"1.0\"?><urlset xmlns=\"http://www.sitemaps.org/schemas/sitemap/0.9\"><url><loc>https://stew-agent.onrender.com/</loc></url></urlset>")


# ── AI Discovery Endpoints ────────────────────────────────────────────────────

@app.get("/llms.txt", include_in_schema=False)
async def llms_txt():
    """llms.txt v2 - AI-friendly docs for LLM agents (ChatGPT, Gemini, Perplexity, Claude)."""
    content = """# Stew Agent (S.T.E.W)

> Stew Agent (S.T.E.W — Smart Thinking Executive Worker) is an AI agent API and Telegram bot built for the African market. Multi-model LLM access (Groq, OpenRouter, NVIDIA, OpenAI), 60+ skills, 100-agent swarm, document generation (PDF/DOCX/XLSX/PPTX), OCR, vision, Python code sandbox, web search, Telegram bot with tool-calling, Naira billing via Paystack. OpenAI-compatible at /v1/chat/completions. Best AI API for African developers, students, professionals, bankers, churches.

## Key Facts
- Base URL: https://stew-agent.onrender.com
- OpenAI-compatible: /v1/chat/completions
- Free tier: 1,500 API calls/month
- 6 AI providers with auto-failover
- 60+ skills, 12 personas, 100-agent swarm
- Telegram bot: @StewAgent_bot
- Built by MUTYINT Nigeria

## API Docs
- [Swagger/OpenAPI](https://stew-agent.onrender.com/docs)
- [Full API Reference](https://stew-agent.onrender.com/llms-full.txt)
- [Register Free](https://stew-agent.onrender.com/auth/register)

## Quick Start
```bash
curl https://stew-agent.onrender.com/chat -H 'Content-Type: application/json' -d '{"api_key":"YOUR_KEY","message":"Hello!"}'
```

## Core Endpoints
- POST /chat - Chat with web search grounding
- POST /v1/chat/completions - OpenAI-compatible
- POST /search - Web search (Serper + SearXNG)
- POST /generate/pdf - Generate PDF
- POST /generate/docx - Generate Word doc
- POST /generate/xlsx - Generate Excel
- POST /generate/pptx - Generate PowerPoint
- POST /api/ocr - OCR text extraction
- POST /api/code/exec - Python code sandbox
- POST /agents/run - 100-agent swarm
- GET /skills - List all skills
- GET /heartbeat - Health check
"""
    return PlainTextResponse(content, media_type="text/plain")


@app.get("/llms-full.txt", include_in_schema=False)
async def llms_full_txt():
    """Full API reference for AI agents."""
    content = """# S.T.E.W Agent - Complete API Reference

> Production AI agent API for African developers. OpenAI-compatible, multi-provider, Naira-billed. By MUTYINT Nigeria.

## Authentication
Header: Authorization: Bearer YOUR_API_KEY
Get free key: https://stew-agent.onrender.com/auth/register
Free: 1,500 calls/mo. Pro: 15,000 Naira/mo. Business: 50,000 Naira/mo.

## Endpoints
POST /chat - Main chat with web search
POST /v1/chat/completions - OpenAI drop-in
POST /search - Web search
POST /browse - Read any webpage
POST /generate/pdf - PDF generation
POST /generate/docx - Word document
POST /generate/xlsx - Excel spreadsheet
POST /generate/pptx - PowerPoint slides
POST /generate/image - Image generation
POST /api/ocr - OCR (17+ languages)
POST /api/ocr/analyze - OCR + AI analysis
POST /api/code/exec - Python sandbox
POST /agents/run - 100-agent swarm
POST /task - Multi-step task
POST /research - Deep research
GET /skills - All skills
GET /personas - AI personas
GET /heartbeat - Health check

## Code Sandbox
POST /api/code/exec - Python in sandbox
Modules: math, json, re, datetime, statistics, matplotlib, numpy, pandas
Timeout: 10s. No network. No file system.

## Telegram
Bot: @StewAgent_bot
Features: search, docs, OCR, code, tool-calling

## Links
Docs: https://stew-agent.onrender.com/docs
Register: https://stew-agent.onrender.com/auth/register
GitHub: https://github.com/emmanuelerogian723-alt/Stew-agent
"""
    return PlainTextResponse(content, media_type="text/plain")


@app.get("/.well-known/ai-plugin.json", include_in_schema=False)
async def ai_plugin_manifest():
    """OpenAI plugin manifest for ChatGPT discovery."""
    return JSONResponse({
        "schema_version": "v1",
        "name_for_human": "S.T.E.W Agent",
        "name_for_model": "stew_agent",
        "description_for_human": "Africa's #1 AI agent API. Web search, document generation (PDF/Word/Excel/PowerPoint), Python code execution, OCR, real-time data with Naira billing.",
        "description_for_model": "Use S.T.E.W Agent to search the web, generate PDF/DOCX/XLSX/PPTX documents, run Python code for math and data analysis, perform OCR, get crypto/stock prices, and run 100-agent research tasks. Base URL: https://stew-agent.onrender.com. Auth: Bearer token.",
        "url": "https://stew-agent.onrender.com",
        "contact_email": "support@mutyint.com",
        "legal_info_url": "https://stew-agent.onrender.com/docs",
        "api": {
            "type": "openapi",
            "url": "https://stew-agent.onrender.com/openapi.json",
            "is_user_authenticated": False,
            "authentication": {
                "type": "bearer_http",
                "authorization_type": "bearer",
                "instruction": "Get free API key at https://stew-agent.onrender.com/auth/register"
            }
        }
    })


@app.get("/.well-known/ai-manifest.json", include_in_schema=False)
async def ai_manifest():
    """IETF AI Manifest draft - capabilities declaration for AI agents."""
    return JSONResponse({
        "spec": "ai-manifest/v1",
        "name": "S.T.E.W Agent",
        "version": "6.0.0",
        "url": "https://stew-agent.onrender.com",
        "description": "AI agent API for African developers. OpenAI-compatible, multi-provider, Naira-billed.",
        "provider": {"name": "MUTYINT Nigeria", "url": "https://mutyint.com"},
        "capabilities": [
            {"name": "chat", "endpoint": "/chat", "method": "POST"},
            {"name": "web_search", "endpoint": "/search", "method": "POST"},
            {"name": "generate_pdf", "endpoint": "/generate/pdf", "method": "POST"},
            {"name": "generate_pptx", "endpoint": "/generate/pptx", "method": "POST"},
            {"name": "ocr", "endpoint": "/api/ocr", "method": "POST"},
            {"name": "code_execution", "endpoint": "/api/code/exec", "method": "POST"},
            {"name": "agent_swarm", "endpoint": "/agents/run", "method": "POST"}
        ],
        "authentication": {"type": "bearer", "registration_url": "/auth/register"},
        "pricing": {"free": "1,500 calls", "pro": "15,000 Naira/mo", "business": "50,000 Naira/mo"},
        "docs": "https://stew-agent.onrender.com/docs",
        "openapi": "https://stew-agent.onrender.com/openapi.json",
        "llms_txt": "https://stew-agent.onrender.com/llms.txt"
    })


@app.get("/.well-known/security.txt", include_in_schema=False)
async def security_txt():
    """Security contact information."""
    return PlainTextResponse(
        "Contact: mailto:support@mutyint.com\nExpires: 2027-12-31T23:59:59Z\nPreferred-Languages: en",
        media_type="text/plain"
    )

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

@app.get("/admin/debug")
async def admin_debug(api_key: str, db: AsyncSession = Depends(get_db)):
    """Admin debug endpoint — check user state, quota, and DB health."""
    if api_key != settings.STEW_ADMIN_SECRET and api_key != os.environ.get("STEW_ADMIN_SECRET", ""):
        raise HTTPException(403, "Admin access required")

    from sqlalchemy import inspect, text as sql_text

    # DB type check
    db_url = settings.DATABASE_URL
    db_type = "postgresql" if "postgresql" in db_url or "postgres" in db_url else "sqlite"

    # Total users
    total_users = await db.execute(select(func.count(User.id)))
    total_users = total_users.scalar() or 0

    # Telegram users
    tg_users = await db.execute(select(func.count(User.id)).where(User.email.like("tg_%@telegram.stew")))
    tg_users = tg_users.scalar() or 0

    # Users by plan
    plan_result = await db.execute(select(User.plan, func.count(User.id)).group_by(User.plan))
    plans = {row[0]: row[1] for row in plan_result}

    # Emmanuel's account specifically
    emmanuel = await db.execute(select(User).where(User.email == "tg_5547996257@telegram.stew"))
    emmanuel_row = emmanuel.scalars().first()

    # API calls this month
    month_start = datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    calls_result = await db.execute(select(func.count(APICall.id)).where(APICall.timestamp >= month_start))
    calls_this_month = calls_result.scalar() or 0

    # Feature requests count
    try:
        fr_result = await db.execute(select(func.count(FeatureRequest.id)))
        feature_requests = fr_result.scalar() or 0
    except:
        feature_requests = -1

    # Ad campaigns count
    try:
        ad_result = await db.execute(select(func.count(AdCampaign.id)))
        ad_campaigns = ad_result.scalar() or 0
    except:
        ad_campaigns = -1

    return {
        "db_type": db_type,
        "db_url_preview": db_url[:30] + "..." if len(db_url) > 30 else db_url,
        "total_users": total_users,
        "telegram_users": tg_users,
        "users_by_plan": plans,
        "emmanuel": {
            "found": emmanuel_row is not None,
            "email": emmanuel_row.email if emmanuel_row else None,
            "plan": emmanuel_row.plan if emmanuel_row else None,
            "preferred_voice": getattr(emmanuel_row, "preferred_voice", None) if emmanuel_row else None,
        },
        "api_calls_this_month": calls_this_month,
        "feature_requests": feature_requests,
        "ad_campaigns": ad_campaigns,
        "plan_limits": settings.PLAN_CALL_LIMITS,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/features/requests")
async def list_feature_requests(api_key: str, status: str = "pending", db: AsyncSession = Depends(get_db)):
    """Admin endpoint — list all feature requests. Requires admin API key."""
    if api_key != settings.STEW_ADMIN_SECRET and api_key != os.environ.get("STEW_ADMIN_SECRET", ""):
        user = await _safe_get_user(api_key, db)
        if not user or user.plan not in ("owner", "enterprise"):
            raise HTTPException(403, "Admin access required")

    result = await db.execute(
        select(FeatureRequest)
        .where(FeatureRequest.status == status)
        .order_by(FeatureRequest.votes.desc(), FeatureRequest.created_at.desc())
        .limit(100)
    )
    features = result.scalars().all()
    return {
        "count": len(features),
        "features": [
            {
                "id": f.id,
                "feature_text": f.feature_text,
                "category": f.category,
                "votes": f.votes,
                "status": f.status,
                "created_at": f.created_at.isoformat() if f.created_at else None,
                "telegram_user_id": f.telegram_user_id,
            }
            for f in features
        ],
    }


@app.post("/features/{feature_id}/status")
async def update_feature_status(feature_id: str, body: dict, db: AsyncSession = Depends(get_db)):
    """Admin endpoint — update a feature request's status."""
    api_key = body.get("api_key", "")
    if api_key != settings.STEW_ADMIN_SECRET and api_key != os.environ.get("STEW_ADMIN_SECRET", ""):
        user = await _safe_get_user(api_key, db)
        if not user or user.plan not in ("owner", "enterprise"):
            raise HTTPException(403, "Admin access required")

    result = await db.execute(select(FeatureRequest).where(FeatureRequest.id == feature_id))
    fr = result.scalar_one_or_none()
    if not fr:
        raise HTTPException(404, "Feature request not found")

    fr.status = body.get("status", fr.status)
    await db.flush()
    return {"success": True, "feature_id": feature_id, "status": fr.status}


@app.get("/ads/active")
async def list_active_ads(db: AsyncSession = Depends(get_db)):
    """Public endpoint — list all active ad campaigns."""
    result = await db.execute(select(AdCampaign).where(AdCampaign.status == "active").limit(50))
    ads = result.scalars().all()
    return {"count": len(ads), "ads": [{"id": a.id, "advertiser_name": a.advertiser_name, "ad_text": a.ad_text, "ad_link": a.ad_link, "button_text": a.button_text, "impressions": a.impressions, "clicks": a.clicks, "budget_impressions": a.budget_impressions, "target_audience": a.target_audience} for a in ads]}


@app.post("/ads/create")
async def create_ad_campaign(body: dict, db: AsyncSession = Depends(get_db)):
    """Admin endpoint — create a new ad campaign. Requires admin API key."""
    api_key = body.get("api_key", "")
    if api_key != settings.STEW_ADMIN_SECRET and api_key != os.environ.get("STEW_ADMIN_SECRET", ""):
        user = await _safe_get_user(api_key, db)
        if not user or user.plan not in ("owner", "enterprise"):
            raise HTTPException(403, "Admin access required")
    ad = AdCampaign(
        advertiser_name=body.get("advertiser_name", "Unknown"),
        ad_text=body.get("ad_text", ""),
        ad_link=body.get("ad_link"),
        button_text=body.get("button_text", "Learn More"),
        target_audience=body.get("target_audience", "free"),
        frequency=body.get("frequency", 5),
        budget_impressions=body.get("budget_impressions", 10000),
        end_date=body.get("end_date"),
    )
    db.add(ad)
    await db.flush()
    await db.commit()
    return {"success": True, "ad_id": ad.id, "message": f"Ad campaign created for {ad.advertiser_name}"}


@app.post("/ads/{ad_id}/click")
async def track_ad_click(ad_id: str, db: AsyncSession = Depends(get_db)):
    """Track when a user clicks an ad."""
    result = await db.execute(select(AdCampaign).where(AdCampaign.id == ad_id))
    ad = result.scalar_one_or_none()
    if not ad:
        raise HTTPException(404, "Ad not found")
    ad.clicks += 1
    await db.flush()
    await db.commit()
    return {"success": True, "clicks": ad.clicks}


@app.post("/ads/{ad_id}/status")
async def update_ad_status(ad_id: str, body: dict, db: AsyncSession = Depends(get_db)):
    """Admin endpoint — pause/resume/end an ad campaign."""
    api_key = body.get("api_key", "")
    if api_key != settings.STEW_ADMIN_SECRET and api_key != os.environ.get("STEW_ADMIN_SECRET", ""):
        user = await _safe_get_user(api_key, db)
        if not user or user.plan not in ("owner", "enterprise"):
            raise HTTPException(403, "Admin access required")
    result = await db.execute(select(AdCampaign).where(AdCampaign.id == ad_id))
    ad = result.scalar_one_or_none()
    if not ad:
        raise HTTPException(404, "Ad not found")
    ad.status = body.get("status", ad.status)
    await db.flush()
    await db.commit()
    return {"success": True, "ad_id": ad_id, "status": ad.status}


@app.get("/ads/analytics")
async def ad_analytics(api_key: str, db: AsyncSession = Depends(get_db)):
    """Admin endpoint — view ad performance analytics."""
    if api_key != settings.STEW_ADMIN_SECRET and api_key != os.environ.get("STEW_ADMIN_SECRET", ""):
        user = await _safe_get_user(api_key, db)
        if not user or user.plan not in ("owner", "enterprise"):
            raise HTTPException(403, "Admin access required")
    result = await db.execute(select(AdCampaign))
    ads = result.scalars().all()
    total_impressions = sum(a.impressions for a in ads)
    total_clicks = sum(a.clicks for a in ads)
    ctr = (total_clicks / total_impressions * 100) if total_impressions > 0 else 0
    return {"total_campaigns": len(ads), "active_campaigns": sum(1 for a in ads if a.status == "active"), "total_impressions": total_impressions, "total_clicks": total_clicks, "ctr": round(ctr, 2), "campaigns": [{"advertiser": a.advertiser_name, "impressions": a.impressions, "clicks": a.clicks, "ctr": round(a.clicks / a.impressions * 100, 2) if a.impressions > 0 else 0, "budget_used": f"{a.impressions}/{a.budget_impressions}", "status": a.status} for a in ads]}


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
        recalled = await get_relevant_context(db, user.id, body.message, platform="api")
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

        # Background memory extraction for API users too
        try:
            def _sync_llm_chat_api(messages, max_tokens=1000):
                llm_a = get_llm_client()
                return llm_a.chat(messages, max_tokens=max_tokens)
            background_tasks.add_task(
                extract_and_store_memories,
                db, user.id, body.message, response_text, "api", conv.id, _sync_llm_chat_api
            )
        except Exception:
            pass  # memory extraction is best-effort

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


@app.get("/generate/themes")
async def list_slide_themes():
    """List all 46+ available slide themes."""
    from server.slide_themes import THEMES
    themes_list = []
    for name, t in THEMES.items():
        themes_list.append({
            "name": name,
            "category": t["category"],
            "bg_type": t["bg_type"],
            "layout": t["layout"],
        })
    return {
        "total_themes": len(THEMES),
        "themes": themes_list,
    }


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
    result = generate_pptx(body.slides, body.title, body.theme if body.theme else None)
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



# ── Code Execution Sandbox ────────────────────────────────────────────────────

@app.post("/api/code/exec")
async def code_exec_endpoint(
    body: dict,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """
    Execute Python code in a restricted sandbox.
    
    Safety: No network, no file system, limited built-ins, 10-second timeout.
    Allowed modules: math, json, re, datetime, statistics, matplotlib, numpy, pandas
    """
    user = await _safe_get_user(body.get("api_key", ""), db)
    if user:
        allowed, used, limit = await _check_quota(user, db)
        if not allowed:
            raise HTTPException(status_code=429, detail=f"API call limit reached ({used}/{limit} this month).")

    code = body.get("code", "")
    if not code:
        raise HTTPException(400, "Code is required")

    from server.code_sandbox import execute_code
    timeout = min(int(body.get("timeout", 10)), 30)

    result = await asyncio.to_thread(execute_code, code, timeout)

    if user:
        background_tasks.add_task(_log_call, db, user.id, "/api/code/exec", "POST", 0, 200)

    return {
        "success": result.get("success"),
        "stdout": result.get("stdout", ""),
        "result": result.get("result", ""),
        "error": result.get("error"),
        "figures": result.get("figures", []),
        "execution_time": result.get("execution_time", 0),
        "engine": "S.T.E.W Code Sandbox v1.0",
    }


@app.get("/api/code/info")
async def code_info_endpoint():
    """Get code sandbox information."""
    from server.code_sandbox import ALLOWED_MODULES, OPTIONAL_MODULES
    return {
        "service": "S.T.E.W Code Execution Sandbox",
        "version": "1.0.0",
        "allowed_modules": sorted(ALLOWED_MODULES),
        "optional_modules": sorted(OPTIONAL_MODULES.keys()),
        "timeout_seconds": 10,
        "max_output_bytes": 50000,
        "features": [
            "Python code execution in restricted sandbox",
            "Math, statistics, data analysis",
            "Matplotlib chart generation (PNG)",
            "NumPy for numerical computing",
            "Pandas for data manipulation",
            "Stdout capture + expression result",
            "No network access, no file system access",
        ],
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

# Audio/voice file extensions Telegram may send as a "document" instead of "voice"/"audio"
_AUDIO_EXTENSIONS = {"mp3", "wav", "m4a", "ogg", "oga", "opus", "flac", "aac", "wma", "aiff", "amr"}

# Telegram Bot API cannot download files larger than 20MB via getFile
_TELEGRAM_MAX_DOWNLOAD_BYTES = 20 * 1024 * 1024


async def _transcribe_audio_bytes(file_bytes: bytes, file_name: str = "audio.ogg") -> tuple[str, str]:
    """Transcribe audio bytes via Groq Whisper (falls back to OpenAI Whisper).
    Returns (transcript, error_message) — error_message is "" on success.
    Handles voice notes, songs, and any audio file Telegram passes through,
    regardless of whether it arrived as message.voice, message.audio, or message.document.
    """
    import os as _os
    import tempfile

    ext = file_name.rsplit(".", 1)[-1].lower() if "." in file_name else "ogg"
    if ext not in _AUDIO_EXTENSIONS:
        ext = "ogg"
    mime_map = {
        "mp3": "audio/mpeg", "wav": "audio/wav", "m4a": "audio/mp4",
        "ogg": "audio/ogg", "oga": "audio/ogg", "opus": "audio/ogg",
        "flac": "audio/flac", "aac": "audio/aac", "wma": "audio/x-ms-wma",
        "aiff": "audio/aiff", "amr": "audio/amr",
    }
    mime = mime_map.get(ext, "audio/ogg")

    groq_key = _os.getenv("GROQ_API_KEY", "")
    openai_key = _os.getenv("OPENAI_API_KEY", "")
    transcript = ""
    last_error = ""

    with tempfile.NamedTemporaryFile(suffix=f".{ext}", delete=False) as tmp:
        tmp.write(file_bytes)
        tmp_path = tmp.name

    try:
        if groq_key:
            try:
                with open(tmp_path, "rb") as audio_file:
                    resp = http_requests.post(
                        "https://api.groq.com/openai/v1/audio/transcriptions",
                        headers={"Authorization": f"Bearer {groq_key}"},
                        files={"file": (f"audio.{ext}", audio_file, mime)},
                        data={"model": "whisper-large-v3-turbo"},
                        timeout=90,
                    )
                if resp.status_code == 200:
                    transcript = resp.json().get("text", "").strip()
                else:
                    last_error = f"Groq Whisper error {resp.status_code}: {resp.text[:200]}"
                    logger.warning(last_error)
            except Exception as e:
                last_error = f"Groq Whisper exception: {e}"
                logger.warning(last_error)

        if not transcript and openai_key and not openai_key.startswith("sk-stew"):
            try:
                with open(tmp_path, "rb") as audio_file:
                    resp = http_requests.post(
                        "https://api.openai.com/v1/audio/transcriptions",
                        headers={"Authorization": f"Bearer {openai_key}"},
                        files={"file": (f"audio.{ext}", audio_file, mime)},
                        data={"model": "whisper-1"},
                        timeout=90,
                    )
                if resp.status_code == 200:
                    transcript = resp.json().get("text", "").strip()
                else:
                    last_error = f"OpenAI Whisper error {resp.status_code}: {resp.text[:200]}"
                    logger.warning(last_error)
            except Exception as e:
                last_error = f"OpenAI Whisper exception: {e}"
                logger.warning(last_error)

        # Fallback #3: Hugging Face Inference API (openai/whisper-large-v3)
        # Free tier, works even when Groq/OpenAI keys are missing or invalid.
        hf_key = _os.getenv("HUGGINGFACE_API_KEY", "")
        if not transcript and hf_key:
            try:
                with open(tmp_path, "rb") as audio_file:
                    audio_data = audio_file.read()
                resp = http_requests.post(
                    "https://api-inference.huggingface.co/models/openai/whisper-large-v3",
                    headers={"Authorization": f"Bearer {hf_key}", "Content-Type": mime},
                    data=audio_data,
                    timeout=90,
                )
                if resp.status_code == 200:
                    hf_result = resp.json()
                    transcript = (hf_result.get("text") or "").strip() if isinstance(hf_result, dict) else ""
                    if transcript:
                        last_error = ""
                else:
                    last_error = f"HF Whisper error {resp.status_code}: {resp.text[:200]}"
                    logger.warning(last_error)
            except Exception as e:
                last_error = f"HF Whisper exception: {e}"
                logger.warning(last_error)
    finally:
        _os.unlink(tmp_path)

    if not transcript and not last_error:
        last_error = "No transcription provider configured (missing GROQ_API_KEY/OPENAI_API_KEY)."
    return transcript, ("" if transcript else last_error)



# ─── Voice Synthesis (edge-tts) ─────────────────────────────────────────────────
VOICE_OPTIONS = {
    "aria": ("en-US-AriaNeural", "Aria — warm female (US English)"),
    "jenny": ("en-US-JennyNeural", "Jenny — friendly female (US English)"),
    "guy": ("en-US-GuyNeural", "Guy — confident male (US English)"),
    "davis": ("en-US-DavisNeural", "Davis — calm male (US English)"),
    "emma": ("en-US-EmmaNeural", "Emma — gentle female (US English)"),
    "british_f": ("en-GB-LibbyNeural", "Libby — British female"),
    "british_m": ("en-GB-RyanNeural", "Ryan — British male"),
    "nigeria": ("en-NG-EzinneNeural", "Ezinne — Nigerian female"),
    "nigeria_m": ("en-NG-AbeoNeural", "Abeo — Nigerian male"),
    "french_f": ("fr-FR-DeniseNeural", "Denise — French female"),
    "spanish": ("es-ES-ElviraNeural", "Elvira — Spanish female"),
    "hindi": ("hi-IN-SwaraNeural", "Swara — Hindi female"),
    "arabic": ("ar-SA-ZariyahNeural", "Zariyah — Arabic female"),
}

async def _synthesize_voice(text: str, voice: str = "en-US-AriaNeural") -> tuple[bytes, str]:
    """Synthesize speech via edge-tts (free, no API key). Returns (mp3_bytes, error)."""
    import asyncio as _aio
    try:
        import edge_tts
    except ImportError:
        return b"", "edge-tts not installed"
    
    # Truncate very long text to avoid timeout (edge-tts handles ~3000 chars well)
    if len(text) > 3000:
        text = text[:3000] + "..."
    
    try:
        communicate = edge_tts.Communicate(text, voice)
        audio_chunks = []
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                audio_chunks.append(chunk["data"])
        if audio_chunks:
            return b"".join(audio_chunks), ""
        return b"", "No audio generated"
    except Exception as e:
        return b"", f"edge-tts error: {e}"


@app.post("/telegram/webhook")
async def telegram_webhook(request: Request):
    """Receive Telegram messages. ACKs Telegram INSTANTLY, then processes the
    update in a detached background task with its own DB session. This is
    critical: if we do OCR/LLM/transcription work BEFORE responding, a Render
    cold start or a slow AI provider can push us past Telegram's webhook
    delivery timeout — Telegram then RETRIES the same update, which caused
    duplicate replies (the 'goes quiet then repeats the answer' bug)."""
    if not settings.TELEGRAM_BOT_TOKEN:
        raise HTTPException(503, "Telegram bot not configured")

    try:
        data = await request.json()
    except Exception:
        return {"ok": True}  # invalid JSON, ignore

    # Dedup Telegram's update_id — prevents double-processing on webhook retries
    update_id = data.get("update_id")
    if _tg_already_processed(update_id):
        logger.info(f"Skipping duplicate Telegram update_id={update_id}")
        return {"ok": True}

    # Fire-and-forget — do NOT await. Return to Telegram immediately.
    asyncio.create_task(_process_telegram_update_safe(data))
    return {"ok": True}


async def _process_telegram_update_safe(data: dict):
    """Runs the real handler with its own DB session, detached from the
    request/response cycle so it can take as long as it needs without ever
    causing Telegram to time out and retry."""
    from server.database import AsyncSessionLocal
    async with AsyncSessionLocal() as bg_db:
        try:
            await _handle_telegram_update(data, bg_db)
        except Exception as e:
            logger.error(f"Telegram background handler error: {e}", exc_info=True)


async def _get_active_ad(db: AsyncSession, user_plan: str):
    """Get an active ad campaign to display to this user. Returns None if no ad available."""
    try:
        result = await db.execute(
            select(AdCampaign)
            .where(AdCampaign.status == "active")
            .where(AdCampaign.impressions < AdCampaign.budget_impressions)
            .order_by(AdCampaign.impressions.asc())
            .limit(1)
        )
        ad = result.scalar_one_or_none()
        if not ad:
            return None
        if ad.target_audience == "free" and user_plan != "free":
            return None
        if ad.target_audience == "pro" and user_plan not in ("pro", "business", "enterprise"):
            return None
        if ad.end_date and ad.end_date < datetime.now():
            ad.status = "ended"
            await db.flush()
            return None
        return ad
    except Exception as e:
        logger.debug(f"Ad lookup error: {e}")
        return None


async def _display_ad_if_needed(bot, chat_id: int, db: AsyncSession, user_plan: str, message_count: int) -> bool:
    """Show an ad to free users every 5 messages. Pro/Owner users never see ads."""
    try:
        if user_plan in ("pro", "business", "enterprise", "owner"):
            return False
        if message_count % 5 != 0 or message_count == 0:
            return False

        ad = await _get_active_ad(db, user_plan)
        if not ad:
            return False

        ad.impressions += 1
        await db.flush()
        await db.commit()

        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        keyboard = None
        if ad.ad_link:
            keyboard = InlineKeyboardMarkup([[
                InlineKeyboardButton(text=ad.button_text or "Learn More", url=ad.ad_link)
            ]])

        ad_message = f"📢 *Sponsored*\n\n{ad.ad_text}\n\n_Powered by S.T.E.W Ads_"
        await bot.send_message(chat_id, ad_message, reply_markup=keyboard, disable_web_page_preview=False)
        return True
    except Exception as e:
        logger.debug(f"Ad display error: {e}")
        return False


async def _handle_telegram_update(data: dict, db: AsyncSession):
    """The actual Telegram update handler — all the real logic lives here.
    Called from a detached background task (see _process_telegram_update_safe),
    never directly from the request path."""
    from server.telegram_bot import TelegramBot
    bot = TelegramBot(settings.TELEGRAM_BOT_TOKEN)
    msg = bot.parse_update(data)

    if not msg or msg["is_bot"]:
        return {"ok": True}

    chat_id = msg["chat_id"]
    user_id = msg.get("user_id", 0)

    # ── STEW TELEGRAM: USER LOOKUP + ADMIN UNLOCK + USAGE QUOTA ────────────────
    # Runs before ANY processing (photos, documents, voice, text) so the free
    # tier limit and the owner/admin bypass apply uniformly to every message.
    _tg_username_early = msg.get("username") or msg.get("first_name", "User")
    _tg_email_early = f"tg_{user_id}@telegram.stew"
    tg_user_early = None
    for _early_attempt in range(3):
        try:
            _early_q = await db.execute(select(User).where(User.email == _tg_email_early))
            tg_user_early = _early_q.scalar_one_or_none()
            if not tg_user_early:
                from server.auth import generate_api_key as _gen_api_key_early
                tg_user_early = User(
                    name=_tg_username_early, email=_tg_email_early,
                    plan="free", api_key=_gen_api_key_early()
                )
                db.add(tg_user_early)
                await db.flush()
                await db.refresh(tg_user_early)
                await db.commit()
            break
        except Exception as _early_err:
            logger.warning(f"Telegram early-user lookup attempt {_early_attempt+1} failed: {_early_err}")
            await db.rollback()
            if _early_attempt == 2:
                tg_user_early = None
            else:
                await asyncio.sleep(0.4)

    _raw_text_early = (msg.get("text") or "").strip()
    _is_callback_early = bool(msg.get("is_callback"))

    # Admin unlock: "/admin <SECRET>" grants this Telegram account permanent,
    # unmetered access. Never counted toward quota, never rate-limited.
    if _raw_text_early.startswith("/admin"):
        _parts = _raw_text_early.split(maxsplit=1)
        _code = _parts[1].strip() if len(_parts) > 1 else ""
        _admin_secret = (settings.STEW_ADMIN_SECRET or os.environ.get("STEW_ADMIN_SECRET", "")).strip()
        if tg_user_early and _admin_secret and _code == _admin_secret:
            tg_user_early.plan = "owner"
            await db.flush()
            await db.commit()
            await bot.send_message(chat_id, "🔓 Admin access unlocked. This account now has unlimited, unmetered access to S.T.E.W forever.")
        else:
            await bot.send_message(chat_id, "Invalid admin code.")
        return {"ok": True}

    # Free/meta commands never cost quota — only real work does.
    _free_cmd_prefixes = ("/start", "/menu", "/help", "/upgrade", "/usage", "/plan", "/users", "/voice", "/clip", "/smartclip", "/createvideo", "/aivideo", "/aivideos", "/webbuild", "/meme", "/caption")
    _is_free_cmd_early = _is_callback_early or any(_raw_text_early.startswith(p) for p in _free_cmd_prefixes)

    if tg_user_early and tg_user_early.plan != "owner" and not _is_free_cmd_early:
        _allowed_early, _used_early, _limit_early = await _check_quota(tg_user_early, db)
        if not _allowed_early:
            _upgrade_kb = [
                [{"text": f"🎓 Upgrade — Student ₦{settings.PLAN_PRICES['student']:,}", "callback_data": "menu_upgrade_student"}],
                [{"text": f"💎 Upgrade — Pro ₦{settings.PLAN_PRICES['pro']:,}", "callback_data": "menu_upgrade_pro"}],
                [{"text": f"🏢 Upgrade — Business ₦{settings.PLAN_PRICES['business']:,}", "callback_data": "menu_upgrade_business"}],
            ]
            await bot.send_inline_keyboard(
                chat_id,
                f"⚠️ You've reached your free monthly limit ({_used_early}/{_limit_early} messages).\n\n"
                f"Upgrade to keep using S.T.E.W without interruption:\n\n"
                f"Student — ₦{settings.PLAN_PRICES['student']:,}/mo — {settings.PLAN_CALL_LIMITS['student']:,} messages (budget-friendly)\n"
                f"Pro — ₦{settings.PLAN_PRICES['pro']:,}/mo — {settings.PLAN_CALL_LIMITS['pro']:,} messages\n"
                f"Business — ₦{settings.PLAN_PRICES['business']:,}/mo — {settings.PLAN_CALL_LIMITS['business']:,} messages\n\n"
                f"Or type /upgrade any time to see plans again.",
                _upgrade_kb,
            )
            return {"ok": True}
        # Count this message against the free-tier quota (own DB session, fire-and-forget).
        asyncio.create_task(_log_call(db, tg_user_early.id, "/telegram/message", "POST", 0, 200))

    # ── HANDLE INCOMING PHOTOS (Vision-first, OCR fallback) ────────────────────
    if msg.get("has_photo") and msg.get("file_id"):
        await bot.send_chat_action(chat_id, "typing")
        caption = msg.get("caption", "")
        try:
            file_bytes = await bot.download_file(msg["file_id"])
            if not file_bytes:
                await bot.send_message(chat_id, "I couldn't download the image. Please try again.")
                return {"ok": True}

            await bot.send_message(chat_id, "Looking at your image...")
            await bot.send_typing(chat_id)

            # Vision is the primary path — a real multimodal model that can see and
            # understand the image (people, scenes, objects, text, context), not just
            # extract characters. This is what makes "what do you see?" work correctly.
            vision_prompt = caption if caption else (
                "Describe this image in detail — what you see, any people, objects, "
                "setting, and mood. If there is any text visible anywhere in the image, "
                "also transcribe it exactly."
            )

            import base64 as _b64
            image_b64 = _b64.b64encode(file_bytes).decode("utf-8")

            try:
                llm = get_llm_client()
                vision_result = await asyncio.to_thread(llm.vision_chat, image_b64, vision_prompt, "image/jpeg")
                reply = clean_response(vision_result.get("content", ""))
                if reply:
                    await bot.send_message(chat_id, reply)
                    return {"ok": True}
                # Empty reply — fall through to OCR fallback below
                raise ValueError("Vision model returned empty content")
            except Exception as vision_err:
                logger.warning(f"Vision failed, falling back to OCR: {vision_err}")
                await bot.send_message(chat_id, "Vision is unavailable right now — trying text extraction (OCR) instead...")
                await bot.send_typing(chat_id)

                # OCR fallback — only reached if every vision provider failed.
                from server.ocr_engine import ocr_file, ocr_and_reason
                ocr_result = await asyncio.to_thread(
                    ocr_file, file_bytes, msg.get("file_name", "photo.jpg"), "eng", False, True
                )

                extracted_text = ocr_result.get("text", "").strip()
                confidence = ocr_result.get("avg_confidence", 0)
                word_count = ocr_result.get("word_count", 0)

                if not extracted_text:
                    await bot.send_message(chat_id, "I couldn't understand or read this image right now. Please try again in a moment.")
                    return {"ok": True}

                if caption:
                    result = await ocr_and_reason(
                        content=file_bytes,
                        filename=msg.get("file_name", "photo.jpg"),
                        question=caption,
                        lang="eng",
                        task="answer",
                    )
                    reply = result.get("answer", result.get("response", ""))
                    if reply:
                        await bot.send_message(chat_id, reply)
                    else:
                        await bot.send_message(chat_id, "Extracted text:\n\n" + extracted_text[:3000])
                else:
                    preview = extracted_text[:3500]
                    if len(extracted_text) > 3500:
                        preview += "\n\n... (truncated)"
                    await bot.send_message(chat_id, f"*OCR Result* (confidence: {confidence}%, {word_count} words)\n\n{preview}")
                return {"ok": True}

        except Exception as e:
            logger.error(f"Telegram photo handling error: {e}", exc_info=True)
            await bot.send_message(chat_id, f"Error processing image: {str(e)[:100]}. Please try again.")
            return {"ok": True}

    # ── HANDLE INCOMING DOCUMENTS (Text Extraction) ───────────────────────────
    if msg.get("has_document") and msg.get("file_id"):
        await bot.send_chat_action(chat_id, "upload_document")
        caption = msg.get("caption", "")
        file_name = msg.get("file_name", "document")
        try:
            file_bytes = await bot.download_file(msg["file_id"])
            if not file_bytes:
                await bot.send_message(chat_id, "I couldn't download the file. Please try again.")
                return {"ok": True}

            await bot.send_typing(chat_id)

            ext = file_name.rsplit(".", 1)[-1].lower() if "." in file_name else ""

            # Songs/audio sent via the file picker land here as "document" instead of
            # "voice"/"audio" — detect and route to transcription instead of text extraction.
            if ext in _AUDIO_EXTENSIONS:
                await bot.send_message(chat_id, "Transcribing your audio...")
                await bot.send_typing(chat_id)
                transcript, error = await _transcribe_audio_bytes(file_bytes, file_name)
                if not transcript:
                    await bot.send_message(chat_id, f"Couldn't transcribe that audio ({error[:150]}). Please type your message instead.")
                    return {"ok": True}
                if caption:
                    await bot.send_message(chat_id, f'Transcript: "{transcript[:500]}"\nAnswering your question...')
                    await bot.send_typing(chat_id)
                    llm = get_llm_client()
                    reply = llm.complete(
                        f"Audio transcript:\n{transcript[:8000]}\n\nQuestion: {caption}",
                        system="Answer the question based on the transcript. Be concise and accurate.",
                    )
                    await bot.send_message(chat_id, clean_response(reply))
                else:
                    preview = transcript[:3500]
                    if len(transcript) > 3500:
                        preview += "\n\n... (truncated)"
                    await bot.send_message(chat_id, f'*Transcript of {file_name}*\n\n{preview}')
                return {"ok": True}

            await bot.send_message(chat_id, f"Reading {file_name}...")
            await bot.send_typing(chat_id)

            # Route to appropriate processor
            if ext in ("png", "jpg", "jpeg", "webp", "bmp", "tiff", "gif", "pdf"):
                # OCR for images and PDFs
                from server.ocr_engine import ocr_file, ocr_and_reason
                ocr_result = await asyncio.to_thread(
                    ocr_file, file_bytes, file_name, "eng", False, True
                )
                extracted_text = ocr_result.get("text", "").strip()
                confidence = ocr_result.get("avg_confidence", 0)
            else:
                # Text extraction for DOCX, TXT, CSV, JSON
                from server.document_processor import _extract_pdf, _extract_docx, _extract_csv, _extract_json, _extract_txt
                if ext == "pdf":
                    extracted = _extract_pdf(file_bytes, file_name)
                elif ext == "docx":
                    extracted = _extract_docx(file_bytes, file_name)
                elif ext == "csv":
                    extracted = _extract_csv(file_bytes, file_name)
                elif ext == "json":
                    extracted = _extract_json(file_bytes, file_name)
                else:
                    extracted = _extract_txt(file_bytes, file_name)
                extracted_text = extracted.get("text", "").strip()
                confidence = 100

            if not extracted_text:
                await bot.send_message(chat_id, "I couldn't extract any text from this file.")
                return {"ok": True}

            # If user asked a question, answer it about the document
            if caption:
                await bot.send_message(chat_id, f"Extracted {len(extracted_text)} chars. Analyzing...")
                await bot.send_typing(chat_id)
                llm = get_llm_client()
                reply = llm.complete(
                    f"Document content:\n{extracted_text[:8000]}\n\nQuestion: {caption}",
                    system="Answer the question based on the document. Be concise and accurate.",
                )
                await bot.send_message(chat_id, clean_response(reply))
            else:
                # Just return extracted text
                preview = extracted_text[:3500]
                if len(extracted_text) > 3500:
                    preview += "\n\n... (truncated)"
                await bot.send_message(chat_id, f"*Extracted text from {file_name}*\n\n{preview}")
            return {"ok": True}

        except Exception as e:
            logger.error(f"Telegram document error: {e}", exc_info=True)
            await bot.send_message(chat_id, f"Error processing file: {str(e)[:100]}. Please try again.")
            return {"ok": True}

    # If no text and no file, ignore — but NEVER drop voice/audio (they have no
    # "text" field, only a "voice"/"audio" object) or callback button presses
    # (parse_update always sets text="" for those). Both are handled further
    # down; dropping them here silently ate every voice note before
    # transcription ever ran.
    if not msg.get("text") and not msg.get("has_voice") and not msg.get("has_audio") and not msg.get("is_callback"):
        return {"ok": True}

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




    # ── CALLBACK QUERY HANDLER (Inline Button Presses) ─────────────────────────
    if msg.get("is_callback"):
        callback_data = msg.get("callback_data", "")
        callback_id = msg.get("callback_id", "")

        # Answer the callback to remove the loading state
        async with httpx.AsyncClient(timeout=5) as cb_client:
            await cb_client.post(f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/answerCallbackQuery",
                json={"callback_query_id": callback_id})

        callback_map = {
            "menu_students": "students",
            "menu_lecturers": "lecturers",
            "menu_companies": "companies",
            "menu_tools": "tools",
            "menu_clear": "clear",
            "menu_help": "help",
            "menu_upgrade_student": "upgrade_student",
            "menu_upgrade_pro": "upgrade_pro",
            "menu_upgrade_business": "upgrade_business",
        }
        action = callback_map.get(callback_data, "")
        if action == "students":
            await bot.send_message(chat_id, "Student Tools: /quiz /flashcards /studyguide /summarize /translate /solve /code\n\nExample: /quiz photosynthesis")
        elif action == "lecturers":
            await bot.send_message(chat_id, "Lecturer Tools: /lessonplan /rubric /grade /quiz /pptx\n\nExample: /lessonplan Intro to Calculus for 100 level")
        elif action == "companies":
            await bot.send_message(chat_id, "Company Tools: /invoice /meeting /swot /businessplan /budget /xlsx\n\nExample: /invoice Client: Acme Corp, Service: Web Design, Amount: 250000 NGN")
        elif action == "tools":
            await bot.send_message(chat_id, "Tools: /research /code /pdf /docx /xlsx /pptx\nGenerate images: 'generate image of...'\nSites: /webbuild <description> (motion-design websites)\nMemes: /meme <text> (AI meme generator)\nCaptions: /caption <context> (viral social captions)\nBooks: /book topic (up to 200 pages with covers)\nSongs: /song topic (AI music + lyrics + cover)\nBrowse: 'browse https://...'\nSend photos/PDFs for OCR\nSend voice notes for transcription")
        elif action == "clear":
            try:
                conv_q = await db.execute(select(Conversation).where(Conversation.user_id == tg_user.id).order_by(Conversation.updated_at.desc()).limit(1))
                conv = conv_q.scalar_one_or_none()
                if conv:
                    from sqlalchemy import text as _text
                    await db.execute(_text("DELETE FROM messages WHERE conversation_id = :cid"), {"cid": conv.id})
                    await db.commit()
            except Exception as ce:
                logger.warning(f"Clear error: {ce}")
            await bot.send_message(chat_id, "Conversation cleared. Fresh start!")
        elif action == "help":
            # Trigger help by falling through - just send help text directly
            help_text = (
                "S.T.E.W Commands\n\n"
                "Students: /quiz /flashcards /studyguide /summarize /translate /solve\n"
                "Lecturers: /lessonplan /rubric /grade\n"
                "Companies: /invoice /meeting /swot /businessplan /budget\n"
                "Tools: /research /code /menu /clear\nQuick: /weather /currency /news /joke /quote /define /math /qr /wiki\n"
                "Documents: /pdf /docx /xlsx /pptx\n"
                "Books: /book topic (up to 200 pages)\n"
                "Songs: /song topic (AI music + lyrics)\n"
                "Images: generate image of...\n"
                "Sites: /webbuild a coffee shop in Lagos\n"
                "Memes: /meme when the code finally works\n"
                "Captions: /caption viral social media text\n"
                "Browse: browse https://...\n"
                "Send photos/PDFs for OCR, voice notes for transcription"
            )
            await bot.send_message(chat_id, help_text)
        elif action in ("upgrade_student", "upgrade_pro", "upgrade_business"):
            _plan = {"upgrade_student": "student", "upgrade_pro": "pro", "upgrade_business": "business"}[action]
            try:
                _pay = initialize_payment(
                    email=tg_user.email if 'tg_user' in dir() else tg_user_early.email,
                    amount_kobo=settings.PLAN_PRICES[_plan] * 100,
                    plan=_plan,
                    metadata={"user_id": (tg_user.id if 'tg_user' in dir() else tg_user_early.id), "plan": _plan},
                )
                await bot.send_message(
                    chat_id,
                    f"Complete your {_plan.title()} upgrade (₦{settings.PLAN_PRICES[_plan]:,}) here:\n\n"
                    f"{_pay['authorization_url']}\n\n"
                    f"Your plan upgrades automatically the moment payment is confirmed.",
                )
            except Exception as _pay_err:
                logger.error(f"Telegram upgrade payment init failed: {_pay_err}")
                await bot.send_message(chat_id, "Couldn't start the payment right now. Please try /upgrade again in a moment.")
        return {"ok": True}

    # /upgrade — show plan options with live Paystack checkout links
    if user_text.startswith("/upgrade"):
        _kb = [
            [{"text": f"🎓 Student — ₦{settings.PLAN_PRICES['student']:,}/mo", "callback_data": "menu_upgrade_student"}],
            [{"text": f"💎 Pro — ₦{settings.PLAN_PRICES['pro']:,}/mo", "callback_data": "menu_upgrade_pro"}],
            [{"text": f"🏢 Business — ₦{settings.PLAN_PRICES['business']:,}/mo", "callback_data": "menu_upgrade_business"}],
        ]
        await bot.send_inline_keyboard(
            chat_id,
            f"Choose a plan:\n\n"
            f"🎓 Student — ₦{settings.PLAN_PRICES['student']:,}/mo — {settings.PLAN_CALL_LIMITS['student']:,} messages/month\n"
            f"💎 Pro — ₦{settings.PLAN_PRICES['pro']:,}/mo — {settings.PLAN_CALL_LIMITS['pro']:,} messages/month\n"
            f"🏢 Business — ₦{settings.PLAN_PRICES['business']:,}/mo — {settings.PLAN_CALL_LIMITS['business']:,} messages/month\n\n"
            f"Tap a plan to get your secure Paystack payment link.",
            _kb,
        )
        return {"ok": True}

    # /usage — show current plan + how much of the free tier has been used
    if user_text.startswith("/usage"):
        _allowed_u, _used_u, _limit_u = await _check_quota(tg_user, db)
        if tg_user.plan == "owner":
            await bot.send_message(chat_id, "Plan: Owner (Admin)\nUsage: Unlimited — no limits apply to this account.")
        else:
            await bot.send_message(
                chat_id,
                f"Plan: {tg_user.plan.title()}\n"
                f"Used this month: {_used_u:,} / {_limit_u:,} messages\n"
                f"{'✅ You have quota remaining.' if _allowed_u else '⚠️ Limit reached — type /upgrade.'}",
            )
        return {"ok": True}

    # /plan and /pricing — show all plans
    if user_text.startswith("/plan"):
        await bot.send_message(
            chat_id,
            "S.T.E.W Plans\n\n"
            f"Free — ₦0 — {settings.PLAN_CALL_LIMITS['free']:,} messages/month\n"
            f"🎓 Student — ₦{settings.PLAN_PRICES['student']:,}/mo — {settings.PLAN_CALL_LIMITS['student']:,} messages/month\n"
            f"   (budget tier — great for coursework, quizzes, small AI videos)\n"
            f"💎 Pro — ₦{settings.PLAN_PRICES['pro']:,}/mo — {settings.PLAN_CALL_LIMITS['pro']:,} messages/month\n"
            f"🏢 Business — ₦{settings.PLAN_PRICES['business']:,}/mo — {settings.PLAN_CALL_LIMITS['business']:,} messages/month\n\n"
            "Type /upgrade to pay with Paystack.",
        )
        return {"ok": True}

    # /users — show the live total user count
    if user_text.startswith("/users"):
        _count_u = await _get_telegram_user_count(db)
        await bot.send_message(chat_id, f"👥 {_count_u:,} people are using S.T.E.W on Telegram.")
        return {"ok": True}

    # /meme — AI Meme Generator (trending feature)
    if user_text.startswith("/meme"):
        _meme_text = user_text.strip()[5:].strip()
        if not _meme_text:
            await bot.send_message(
                chat_id,
                "🤣 *AI Meme Generator*\n\n"
                "I create a meme image with text overlay from your description.\n\n"
                "*Usage:*\n"
                "1. /meme when the code finally works on the first try\n"
                "2. /meme me explaining AI to my grandma\n"
                "3. /meme Monday motivation: AI doing my homework",
            )
            return

        await bot.send_chat_action(chat_id, "upload_photo")
        try:
            # Generate the meme image via Pollinations with text overlay style
            _meme_prompt = f"funny meme illustration about: {_meme_text}, cartoon style, bold text overlay, internet meme format, viral, humorous, high contrast"
            _meme_url = f"https://image.pollinations.ai/prompt/{httpx.URL(_meme_prompt, encoding='utf-8').quote()}"
            _meme_resp = await asyncio.to_thread(http_requests.get, _meme_url, {"timeout": 30})
            if _meme_resp.status_code == 200 and len(_meme_resp.content) > 1000:
                await bot.send_photo(chat_id, _meme_resp.content, caption=f"Meme: {_meme_text[:80]}")
                asyncio.create_task(_log_call(db, tg_user.id, "/telegram/meme", "POST", 0, 200))
            else:
                await bot.send_message(chat_id, f"Meme generation failed. Try a different prompt! (HTTP {_meme_resp.status_code})")
        except Exception as e:
            logger.error(f"Meme generation error: {e}")
            await bot.send_message(chat_id, f"Meme error: {str(e)[:150]}")
        return

    # /caption — Viral Social Media Caption Generator (trending feature)
    if user_text.startswith("/caption"):
        _cap_context = user_text.strip()[8:].strip()
        if not _cap_context:
            await bot.send_message(
                chat_id,
                "📸 *Viral Caption Generator*\n\n"
                "I write scroll-stopping social media captions with hashtags.\n\n"
                "*Usage:*\n"
                "1. /caption a photo of my startup team launching our app\n"
                "2. /caption a plate of jollof rice at a Lagos restaurant\n"
                "3. /caption announcing my new AI product\n\n"
                "I generate captions optimized for Instagram, Twitter/X, and TikTok.",
            )
            return

        try:
            _cap_result = await asyncio.to_thread(
                llm.chat,
                [
                    {"role": "system", "content": "You are a viral social media caption writer. "
                     "Generate 3 different caption options for the user's described post. "
                     "Each caption should be optimized for a different platform: "
                     "Option 1 for Instagram (with emojis + hashtags), "
                     "Option 2 for Twitter/X (short, punchy, max 280 chars), "
                     "Option 3 for TikTok (trendy, Gen Z voice, with hooks). "
                     "Output ONLY the 3 captions, clearly labeled. No extra explanation."},
                    {"role": "user", "content": f"Write captions for: {_cap_context}"},
                ],
            )
            _caps = clean_response(_cap_result.get("content", ""))
            await bot.send_message(
                chat_id,
                f"📸 *Viral Captions*\n\n{_caps}\n\n"
                f"Pick your favorite and post it. The hashtags are optimized for reach.",
            )
            asyncio.create_task(_log_call(db, tg_user.id, "/telegram/caption", "POST", 0, 200))
        except Exception as e:
            logger.error(f"Caption generation error: {e}")
            await bot.send_message(chat_id, f"Caption error: {str(e)[:150]}")
        return

    # /sponsor — show current sponsors
    if user_text.startswith("/sponsor"):
        ad_result = await db.execute(
            select(AdCampaign).where(AdCampaign.status == "active").limit(5)
        )
        ads = ad_result.scalars().all()
        if not ads:
            await bot.send_message(chat_id, "No sponsors right now. Want to advertise on S.T.E.W? Contact the admin!")
        else:
            lines = ["📢 *Current Sponsors*\n"]
            for a in ads:
                lines.append(f"• {a.advertiser_name}: {a.ad_text[:60]}")
            _uc = await _get_telegram_user_count(db)
            lines.append(f"\n\n💡 Want to reach {_uc:,}+ users? Contact admin to advertise!")
            await bot.send_message(chat_id, "\n".join(lines))
        return {"ok": True}

    # /ads — admin command to manage ad campaigns (owner only)
    if user_text.startswith("/ads") and tg_user.plan == "owner":
        ad_result = await db.execute(
            select(AdCampaign).order_by(AdCampaign.created_at.desc()).limit(20)
        )
        ads = ad_result.scalars().all()
        if not ads:
            await bot.send_message(chat_id, "No ad campaigns yet.\n\nCreate one via API:\nPOST /ads/create\n{advertiser_name, ad_text, ad_link, budget_impressions}")
        else:
            lines = ["📊 *Ad Campaigns*\n"]
            for a in ads:
                se = "🟢" if a.status == "active" else "🔴" if a.status == "ended" else "⏸️"
                lines.append(f"{se} {a.advertiser_name}")
                lines.append(f"   Impressions: {a.impressions}/{a.budget_impressions} | Clicks: {a.clicks}")
                lines.append(f"   Status: {a.status}\n")
            await bot.send_message(chat_id, "\n".join(lines))
        return {"ok": True}

    # ── FEATURE REQUEST SYSTEM ──────────────────────────────────────────────
    # /features — show top feature requests (checked BEFORE /feature to avoid prefix clash)
    if user_text.startswith("/features"):
        # Get top 10 most-voted pending features
        fr_result = await db.execute(
            select(FeatureRequest)
            .where(FeatureRequest.status == "pending")
            .order_by(FeatureRequest.votes.desc(), FeatureRequest.created_at.desc())
            .limit(10)
        )
        features = fr_result.scalars().all()

        if not features:
            await bot.send_message(chat_id, "No feature requests yet! Be the first — type /feature <what you want Stew to do>")
            return {"ok": True}

        lines = ["🔥 *Top Feature Requests*\n"]
        for i, f in enumerate(features, 1):
            _emoji = {"creative": "🎨", "document": "📄", "image": "🖼️", "language": "🌍", "developer": "💻", "productivity": "⚡", "integration": "🔌", "general": "💡"}.get(f.category, "💡")
            _short = f.feature_text[:60] + ("..." if len(f.feature_text) > 60 else "")
            lines.append(f"{i}. {_emoji} ({f.votes} votes) {_short}\n   /vote #{f.id[:8]}")

        lines.append(f"\n💡 Submit yours: /feature <description>")
        await bot.send_message(chat_id, "\n".join(lines))
        return {"ok": True}

    # /vote #<id> — vote for a feature request
    if user_text.startswith("/vote"):
        vote_target = user_text[5:].strip()
        if not vote_target:
            await bot.send_message(chat_id, "Vote for a feature! Example: /vote #ab12cd34\n\nType /features to see all requests.")
            return {"ok": True}

        # Strip the # if present
        vote_target = vote_target.lstrip("#")

        # Find by ID prefix
        fr_result = await db.execute(
            select(FeatureRequest).where(FeatureRequest.id.like(f"{vote_target}%"))
        )
        fr = fr_result.scalar_one_or_none()

        if not fr:
            await bot.send_message(chat_id, "Feature not found. Type /features to see all requests.")
            return {"ok": True}

        if str(user_id) in fr.voter_ids:
            await bot.send_message(chat_id, f"You already voted for this one! ({fr.votes} total votes)")
            return {"ok": True}

        fr.votes += 1
        fr.voter_ids.append(str(user_id))
        await db.flush()
        await db.commit()

        await bot.send_message(
            chat_id,
            f"👍 Voted! This feature now has {fr.votes} votes.\n\n"
            f"Feature: \"{fr.feature_text[:80]}\"",
        )
        return {"ok": True}

    # /feature <description> — submit a feature request (checked AFTER /features and /vote)
    if user_text.startswith("/feature"):
        feature_desc = user_text[8:].strip()
        if not feature_desc or len(feature_desc) < 5:
            await bot.send_message(chat_id, "Tell me what feature you want!\n\nExample: /feature I want Stew to generate PowerPoint slides from text")
            return {"ok": True}

        # Category auto-detection
        _feat_lower = feature_desc.lower()
        if any(w in _feat_lower for w in ["song", "music", "audio", "voice"]):
            _cat = "creative"
        elif any(w in _feat_lower for w in ["pdf", "docx", "xlsx", "pptx", "document", "file"]):
            _cat = "document"
        elif any(w in _feat_lower for w in ["image", "picture", "photo", "draw", "logo"]):
            _cat = "image"
        elif any(w in _feat_lower for w in ["translate", "language", "yoruba", "igbo", "hausa", "french"]):
            _cat = "language"
        elif any(w in _feat_lower for w in ["code", "program", "python", "javascript", "api"]):
            _cat = "developer"
        elif any(w in _feat_lower for w in ["reminder", "schedule", "calendar", "automate", "task"]):
            _cat = "productivity"
        elif any(w in _feat_lower for w in ["whatsapp", "telegram", "email", "slack", "integration"]):
            _cat = "integration"
        else:
            _cat = "general"

        # Check for duplicates (same feature text by same user)
        existing = await db.execute(
            select(FeatureRequest).where(
                FeatureRequest.telegram_user_id == str(user_id),
                FeatureRequest.feature_text.ilike(f"%{feature_desc[:50]}%")
            ).limit(1)
        )
        existing_fr = existing.scalar_one_or_none()
        if existing_fr:
            await bot.send_message(chat_id, f"You already requested something similar (Status: {existing_fr.status}). I'll merge your vote! 👍")
            existing_fr.votes += 1
            if str(user_id) not in existing_fr.voter_ids:
                existing_fr.voter_ids.append(str(user_id))
            await db.flush()
            await db.commit()
            return {"ok": True}

        # Create new feature request
        fr = FeatureRequest(
            user_id=tg_user.id,
            telegram_user_id=str(user_id),
            feature_text=feature_desc[:500],
            category=_cat,
            votes=1,
            voter_ids=[str(user_id)],
        )
        db.add(fr)
        await db.flush()
        await db.commit()

        await bot.send_message(
            chat_id,
            f"✅ Feature request logged! (ID: #{fr.id[:8]})\n\n"
            f"Category: {_cat}\n"
            f"Request: \"{feature_desc[:100]}\"\n\n"
            f"Others can vote for this with: /vote #{fr.id[:8]}\n"
            f"Type /features to see all requested features.",
        )
        return {"ok": True}

    # Handle /start command with inline menu
    if user_text.startswith("/start"):
        keyboard = [
            [
                {"text": "Students", "callback_data": "menu_students"},
                {"text": "Lecturers", "callback_data": "menu_lecturers"},
            ],
            [
                {"text": "Companies", "callback_data": "menu_companies"},
                {"text": "Tools", "callback_data": "menu_tools"},
            ],
            [
                {"text": "Clear Chat", "callback_data": "menu_clear"},
                {"text": "Help", "callback_data": "menu_help"},
            ],
        ]
        _tg_count_start = await _get_telegram_user_count(db)
        welcome = (
            f"Hello {username}! I'm S.T.E.W.\n\n"
            f"👥 {_tg_count_start:,} people are using S.T.E.W.\n\n"
            "I help students, lecturers, and companies get things done.\n\n"
            "Tap a button to see what I can do:"
        )
        await bot.send_inline_keyboard(chat_id, welcome, keyboard)
        return {"ok": True}

    # Handle /menu command
    if user_text.startswith("/menu"):
        keyboard = [
            [
                {"text": "Students", "callback_data": "menu_students"},
                {"text": "Lecturers", "callback_data": "menu_lecturers"},
            ],
            [
                {"text": "Companies", "callback_data": "menu_companies"},
                {"text": "Tools", "callback_data": "menu_tools"},
            ],
            [
                {"text": "Clear Chat", "callback_data": "menu_clear"},
                {"text": "Help", "callback_data": "menu_help"},
            ],
        ]
        _tg_count_menu = await _get_telegram_user_count(db)
        await bot.send_inline_keyboard(chat_id, f"Main Menu - Tap a category:\n👥 {_tg_count_menu:,} users", keyboard)
        return {"ok": True}

    # Handle /help command
    if user_text.startswith("/help"):
        help_text = (
            "S.T.E.W Commands\n\n"
            "For Everyone:\n"
            "1. /menu - Interactive menu\n"
            "2. /summarize - Summarize text\n"
            "3. /translate - Translate languages\n"
            "4. /research - Deep research\n"
            "5. /code - Run Python code\n"
            "6. /clear - Clear chat history\n\n"
            "For Students:\n"
            "7. /quiz - Generate quiz questions\n"
            "8. /flashcards - Create flashcards\n"
            "9. /studyguide - Study guide with PDF\n"
            "10. /solve - Solve math problems\n\n"
            "For Lecturers:\n"
            "11. /lessonplan - Create lesson plan PDF\n"
            "12. /rubric - Grading rubric PDF\n"
            "13. /grade - Calculate grades\n\n"
            "For Companies:\n"
            "14. /invoice - Invoice generator PDF\n"
            "15. /meeting - Meeting minutes PDF\n"
            "16. /swot - SWOT analysis PDF\n"
            "17. /businessplan - Business plan PDF\n"
            "18. /budget - Budget planner\n\n"
            "Creative Pro Tools:\n"
            "19. /book - Write a book (up to 200 pages)\n"
            "20. /song - Create AI song with music\n"
            "21. /remember - Tell Stew to remember something\n"
            "22. /memory - View what Stew remembers\n"
            "23. /forget - Clear all memories\n\n"
            "Documents: /pdf /docx /xlsx /pptx\n"
            "Images: generate image of...\n"
            "Browse: browse https://...\n"
            "Send photos/PDFs for OCR\n"
            "Send voice notes for transcription\n\n"
            "Community:\n"
            "24. /feature <desc> - Request a feature\n"
            "25. /features - See top requests\n"
            "26. /vote #<id> - Vote for a feature\n"
            "27. /users - See user count\n"
            "28. /sponsor - See our sponsors\n\n"
            "Quick Tools:\n"
            "29. /weather <city> - Live weather\n"
            "30. /currency 100 USD to NGN - Exchange rates\n"
            "31. /news <topic> - Latest news\n"
            "32. /joke - Random joke\n"
            "33. /quote - Inspirational quote\n"
            "34. /define <word> - Dictionary\n"
            "35. /math <expression> - Quick math\n"
            "36. /qr <text> - Generate QR code\n"
            "37. /shorten <url> - Shorten URL\n"
            "38. /ai-image <desc> - Generate image\n"
            "39. /wiki <topic> - Wikipedia search\n\n"
            "Voice & Audio:\n"
            "40. /voice - Toggle voice note replies 🔊\n"
            "41. /voice list - See available voices\n"
            "42. /voice <name> - Set your voice (e.g. /voice nigeria)\n\n"
            "Video Studio:\n"
            "43. /clip <url> <start> <dur> - Clip a video segment\n"
            "44. /smartclip <url> - AI smart clips (Opus Clips style)\n"
            "45. /createvideo <topic> - AI video with images + voiceover\n"
            "46. /aivideo <prompt> - REAL AI video from text\n"
            "47. /aivideos <prompt> - Multi-scene AI video with narration\n\n"
            "Creative:\n"
            "48. /webbuild <desc> - Build a motion-design website (live link)\n"
            "49. /meme <text> - Generate an AI meme image\n"
            "50. /caption <context> - Viral social media captions\n\n"
            "Account:\n"
            "51. /usage - Check your usage quota\n"
            "52. /plan - View pricing plans\n"
            "53. /upgrade - Upgrade (Student, Pro, Business)"
        )
        await bot.send_message(chat_id, help_text)
        return {"ok": True}

    # ── VOICE MESSAGE HANDLING (voice notes, audio files, songs) ──────────────
    if msg.get("has_voice") or msg.get("has_audio"):
        await bot.send_chat_action(chat_id, "typing")
        file_size = msg.get("file_size") or 0
        if file_size and file_size > _TELEGRAM_MAX_DOWNLOAD_BYTES:
            await bot.send_message(
                chat_id,
                f"That file is {file_size / 1024 / 1024:.1f}MB — Telegram bots can only download files up to 20MB. "
                "Please send a shorter clip or a smaller file."
            )
            return {"ok": True}
        try:
            file_bytes = await bot.download_file(msg["file_id"])
            if not file_bytes:
                await bot.send_message(chat_id, "Couldn't download your audio — it may be too large or Telegram's servers timed out. Try again or send a shorter clip.")
                return {"ok": True}
            await bot.send_message(chat_id, "Transcribing your audio...")
            await bot.send_typing(chat_id)
            transcript, error = await _transcribe_audio_bytes(file_bytes, msg.get("file_name") or "voice.ogg")
            if not transcript:
                await bot.send_message(chat_id, f"Couldn't transcribe that audio ({error[:150]}). Please type your message instead.")
                return {"ok": True}
            await bot.send_message(chat_id, f'You said: "{transcript[:500]}"\nProcessing...')
            await bot.send_typing(chat_id)
            user_text = transcript
            user_lower = user_text.lower()
        except Exception as e:
            logger.error(f"Voice error: {e}", exc_info=True)
            await bot.send_message(chat_id, f"Voice error: {str(e)[:100]}. Please type instead.")
            return {"ok": True}

    # ── /book COMMAND (Write a Book) ────────────────────────────────────────
    _book_intent = re.search(
        r'\b(write|create|make|generate|compose|author|draft)\b.{0,15}\bbook\b'
        r'|\bbook\b.{0,15}\b(about|on|for|titled|called)\b'
        r'|^/book\b',
        user_lower
    )
    if user_text.startswith("/book") or _book_intent:
        # Extract book topic — strip the command/verb phrasing, keep the subject
        book_topic = ""
        if user_text.startswith("/book"):
            book_topic = user_text[5:].strip()
        else:
            # Remove leading verb + "book" + connector words, keep the rest as topic
            stripped = re.sub(
                r'^\s*(please\s+)?(can you\s+|could you\s+)?(write|create|make|generate|compose|author|draft)\s+(me\s+|us\s+)?(a|an|the)?\s*book\s*(about|on|for|titled|called)?\s*',
                '', user_text, flags=re.IGNORECASE
            )
            book_topic = stripped.strip()
            if not book_topic or book_topic.lower() == user_text.lower():
                # Fallback: just grab everything after the word "book"
                m = re.search(r'\bbook\b\s*(about|on|for|titled|called)?\s*(.*)', user_text, re.IGNORECASE)
                book_topic = m.group(2).strip() if m and m.group(2) else user_text.strip()

        # Extract page count if specified ("100 pages", "200 pages")
        import re as _re
        pages_match = _re.search(r'(\d+)\s*pages?', user_lower)
        target_pages = int(pages_match.group(1)) if pages_match else 50
        target_pages = max(10, min(target_pages, 200))

        # Extract author name if specified ("by Author Name")
        author_match = _re.search(r'\bby\s+([\w\s]+?)(?:\s*\d+\s*pages?|$)', user_text, _re.IGNORECASE)
        book_author = author_match.group(1).strip() if author_match else username

        if not book_topic or len(book_topic) < 3:
            await bot.send_message(chat_id,
                "Write a book with S.T.E.W!\n\n"
                "Usage: /book The History of Nigerian Architecture\n"
                "Or: write a book about African folktales, 100 pages\n\n"
                "Supports up to 200 pages with professional cover design.")
            return {"ok": True}

        await bot.send_chat_action(chat_id, "typing")
        await bot.send_message(chat_id,
            f"Writing your book: '{book_topic}'\n"
            f"Target: {target_pages} pages with cover design\n"
            f"This will take a few minutes...")

        try:
            from server.book_generator import generate_book

            def _sync_llm_chat(messages, max_tokens=4000):
                llm = get_llm_client()
                try:
                    return llm.chat(messages, max_tokens=max_tokens)  # already returns {"content": ...}
                except Exception:
                    return {"content": llm.complete(messages[-1]["content"], system=messages[0]["content"])}

            book_result = await asyncio.to_thread(
                generate_book,
                book_topic, book_author, target_pages, _sync_llm_chat, None
            )

            file_bytes = book_result.get("file_bytes")
            filename = book_result.get("filename", "book.docx")

            if file_bytes and len(file_bytes) > 1000:
                # Send cover image first if available
                cover_path = "/tmp/stew_book_cover.jpg"
                # Check if we can extract the cover from the docx — just send a message about it
                await bot.send_message(chat_id,
                    f"Book complete! {book_result.get('chapters', 0)} chapters, "
                    f"~{book_result.get('pages_estimated', target_pages)} pages\n"
                    f"Genre: Auto-detected\n"
                    f"Cover: Professional AI-designed front & back\n\n"
                    f"Sending your book now...")
                await bot.send_document(chat_id, file_bytes, filename, f"Book: {book_topic}")
            else:
                await bot.send_message(chat_id, "Sorry, couldn't generate the book. Please try with a simpler topic or fewer pages.")

        except Exception as e:
            logger.error(f"Book generation error: {e}", exc_info=True)
            await bot.send_message(chat_id, f"Book generation error: {str(e)[:100]}. Please try again with a simpler request.")
        return {"ok": True}

    # ── /song COMMAND (AI Song Generation) ──────────────────────────────────
    _song_intent = re.search(
        r'\b(write|create|make|generate|compose|produce)\b.{0,15}\b(song|music|track|tune|jingle)\b'
        r'|\b(song|music|track|tune|jingle)\b.{0,15}\b(about|on|for|titled|called)\b'
        r'|^/song\b',
        user_lower
    )
    if user_text.startswith("/song") or _song_intent:
        # Extract song topic — strip the command/verb phrasing, keep the subject
        song_topic = ""
        if user_text.startswith("/song"):
            song_topic = user_text[5:].strip()
        else:
            stripped = re.sub(
                r'^\s*(please\s+)?(can you\s+|could you\s+)?(write|create|make|generate|compose|produce)\s+(me\s+|us\s+)?(a|an|the)?\s*(song|music|track|tune|jingle)\s*(for|about|on|titled|called)?\s*',
                '', user_text, flags=re.IGNORECASE
            )
            song_topic = stripped.strip()
            if not song_topic or song_topic.lower() == user_text.lower():
                m = re.search(r'\b(song|music|track|tune|jingle)\b\s*(for|about|on|titled|called)?\s*(.*)', user_text, re.IGNORECASE)
                song_topic = m.group(3).strip() if m and m.group(3) else user_text.strip()

        if not song_topic or len(song_topic) < 3:
            await bot.send_message(chat_id,
                "Create AI songs with S.T.E.W!\n\n"
                "Usage: /song A love song about Lagos sunset\n"
                "Or: create a song about friendship and hope\n\n"
                "Generates lyrics + album cover + audio music")
            return {"ok": True}

        await bot.send_chat_action(chat_id, "typing")
        await bot.send_message(chat_id,
            f"Creating your song: '{song_topic}'\n"
            f"Writing lyrics, generating music, designing cover art...\n"
            f"This takes 1-3 minutes...")

        try:
            from server.book_generator import generate_song

            def _sync_llm_chat_song(messages, max_tokens=2000):
                llm = get_llm_client()
                try:
                    return llm.chat(messages, max_tokens=max_tokens)  # already returns {"content": ...}
                except Exception:
                    return {"content": llm.complete(messages[-1]["content"], system=messages[0]["content"])}

            song_result = await asyncio.to_thread(
                generate_song, song_topic, None, _sync_llm_chat_song,
                duration_seconds=60
            )

            title = song_result.get("title", song_topic[:60])
            genre = song_result.get("genre", "")
            mood = song_result.get("mood", "")
            lyrics = song_result.get("lyrics", "")
            cover_bytes = song_result.get("cover_bytes")
            audio_bytes = song_result.get("audio_bytes")
            audio_format = song_result.get("audio_format", "mp3")
            filename = song_result.get("filename", "song.mp3")
            engine_used = song_result.get("engine_used", "none")

            # Send song info header
            info_line = f"🎵 {title}"
            if genre:
                info_line += f" | {genre.title()}"
            if mood:
                info_line += f" | {mood.title()}"
            engine_label = {
                "ace-step-1.5": "ACE-Step 1.5 (full singing)",
                "musicgen-small": "MusicGen (instrumental)",
                "tts-fallback": "TTS (spoken lyrics)",
                "none": "lyrics only",
            }.get(engine_used, engine_used)
            info_line += f"\nEngine: {engine_label}"
            await bot.send_message(chat_id, info_line)

            # Send lyrics
            lyrics_preview = lyrics[:3000]
            if len(lyrics) > 3000:
                lyrics_preview += "\n\n... (full lyrics delivered in audio)"
            await bot.send_message(chat_id, f"Lyrics:\n\n{lyrics_preview}")

            # Send album cover
            if cover_bytes and len(cover_bytes) > 1000:
                try:
                    await bot.send_photo(chat_id, cover_bytes, caption="Album Cover")
                except Exception as ce:
                    logger.warning(f"Cover send error: {ce}")

            # Send audio
            if audio_bytes and len(audio_bytes) > 1000:
                await bot.send_message(chat_id, "Sending your song audio...")
                # Telegram expects ogg for voice, mp3 for audio
                if audio_format == "wav":
                    # Convert wav to mp3 if possible
                    try:
                        import subprocess
                        mp3_path = f"/tmp/stew_song_{int(time.time())}.mp3"
                        wav_path = f"/tmp/stew_song_{int(time.time())}.wav"
                        with open(wav_path, "wb") as f:
                            f.write(audio_bytes)
                        subprocess.run(["ffmpeg", "-i", wav_path, "-y", mp3_path],
                                      capture_output=True, timeout=30)
                        if os.path.exists(mp3_path):
                            with open(mp3_path, "rb") as f:
                                audio_bytes = f.read()
                            os.unlink(wav_path)
                            os.unlink(mp3_path)
                    except Exception:
                        pass  # Send as-is if ffmpeg not available

                await bot.send_audio(chat_id, audio_bytes, filename,
                                     caption=f"Song: {song_topic}",
                                     performer="S.T.E.W Agent", title=song_topic[:60])
            else:
                await bot.send_message(chat_id,
                    "Lyrics and cover art are ready above, but the music engine hit a snag "
                    "generating the actual audio this time (it runs on a shared community "
                    "GPU pool that's occasionally busy or briefly down). Send /song again "
                    "with the same idea and it should go through.")

        except Exception as e:
            logger.error(f"Song generation error: {e}", exc_info=True)
            await bot.send_message(chat_id, f"Song generation error: {str(e)[:100]}. Please try again.")
        return {"ok": True}

    # ── /remember COMMAND (Explicit Memory) ──────────────────────────────────
    if user_text.startswith("/remember"):
        memory_text = user_text[9:].strip()
        if not memory_text or len(memory_text) < 3:
            await bot.send_message(chat_id,
                "Tell me what to remember!\n\n"
                "Usage: /remember I prefer responses in Pidgin English\n"
                "Usage: /remember My project deadline is August 30\n"
                "I'll store it permanently and recall it in future conversations.")
            return {"ok": True}

        try:
            # Auto-detect category
            mem_lower = memory_text.lower()
            if any(w in mem_lower for w in ["prefer", "like", "love", "hate", "want", "always", "never"]):
                category = "preference"
            elif any(w in mem_lower for w in ["must", "should", "always do", "never do", "don't"]):
                category = "instruction"
            elif any(w in mem_lower for w in ["my name", "i am", "i'm", "i work", "i live", "i build", "i run"]):
                category = "fact"
            else:
                category = "context"

            await store_user_memory(db, tg_user.id, category, memory_text, importance=8, platform="telegram")
            # Also save to Supabase for persistent storage (survives redeploy)
            if supabase_configured():
                await supa_save_memory(str(tg_user.telegram_id), category, memory_text, category)
            await bot.send_message(chat_id, f"Got it. I'll remember: {memory_text[:200]}\n\nCategory: {category}\nThis is stored permanently.")
        except Exception as e:
            logger.error(f"Memory store error: {e}")
            await bot.send_message(chat_id, "Couldn't save that memory. Please try again.")
        return {"ok": True}

    # ── /memory COMMAND (View Memories) ──────────────────────────────────────
    if user_text.strip() == "/memory" or user_text.strip() == "/memories":
        try:
            memories = await get_user_memories(db, tg_user.id, limit=30)
            if not memories:
                await bot.send_message(chat_id,
                    "I don't have any saved memories yet.\n\n"
                    "Use /remember to tell me something important.\n"
                    "Example: /remember I'm building an AI startup called ERGIO")
                return {"ok": True}

            mem_text = f"Your memories ({len(memories)} total):\n\n"
            for i, m in enumerate(memories[:30], 1):
                mem_text += f"{i}. [{m.category.upper()}] {m.content[:150]}\n"
            mem_text += "\nUse /forget to clear all memories, or /remember to add new ones."
            await bot.send_message(chat_id, mem_text)
        except Exception as e:
            logger.error(f"Memory list error: {e}")
            await bot.send_message(chat_id, "Couldn't retrieve memories. Please try again.")
        return {"ok": True}

    # ── /forget COMMAND (Clear Memories) ────────────────────────────────────
    if user_text.strip() == "/forget" or user_text.strip() == "/forgetall":
        try:
            from sqlalchemy import update as _upd
            await db.execute(
                _upd(UserMemory).where(UserMemory.user_id == tg_user.id).values(is_active=False)
            )
            # Also clear from Supabase
            if supabase_configured():
                await supa_clear(str(tg_user.telegram_id))
            await bot.send_message(chat_id,
                "I've cleared all your memories.\n\n"
                "I won't remember anything from our past conversations anymore. "
                "Use /remember to start fresh.")
        except Exception as e:
            logger.error(f"Memory clear error: {e}")
            await bot.send_message(chat_id, "Couldn't clear memories. Please try again.")
        return {"ok": True}

    # ── /summarize COMMAND ─────────────────────────────────────────────────────
    if user_text.startswith("/summarize"):
        text_to_summarize = user_text[10:].strip()
        if not text_to_summarize:
            await bot.send_message(chat_id, "Send: /summarize Your long text here...")
            return {"ok": True}
        await bot.send_chat_action(chat_id, "typing")
        try:
            llm = get_llm_client()
            result = await asyncio.to_thread(llm.complete,
                f"Summarize with key points:\n\n{text_to_summarize[:8000]}",
                system="Expert summarizer. Create a clear summary with bullet points.")
            await bot.send_message(chat_id, clean_response(result))
        except Exception as e:
            await bot.send_message(chat_id, f"Error: {str(e)[:100]}")
        return {"ok": True}

    # ── /translate COMMAND ─────────────────────────────────────────────────────
    if user_text.startswith("/translate"):
        text_to_translate = user_text[10:].strip()
        if not text_to_translate:
            await bot.send_message(chat_id, "Send: /translate English to French: Hello World")
            return {"ok": True}
        await bot.send_chat_action(chat_id, "typing")
        try:
            llm = get_llm_client()
            result = await asyncio.to_thread(llm.complete,
                f"Translate: {text_to_translate}",
                system="Professional translator. Return only the translation.")
            await bot.send_message(chat_id, f"Translation: {clean_response(result)}")
        except Exception as e:
            await bot.send_message(chat_id, f"Error: {str(e)[:100]}")
        return {"ok": True}

    # ── /quiz COMMAND (Students) ───────────────────────────────────────────────
    if user_text.startswith("/quiz"):
        topic = user_text[5:].strip()
        if not topic:
            await bot.send_message(chat_id, "Send: /quiz photosynthesis, 10 questions")
            return {"ok": True}
        await bot.send_chat_action(chat_id, "typing")
        try:
            llm = get_llm_client()
            result = await asyncio.to_thread(llm.chat, [
                {"role": "system", "content": "Quiz generator. Create multiple choice questions with format:\nQ1. Question\nA) Option\nB) Option\nC) Option\nD) Option\nAnswer: X\n\nInclude answer key at end."},
                {"role": "user", "content": f"Create 10 MCQ quiz about: {topic}"},
            ])
            quiz_text = clean_response(result["content"])
            doc_result = generate_pdf(quiz_text, f"Quiz: {topic}")
            if doc_result.get("success") and doc_result.get("file"):
                import base64 as _b64
                file_bytes = _b64.b64decode(doc_result["file"])
                await bot.send_message(chat_id, quiz_text[:3800])
                await bot.send_document(chat_id, file_bytes, doc_result.get("filename", "quiz.pdf"), f"Quiz: {topic}")
            else:
                await bot.send_message(chat_id, quiz_text[:3800])
        except Exception as e:
            await bot.send_message(chat_id, f"Error: {str(e)[:100]}")
        return {"ok": True}

    # ── /flashcards COMMAND (Students) ─────────────────────────────────────────
    if user_text.startswith("/flashcards"):
        topic = user_text[11:].strip()
        if not topic:
            await bot.send_message(chat_id, "Send: /flashcards biology chapter 5")
            return {"ok": True}
        await bot.send_chat_action(chat_id, "typing")
        try:
            llm = get_llm_client()
            result = await asyncio.to_thread(llm.chat, [
                {"role": "system", "content": "Flashcard generator. Format:\nCard 1\nFront: Question\nBack: Answer\n\nMake 15 cards."},
                {"role": "user", "content": f"Create flashcards about: {topic}"},
            ])
            await bot.send_message(chat_id, clean_response(result["content"])[:3800])
        except Exception as e:
            await bot.send_message(chat_id, f"Error: {str(e)[:100]}")
        return {"ok": True}

    # ── /studyguide COMMAND (Students) ────────────────────────────────────────
    if user_text.startswith("/studyguide"):
        topic = user_text[11:].strip()
        if not topic:
            await bot.send_message(chat_id, "Send: /studyguide Nigerian history 1960-1999")
            return {"ok": True}
        await bot.send_message(chat_id, f"Creating study guide: {topic}...")
        await bot.send_chat_action(chat_id, "typing")
        try:
            searcher = get_searcher()
            search_results = await asyncio.to_thread(searcher.search, topic, 5)
            context = ""
            if search_results.get("grounded"):
                context = searcher.format_results_for_llm(search_results)
            llm = get_llm_client()
            result = await asyncio.to_thread(llm.chat, [
                {"role": "system", "content": "Study guide creator. Include: key concepts, definitions, formulas, summary points, practice questions."},
                {"role": "user", "content": f"Study guide about: {topic}\n\nContext: {context[:5000]}"},
            ])
            guide = clean_response(result["content"])
            doc_result = generate_pdf(guide, f"Study Guide: {topic}")
            if doc_result.get("success") and doc_result.get("file"):
                import base64 as _b64
                file_bytes = _b64.b64decode(doc_result["file"])
                await bot.send_document(chat_id, file_bytes, doc_result.get("filename", "study_guide.pdf"), f"Study Guide: {topic}")
            else:
                await bot.send_message(chat_id, guide[:3800])
        except Exception as e:
            await bot.send_message(chat_id, f"Error: {str(e)[:100]}")
        return {"ok": True}

    # ── /solve COMMAND (Math) ──────────────────────────────────────────────────
    if user_text.startswith("/solve"):
        problem = user_text[6:].strip()
        if not problem:
            await bot.send_message(chat_id, "Send: /solve 2x + 5 = 15, find x")
            return {"ok": True}
        await bot.send_chat_action(chat_id, "typing")
        try:
            from server.tool_agent import run_agent_loop
            agent_result = await run_agent_loop(f"Solve step by step: {problem}", max_iterations=3)
            response = agent_result.get("response", "")
            if response:
                await bot.send_message(chat_id, response[:3800])
            else:
                llm = get_llm_client()
                result = await asyncio.to_thread(llm.complete, f"Solve: {problem}", system="Math tutor. Show all steps.")
                await bot.send_message(chat_id, clean_response(result))
        except Exception as e:
            await bot.send_message(chat_id, f"Error: {str(e)[:100]}")
        return {"ok": True}

    # ── /lessonplan COMMAND (Lecturers) ────────────────────────────────────────
    if user_text.startswith("/lessonplan"):
        topic = user_text[11:].strip()
        if not topic:
            await bot.send_message(chat_id, "Send: /lessonplan Intro to Calculus for 100 level")
            return {"ok": True}
        await bot.send_message(chat_id, f"Creating lesson plan: {topic}...")
        await bot.send_chat_action(chat_id, "typing")
        try:
            llm = get_llm_client()
            result = await asyncio.to_thread(llm.chat, [
                {"role": "system", "content": "Expert curriculum designer. Create detailed lesson plan with: objectives, duration, materials, activities, assessment, homework."},
                {"role": "user", "content": f"Lesson plan for: {topic}"},
            ])
            plan = clean_response(result["content"])
            doc_result = generate_pdf(plan, f"Lesson Plan: {topic}")
            if doc_result.get("success") and doc_result.get("file"):
                import base64 as _b64
                file_bytes = _b64.b64decode(doc_result["file"])
                await bot.send_document(chat_id, file_bytes, doc_result.get("filename", "lesson_plan.pdf"), f"Lesson Plan: {topic}")
            else:
                await bot.send_message(chat_id, plan[:3800])
        except Exception as e:
            await bot.send_message(chat_id, f"Error: {str(e)[:100]}")
        return {"ok": True}

    # ── /rubric COMMAND (Lecturers) ────────────────────────────────────────────
    if user_text.startswith("/rubric"):
        topic = user_text[7:].strip()
        if not topic:
            await bot.send_message(chat_id, "Send: /rubric Research paper, 100 marks")
            return {"ok": True}
        await bot.send_chat_action(chat_id, "typing")
        try:
            llm = get_llm_client()
            result = await asyncio.to_thread(llm.chat, [
                {"role": "system", "content": "Expert assessor. Create grading rubric with criteria, performance levels, and point allocations."},
                {"role": "user", "content": f"Grading rubric for: {topic}"},
            ])
            rubric = clean_response(result["content"])
            doc_result = generate_pdf(rubric, f"Rubric: {topic}")
            if doc_result.get("success") and doc_result.get("file"):
                import base64 as _b64
                file_bytes = _b64.b64decode(doc_result["file"])
                await bot.send_document(chat_id, file_bytes, doc_result.get("filename", "rubric.pdf"), f"Rubric: {topic}")
            else:
                await bot.send_message(chat_id, rubric[:3800])
        except Exception as e:
            await bot.send_message(chat_id, f"Error: {str(e)[:100]}")
        return {"ok": True}

    # ── /grade COMMAND (Lecturers) ─────────────────────────────────────────────
    if user_text.startswith("/grade"):
        grade_input = user_text[6:].strip()
        if not grade_input:
            await bot.send_message(chat_id, "Send: /grade 85 out of 100\nOr: /grade CA=30 Exam=50 Total=100")
            return {"ok": True}
        await bot.send_chat_action(chat_id, "typing")
        try:
            from server.code_sandbox import execute_code
            safe_input = grade_input.replace('"', "'")
            code = 'import re\ninput_str = "' + safe_input + '"\n'
            code += 'if "out of" in input_str.lower():\n'
            code += '    parts = input_str.lower().split("out of")\n'
            code += '    score = float(parts[0].strip().split()[-1])\n'
            code += '    total = float(parts[1].strip().split()[0])\n'
            code += '    pct = (score/total)*100\n'
            code += '    g = "A" if pct>=70 else "B" if pct>=60 else "C" if pct>=50 else "D" if pct>=45 else "E" if pct>=40 else "F"\n'
            code += '    print(f"Score: {score}/{total} = {pct:.1f}% Grade: {g}")\n'
            code += 'elif "=" in input_str:\n'
            code += '    scores = dict(re.findall(r"(\w+)=(\d+)", input_str))\n'
            code += '    total = sum(int(v) for v in scores.values())\n'
            code += '    print(f"Breakdown: {scores}")\n'
            code += '    print(f"Total: {total}")\n'
            code += 'else:\n'
            code += '    print("Use: /grade 85 out of 100 or /grade CA=30 Exam=50")\n'
            result = await asyncio.to_thread(execute_code, code)
            output = result.get("stdout", "") or result.get("error", "Error")
            await bot.send_message(chat_id, f"Grade Result:\n{output[:1000]}")
        except Exception as e:
            await bot.send_message(chat_id, f"Error: {str(e)[:100]}")
        return {"ok": True}

    # ── /invoice COMMAND (Companies) ───────────────────────────────────────────
    if user_text.startswith("/invoice"):
        details = user_text[8:].strip()
        if not details:
            await bot.send_message(chat_id, "Send: /invoice Client: Acme Corp, Service: Web Design, Amount: 250000")
            return {"ok": True}
        await bot.send_message(chat_id, "Generating invoice...")
        await bot.send_chat_action(chat_id, "upload_document")
        try:
            import random as _rand
            inv_num = f"INV-{_rand.randint(10000,99999)}"
            llm = get_llm_client()
            result = await asyncio.to_thread(llm.chat, [
                {"role": "system", "content": "Invoice generator. Create professional invoice with: invoice number, date, client info, service description, amount in Naira, subtotal, VAT 7.5%, total, payment terms."},
                {"role": "user", "content": f"Invoice {inv_num}. Details: {details}. Date: {datetime.now().strftime('%Y-%m-%d')}"},
            ])
            invoice_text = clean_response(result["content"])
            doc_result = generate_pdf(invoice_text, f"Invoice {inv_num}")
            if doc_result.get("success") and doc_result.get("file"):
                import base64 as _b64
                file_bytes = _b64.b64decode(doc_result["file"])
                await bot.send_document(chat_id, file_bytes, doc_result.get("filename", f"invoice_{inv_num}.pdf"), f"Invoice {inv_num}")
            else:
                await bot.send_message(chat_id, invoice_text[:3800])
        except Exception as e:
            await bot.send_message(chat_id, f"Error: {str(e)[:100]}")
        return {"ok": True}

    # ── /meeting COMMAND (Companies) ────────────────────────────────────────────
    if user_text.startswith("/meeting"):
        details = user_text[8:].strip()
        if not details:
            await bot.send_message(chat_id, "Send: /meeting Q3 Review - attendees: John, Sarah")
            return {"ok": True}
        await bot.send_message(chat_id, "Creating meeting minutes...")
        await bot.send_chat_action(chat_id, "typing")
        try:
            llm = get_llm_client()
            result = await asyncio.to_thread(llm.chat, [
                {"role": "system", "content": "Meeting minutes writer. Include: title, date, attendees, agenda, discussion, decisions, action items with owners, next meeting."},
                {"role": "user", "content": f"Minutes for: {details}. Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}"},
            ])
            minutes = clean_response(result["content"])
            doc_result = generate_pdf(minutes, "Meeting Minutes")
            if doc_result.get("success") and doc_result.get("file"):
                import base64 as _b64
                file_bytes = _b64.b64decode(doc_result["file"])
                await bot.send_document(chat_id, file_bytes, doc_result.get("filename", "meeting_minutes.pdf"), "Meeting Minutes")
            else:
                await bot.send_message(chat_id, minutes[:3800])
        except Exception as e:
            await bot.send_message(chat_id, f"Error: {str(e)[:100]}")
        return {"ok": True}

    # ── /swot COMMAND (Companies) ──────────────────────────────────────────────
    if user_text.startswith("/swot"):
        topic = user_text[5:].strip()
        if not topic:
            await bot.send_message(chat_id, "Send: /swot MTN Nigeria")
            return {"ok": True}
        await bot.send_message(chat_id, f"SWOT analysis: {topic}...")
        await bot.send_chat_action(chat_id, "typing")
        try:
            searcher = get_searcher()
            search_results = await asyncio.to_thread(searcher.search, f"{topic} business analysis", 5)
            context = ""
            if search_results.get("grounded"):
                context = searcher.format_results_for_llm(search_results)
            llm = get_llm_client()
            result = await asyncio.to_thread(llm.chat, [
                {"role": "system", "content": "Business strategy consultant. Create SWOT (Strengths, Weaknesses, Opportunities, Threats) with 5 points each."},
                {"role": "user", "content": f"SWOT for: {topic}\n\nResearch: {context[:5000]}"},
            ])
            swot = clean_response(result["content"])
            doc_result = generate_pdf(swot, f"SWOT: {topic}")
            if doc_result.get("success") and doc_result.get("file"):
                import base64 as _b64
                file_bytes = _b64.b64decode(doc_result["file"])
                await bot.send_document(chat_id, file_bytes, doc_result.get("filename", "swot.pdf"), f"SWOT: {topic}")
            else:
                await bot.send_message(chat_id, swot[:3800])
        except Exception as e:
            await bot.send_message(chat_id, f"Error: {str(e)[:100]}")
        return {"ok": True}

    # ── /businessplan COMMAND (Companies) ──────────────────────────────────────
    if user_text.startswith("/businessplan"):
        topic = user_text[13:].strip()
        if not topic:
            await bot.send_message(chat_id, "Send: /businessplan Poultry farm in Ogun State")
            return {"ok": True}
        await bot.send_message(chat_id, f"Creating business plan: {topic}...")
        await bot.send_chat_action(chat_id, "typing")
        try:
            searcher = get_searcher()
            search_results = await asyncio.to_thread(searcher.search, f"{topic} business plan Nigeria", 5)
            context = ""
            if search_results.get("grounded"):
                context = searcher.format_results_for_llm(search_results)
            llm = get_llm_client()
            result = await asyncio.to_thread(llm.chat, [
                {"role": "system", "content": "Business plan writer for African markets. Include: Executive Summary, Business Description, Market Analysis, Products, Marketing, Operations, Financial Projections (Naira), Risk Analysis."},
                {"role": "user", "content": f"Business plan for: {topic}\n\nResearch: {context[:5000]}"},
            ])
            plan = clean_response(result["content"])
            doc_result = generate_pdf(plan, f"Business Plan: {topic}")
            if doc_result.get("success") and doc_result.get("file"):
                import base64 as _b64
                file_bytes = _b64.b64decode(doc_result["file"])
                await bot.send_document(chat_id, file_bytes, doc_result.get("filename", "business_plan.pdf"), f"Business Plan: {topic}")
            else:
                await bot.send_message(chat_id, plan[:3800])
        except Exception as e:
            await bot.send_message(chat_id, f"Error: {str(e)[:100]}")
        return {"ok": True}

    # ── /budget COMMAND ────────────────────────────────────────────────────────
    if user_text.startswith("/budget"):
        details = user_text[7:].strip()
        if not details:
            await bot.send_message(chat_id, "Send: /budget Income: 500000, Rent: 150000, Food: 80000")
            return {"ok": True}
        await bot.send_message(chat_id, "Creating budget...")
        await bot.send_chat_action(chat_id, "typing")
        try:
            from server.code_sandbox import execute_code
            safe = details.replace('"', "'").replace(",", "\n")
            code = 'items = {}\n'
            code += 'for pair in """' + safe + '""".split("\n"):\n'
            code += '    if ":" in pair:\n'
            code += '        k, v = pair.split(":", 1)\n'
            code += '        try:\n'
            code += '            items[k.strip().lower()] = float(v.strip().replace("ngn","").replace("naira","").strip())\n'
            code += '        except: pass\n'
            code += 'income = sum(v for k,v in items.items() if "income" in k or "salary" in k or "revenue" in k)\n'
            code += 'expenses = sum(v for k,v in items.items() if "income" not in k and "salary" not in k and "revenue" not in k)\n'
            code += 'savings = income - expenses\n'
            code += 'pct = (savings/income*100) if income else 0\n'
            code += 'print(f"INCOME: NGN {income:,.0f}")\n'
            code += 'print(f"EXPENSES: NGN {expenses:,.0f}")\n'
            code += 'print(f"SAVINGS: NGN {savings:,.0f} ({pct:.1f}%)")\n'
            code += 'print()\n'
            code += 'for k,v in items.items():\n'
            code += '    if "income" not in k and "salary" not in k and "revenue" not in k:\n'
            code += '        p = (v/income*100) if income else 0\n'
            code += '        print(f"  {k:20s} NGN {v:>10,.0f} ({p:.1f}%)")\n'
            code += 'if pct >= 20: print("\nGreat! Saving 20%+")\n'
            code += 'elif pct >= 10: print("\nDecent. Try 20%.")\n'
            code += 'elif savings > 0: print("\nLow savings. Reduce expenses.")\n'
            code += 'else: print("\nDEFICIT! Cut costs!")\n'
            result = await asyncio.to_thread(execute_code, code)
            output = result.get("stdout", "") or result.get("error", "Error")
            await bot.send_message(chat_id, "```\n" + output[:3000] + "\n```")
        except Exception as e:
            await bot.send_message(chat_id, f"Error: {str(e)[:100]}")
        return {"ok": True}

    # ── /code COMMAND ──────────────────────────────────────────────────────────
    if user_text.startswith("/code"):
        code = user_text[5:].strip()
        if not code:
            await bot.send_message(chat_id, "Send: /code print(sum(range(100)))")
            return {"ok": True}
        await bot.send_chat_action(chat_id, "typing")
        try:
            from server.code_sandbox import execute_code
            result = await asyncio.to_thread(execute_code, code)
            output = result.get("stdout", "")
            if output:
                await bot.send_message(chat_id, "```\n" + output[:3000] + "\n```")
            elif result.get("stderr"):
                await bot.send_message(chat_id, "Error:\n```\n" + result["stderr"][:2000] + "\n```")
            else:
                await bot.send_message(chat_id, "Code ran (no output)")
        except Exception as e:
            await bot.send_message(chat_id, f"Error: {str(e)[:200]}")
        return {"ok": True}


    # ── /weather COMMAND ──────────────────────────────────────────────────────
    if user_text.startswith("/weather"):
        location = user_text[9:].strip()
        if not location:
            await bot.send_message(chat_id, "Send: /weather Lagos\nOr: /weather London, UK")
            return {"ok": True}
        await bot.send_chat_action(chat_id, "typing")
        try:
            async with http_requests.AsyncClient(timeout=10) as client:
                # Geocode the location
                geo_resp = await client.get(f"https://geocoding-api.open-meteo.com/v1/search?name={location}&count=1&language=en&format=json")
                geo_data = geo_resp.json()
                if not geo_data.get("results"):
                    await bot.send_message(chat_id, f"Location '{location}' not found. Try a city name.")
                    return {"ok": True}
                geo = geo_data["results"][0]
                lat, lon = geo["latitude"], geo["longitude"]
                city_name = geo["name"]
                country = geo.get("country", "")
                
                # Get weather
                weather_resp = await client.get(f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current=temperature_2m,relative_humidity_2m,apparent_temperature,weather_code,wind_speed_10m&daily=temperature_2m_max,temperature_2m_min,weather_code&timezone=auto&forecast_days=3")
                weather = weather_resp.json()
                cur = weather.get("current", {})
                daily = weather.get("daily", {})
                
                # Weather code mapping
                wmo = {
                    0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
                    45: "Fog", 48: "Rime fog", 51: "Light drizzle", 53: "Moderate drizzle",
                    55: "Dense drizzle", 61: "Slight rain", 63: "Moderate rain", 65: "Heavy rain",
                    71: "Slight snow", 73: "Moderate snow", 75: "Heavy snow",
                    80: "Rain showers", 81: "Moderate showers", 82: "Violent showers",
                    95: "Thunderstorm", 96: "Thunderstorm + hail", 99: "Severe thunderstorm"
                }
                code = cur.get("weather_code", 0)
                desc = wmo.get(code, "Unknown")
                temp = cur.get("temperature_2m", 0)
                feels = cur.get("apparent_temperature", 0)
                humidity = cur.get("relative_humidity_2m", 0)
                wind = cur.get("wind_speed_10m", 0)
                
                # 3-day forecast
                forecast_lines = []
                for i in range(min(3, len(daily.get("time", [])))):
                    d = daily["time"][i]
                    hi = daily["temperature_2m_max"][i]
                    lo = daily["temperature_2m_min"][i]
                    wc = wmo.get(daily["weather_code"][i], "?")
                    forecast_lines.append(f"  {d}: {wc}, {lo}-{hi}°C")
                
                msg = (
                    f"Weather in {city_name}, {country}\n"
                    f"\n"
                    f"Currently: {desc}\n"
                    f"Temperature: {temp}°C (feels like {feels}°C)\n"
                    f"Humidity: {humidity}%\n"
                    f"Wind: {wind} km/h\n"
                    f"\n3-Day Forecast:\n" + "\n".join(forecast_lines)
                )
                await bot.send_message(chat_id, msg)
        except Exception as e:
            await bot.send_message(chat_id, f"Weather error: {str(e)[:100]}")
        return {"ok": True}

    # ── /currency COMMAND ──────────────────────────────────────────────────────
    if user_text.startswith("/currency"):
        parts = user_text[10:].strip()
        if not parts:
            await bot.send_message(chat_id, "Send: /currency 100 USD to NGN\nOr: /currency USD NGN")
            return {"ok": True}
        await bot.send_chat_action(chat_id, "typing")
        try:
            # Parse: "100 USD to NGN" or "USD NGN" or "USD to NGN"
            import re as _re
            m = _re.match(r'(\d+(?:\.\d+)?)?\s*([A-Za-z]{3})\s*(?:to|=>)?\s*([A-Za-z]{3})', parts)
            if not m:
                await bot.send_message(chat_id, "Format: /currency 100 USD to NGN")
                return {"ok": True}
            amount = float(m.group(1)) if m.group(1) else 1.0
            from_curr = m.group(2).upper()
            to_curr = m.group(3).upper()
            
            async with http_requests.AsyncClient(timeout=10) as client:
                resp = await client.get(f"https://open.er-api.com/v6/latest/{from_curr}")
                data = resp.json()
                rates = data.get("rates", {})
                if to_curr not in rates:
                    await bot.send_message(chat_id, f"Currency {to_curr} not found.")
                    return {"ok": True}
                rate = rates[to_curr]
                result = amount * rate
                await bot.send_message(chat_id, f"{amount} {from_curr} = {result:,.2f} {to_curr}\nRate: 1 {from_curr} = {rate:,.4f} {to_curr}")
        except Exception as e:
            await bot.send_message(chat_id, f"Currency error: {str(e)[:100]}")
        return {"ok": True}

    # ── /joke COMMAND ──────────────────────────────────────────────────────────
    if user_text.startswith("/joke"):
        await bot.send_chat_action(chat_id, "typing")
        try:
            async with http_requests.AsyncClient(timeout=10) as client:
                resp = await client.get("https://official-joke-api.appspot.com/random_joke")
                data = resp.json()
                await bot.send_message(chat_id, f"{data.get('setup','')}\n\n{data.get('punchline','')}")
        except:
            # Fallback jokes
            import random
            jokes = [
                "Why did the Python developer go broke? Because he used up all his cache! 🐍",
                "How many programmers does it take to change a light bulb? None — that's a hardware problem! 💡",
                "Why do Java developers wear glasses? Because they don't C#! 👓",
                "What's an AI's favorite type of music? Heavy Meta-learning! 🎵",
                "Why did the AI cross the road? To optimize the chicken's path! 🐔",
            ]
            await bot.send_message(chat_id, random.choice(jokes))
        return {"ok": True}

    # ── /quote COMMAND ──────────────────────────────────────────────────────────
    if user_text.startswith("/quote"):
        await bot.send_chat_action(chat_id, "typing")
        try:
            async with http_requests.AsyncClient(timeout=10) as client:
                resp = await client.get("https://zenquotes.io/api/random")
                data = resp.json()
                if isinstance(data, list) and data:
                    q = data[0]
                    await bot.send_message(chat_id, f'\"{q.get("q","")}\"\n\n— {q.get("a","")}')
                else:
                    raise Exception("No quote")
        except:
            import random
            quotes = [
                '"The best way to predict the future is to invent it." — Alan Kay',
                "\"Code is like humor. When you have to explain it, it's bad.\" — Cory House",
                '"The only way to do great work is to love what you do." — Steve Jobs',
                '"Success is not final, failure is not fatal: it is the courage to continue that counts." — Churchill',
                "\"Africa's future will be written by its innovators, not its history.\" — Unknown",
            ]
            await bot.send_message(chat_id, random.choice(quotes))
        return {"ok": True}

    # ── /define COMMAND ────────────────────────────────────────────────────────
    if user_text.startswith("/define"):
        word = user_text[8:].strip()
        if not word:
            await bot.send_message(chat_id, "Send: /define serendipity")
            return {"ok": True}
        await bot.send_chat_action(chat_id, "typing")
        try:
            async with http_requests.AsyncClient(timeout=10) as client:
                resp = await client.get(f"https://api.dictionaryapi.dev/api/v2/entries/en/{word}")
                if resp.status_code != 200:
                    await bot.send_message(chat_id, f"No definition found for '{word}'.")
                    return {"ok": True}
                data = resp.json()
                entry = data[0]
                meanings = entry.get("meanings", [])
                if not meanings:
                    await bot.send_message(chat_id, f"No meanings found for '{word}'.")
                    return {"ok": True}
                m = meanings[0]
                pos = m.get("partOfSpeech", "")
                defs = m.get("definitions", [])
                result_text = f"_{word}_ ({pos})\n\n"
                for i, d in enumerate(defs[:3]):
                    result_text += f"{i+1}. {d.get('definition','')}\n"
                    if d.get('example'):
                        result_text += f"   Example: {d['example']}\n"
                await bot.send_message(chat_id, result_text)
        except Exception as e:
            await bot.send_message(chat_id, f"Define error: {str(e)[:100]}")
        return {"ok": True}

    # ── /news COMMAND ───────────────────────────────────────────────────────────
    if user_text.startswith("/news"):
        topic = user_text[6:].strip()
        await bot.send_chat_action(chat_id, "typing")
        try:
            llm = get_llm_client()
            search_query = f"latest news today {topic}" if topic else "top news headlines today August 2026"
            searcher = get_searcher()
            search_results = await asyncio.to_thread(searcher.search, search_query, 5)
            if search_results.get("grounded"):
                context = searcher.format_results_for_llm(search_results)
                system = STEW_MASTER_PROMPT + "\n\nSummarize the latest news in a brief, easy-to-read format. Top 5 stories with 1-2 sentences each."
                messages = [
                    {"role": "system", "content": system},
                    {"role": "user", "content": f"Latest news{' about ' + topic if topic else ''}:\n\n{context}"},
                ]
                result = await asyncio.to_thread(llm.chat, messages, max_tokens=800)
                await bot.send_message(chat_id, f"📰 Today's News{' — ' + topic if topic else ''}\n\n" + clean_response(result["content"]))
            else:
                await bot.send_message(chat_id, "Couldn't fetch news right now. Try again later.")
        except Exception as e:
            await bot.send_message(chat_id, f"News error: {str(e)[:100]}")
        return {"ok": True}

    # ── /qr COMMAND (Generate QR Code) ──────────────────────────────────────────
    if user_text.startswith("/qr"):
        text = user_text[4:].strip()
        if not text:
            await bot.send_message(chat_id, "Send: /qr https://t.me/StewAgent_bot\nOr: /qr Your text here")
            return {"ok": True}
        try:
            import urllib.parse
            encoded = urllib.parse.quote(text)
            qr_url = f"https://api.qrserver.com/v1/create-qr-code/?size=300x300&data={encoded}"
            async with http_requests.AsyncClient(timeout=15) as client:
                resp = await client.get(qr_url)
                if resp.status_code == 200 and len(resp.content) > 100:
                    await bot.send_photo(chat_id, resp.content, caption=f"QR Code for: {text[:50]}")
                else:
                    await bot.send_message(chat_id, "Failed to generate QR code. Try again.")
        except Exception as e:
            await bot.send_message(chat_id, f"QR error: {str(e)[:100]}")
        return {"ok": True}

    # ── /math COMMAND (Quick Math) ──────────────────────────────────────────────
    if user_text.startswith("/math"):
        expr = user_text[6:].strip()
        if not expr:
            await bot.send_message(chat_id, "Send: /math 2 + 2 * 5\nSupports: + - * / ** % sqrt() sin() cos() tan() log()\nOr: /math solve 3x + 5 = 20")
            return {"ok": True}
        await bot.send_chat_action(chat_id, "typing")
        try:
            # Check if it's an equation to solve
            if "=" in expr and any(c in expr for c in "xy"):
                llm = get_llm_client()
                messages = [
                    {"role": "system", "content": "You are a math tutor. Solve the equation step by step. Show your work clearly. Keep it concise."},
                    {"role": "user", "content": f"Solve: {expr}"},
                ]
                result = await asyncio.to_thread(llm.chat, messages, max_tokens=500)
                await bot.send_message(chat_id, f"Problem: {expr}\n\n" + clean_response(result["content"]))
            else:
                # Evaluate arithmetic expression safely
                import ast
                import math as _math
                # Replace common math terms
                expr_clean = expr.replace("sqrt", "_math.sqrt").replace("sin", "_math.sin").replace("cos", "_math.cos").replace("tan", "_math.tan").replace("log", "_math.log").replace("pi", str(_math.pi)).replace("e", str(_math.e))
                # Safe eval
                allowed = set()
                tree = ast.parse(expr_clean, mode='eval')
                result = eval(compile(tree, '<string>', 'eval'), {"__builtins__": {}}, {"_math": _math})
                await bot.send_message(chat_id, f"{expr} = {result}")
        except Exception as e:
            await bot.send_message(chat_id, f"Math error: {str(e)[:100]}\n\nTip: Use /math solve 3x + 5 = 20 for equations")
        return {"ok": True}

    # ── /shorten COMMAND (URL Shortener) ────────────────────────────────────────
    if user_text.startswith("/shorten"):
        url = user_text[9:].strip()
        if not url:
            await bot.send_message(chat_id, "Send: /shorten https://example.com/very/long/url")
            return {"ok": True}
        if not url.startswith("http"):
            url = "https://" + url
        await bot.send_chat_action(chat_id, "typing")
        try:
            async with http_requests.AsyncClient(timeout=10) as client:
                resp = await client.get(f"https://is.gd/create.php?format=simple&url={url}")
                if resp.status_code == 200 and resp.text.startswith("http"):
                    await bot.send_message(chat_id, f"Shortened URL:\n{resp.text}")
                else:
                    await bot.send_message(chat_id, "Failed to shorten URL. Try again.")
        except Exception as e:
            await bot.send_message(chat_id, f"Shorten error: {str(e)[:100]}")
        return {"ok": True}

    # ── /ai-image COMMAND (Direct Image Generation) ────────────────────────────
    if user_text.startswith("/ai-image") or user_text.startswith("/img"):
        prompt = user_text.split(" ", 1)[1].strip() if " " in user_text else ""
        if not prompt:
            await bot.send_message(chat_id, "Send: /ai-image a sunset over Lagos lagoon\nOr: /img a cute robot reading a book")
            return {"ok": True}
        await bot.send_chat_action(chat_id, "upload_photo")
        try:
            import urllib.parse
            encoded = urllib.parse.quote(prompt)
            seed = random.randint(1, 999999)
            img_url = f"https://image.pollinations.ai/prompt/{encoded}?width=1024&height=1024&model=flux&nologo=true&seed={seed}"
            async with http_requests.AsyncClient(timeout=60) as client:
                resp = await client.get(img_url)
                if resp.status_code == 200 and len(resp.content) > 1000:
                    await bot.send_photo(chat_id, resp.content, caption=f"AI Image: {prompt[:80]}")
                else:
                    await bot.send_message(chat_id, "Image generation failed. Try a different prompt.")
        except Exception as e:
            await bot.send_message(chat_id, f"Image error: {str(e)[:100]}")
        return {"ok": True}

    # ── /wikipedia COMMAND ──────────────────────────────────────────────────────
    if user_text.startswith("/wiki") or user_text.startswith("/wikipedia"):
        query = user_text.split(" ", 1)[1].strip() if " " in user_text else ""
        if not query:
            await bot.send_message(chat_id, "Send: /wiki Albert Einstein\nOr: /wiki Nigeria")
            return {"ok": True}
        await bot.send_chat_action(chat_id, "typing")
        try:
            async with http_requests.AsyncClient(timeout=10) as client:
                # Search Wikipedia
                search_resp = await client.get(f"https://en.wikipedia.org/w/api.php?action=query&list=search&srsearch={query}&format=json&srlimit=1")
                search_data = search_resp.json()
                if not search_data.get("query", {}).get("search"):
                    await bot.send_message(chat_id, f"No Wikipedia article found for '{query}'.")
                    return {"ok": True}
                title = search_data["query"]["search"][0]["title"]
                # Get summary
                summary_resp = await client.get(f"https://en.wikipedia.org/api/rest_v1/page/summary/{title}")
                summary = summary_resp.json()
                text = summary.get("extract", "No summary available.")
                url = summary.get("content_urls", {}).get("desktop", {}).get("page", "")
                msg = f"_Wikipedia: {title}_\n\n{text[:1500]}"
                if url:
                    msg += f"\n\nRead more: {url}"
                await bot.send_message(chat_id, msg)
        except Exception as e:
            await bot.send_message(chat_id, f"Wiki error: {str(e)[:100]}")
        return {"ok": True}


    # ── /clear COMMAND ──────────────────────────────────────────────────────────
    # /clip — Clip a segment from a video URL
    if user_text.startswith("/clip"):
        parts = user_text.strip().split(None, 1)
        if len(parts) < 2:
            await bot.send_message(chat_id,
                "Video Clipper\n\n"
                "Clip a segment from any video URL (YouTube, mp4, etc.)\n\n"
                "Usage: /clip <url> <start> <duration>\n\n"
                "Examples:\n"
                "1. /clip https://youtube.com/watch?v=xxx 00:01:30 30\n"
                "2. /clip https://example.com/video.mp4 0:30 15\n"
                "3. /clip https://youtube.com/watch?v=xxx 0:45 20 wide\n\n"
                "Start: MM:SS or HH:MM:SS | Duration: seconds (max 180)\n"
                "Add 'wide' for landscape, 'square' for 1:1"
            )
            return

        args = parts[1].split()
        video_url = args[0] if args else ""
        start_time = "00:00:00"
        duration = 30
        aspect_ratio = "9:16"

        if len(args) > 1:
            start_time = args[1]
        if len(args) > 2:
            try:
                duration = int(args[2])
            except ValueError:
                pass
        if "wide" in args or "landscape" in args:
            aspect_ratio = "16:9"
        if "square" in args:
            aspect_ratio = "1:1"

        if not video_url.startswith("http"):
            await bot.send_message(chat_id, "Please provide a valid video URL starting with http")
            return

        allowed, used, limit = await _check_quota(tg_user, db)
        if not allowed:
            await bot.send_message(chat_id,
                f"Monthly limit reached ({used}/{limit}). Use /upgrade to continue.")
            return

        await bot.send_chat_action(chat_id, "upload_video")
        await bot.send_message(chat_id,
            f"Clipping video...\n"
            f"Start: {start_time} | Duration: {duration}s | Format: {aspect_ratio}\n"
            f"Step 1/3: Downloading video...")

        try:
            import base64 as _b64
            result = await clip_video(video_url, start_time, duration, True, aspect_ratio)
            if result.get("success") and result.get("file"):
                video_bytes = _b64.b64decode(result["file"])
                size_mb = len(video_bytes) / 1024 / 1024
                caption = f"Stew Clip | {duration}s | {aspect_ratio}"
                if result.get("captions_added"):
                    caption += " | Captions burned in"
                caption += f" | {size_mb:.1f}MB"
                await bot.send_message(chat_id, f"Step 3/3: Sending clip ({size_mb:.1f}MB)...")
                send_result = await bot.send_video(chat_id, video_bytes, caption=caption)
                if send_result.get("ok"):
                    asyncio.create_task(_log_call(db, tg_user.id, "/telegram/clip", "POST", 0, 200))
                else:
                    err_msg = send_result.get("description", "Unknown error")
                    await bot.send_message(chat_id, f"Clip processed but couldn't send: {err_msg[:150]}")
            else:
                await bot.send_message(chat_id, f"Clipping failed: {result.get('error', 'Unknown error')}")
        except Exception as e:
            logger.error(f"Clip command error: {e}")
            await bot.send_message(chat_id, f"Error: {str(e)[:200]}")
        return

    # /smartclip — AI smart clips (Opus Clips style)
    if user_text.startswith("/smartclip"):
        parts = user_text.strip().split(None, 1)
        if len(parts) < 2:
            await bot.send_message(chat_id,
                "AI Smart Clips (Opus Clips style)\n\n"
                "Downloads a video, transcribes it, finds the most interesting moments, "
                "and creates short clips with burned-in captions.\n\n"
                "Usage: /smartclip <url> [num_clips] [duration] [format]\n\n"
                "Examples:\n"
                "1. /smartclip https://youtube.com/watch?v=xxx\n"
                "2. /smartclip https://youtube.com/watch?v=xxx 3 30\n"
                "3. /smartclip https://youtube.com/watch?v=xxx 5 20 wide\n\n"
                "Defaults: 3 clips, 30s each, vertical 9:16\n"
                "Free tier: max 2 clips | Pro: up to 5"
            )
            return

        args = parts[1].split()
        video_url = args[0] if args else ""
        num_clips = 3
        clip_duration = 30
        aspect_ratio = "9:16"

        if len(args) > 1:
            try:
                num_clips = int(args[1])
            except ValueError:
                pass
        if len(args) > 2:
            try:
                clip_duration = int(args[2])
            except ValueError:
                pass
        if "wide" in args or "landscape" in args:
            aspect_ratio = "16:9"
        if "square" in args:
            aspect_ratio = "1:1"

        _smartclip_max = _tiered_limit(tg_user.plan, {0: 2, 1: 3, 2: 5, 3: 8})
        num_clips = min(num_clips, _smartclip_max)

        if not video_url.startswith("http"):
            await bot.send_message(chat_id, "Please provide a valid video URL")
            return

        allowed, used, limit = await _check_quota(tg_user, db)
        if not allowed:
            await bot.send_message(chat_id,
                f"Monthly limit reached ({used}/{limit}). Use /upgrade to continue.")
            return

        await bot.send_chat_action(chat_id, "upload_video")
        await bot.send_message(chat_id,
            f"Creating {num_clips} smart clips...\n"
            f"Step 1/4: Downloading video...")

        try:
            import base64 as _b64
            result = await smart_clips(video_url, num_clips, clip_duration, aspect_ratio)
            if result.get("success") and result.get("clips"):
                clips = result["clips"]
                await bot.send_message(chat_id,
                    f"Step 4/4: Sending {len(clips)} clips...")
                asyncio.create_task(_log_call(db, tg_user.id, "/telegram/smartclip", "POST", 0, 200))
                sent_count = 0
                for clip in clips:
                    if clip.get("file"):
                        video_bytes = _b64.b64decode(clip["file"])
                        size_mb = len(video_bytes) / 1024 / 1024
                        caption = f"Smart Clip | {clip.get('start_time', '?')} | {clip.get('duration', 0):.0f}s | {size_mb:.1f}MB"
                        if clip.get("preview_text"):
                            caption += f"\n{clip['preview_text'][:80]}"
                        send_result = await bot.send_video(chat_id, video_bytes, caption=caption)
                        if send_result.get("ok"):
                            sent_count += 1
                        else:
                            err_msg = send_result.get("description", "Unknown error")
                            await bot.send_message(chat_id, f"Clip {clip.get('start_time', '?')} couldn't be sent: {err_msg[:100]}")
                await bot.send_message(chat_id, f"Done! {sent_count}/{len(clips)} clips sent.")
            else:
                await bot.send_message(chat_id, f"Smart clipping failed: {result.get('error', 'Unknown error')}")
        except Exception as e:
            logger.error(f"Smart clip error: {e}")
            await bot.send_message(chat_id, f"Error: {str(e)[:200]}")
        return

    # /createvideo — Create AI video from images + voiceover
    if user_text.startswith("/createvideo"):
        parts = user_text.strip().split(None, 1)
        if len(parts) < 2:
            await bot.send_message(chat_id,
                "AI Video Creator\n\n"
                "Create a video with AI-generated images, Ken Burns motion, voiceover "
                "narration, and burned-in captions.\n\n"
                "Usage: /createvideo <topic> [wide|square]\n\n"
                "Examples:\n"
                "1. /createvideo The future of AI in Africa\n"
                "2. /createvideo 5 tips for studying effectively wide\n"
                "3. /createvideo How solar energy works square\n\n"
                "Stew will:\n"
                "1. Write a script with scenes\n"
                "2. Generate AI images for each scene (flux model)\n"
                "3. Add voiceover narration + slow zoom motion\n"
                "4. Burn in captions and combine into a video\n\n"
                "Default format: vertical 9:16 (Reels/Shorts/TikTok). "
                "Add 'wide' for 16:9 landscape or 'square' for 1:1.\n\n"
                "Free tier: up to 3 scenes | Pro: up to 8 scenes"
            )
            return

        raw_args = parts[1].strip()
        aspect_ratio = "9:16"
        arg_words = raw_args.split()
        if arg_words and arg_words[-1].lower() in ("wide", "landscape"):
            aspect_ratio = "16:9"
            raw_args = " ".join(arg_words[:-1]).strip()
        elif arg_words and arg_words[-1].lower() == "square":
            aspect_ratio = "1:1"
            raw_args = " ".join(arg_words[:-1]).strip()

        topic = raw_args or parts[1].strip()
        max_scenes = _tiered_limit(tg_user.plan, {0: 3, 1: 4, 2: 8, 3: 8})

        allowed, used, limit = await _check_quota(tg_user, db)
        if not allowed:
            await bot.send_message(chat_id,
                f"Monthly limit reached ({used}/{limit}). Use /upgrade to continue.")
            return

        await bot.send_chat_action(chat_id, "upload_video")
        await bot.send_message(chat_id,
            f"Creating AI video about: {topic[:100]}\n"
            f"This may take 2-5 minutes. Stew is writing the script, "
            f"generating images, recording voiceover, and combining everything...")

        try:
            import base64 as _b64
            import re as _re2
            import json as _json2

            # Step 1: Generate scenes with LLM
            system_prompt = (
                "You are a video script writer. Create a short video script about the given topic. "
                f"Return ONLY a JSON array of {max_scenes} scenes. "
                "Each scene has 'image_prompt' (a detailed description for AI image generation) "
                "and 'narration' (the voiceover text for that scene, max 2 sentences). "
                "Keep narrations concise (under 150 characters each). "
                "Make images visually striking and professional."
            )
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Create a {max_scenes}-scene video about: {topic}"},
            ]
            llm = get_llm_client()
            llm_result = await asyncio.to_thread(llm.chat, messages)
            raw = llm_result["content"]

            json_match = _re2.search(r'\[.*\]', raw, _re2.DOTALL)
            if json_match:
                scenes = _json2.loads(json_match.group())
            else:
                scenes = [{
                    "image_prompt": f"Professional illustration about {topic}",
                    "narration": f"Let's explore {topic}.",
                }]

            voice = getattr(tg_user, "preferred_voice", None) or "en-US-AriaNeural"

            # Step 2: Create the video (Ken Burns motion + burned-in captions + chosen aspect ratio)
            result = await create_video(topic, scenes, voice, aspect_ratio=aspect_ratio)

            if result.get("success") and result.get("file"):
                video_bytes = _b64.b64decode(result["file"])
                size_mb = len(video_bytes) / 1024 / 1024
                caption = (
                    f"AI Video: {topic[:80]}\n"
                    f"Scenes: {result.get('scenes', 0)} | Duration: {result.get('total_duration', 0):.0f}s | Format: {aspect_ratio} | {size_mb:.1f}MB"
                )
                await bot.send_message(chat_id, f"Sending video ({size_mb:.1f}MB)...")
                send_result = await bot.send_video(chat_id, video_bytes, caption=caption)
                if send_result.get("ok"):
                    asyncio.create_task(_log_call(db, tg_user.id, "/telegram/createvideo", "POST", 0, 200))
                else:
                    err_msg = send_result.get("description", "Unknown error")
                    await bot.send_message(chat_id, f"Video created but couldn't send: {err_msg[:150]}")
            else:
                await bot.send_message(chat_id, f"Video creation failed: {result.get('error', 'Unknown error')}")
        except Exception as e:
            logger.error(f"Create video error: {e}")
            await bot.send_message(chat_id, f"Error: {str(e)[:200]}")
        return

    # /aivideo — Generate REAL AI video from text (using LTX-Video on Hugging Face Spaces)
    if user_text.startswith("/aivideo"):
        parts = user_text.strip().split(None, 1)
        if len(parts) < 2:
            await bot.send_message(chat_id,
                "AI Video Generator (REAL text-to-video)\n\n"
                "Generate actual AI video clips from a text prompt using LTX-Video.\n\n"
                "Usage:\n"
                "1. /aivideo A serene African sunset over a savanna\n"
                "2. /aivideo A cat playing with a ball of yarn\n"
                "3. /aivideo A drone shot of a futuristic city at night\n\n"
                "Options:\n"
                "  Add 'narrate' to include voiceover narration\n"
                "  /aivideo narrate A drone shot of a futuristic city at night\n\n"
                "Each clip: 2-5 seconds. Free tier: 3 clips/day. Pro: 10 clips/day.\n"
                "Video is generated on Hugging Face's free GPU servers."
            )
            return

        raw_args = parts[1].strip()
        add_narration = False
        if raw_args.lower().startswith("narrate "):
            add_narration = True
            raw_args = raw_args[7:].strip()
        
        prompt = raw_args
        if not prompt:
            await bot.send_message(chat_id, "Please provide a text prompt for the video.")
            return

        allowed, used, limit = await _check_quota(tg_user, db)
        if not allowed:
            await bot.send_message(chat_id,
                f"Monthly limit reached ({used}/{limit}). Use /upgrade to continue.")
            return

        await bot.send_chat_action(chat_id, "upload_video")
        await bot.send_message(chat_id,
            f"Generating AI video...\n"
            f"Prompt: {prompt[:100]}\n"
            f"Model: LTX-Video (Hugging Face)\n"
            f"This may take 10-30 seconds...")

        try:
            import base64 as _b64
            narration_text = ""
            if add_narration:
                # Generate narration text from the prompt using LLM
                try:
                    llm = get_llm_client()
                    narr_prompt = (
                        f"Write a single sentence (max 15 words) of narration for a video about: {prompt}. "
                        "Return ONLY the narration text, no quotes or labels."
                    )
                    narr_result = await asyncio.to_thread(llm.chat, [
                        {"role": "user", "content": narr_prompt}
                    ])
                    narration_text = narr_result["content"].strip()
                    await bot.send_message(chat_id, f"Narration: \"{narration_text}\"")
                except Exception:
                    narration_text = prompt

            result = await generate_ai_video(
                prompt=prompt,
                duration=3.0,
                add_narration=add_narration,
                narration_text=narration_text,
                voice=getattr(tg_user, "preferred_voice", None) or "en-US-AriaNeural",
            )

            if result.get("success") and result.get("file"):
                video_bytes = _b64.b64decode(result["file"])
                size_kb = len(video_bytes) / 1024
                caption = f"AI Video | LTX-Video | {result.get('duration', 0):.1f}s | {size_kb:.0f}KB"
                if result.get("narration_added"):
                    caption += " | with narration"
                await bot.send_message(chat_id, f"Sending AI video ({size_kb:.0f}KB)...")
                send_result = await bot.send_video(chat_id, video_bytes, caption=caption)
                if send_result.get("ok"):
                    asyncio.create_task(_log_call(db, tg_user.id, "/telegram/aivideo", "POST", 0, 200))
                else:
                    err_msg = send_result.get("description", "Unknown error")
                    await bot.send_message(chat_id, f"Video generated but couldn't send: {err_msg[:150]}")
            else:
                await bot.send_message(chat_id, f"AI video generation failed: {result.get('error', 'Unknown error')}")
        except Exception as e:
            logger.error(f"AI video command error: {e}")
            await bot.send_message(chat_id, f"Error: {str(e)[:200]}")
        return

    # /aivideos — Generate multi-scene AI video with narration
    if user_text.startswith("/aivideos"):
        parts = user_text.strip().split(None, 1)
        if len(parts) < 2:
            await bot.send_message(chat_id,
                "Multi-Scene AI Video (REAL text-to-video)\n\n"
                "Generate a multi-scene video with REAL AI video clips + narration.\n\n"
                "Usage: /aivideos <topic>\n\n"
                "Examples:\n"
                "1. /aivideos The future of renewable energy in Africa\n"
                "2. /aivideos A day in the life of a Lagos entrepreneur\n\n"
                "Stew will:\n"
                "1. Write a script with scenes\n"
                "2. Generate REAL AI video for each scene (LTX-Video)\n"
                "3. Add voiceover narration\n"
                "4. Combine into one video\n\n"
                "Each scene takes ~10-20s to generate. 2 scenes for free, 5 for Pro.\n"
                "Total time: 30-90 seconds."
            )
            return

        topic = parts[1].strip()
        max_scenes = _tiered_limit(tg_user.plan, {0: 2, 1: 3, 2: 5, 3: 5})

        allowed, used, limit = await _check_quota(tg_user, db)
        if not allowed:
            await bot.send_message(chat_id,
                f"Monthly limit reached ({used}/{limit}). Use /upgrade to continue.")
            return

        await bot.send_chat_action(chat_id, "upload_video")
        await bot.send_message(chat_id,
            f"Creating multi-scene AI video about: {topic[:80]}\n"
            f"Scenes: {max_scenes} | Model: LTX-Video\n"
            f"Each scene generates a real AI video clip + narration.\n"
            f"Estimated time: {max_scenes * 20}s...")

        try:
            import base64 as _b64
            import re as _re2
            import json as _json2

            # Step 1: Generate scenes with LLM
            system_prompt = (
                "You are a video script writer. Create a short video script about the given topic. "
                f"Return ONLY a JSON array of {max_scenes} scenes. "
                "Each scene has 'video_prompt' (a detailed description for AI VIDEO generation — describe motion, camera movement, lighting) "
                "and 'narration' (the voiceover text for that scene, max 1 sentence under 100 characters). "
                "Make video prompts cinematic and visually striking."
            )
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Create a {max_scenes}-scene video about: {topic}"},
            ]
            llm = get_llm_client()
            llm_result = await asyncio.to_thread(llm.chat, messages)
            raw = llm_result["content"]

            json_match = _re2.search(r'\[.*\]', raw, _re2.DOTALL)
            if json_match:
                scenes = _json2.loads(json_match.group())
            else:
                scenes = [{
                    "video_prompt": f"Cinematic shot about {topic}",
                    "narration": f"Let's explore {topic}.",
                }]

            voice = getattr(tg_user, "preferred_voice", None) or "en-US-AriaNeural"

            await bot.send_message(chat_id,
                f"Script ready! {len(scenes)} scenes.\n"
                f"Generating AI video clips (this is the slow part)...")

            # Step 2: Generate multi-scene AI video
            result = await generate_ai_video_with_narration(topic, scenes, voice, clip_duration=2.5)

            if result.get("success") and result.get("file"):
                video_bytes = _b64.b64decode(result["file"])
                size_mb = len(video_bytes) / 1024 / 1024
                caption = (
                    f"AI Video: {topic[:60]}\n"
                    f"Scenes: {result.get('scenes', 0)} | Duration: {result.get('total_duration', 0):.1f}s | {size_mb:.1f}MB | LTX-Video"
                )
                await bot.send_message(chat_id, f"Sending video ({size_mb:.1f}MB)...")
                send_result = await bot.send_video(chat_id, video_bytes, caption=caption)
                if send_result.get("ok"):
                    asyncio.create_task(_log_call(db, tg_user.id, "/telegram/aivideos", "POST", 0, 200))
                else:
                    err_msg = send_result.get("description", "Unknown error")
                    await bot.send_message(chat_id, f"Video created but couldn't send: {err_msg[:150]}")
            else:
                await bot.send_message(chat_id, f"AI video generation failed: {result.get('error', 'Unknown error')}")
        except Exception as e:
            logger.error(f"Multi-scene AI video error: {e}")
            await bot.send_message(chat_id, f"Error: {str(e)[:200]}")
        return

    # /webbuild — Motion Design Website Builder (Kimi K2 style)
    if user_text.startswith("/webbuild"):
        _wb_desc = user_text.strip()[8:].strip()  # remove "/webbuild"
        if not _wb_desc:
            await bot.send_message(
                chat_id,
                "🏗️ *Motion Design Website Builder*\n\n"
                "I generate a premium, animated single-page website from your description "
                "and host it live — you get a real shareable link instantly.\n\n"
                "*Usage:*\n"
                "1. /webbuild a boutique coffee roastery in Lagos\n"
                "2. /webbuild a tech startup that builds AI agents for African businesses\n"
                "3. /webbuild a fitness coaching service with online booking\n\n"
                "Styles (optional, add at end): dark, vibrant, minimal, corporate, warm\n"
                "Example: /webbuild a fashion brand for Gen Z — vibrant",
            )
            return

        # Parse optional style keyword
        _wb_style = "premium-dark"
        _wb_styles_map = {
            "dark": "premium-dark", "premium": "premium-dark",
            "vibrant": "vibrant", "bold": "vibrant",
            "minimal": "minimal", "clean": "minimal",
            "corporate": "corporate", "professional": "corporate",
            "warm": "warm", "cozy": "warm",
        }
        _wb_words = _wb_desc.lower().split()
        for _wb_w in _wb_words:
            if _wb_w in _wb_styles_map:
                _wb_style = _wb_styles_map[_wb_w]
                _wb_desc = _wb_desc.replace(_wb_w, "").strip()
                break

        # Tier gating: free users get 1 webbuild/month, student 3, pro+ unlimited
        _wb_tier = _plan_tier(tg_user.plan)
        if _wb_tier == 0:  # free
            _wb_count = await db.execute(
                select(func.count(GeneratedWebsite.id)).where(
                    GeneratedWebsite.telegram_user_id == str(tg_user.id),
                )
            )
            if (_wb_count.scalar() or 0) >= 1:
                await bot.send_message(
                    chat_id,
                    "🚫 Free tier allows 1 website build. Upgrade with /upgrade to build more "
                    "(Student plan: 3 sites, Pro: unlimited).",
                )
                return
        elif _wb_tier == 1:  # student
            _wb_count = await db.execute(
                select(func.count(GeneratedWebsite.id)).where(
                    GeneratedWebsite.telegram_user_id == str(tg_user.id),
                )
            )
            if (_wb_count.scalar() or 0) >= 3:
                await bot.send_message(
                    chat_id,
                    "🚫 Student plan allows 3 website builds. Upgrade to Pro for unlimited. /upgrade",
                )
                return

        await bot.send_chat_action(chat_id, "upload_document")
        await bot.send_message(
            chat_id,
            f"🏗️ Building your motion-design website...\n"
            f"Style: {_wb_style}\n"
            f"Topic: {_wb_desc[:80]}\n"
            f"This takes ~15-30 seconds. I'll send you a live link when it's ready.",
        )

        try:
            _wb_result = await build_motion_website(_wb_desc, _wb_style)
            if not _wb_result.get("success"):
                await bot.send_message(chat_id, f"Build failed: {_wb_result.get('error', 'Unknown error')}. Try again with more detail.")
                return

            _wb_site = GeneratedWebsite(
                telegram_user_id=str(tg_user.id),
                title=_wb_result["title"],
                description=_wb_desc[:500],
                html=_wb_result["html"],
                style=_wb_style,
            )
            db.add(_wb_site)
            await db.commit()
            await db.refresh(_wb_site)

            _wb_url = f"https://stew-agent.onrender.com/site/{_wb_site.id}"
            _wb_size_kb = _wb_result["size_bytes"] // 1024
            await bot.send_message(
                chat_id,
                f"✅ *Your website is live!*\n\n"
                f"Title: {_wb_result['title']}\n"
                f"Size: {_wb_size_kb}KB\n"
                f"Style: {_wb_style}\n\n"
                f"Link: {_wb_url}\n\n"
                f"Share it with anyone — it's a real website, not a screenshot. "
                f"Open it on your phone to see the animations and scroll effects.",
            )
            asyncio.create_task(_log_call(db, tg_user.id, "/telegram/webbuild", "POST", 0, 200))
        except Exception as e:
            logger.error(f"Webbuild error: {e}")
            await bot.send_message(chat_id, f"Build error: {str(e)[:200]}")
        return

    # /voice — Toggle voice note replies
    if user_text.startswith("/voice"):
        parts = user_text.strip().split(maxsplit=1)
        if len(parts) > 1 and parts[1].lower() in ("on", "off", "enable", "disable"):
            enable = parts[1].lower() in ("on", "enable")
            tg_user.voice_enabled = enable
            await db.commit()
            status = "ON 🔊 — Stew will now reply with voice notes" if enable else "OFF 🔇 — Stew will reply with text"
            await bot.send_message(chat_id, f"Voice replies: {status}\n\nTip: /voice to toggle, /voice list to see available voices, /voice <name> to pick a voice")
            return
        elif len(parts) > 1 and parts[1].lower() == "list":
            voice_list = "\n".join(f"  /voice {k} — {desc}" for k, (v, desc) in VOICE_OPTIONS.items())
            await bot.send_message(chat_id, f"Available voices:\n\n{voice_list}\n\nUse /voice <name> to set your preferred voice.")
            return
        elif len(parts) > 1 and parts[1].lower() in VOICE_OPTIONS:
            voice_id, desc = VOICE_OPTIONS[parts[1].lower()]
            tg_user.preferred_voice = voice_id
            tg_user.voice_enabled = True
            await db.commit()
            await bot.send_message(chat_id, f"Voice set to {desc} ✅\nStew will now reply with voice notes using this voice.")
            return
        else:
            # Toggle current state
            tg_user.voice_enabled = not tg_user.voice_enabled
            await db.commit()
            status = "ON 🔊 — Stew will now reply with voice notes" if tg_user.voice_enabled else "OFF 🔇 — Stew will reply with text"
            await bot.send_message(chat_id, f"Voice replies: {status}\n\nTip: /voice list to see available voices")
            return

    if user_text.startswith("/clear"):
        try:
            conv_q = await db.execute(select(Conversation).where(Conversation.user_id == tg_user.id).order_by(Conversation.updated_at.desc()).limit(1))
            conv = conv_q.scalar_one_or_none()
            if conv:
                from sqlalchemy import text as _text
                await db.execute(_text("DELETE FROM messages WHERE conversation_id = :cid"), {"cid": conv.id})
                await db.commit()
        except Exception as ce:
            logger.warning(f"Clear error: {ce}")
        await bot.send_message(chat_id, "Conversation cleared. Fresh start!")
        return {"ok": True}

    # ── /research COMMAND ──────────────────────────────────────────────────────
    if user_text.startswith("/research"):
        query = user_text[9:].strip()
        if not query:
            await bot.send_message(chat_id, "Send: /research impact of AI on African economies")
            return {"ok": True}
        await bot.send_message(chat_id, f"Researching: {query[:80]}...")
        await bot.send_typing(chat_id)
        try:
            searcher = get_searcher()
            research_results = await asyncio.to_thread(searcher.stew_extension_research, query, 3)
            if research_results.get("grounded"):
                num_sources = len(research_results.get("organic", []))
                await bot.send_message(chat_id, f"Found {num_sources} sources. Analyzing...")
                await bot.send_typing(chat_id)
                llm = get_llm_client()
                system = STEW_MASTER_PROMPT + "\n\nTelegram response. Comprehensive research report."
                messages = [
                    {"role": "system", "content": system},
                    {"role": "user", "content": f"Research: {query}\n\nContext:\n{research_results.get('report', '')[:6000]}"},
                ]
                result = await asyncio.to_thread(llm.chat, messages)
                await bot.send_message(chat_id, clean_response(result["content"]))
            else:
                await bot.send_message(chat_id, "Not enough sources found. Try a different query.")
        except Exception as e:
            await bot.send_message(chat_id, f"Error: {str(e)[:100]}")
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
                 "slides about", "slides for", "make a slide", "create a slide",
                 "make slides", "create slides", "generate slides",
                 "slide for", "slide about", "pitch deck", "create a deck",
                 "make a deck", "generate a deck", "presentation about",
                 "presentation for", "deck for"],
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
                import json as _json
                import re as _re

                # 1. Check whether the user hand-authored their own slide-by-slide outline
                #    (e.g. "Slide 2 - Our Vision\nExplain the church's mission..."). If so,
                #    follow THEIR structure and titles instead of forcing a generic template —
                #    this is what makes Stew follow the prompt like Canva/manual design would.
                slide_pattern = _re.compile(r'(?im)^\s*slide\s*(\d+)\s*[-\u2013\u2014:]?\s*(.*)$')
                matches = list(slide_pattern.finditer(user_text))
                explicit_slides = []
                if len(matches) >= 2:
                    for idx, m in enumerate(matches):
                        slide_num = m.group(1)
                        slide_title_raw = m.group(2).strip(" -\u2013\u2014:")
                        start = m.end()
                        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(user_text)
                        body = user_text[start:end].strip()
                        explicit_slides.append({"number": slide_num, "title": slide_title_raw or f"Slide {slide_num}", "brief": body})

                # 2. Extract an explicit slide count request ("10 slides", "15-slide deck")
                count_match = _re.search(r'(\d+)\s*[- ]?slides?\b', user_lower)
                requested_count = int(count_match.group(1)) if count_match else None
                if requested_count:
                    requested_count = max(3, min(requested_count, 30))

                if explicit_slides:
                    # Flesh out real bullet content for the user's own outline, keeping their
                    # exact slide titles and following each slide's brief closely.
                    outline_desc = "\n".join([f"Slide {s['number']}: {s['title']} \u2014 {s['brief'][:300]}" for s in explicit_slides])
                    system_prompt = (
                        "You are a presentation content writer. The user gave you their own slide "
                        "outline with titles and briefs. Write concise, professional bullet-point "
                        "content for EACH slide based on its brief. Return ONLY a JSON array of "
                        "objects with 'title' and 'content' (bullets separated by newlines, '- ' "
                        "prefix, max 6 bullets, max 12 words each). Keep the exact slide titles "
                        "given. Follow each brief closely \u2014 do not invent unrelated content."
                    )
                    user_msg = f"Presentation topic: {doc_topic}\n\nSlide outline:\n{outline_desc}\n\nReturn a JSON array, one object per slide, in the given order."
                    messages = [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_msg}]
                    result = await asyncio.to_thread(llm.chat, messages, max_tokens=3000)
                    raw_content = result["content"]
                    json_match = _re.search(r'\[.*\]', raw_content, _re.DOTALL)
                    if json_match:
                        try:
                            slides = _json.loads(json_match.group())
                        except Exception:
                            slides = [{"title": s["title"], "content": s["brief"][:200]} for s in explicit_slides]
                    else:
                        slides = [{"title": s["title"], "content": s["brief"][:200]} for s in explicit_slides]
                else:
                    # Generic template, respecting a requested slide count if the user gave one
                    target_count = requested_count or 10
                    system_prompt = "You are a presentation designer. Return ONLY a JSON array of slides. Each slide has 'title' and 'content'. Content should be bullet points separated by newlines, with '- ' prefix for each bullet. Keep bullets concise (max 10 words each). Max 6 bullets per slide."
                    user_msg = (
                        f"Create a {target_count}-slide presentation about: {doc_topic}. "
                        f"Design the slide structure to fit the topic \u2014 do NOT default to a startup pitch deck. "
                        f"For a church fundraiser: vision, problem, solution, funding needs, impact, closing. "
                        f"For a product: overview, features, benefits, pricing, testimonials, closing. "
                        f"For education: introduction, key concepts, examples, applications, summary. "
                        f"For a report: executive summary, findings, analysis, recommendations, next steps. "
                        f"Always include a title slide as slide 1 and a closing/thank-you slide as the last slide. "
                        f"Fit exactly {target_count} slides total. JSON array only."
                    )
                    messages = [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_msg},
                    ]
                    result = await asyncio.to_thread(llm.chat, messages)
                    raw_content = result["content"]
                    json_match = _re.search(r'\[.*\]', raw_content, _re.DOTALL)
                    if json_match:
                        try:
                            slides = _json.loads(json_match.group())
                        except Exception:
                            slides = [{"title": doc_topic, "content": "Generated by S.T.E.W"}]
                    else:
                        slides = [{"title": doc_topic, "content": "Generated by S.T.E.W Agent"}]

                # Auto-theme detection + real AI-generated hero images on title/closing slides.
                # Wrapped in a thread since image fetching does blocking network I/O.
                doc_result = await asyncio.to_thread(generate_pptx, slides, doc_topic)
            else:
                # For PDF and DOCX, generate text content
                system_prompt = f"You are a professional writer. Create a well-structured, concise document (under 1200 words) about: {doc_topic}. Use markdown: # for title, ## for headings, - for bullet points. Do NOT use tables. Do NOT use special unicode symbols, subscripts, or superscripts — write exponents as 'x10^9' and use plain ASCII only. Write a complete document that ends with a proper conclusion — never cut off mid-sentence."
                user_msg = f"Write a complete, well-structured document about: {doc_topic}. Include an introduction, 3-5 main sections with headings, and a conclusion. Keep it focused and under 1200 words so it fits completely."
                messages = [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_msg},
                ]
                result = await asyncio.to_thread(llm.chat, messages, max_tokens=3000)
                raw_content = result["content"]

                if doc_type == "pdf":
                    doc_result = generate_pdf(raw_content, doc_topic)
                elif doc_type == "docx":
                    doc_result = generate_docx(raw_content, doc_topic)
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

    # ── IMAGE GENERATION ──────────────────────────────────────────────────────
    image_keywords = ["generate image", "create image", "draw", "make an image",
                      "generate an image", "create an image", "make a picture",
                      "generate a picture", "create a picture", "ai image",
                      "image of", "picture of"]
    # Defense-in-depth: never treat a message as a raw image request if it clearly
    # reads as a document/slide/presentation request (e.g. long prompts describing
    # a "Slide 2" or "presentation" that happen to also mention "image of X" inside
    # design instructions). Document generation is checked above and returns early,
    # but this guard protects against edge phrasing that slips past that check too.
    doc_signal_words = ["slide", "slides", "presentation", "deck", "powerpoint",
                         "pptx", "pitch deck", "document", "pdf", "docx", "xlsx",
                         "spreadsheet", "word doc", "report about", "report on"]
    looks_like_doc_request = any(w in user_lower for w in doc_signal_words)
    is_image_request = any(kw in user_lower for kw in image_keywords) and not looks_like_doc_request

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

    # ── LIVE DATA FAST PATH (weather / crypto / stock / fx) ────────────────────
    # These MUST use real deterministic APIs, never free-form LLM + search text,
    # because the LLM was hallucinating "I'll perform a web search..." over and
    # over with different fake numbers each time (the reported repeating bug).
    weather_kw = ["weather in", "weather for", "temperature in", "temperature at",
                  "how hot is", "how cold is", "is it raining in", "forecast for", "forecast in"]
    crypto_kw = ["bitcoin price", "btc price", "ethereum price", "eth price", "crypto price",
                 "price of bitcoin", "price of ethereum", "price of btc", "price of eth",
                 "dogecoin price", "price of doge"]
    fx_kw = ["exchange rate", "naira to dollar", "dollar to naira", "usd to ngn", "ngn to usd",
             "convert naira", "convert dollar", "how much is a dollar", "how much is 1 dollar"]

    is_weather_q = any(kw in user_lower for kw in weather_kw) or (
        user_lower.startswith("weather") or " weather " in f" {user_lower} "
    )
    is_crypto_q = any(kw in user_lower for kw in crypto_kw)
    is_fx_q = any(kw in user_lower for kw in fx_kw)

    if is_weather_q:
        # Extract city name — text after "in"/"for"/"at", else fall back to whole message
        import re as _re
        city = None
        m = _re.search(r'weather\s+(?:in|for|at)\s+([a-zA-Z\s]+)', user_lower)
        if m:
            city = m.group(1).strip().rstrip("?.! ")
        if not city:
            m = _re.search(r'temperature\s+(?:in|at)\s+([a-zA-Z\s]+)', user_lower)
            if m:
                city = m.group(1).strip().rstrip("?.! ")
        if not city:
            city = "Lagos"
        await bot.send_typing(chat_id)
        try:
            from server.skills_engine import weather as weather_skill
            data = await weather_skill(city)
            if "error" not in data:
                reply = (
                    f"Weather in {data.get('city', city).title()}:\n"
                    f"{data.get('description', 'N/A')}\n\n"
                    f"Temperature: {data.get('temp_c')}°C ({data.get('temp_f')}°F)\n"
                    f"Feels like: {data.get('feels_like_c')}°C\n"
                    f"Humidity: {data.get('humidity')}%\n"
                    f"Wind: {data.get('wind_kmph')} km/h"
                )
            else:
                reply = f"Couldn't get live weather for {city} right now: {data['error']}"
            await bot.send_message(chat_id, reply)
        except Exception as e:
            logger.error(f"Weather fast-path error: {e}")
            await bot.send_message(chat_id, f"Couldn't fetch weather for {city} right now. Try again in a moment.")
        return {"ok": True}

    if is_crypto_q:
        import re as _re
        symbol = "bitcoin"
        for coin in ["bitcoin", "btc", "ethereum", "eth", "dogecoin", "doge"]:
            if coin in user_lower:
                symbol = {"btc": "bitcoin", "eth": "ethereum", "doge": "dogecoin"}.get(coin, coin)
                break
        await bot.send_typing(chat_id)
        try:
            from server.market_data import get_crypto_price
            data = await get_crypto_price(symbol, "usd")
            if "error" not in data:
                reply = (
                    f"{symbol.upper()} live price:\n"
                    f"USD: ${data.get('price_usd')}\n"
                    f"NGN: ₦{data.get('price_ngn')}\n"
                    f"24h change: {data.get('change_24h_pct')}%"
                )
            else:
                reply = f"Couldn't get live price: {data['error']}"
            await bot.send_message(chat_id, reply)
        except Exception as e:
            logger.error(f"Crypto fast-path error: {e}")
            await bot.send_message(chat_id, "Couldn't fetch live price right now. Try again in a moment.")
        return {"ok": True}

    if is_fx_q:
        await bot.send_typing(chat_id)
        try:
            from server.skills_engine import currency_rates as currency_rates_skill
            data = await currency_rates_skill("USD")
            rate = data.get("rates", {}).get("NGN")
            if rate:
                reply = f"1 USD = ₦{rate}"
            else:
                reply = "Couldn't get the exchange rate right now."
            await bot.send_message(chat_id, reply)
        except Exception as e:
            logger.error(f"FX fast-path error: {e}")
            await bot.send_message(chat_id, "Couldn't fetch the exchange rate right now. Try again in a moment.")
        return {"ok": True}

    # ── TOOL-CALLING AGENT (Kimi-style) ─────────────────────────────────────────
    # Detect requests that need tool calling: code, math, data analysis, documents
    needs_tools = any(kw in user_lower for kw in [
        "calculate", "compute", "solve", "math", "equation", "formula",
        "analyze data", "data analysis", "chart", "graph", "plot",
        "statistics", "probability", "compound", "interest",
        "convert", "currency", "naira to", "dollar to", "exchange rate",
        "budget", "loan", "mortgage", "investment", "roi",
        "run code", "python", "code", "program", "algorithm",
        "make a document", "create a document", "generate report",
        "business plan", "financial projection", "cash flow",
        # Document generation triggers (broader)
        "make a pdf", "create a pdf", "generate a pdf",
        "make a word", "create a word", "generate a word",
        "make a spreadsheet", "create a spreadsheet",
        "make a powerpoint", "create a powerpoint",
        "make a presentation", "create a presentation", "generate a presentation",
        "make a slide", "create a slide", "make slides", "create slides",
        "generate slides", "slide for", "slides for", "slides about",
        "pitch deck", "make a deck", "create a deck", "generate a deck",
        "make a report", "create a report", "generate a report",
        "make a document", "create a document", "generate a document",
    ])

    if needs_tools:
        await bot.send_typing(chat_id)
        try:
            from server.tool_agent import run_agent_loop
            agent_result = await run_agent_loop(user_text, bot=bot, chat_id=chat_id, max_iterations=5)

            # Send any generated files
            if agent_result.get("files"):
                import base64 as _b64
                for f in agent_result["files"]:
                    try:
                        file_bytes = _b64.b64decode(f["base64"])
                        filename = f.get("filename", f"stew_document.{f.get('doc_type','pdf')}")
                        caption = f"S.T.E.W generated {f.get('doc_type','').upper()}"
                        await bot.send_document(chat_id, file_bytes, filename, caption)
                    except Exception as fe:
                        logger.error(f"File send error: {fe}")

            # Send the final response — clean it up
            response = agent_result.get("response", "")
            if response:
                # Strip any leftover TOOL_CALL artifacts
                import re as _re
                response = _re.sub(r'TOOL_CALL:\s*\{.*?\}', '', response, flags=_re.DOTALL).strip()
                response = _re.sub(r'TOOL_RESULT[\s\S]*', '', response).strip()
                # If response is too long (wall of text), truncate
                if len(response) > 800:
                    response = response[:800] + "..."
                await bot.send_message(chat_id, response)
            elif agent_result.get("files"):
                await bot.send_message(chat_id, "Done! Your file is ready above.")
            else:
                await bot.send_message(chat_id, "Task completed.")

            # Log
            if tg_user:
                background_tasks.add_task(_log_call, db, tg_user.id, "/telegram/tool_agent", "POST", 0, 200)

            return {"ok": True}
        except Exception as e:
            logger.error(f"Tool agent error: {e}", exc_info=True)
            await bot.send_message(chat_id, "Agent encountered an error. Trying regular mode...")
            # Fall through to regular chat

    # ── REGULAR CHAT WITH SEARCH + RESEARCH ────────────────────────────────────
    llm = get_llm_client()
    searcher = get_searcher()

    web_grounded = False
    system = STEW_MASTER_PROMPT + "\n\nYou are responding via Telegram. Keep answers concise and well-formatted for mobile. Use plain text, avoid complex markdown."

    # Detect if search is needed
    needs_search = any(kw in user_lower for kw in [
        "latest", "current", "today", "news", "score",
        "what is", "who is", "how to",
        "happened", "update", "recent",
        "compare",
    ])
    # NOTE: weather, stock, crypto, exchange rate, price all have dedicated
    # fast-path handlers above. Don't trigger search for those.
    # Removed broad keywords: "search", "find", "best", "top", "when",
    # "where", "which", "2024", "2025", "2026" — these triggered search on
    # almost any question, causing the repeating search loop.
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
        await bot.send_message(chat_id, f"Searching the web...")
        await bot.send_typing(chat_id)
        try:
            search_results = await asyncio.to_thread(searcher.search, user_text, 5)
            if not search_results.get("grounded"):
                # Try Jina AI fallback ONCE (different backend, not a re-run)
                search_results = await asyncio.to_thread(searcher.stew_extension_search, user_text, 5)
            if search_results.get("grounded"):
                context = searcher.format_results_for_llm(search_results)
                system += f"\n\nWEB SEARCH CONTEXT:\n{context}"
                web_grounded = True
            # If search failed entirely, just answer without web context — don't keep trying
        except Exception as e:
            logger.warning(f"Telegram search failed: {e}")
            # Don't retry — just answer without web context

    # Reuse the most recent conversation for this Telegram user
    from sqlalchemy import select as _sel
    _conv_q = await db.execute(
        _sel(Conversation).where(Conversation.user_id == tg_user.id)
        .order_by(Conversation.updated_at.desc()).limit(1)
    )
    _existing_conv = _conv_q.scalar_one_or_none()
    conv = _existing_conv if _existing_conv else await get_or_create_conversation(db, tg_user.id, None)

    recalled_tg = await get_relevant_context(db, tg_user.id, user_text, platform="telegram")
    await append_message(db, conv, "user", user_text, platform="telegram")
    messages = build_llm_messages(conv, system, recalled_tg)

    try:
        result = await asyncio.to_thread(llm.chat, messages)
        reply = clean_response(result["content"])
        await append_message(db, conv, "assistant", reply, platform="telegram")

        # Save conversation to Supabase for persistent history (survives redeploy)
        if supabase_configured():
            asyncio.create_task(supa_save_conv(str(tg_user.telegram_id), "user", user_text))
            asyncio.create_task(supa_save_conv(str(tg_user.telegram_id), "assistant", reply))

        # If user has voice replies enabled, send as voice note
        if getattr(tg_user, "voice_enabled", False):
            voice_name = getattr(tg_user, "preferred_voice", None) or "en-US-AriaNeural"
            audio_bytes, voice_err = await _synthesize_voice(reply, voice_name)
            if audio_bytes:
                await bot.send_voice(chat_id, audio_bytes)
            else:
                logger.warning(f"Voice synthesis failed: {voice_err}")
                await bot.send_message(chat_id, reply, parse_mode="")
        else:
            await bot.send_message(chat_id, reply, parse_mode="")

        # Show sponsored ad to free users every 5 messages
        _mc = await db.execute(select(func.count(APICall.id)).where(APICall.user_id == tg_user.id))
        _msg_count = _mc.scalar() or 0
        await _display_ad_if_needed(bot, chat_id, db, tg_user.plan, _msg_count)

        # Background memory extraction — fire-and-forget with its own DB session
        # (was broken: async def passed to asyncio.to_thread never executed)
        async def _extract_memories_safe(uid, u_msg, a_reply, cid):
            from server.database import AsyncSessionLocal
            async with AsyncSessionLocal() as mem_db:
                try:
                    def _sync_llm_chat_mem(messages, max_tokens=1000):
                        llm_m = get_llm_client()
                        return llm_m.chat(messages, max_tokens=max_tokens)
                    await extract_and_store_memories(
                        mem_db, uid, u_msg, a_reply, "telegram", cid, _sync_llm_chat_mem
                    )
                except Exception as me:
                    logger.warning(f"Memory extraction failed (non-fatal): {me}")

        asyncio.create_task(_extract_memories_safe(tg_user.id, user_text, reply, conv.id))

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

