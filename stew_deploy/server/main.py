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
    Depends, FastAPI, File, Form, HTTPException,
    Request, UploadFile, BackgroundTasks
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr, field_validator
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
from server.memory import (
    append_message, build_llm_messages, get_or_create_conversation,
)
from server.middleware import RateLimitMiddleware, SecurityHeadersMiddleware
from server.models import APICall, Conversation, Document, PaymentTransaction, User
from server.payments import initialize_payment, validate_webhook_signature, verify_payment, upgrade_user_plan
from server.search import get_searcher

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)
settings = get_settings()

from server.system_prompt import STEW_MASTER_PROMPT as STEW_SYSTEM_PROMPT
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
    version="5.0.0",
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

async def _log_call(db: AsyncSession, user_id: Optional[str], endpoint: str,
                    method: str, tokens: int, status: int):
    call = APICall(
        user_id=user_id,
        endpoint=endpoint,
        method=method,
        tokens_used=tokens,
        status_code=status,
    )
    db.add(call)
    await db.commit()


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


class ChatRequest(BaseModel):
    message: str
    conversation_id: Optional[str] = None
    api_key: Optional[str] = None
    web_search: bool = True


class TaskRequest(BaseModel):
    task: str
    api_key: str
    context: Optional[str] = None


class BrowseRequest(BaseModel):
    url: str
    question: Optional[str] = None
    api_key: str


class GeneratePDFRequest(BaseModel):
    content: str
    title: str = "Document"
    api_key: str


class GenerateDOCXRequest(BaseModel):
    content: str
    title: str = "Document"
    api_key: str


class GenerateXLSXRequest(BaseModel):
    data: list[dict]
    sheet_name: str = "Sheet1"
    title: str = "Spreadsheet"
    api_key: str


class GeneratePPTXRequest(BaseModel):
    slides: list[dict]
    title: str = "Presentation"
    api_key: str


class GenerateHTMLRequest(BaseModel):
    content: str
    title: str = "Report"
    api_key: str


class APICallRequest(BaseModel):
    url: str
    method: str = "GET"
    headers: dict = {}
    body: Optional[dict] = None
    api_key: str


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

@app.get("/heartbeat")
async def heartbeat():
    return {
        "status": "ok",
        "version": "5.0.0",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "environment": settings.ENVIRONMENT,
        "providers": {
            "groq": bool(settings.GROQ_API_KEY),
            "openrouter": bool(settings.OPENROUTER_API_KEY),
            "openai": bool(settings.OPENAI_API_KEY),
            "search": bool(settings.SERPER_API_KEY),
            "payments": bool(settings.PAYSTACK_SECRET_KEY),
        },
    }


# ── Auth ───────────────────────────────────────────────────────────────────────

@app.post("/auth/register", status_code=201)
async def register(body: RegisterRequest, db: AsyncSession = Depends(get_db)):
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

    return {
        "api_key": user.api_key,
        "user_id": user.id,
        "plan": user.plan,
        "calls_limit": settings.PLAN_CALL_LIMITS[user.plan],
        "success": True,
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
        "success": True,
    }


@app.get("/auth/me")
async def get_me(current_user: User = Depends(get_current_user_jwt)):
    return {
        "id": current_user.id,
        "name": current_user.name,
        "email": current_user.email,
        "plan": current_user.plan,
        "api_key": current_user.api_key,
        "created_at": current_user.created_at.isoformat(),
    }


# ── Chat ───────────────────────────────────────────────────────────────────────

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
            user = await get_user_by_api_key(body.api_key, db)
        except HTTPException:
            pass

    # Web search grounding
    search_results = None
    sources = []
    web_grounded = False

    if body.web_search and searcher._is_available():
        # Decide if query needs fresh data
        needs_search_keywords = [
            "latest", "current", "today", "news", "score", "price",
            "weather", "stock", "who won", "when is", "what is the",
        ]
        if any(kw in body.message.lower() for kw in needs_search_keywords):
            try:
                search_results = searcher.search(body.message, num_results=5)
                if search_results.get("grounded"):
                    web_grounded = True
                    sources = [
                        {"title": r["title"], "url": r["link"], "snippet": r["snippet"]}
                        for r in search_results.get("organic", [])
                    ]
            except Exception as e:
                logger.warning(f"Search failed, continuing without: {e}")

    # Build messages
    system = STEW_SYSTEM_PROMPT
    if search_results and web_grounded:
        context = searcher.format_results_for_llm(search_results)
        system += f"\n\nWEB SEARCH CONTEXT (use ONLY this for factual claims):\n{context}"

    if user:
        conv = await get_or_create_conversation(db, user.id, body.conversation_id)
        await append_message(db, conv, "user", body.message)
        messages = build_llm_messages(conv, system)
    else:
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": body.message},
        ]

    result = llm.chat(messages)
    response_text = result["content"]
    tokens = result["tokens"].get("total", 0)

    if user:
        await append_message(db, conv, "assistant", response_text)

    if user:
        background_tasks.add_task(_log_call, db, user.id, "/chat", "POST", tokens, 200)

    return {
        "response": response_text,
        "web_grounded": web_grounded,
        "sources": sources,
        "provider": result.get("provider"),
        "conversation_id": conv.id if user else None,
        "success": True,
    }


# ── Task ───────────────────────────────────────────────────────────────────────

@app.post("/task")
async def task(
    body: TaskRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    user = await get_user_by_api_key(body.api_key, db)
    llm = get_llm_client()
    searcher = get_searcher()

    # Always try to search for task context
    search_context = ""
    sources = []
    web_grounded = False

    if searcher._is_available():
        try:
            sr = searcher.search(body.task, num_results=5)
            if sr.get("grounded"):
                search_context = searcher.format_results_for_llm(sr)
                sources = [
                    {"title": r["title"], "url": r["link"]}
                    for r in sr.get("organic", [])
                ]
                web_grounded = True
        except Exception as e:
            logger.warning(f"Task search failed: {e}")

    system = STEW_SYSTEM_PROMPT
    if search_context:
        system += f"\n\nWEB CONTEXT:\n{search_context}"
    if body.context:
        system += f"\n\nADDITIONAL CONTEXT:\n{body.context}"

    result = llm.chat([
        {"role": "system", "content": system},
        {"role": "user", "content": f"Complete this task:\n{body.task}"},
    ])

    background_tasks.add_task(
        _log_call, db, user.id, "/task", "POST", result["tokens"].get("total", 0), 200
    )

    return {
        "output": result["content"],
        "web_grounded": web_grounded,
        "sources": sources,
        "provider": result.get("provider"),
        "success": True,
    }


# ── Browse ─────────────────────────────────────────────────────────────────────

@app.post("/browse/navigate")
async def browse_navigate(
    body: BrowseRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    user = await get_user_by_api_key(body.api_key, db)
    llm = get_llm_client()

    try:
        import httpx
        from bs4 import BeautifulSoup

        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
            headers = {
                "User-Agent": (
                    "Mozilla/5.0 (compatible; STEWBot/5.0; +https://stew-agent.onrender.com)"
                )
            }
            resp = await client.get(body.url, headers=headers)
            resp.raise_for_status()
            html = resp.text
            title = resp.url

        soup = BeautifulSoup(html, "html.parser")
        page_title = soup.title.string.strip() if soup.title else str(title)

        # Remove script/style noise
        for tag in soup(["script", "style", "nav", "footer", "aside"]):
            tag.decompose()
        text = soup.get_text(separator=" ", strip=True)[:8000]  # cap context

        visual_analysis = ""
        if body.question:
            result = llm.complete(
                f"Page content:\n{text}\n\nQuestion: {body.question}",
                system="You are analyzing a webpage. Answer the question based ONLY on the page content provided.",
            )
            visual_analysis = result

        background_tasks.add_task(_log_call, db, user.id, "/browse/navigate", "POST", 0, 200)

        return {
            "url": body.url,
            "title": page_title,
            "content": text,
            "visual_analysis": visual_analysis,
            "success": True,
        }
    except ImportError:
        raise HTTPException(500, "httpx or beautifulsoup4 not installed")
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
    user = await get_user_by_api_key(body.api_key, db)
    result = generate_pdf(body.content, body.title)
    background_tasks.add_task(_log_call, db, user.id, "/generate/pdf", "POST", 0, 200)
    return result


@app.post("/generate/docx")
async def gen_docx(
    body: GenerateDOCXRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    user = await get_user_by_api_key(body.api_key, db)
    result = generate_docx(body.content, body.title)
    background_tasks.add_task(_log_call, db, user.id, "/generate/docx", "POST", 0, 200)
    return result


@app.post("/generate/xlsx")
async def gen_xlsx(
    body: GenerateXLSXRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    user = await get_user_by_api_key(body.api_key, db)
    result = generate_xlsx(body.data, body.sheet_name, body.title)
    background_tasks.add_task(_log_call, db, user.id, "/generate/xlsx", "POST", 0, 200)
    return result


@app.post("/generate/pptx")
async def gen_pptx(
    body: GeneratePPTXRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    user = await get_user_by_api_key(body.api_key, db)
    result = generate_pptx(body.slides, body.title)
    background_tasks.add_task(_log_call, db, user.id, "/generate/pptx", "POST", 0, 200)
    return result


@app.post("/generate/html")
async def gen_html(
    body: GenerateHTMLRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    user = await get_user_by_api_key(body.api_key, db)
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
    user = await get_user_by_api_key(api_key, db)
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

    background_tasks.add_task(_log_call, db, user.id, "/upload/document", "POST", 0, 200)

    return {
        "filename": extracted["filename"],
        "file_type": extracted["file_type"],
        "text": text,
        "answer": answer,
        "document_id": doc.id,
        "success": True,
    }


# ── API Proxy ──────────────────────────────────────────────────────────────────

@app.post("/api/call")
async def api_proxy_call(
    body: APICallRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    user = await get_user_by_api_key(body.api_key, db)

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
        background_tasks.add_task(_log_call, db, user.id, "/api/call", "POST", 0, resp.status_code)
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
    user = await get_user_by_api_key(body.api_key, db)
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
    user = await get_user_by_api_key(body.api_key, db)
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

@app.exception_handler(404)
async def not_found(request: Request, exc):
    return JSONResponse(
        status_code=404,
        content={"detail": f"Endpoint {request.url.path} not found", "success": False},
    )


@app.exception_handler(500)
async def internal_error(request: Request, exc):
    logger.error(f"Internal error on {request.url.path}: {exc}")
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error", "success": False},
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
    user = await get_user_by_api_key(body.api_key, db)
    from server.skills_engine import run_skill
    result = await run_skill(body.skill, **body.params)
    return {"skill": body.skill, "result": result, "success": "error" not in result}


# ── Browse ─────────────────────────────────────────────────────────────────────

@app.post("/browse")
async def browse_url(body: BrowseRequest, db: AsyncSession = Depends(get_db)):
    """Browse any URL and extract content. Falls back to DuckDuckGo if Serper is down."""
    user = await get_user_by_api_key(body.api_key, db)
    from server.browser import StewBrowser
    browser = StewBrowser()
    if body.url.startswith("http"):
        result = await browser.fetch(body.url)
    else:
        # Treat as search query
        result = await browser.search_web_fallback(body.url)
    return {"success": True, "question": body.question, **result}


# ── Telegram Webhook ───────────────────────────────────────────────────────────

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

    # Show typing
    await bot.send_typing(chat_id)

    # Get or create a stew user for this telegram user
    tg_email = f"tg_{msg['user_id']}@telegram.stew"
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

    # Handle /start command
    if user_text.startswith("/start"):
        welcome = (
            f"👋 Hello {username}! I'm *S.T.E.W* — Smart Thinking Executive Worker.\n\n"
            "I can:\n"
            "🔍 Search the web\n"
            "📄 Generate PDF, Word, Excel, PowerPoint\n"
            "💻 Write & review code\n"
            "🤖 Automate tasks\n"
            "📊 Analyze data\n\n"
            f"Your API key: `{tg_user.api_key}`\n\n"
            "Just send me any message or question to get started!"
        )
        await bot.send_message(chat_id, welcome)
        return {"ok": True}

    # Regular message — run through S.T.E.W
    llm = get_llm_client()
    searcher = get_searcher()

    web_grounded = False
    system = STEW_SYSTEM_PROMPT + "\n\nYou are responding via Telegram. Keep answers concise and well-formatted for mobile. Use plain text, avoid complex markdown."

    needs_search = any(kw in user_text.lower() for kw in [
        "latest", "current", "today", "news", "score", "price",
        "weather", "stock", "search", "find", "what is", "who is"
    ])
    if needs_search and searcher._is_available():
        try:
            search_results = searcher.search(user_text, num_results=4)
            if search_results.get("grounded"):
                context = searcher.format_results_for_llm(search_results)
                system += f"\n\nWEB SEARCH CONTEXT:\n{context}"
                web_grounded = True
        except Exception:
            pass

    conv = await get_or_create_conversation(db, tg_user.id, None)
    await append_message(db, conv, "user", user_text)
    messages = build_llm_messages(conv, system)

    try:
        result = llm.chat(messages)
        reply = result["content"]
        await append_message(db, conv, "assistant", reply)
        await bot.send_message(chat_id, reply, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Telegram LLM error: {e}")
        await bot.send_message(chat_id, "⚠️ I encountered an error. Please try again in a moment.")

    return {"ok": True}


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
