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
from fastapi.responses import JSONResponse, FileResponse, HTMLResponse
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
from server.orchestrator import orchestrate_text, orchestrate_image
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
    return {
        "status": "ok",
        "version": "6.0.0",
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

    # Send welcome email in background (non-blocking)
    import asyncio
    asyncio.create_task(send_welcome_email(user.email, user.name, user.api_key, user.plan))

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


@app.get("/auth/usage")
async def auth_usage(api_key: str, db: AsyncSession = Depends(get_db)):
    """Return the user's current plan, calls used, and remaining quota."""
    user = await get_user_by_api_key(api_key, db)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid API key")

    # Count calls this month
    from datetime import timedelta
    month_start = datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    result = await db.execute(
        select(func.count(APICall.id)).where(
            APICall.user_id == user.id,
            APICall.timestamp >= month_start
        )
    )
    calls_used = result.scalar() or 0

    plan_limit = settings.PLAN_CALL_LIMITS.get(user.plan, 500)
    calls_remaining = max(0, plan_limit - calls_used)

    return {
        "success": True,
        "plan": user.plan,
        "calls_used": calls_used,
        "calls_limit": plan_limit,
        "calls_remaining": calls_remaining,
        "reset_date": (month_start.replace(month=month_start.month + 1) if month_start.month < 12 else month_start.replace(year=month_start.year + 1, month=1)).isoformat(),
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

    # Web search grounding
    search_results = None
    sources = []
    web_grounded = False

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
        msg_lower = body.message.lower()
        should_search = any(kw in msg_lower for kw in needs_search_keywords)
        # Also search if the message looks like a question about real-world facts
        if not should_search and any(q in msg_lower for q in ["who is", "where is", "how much", "how many"]):
            should_search = True
        if should_search:
            try:
                search_results = await asyncio.to_thread(searcher.search, body.message, 5)
                if search_results.get("grounded"):
                    web_grounded = True
                    sources = [
                        {"title": r["title"], "url": r["link"], "snippet": r["snippet"]}
                        for r in search_results.get("organic", [])
                    ]
            except Exception as e:
                logger.warning(f"Search failed, continuing without: {e}")

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
        background_tasks.add_task(_log_call, db, user.id if user else None, "/chat", "POST", tokens, 200)

    return {
        "response": response_text,
        "web_grounded": web_grounded,
        "sources": sources,
        "provider": result.get("provider"),
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

    pool = AgentPool()

    class BrainAdapter:
        async def call_llm(self, prompt: str, system: str = "", max_tokens: int = 2048) -> str:
            llm = get_llm_client()
            messages = [
                {"role": "system", "content": system or "You are a helpful AI agent."},
                {"role": "user", "content": prompt},
            ]
            try:
                result = llm.chat(messages)
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

        background_tasks.add_task(_log_call, db, user.id if user else None, "/browse/navigate", "POST", 0, 200)

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
    user = await _safe_get_user(body.api_key, db)
    result = generate_pdf(body.content, body.title)
    background_tasks.add_task(_log_call, db, user.id if user else None, "/generate/pdf", "POST", 0, 200)
    return result


@app.post("/generate/docx")
async def gen_docx(
    body: GenerateDOCXRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    user = await _safe_get_user(body.api_key, db)
    result = generate_docx(body.content, body.title)
    background_tasks.add_task(_log_call, db, user.id if user else None, "/generate/docx", "POST", 0, 200)
    return result


@app.post("/generate/xlsx")
async def gen_xlsx(
    body: GenerateXLSXRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    user = await _safe_get_user(body.api_key, db)
    result = generate_xlsx(body.data, body.sheet_name, body.title)
    background_tasks.add_task(_log_call, db, user.id if user else None, "/generate/xlsx", "POST", 0, 200)
    return result


@app.post("/generate/pptx")
async def gen_pptx(
    body: GeneratePPTXRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    user = await _safe_get_user(body.api_key, db)
    result = generate_pptx(body.slides, body.title)
    background_tasks.add_task(_log_call, db, user.id if user else None, "/generate/pptx", "POST", 0, 200)
    return result


@app.post("/generate/html")
async def gen_html(
    body: GenerateHTMLRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    user = await _safe_get_user(body.api_key, db)
    result = generate_html(body.content, body.title)
    background_tasks.add_task(_log_call, db, user.id if user else None, "/generate/html", "POST", 0, 200)
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

    return {
        "success": True,
        "message": f"API key fine-tuned to {body.persona or 'general'} persona",
        "settings": {
            "persona": getattr(user, 'persona', 'general'),
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
    user = await _safe_get_user(body.api_key, db)
    from server.skills_engine import run_skill
    result = await run_skill(body.skill, **body.params)
    return {"skill": body.skill, "result": result, "success": "error" not in result}





@app.post("/browse")
async def browse_url(body: BrowseRequest, db: AsyncSession = Depends(get_db)):
    """Browse any URL and extract content. Falls back to DuckDuckGo if Serper is down."""
    user = await _safe_get_user(body.api_key, db)
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
            search_results = await asyncio.to_thread(searcher.search, user_text, 4)
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

