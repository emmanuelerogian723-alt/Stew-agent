"""
S.T.E.W — Secret Task Execution Worker
Server v3.0 — 50+ Endpoints
=====================================
Real browser. Vision. Deep research. File creation.
Multi-model AI routing. 100 agent swarm.
Created by Emmanuel Ene Rejoice Gideon — MUTYINT
"""

import asyncio
import base64
import os
import sys
import json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, BackgroundTasks, Request
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from pydantic import BaseModel
from typing import Any, Dict, List, Optional
from datetime import datetime
from loguru import logger

from soul.SOUL import StewHeart, StewConsciousness, STEW_SOUL
from server.security import (
    security_middleware, sanitize_input, validate_url,
    get_security_stats, manual_block, manual_unblock,
    get_client_ip, check_rate_limit
)
from memory.memory_engine import MemoryEngine
from agents.agent_pool import AgentPool
from skills.skills_engine import SkillsEngine
from core.brain import StewBrain
import hashlib
import secrets
import time

# ═══════════════════════════════════════════
# API KEY AUTHENTICATION SYSTEM
# ═══════════════════════════════════════════
# In-memory store (persists while server runs; use DB for production)
_api_keys: Dict[str, Dict] = {}
_free_key = "stew_free_playground_2026"

def _generate_key() -> str:
    return "stew_" + secrets.token_hex(24)

def _validate_api_key(key: str) -> Dict:
    """Returns key info or raises 401"""
    if not key:
        raise HTTPException(status_code=401, detail="API key required. Get yours at /auth/register")
    if key == _free_key:
        return {"plan": "playground", "email": "playground", "calls": 0, "limit": 50}
    if key in _api_keys:
        info = _api_keys[key]
        if info.get("calls", 0) >= info.get("limit", 1000):
            raise HTTPException(status_code=429, detail="API limit reached. Upgrade your plan.")
        _api_keys[key]["calls"] = info.get("calls", 0) + 1
        _api_keys[key]["last_used"] = datetime.now().isoformat()
        return info
    raise HTTPException(status_code=401, detail="Invalid API key. Get yours at /auth/register")

class RegisterRequest(BaseModel):
    email: str
    name: str
    plan: str = "free"  # free | pro

class ValidateRequest(BaseModel):
    api_key: str



logger.info("⚡ S.T.E.W — Secret Task Execution Worker — INITIALIZING")

heart = StewHeart()
consciousness = StewConsciousness()
memory = MemoryEngine()
agent_pool = AgentPool()
skills = SkillsEngine()
brain = StewBrain(consciousness, heart, memory)

@asynccontextmanager
async def lifespan(app_instance):
    logger.info("🚀 S.T.E.W v3.0 is ALIVE")
    heart.beat()
    memory.know("startup", f"S.T.E.W v3.0 started at {datetime.now().isoformat()}")
    os.makedirs("output", exist_ok=True)
    os.makedirs("workspace", exist_ok=True)
    os.makedirs("screenshots", exist_ok=True)
    task = asyncio.create_task(heart.start_heartbeat())
    yield
    heart.is_alive = False
    task.cancel()

app = FastAPI(
    title="S.T.E.W — Secret Task Execution Worker",
    version="3.0.0",
    description="Autonomous AI Agent by MUTYINT",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan
)

# ── Allowed origins (add your domains here)
ALLOWED_ORIGINS = [
    "https://slimeai-frontend.vercel.app",
    "https://slime-ai.vercel.app",
    "https://mutyint.com",
    "https://www.mutyint.com",
    "http://localhost:3000",
    "http://localhost:8000",
    "http://127.0.0.1:5500",
    "*",  # Remove this line when you have a real domain
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "Authorization", "X-API-Key"],
)

# ── Security middleware (rate limiting, IP blocking, input sanitization)
app.middleware("http")(security_middleware)

# ═══════════════════════════════════════════
# REQUEST MODELS
# ═══════════════════════════════════════════

class TaskRequest(BaseModel):
    task: str
    context: Optional[Dict] = None
    agent_ids: Optional[List[int]] = None
    use_all_agents: bool = False

class ChatRequest(BaseModel):
    message: str
    conversation_id: Optional[str] = None

class SearchRequest(BaseModel):
    query: str
    num_results: int = 10

class CodeRequest(BaseModel):
    description: str
    language: str = "python"

class WebsiteRequest(BaseModel):
    name: str
    description: str
    color: str = "#0a0050"

class DocumentRequest(BaseModel):
    title: str
    content: str
    doc_type: str = "pdf"

class SpreadsheetRequest(BaseModel):
    name: str
    data: List[List]

class MemoryRequest(BaseModel):
    key: str
    value: str

class AgentRequest(BaseModel):
    agent_ids: List[int]
    task: str

class BrowseRequest(BaseModel):
    url: str
    question: Optional[str] = None

class ClickRequest(BaseModel):
    url: str
    selector: str

class FormFillRequest(BaseModel):
    url: str
    form_data: Dict[str, str]

class VisionRequest(BaseModel):
    image_url: Optional[str] = None
    image_base64: Optional[str] = None
    question: str = "Describe everything you see in detail"

class ScreenshotAnalyzeRequest(BaseModel):
    url: str
    question: str = "What do you see on this website?"

class ApiCallRequest(BaseModel):
    method: str = "GET"
    url: str
    headers: Optional[Dict] = None
    body: Optional[Dict] = None

class BulkScrapeRequest(BaseModel):
    urls: List[str]

class DownloadRequest(BaseModel):
    url: str
    filename: Optional[str] = None

class EmailRequest(BaseModel):
    to: str
    subject: str
    body: str

class TranslateRequest(BaseModel):
    text: str
    target_language: str

class SummarizeRequest(BaseModel):
    text: str

class SentimentRequest(BaseModel):
    text: str

class WeatherRequest(BaseModel):
    city: str

class CurrencyRequest(BaseModel):
    amount: float
    from_currency: str
    to_currency: str

class StockRequest(BaseModel):
    symbol: str

class WebhookRequest(BaseModel):
    url: str
    event: str
    data: Dict

class DeepResearchRequest(BaseModel):
    topic: str

class WorkspaceRequest(BaseModel):
    filename: str
    content: Optional[str] = None

class OCRRequest(BaseModel):
    image_url: Optional[str] = None
    image_base64: Optional[str] = None

# ═══════════════════════════════════════════
# HOME PAGE
# ═══════════════════════════════════════════


# ═══ KEEP-ALIVE SYSTEM ═══════════════════════════════════════
import asyncio
from datetime import datetime

_ping_count = 0
_last_ping = None

async def self_ping_loop():
    """STEW keeps itself alive by pinging its own heartbeat every 10 minutes"""
    global _ping_count, _last_ping
    await asyncio.sleep(30)  # Wait 30s after startup before first ping
    while True:
        try:
            async with __import__("httpx").AsyncClient(timeout=15) as client:
                r = await client.get("https://stew-agent.onrender.com/heartbeat")
                if r.status_code == 200:
                    _ping_count += 1
                    _last_ping = datetime.utcnow().isoformat() + "Z"
                    logger.info(f"🫀 Self-ping #{_ping_count} — STEW is alive!")
        except Exception as e:
            logger.warning(f"Self-ping failed (retrying in 10m): {e}")
        await asyncio.sleep(600)  # Ping every 10 minutes

@app.on_event("startup")
async def startup_keep_alive():
    """Launch the self-ping background task on server start"""
    asyncio.create_task(self_ping_loop())
    logger.info("🫀 STEW Keep-Alive system started — pinging every 10 minutes")

@app.get("/alive")
async def alive_status():
    """Keep-alive status dashboard"""
    return {
        "status": "immortal",
        "self_pings": _ping_count,
        "last_ping": _last_ping,
        "ping_interval_minutes": 10,
        "message": "S.T.E.W never sleeps. 🫀",
        "render_note": "Free tier sleeps after 15min idle — self-ping prevents this",
        "uptime_strategy": "STEW pings itself every 10 minutes to stay warm",
    }

@app.get("/", response_class=HTMLResponse)
async def home():
    import os
    landing_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "landing.html")
    if os.path.exists(landing_path):
        with open(landing_path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    return HTMLResponse(content="<h1>S.T.E.W API</h1><p>Landing page not found.</p>")

@app.post("/task")
async def execute_task(request: TaskRequest):
    """Master endpoint — execute any task with full intelligence"""
    if request.use_all_agents:
        results = await agent_pool.run_all_100(request.task, brain=brain)
        combined = "\n\n".join([r.get("output", "") for r in results if r.get("output")])
        if brain and combined:
            synthesis = await brain.call_llm(
                f"Synthesize these multi-agent findings into one comprehensive answer for: {request.task}\n\n{combined[:3000]}",
                system="You are S.T.E.W. Combine these findings into one clear, well-structured response."
            )
            return {"task": request.task, "agents_deployed": len(results), "output": synthesis, "response": synthesis, "success": True}
        return {"task": request.task, "agents_deployed": len(results), "output": combined, "response": combined, "success": True}

    # Single intelligent execution
    result = await brain.think(request.task, request.context)
    output = result.get('output', str(result)) if isinstance(result, dict) else str(result)
    memory.add_conversation("assistant", str(output))
    return {"task": request.task, "output": output, "response": output, "success": True}

@app.post("/chat")
async def chat(request: ChatRequest):
    """AI conversation endpoint — smart routing with real-time web search"""
    is_safe, result = sanitize_input(request.message, "message")
    if not is_safe:
        raise HTTPException(status_code=400, detail=result)
    msg = request.message.lower()

    # ── Detect document requests → call document builder endpoints directly ──
    if any(w in msg for w in ["create pdf", "make pdf", "generate pdf", "write pdf", "pdf report"]):
        result = await skills.create_pdf("Document", request.message)
        return {"message": request.message, "response": f"✅ PDF created! Download: {result.get('download_url', result.get('path', 'output/document.pdf'))}", "file": result, "success": True}
    if any(w in msg for w in ["create word", "make word", "word doc", "word document", "docx"]):
        result = await skills.create_word_doc("Document", request.message)
        return {"message": request.message, "response": f"✅ Word document created! Download: {result.get('download_url', result.get('path', 'output/document.docx'))}", "file": result, "success": True}
    if any(w in msg for w in ["spreadsheet", "excel", "create excel", "make excel", "xlsx"]):
        result = await skills.create_spreadsheet("Data", [[request.message]])
        return {"message": request.message, "response": f"✅ Spreadsheet created! Download: {result.get('download_url', result.get('path', 'output/spreadsheet.xlsx'))}", "file": result, "success": True}

    # ── Detect email requests → honest response + attempt real send ──
    if any(w in msg for w in ["send email", "send an email", "email to "]):
        smtp_user = os.getenv("SMTP_USER", "")
        smtp_pass = os.getenv("SMTP_PASS", "")
        if not smtp_user or not smtp_pass:
            return {
                "message": request.message,
                "response": (
                    "📧 To send real emails, STEW needs SMTP credentials configured. "
                    "Set SMTP_USER and SMTP_PASS environment variables (Gmail App Password works). "
                    "Once configured, use POST /email/send with {to, subject, body}."
                ),
                "success": False,
                "hint": "Configure SMTP_USER and SMTP_PASS environment variables to enable real email sending."
            }
        # Credentials exist — pass to task handler
        pass

    # ── Detect finance questions → call /finance endpoints directly ──
    if any(w in msg for w in ["currency", "exchange rate", "dollar to naira", "usd to ngn", "ngn to usd", "convert usd", "convert ngn", "naira to", "to naira"]):
        import re
        nums = re.findall(r"\d+(?:\.\d+)?", request.message)
        amount = float(nums[0]) if nums else 1.0
        from_c = "USD" if "usd" in msg or "dollar" in msg else "NGN"
        to_c = "NGN" if from_c == "USD" else "USD"
        if "ngn to usd" in msg or "naira to dollar" in msg or "naira to usd" in msg:
            from_c, to_c = "NGN", "USD"
        result = await skills.convert_currency(amount, from_c, to_c)
        return {"message": request.message, "response": result.get("result", str(result)), "data": result, "success": True}

    if any(w in msg for w in ["stock price", "share price", "stock of", "price of ", "market price", "nasdaq", "nyse"]):
        import re
        symbols = re.findall(r"\b[A-Z]{2,5}\b", request.message.upper())
        symbol = symbols[0] if symbols else "AAPL"
        result = await skills.stock_price(symbol)
        return {"message": request.message, "response": result.get("result", str(result)), "data": result, "success": True}

    # ── Default: use brain.think() which has full Serper web grounding ──
    try:
        memory.add_conversation("user", request.message)
        result = await asyncio.wait_for(
            brain.think(request.message),
            timeout=30.0
        )
        response = result.get("output", str(result)) if isinstance(result, dict) else str(result)
        web_grounded = result.get("web_grounded", False) if isinstance(result, dict) else False
        memory.add_conversation("assistant", response)
        return {
            "message": request.message,
            "response": response,
            "web_grounded": web_grounded,
            "success": True
        }
    except asyncio.TimeoutError:
        logger.error("Chat timeout")
        return {"message": request.message, "response": "S.T.E.W is processing hard right now — try again in a moment! ⚡", "success": False}
    except Exception as e:
        logger.error(f"Chat error: {e}")
        return {"message": request.message, "response": f"S.T.E.W encountered an issue: {str(e)[:100]}. Retry!", "success": False}

# ═══════════════════════════════════════════
# BROWSER CONTROL — REAL PLAYWRIGHT
# ═══════════════════════════════════════════

@app.post("/browse/navigate")
async def browse_navigate(request: BrowseRequest):
    """Navigate to a URL — static fetch first, Playwright for JS-heavy sites"""
    # Try static fetch first (fast, low-resource)
    static_result = await skills.scrape_webpage(request.url)
    if static_result.get("success") and len(static_result.get("content", "")) > 300:
        response_data = {
            "url": request.url,
            "title": static_result.get("title", ""),
            "content": static_result.get("content", ""),
            "method": "static",
            "success": True
        }
        if request.question and brain:
            answer = await brain.call_llm(
                f"Based on this page content, answer: {request.question}\n\nContent:\n{static_result.get('content','')[:4000]}",
                system="You are S.T.E.W. Answer the question using the provided web page content."
            )
            response_data["visual_analysis"] = answer
        return response_data
    # Fall back to full Playwright for JS-heavy sites
    try:
        result = await asyncio.wait_for(skills.browser_navigate(request.url), timeout=25.0)
        if request.question and result.get("success") and result.get("screenshot"):
            analysis = await brain.analyze_image(result["screenshot"], request.question)
            result["visual_analysis"] = analysis
        result["method"] = "playwright"
        return result
    except asyncio.TimeoutError:
        return {
            "url": request.url,
            "error": "This site requires heavy JavaScript rendering which timed out on the current tier. For complex sites like BBC/TechCrunch, full browser automation is available on Pro plan.",
            "tip": "Try /browse/extract for text-only extraction, or upgrade to Pro for full Playwright browsing.",
            "success": False
        }

@app.post("/browse/click")
async def browse_click(request: ClickRequest):
    """Click an element on a webpage"""
    return await skills.browser_click(request.url, request.selector)

@app.post("/browse/form")
async def browse_form(request: FormFillRequest):
    """Fill out a form on any website"""
    return await skills.browser_fill_form(request.url, request.form_data, brain)

@app.post("/browse/screenshot")
async def browse_screenshot(request: BrowseRequest):
    """Take a full screenshot of any website"""
    return await skills.browser_screenshot(request.url)

@app.post("/browse/extract")
async def browse_extract(request: BrowseRequest):
    """Extract text from a webpage"""
    return await skills.browser_extract_text(request.url)

@app.post("/browse/links")
async def browse_links(request: BrowseRequest):
    """Get all links from a webpage"""
    return await skills.browser_get_links(request.url)

# ═══════════════════════════════════════════
# VISION — IMAGE ANALYSIS
# ═══════════════════════════════════════════

@app.post("/vision/analyze")
async def vision_analyze(request: VisionRequest):
    """Analyze an image — describe, read text, answer questions"""
    image_source = request.image_url or request.image_base64
    if not image_source:
        raise HTTPException(status_code=400, detail="Provide image_url or image_base64")
    if brain:
        analysis = await brain.analyze_image(image_source, request.question)
        return {"question": request.question, "analysis": analysis, "success": True}
    return {"error": "Vision brain not initialized", "success": False}

@app.post("/vision/ocr")
async def vision_ocr(request: OCRRequest):
    """Extract text from an image using OCR"""
    if request.image_url:
        return await skills.ocr_image_url(request.image_url)
    elif request.image_base64:
        return await skills.ocr_image_base64(request.image_base64)
    raise HTTPException(status_code=400, detail="Provide image_url or image_base64")

@app.post("/vision/screenshot-analyze")
async def vision_screenshot_analyze(request: ScreenshotAnalyzeRequest):
    """Screenshot a website and visually analyze it"""
    return await skills.screenshot_and_analyze(request.url, request.question, brain)

# ═══════════════════════════════════════════
# SEARCH & RESEARCH
# ═══════════════════════════════════════════

@app.post("/search")
async def search(request: SearchRequest):
    results = await skills.web_search(request.query, request.num_results)
    memory.know(f"search_{request.query[:30]}", str(results)[:500])
    return {"query": request.query, "results": results, "count": len(results), "success": True}

@app.post("/search/news")
async def search_news(request: SearchRequest):
    results = await skills.news_search(request.query, request.num_results)
    return {"query": request.query, "news": results, "count": len(results), "success": True}

@app.post("/search/youtube")
async def search_youtube(request: SearchRequest):
    results = await skills.youtube_search(request.query)
    return {"query": request.query, "videos": results, "count": len(results), "success": True}

@app.post("/research/deep")
async def deep_research(request: DeepResearchRequest):
    return await skills.deep_research(request.topic, brain)

@app.post("/scrape")
async def scrape(request: BrowseRequest):
    return await skills.scrape_webpage(request.url)

@app.post("/scrape/bulk")
async def scrape_bulk(request: BulkScrapeRequest):
    results = await skills.bulk_scrape(request.urls)
    return {"results": results, "count": len(results), "success": True}

# ═══════════════════════════════════════════
# CODE GENERATION & EXECUTION
# ═══════════════════════════════════════════

@app.post("/code")
async def write_code(request: CodeRequest):
    return await skills.write_code(request.description, request.language, brain)

@app.post("/code/run")
async def run_code(request: CodeRequest):
    code_result = await skills.write_code(request.description, request.language, brain)
    if request.language == "python":
        run_result = await skills.run_python_code(code_result.get("code", ""))
        code_result["execution"] = run_result
    return code_result

@app.post("/code/debug")
async def debug_code(request: CodeRequest):
    return await skills.debug_code(request.description, "", brain)

# ═══════════════════════════════════════════
# DOCUMENT BUILDING
# ═══════════════════════════════════════════

@app.post("/build/document")
async def build_document(request: DocumentRequest):
    if request.doc_type == "pdf":
        return await skills.create_pdf(request.title, request.content)
    elif request.doc_type == "word":
        return await skills.create_word_doc(request.title, request.content)
    return {"error": "Use doc_type 'pdf' or 'word'", "success": False}

@app.post("/build/pdf")
async def build_pdf(request: DocumentRequest):
    return await skills.create_pdf(request.title, request.content)

@app.post("/build/word")
async def build_word(request: DocumentRequest):
    return await skills.create_word_doc(request.title, request.content)

@app.post("/build/spreadsheet")
async def build_spreadsheet(request: SpreadsheetRequest):
    return await skills.create_spreadsheet(request.name, request.data)

@app.post("/build/website")
async def build_website(request: WebsiteRequest):
    return await skills.create_website(request.name, request.description, request.color)

@app.post("/build/report")
async def build_report(request: DocumentRequest):
    return await skills.create_html_report(request.title, request.content)

@app.post("/read/pdf")
async def read_pdf(request: WorkspaceRequest):
    return await skills.read_pdf(request.filename)

# ═══════════════════════════════════════════
# REAL-WORLD DATA
# ═══════════════════════════════════════════

@app.post("/weather")
async def weather(request: WeatherRequest):
    return await skills.weather_info(request.city)

@app.post("/finance/currency")
async def currency(request: CurrencyRequest):
    return await skills.convert_currency(request.amount, request.from_currency, request.to_currency)

@app.post("/finance/stock")
async def stock(request: StockRequest):
    return await skills.stock_price(request.symbol)

@app.post("/translate")
async def translate(request: TranslateRequest):
    return await skills.translate_text(request.text, request.target_language, brain=brain)

@app.post("/summarize")
async def summarize(request: SummarizeRequest):
    return await skills.summarize_text(request.text, brain)

@app.post("/sentiment")
async def sentiment(request: SentimentRequest):
    return await skills.analyze_sentiment(request.text)

# ═══════════════════════════════════════════
# WORKSPACE & MEMORY
# ═══════════════════════════════════════════

@app.post("/workspace/save")
async def workspace_save(request: WorkspaceRequest):
    return await skills.save_to_workspace(request.filename, request.content or "")

@app.post("/workspace/read")
async def workspace_read(request: WorkspaceRequest):
    return await skills.read_from_workspace(request.filename)

@app.get("/workspace/list")
async def workspace_list():
    return await skills.list_files("workspace")

@app.get("/files/list")
async def files_list():
    return await skills.list_files("output")

@app.post("/memory/set")
async def memory_set(request: MemoryRequest):
    memory.know(request.key, request.value)
    return {"key": request.key, "value": request.value, "success": True}

@app.get("/memory/get/{key}")
async def memory_get(key: str):
    value = memory.recall(key)
    return {"key": key, "value": value, "success": value is not None}

# ═══════════════════════════════════════════
# API & AUTOMATION
# ═══════════════════════════════════════════

@app.post("/api/call")
async def api_call(request: ApiCallRequest):
    return await skills.api_call(request.method, request.url, request.headers, request.body)

@app.post("/webhook/send")
async def send_webhook(request: WebhookRequest):
    return await skills.send_webhook(request.url, request.event, request.data)

@app.post("/monitor")
async def monitor(request: BrowseRequest):
    return await skills.monitor_website(request.url)

@app.post("/email/send")
async def send_email(request: EmailRequest):
    return await skills.send_email(request.to, request.subject, request.body)

# ═══════════════════════════════════════════
# AGENT SYSTEM
# ═══════════════════════════════════════════

@app.post("/agents/run")
async def run_agents(request: AgentRequest):
    results = await agent_pool.run_parallel([request.task] * len(request.agent_ids),
                                            request.agent_ids, brain=brain)
    combined = "\n\n".join([r.get("output", "") for r in results])
    return {"task": request.task, "agents": len(results), "output": combined, "results": results, "success": True}

@app.post("/agents/all")
async def deploy_all_agents(request: TaskRequest):
    results = await agent_pool.run_all_100(request.task, brain=brain)
    combined = "\n\n".join([r.get("output", "") for r in results if r.get("output")])
    synthesis = await brain.call_llm(
        f"Synthesize into one answer for: {request.task}\n\n{combined[:3000]}",
        system="Be comprehensive and well-structured."
    )
    return {"task": request.task, "agents_deployed": len(results), "synthesis": synthesis, "success": True}

@app.get("/agents/status")
async def agents_status():
    return agent_pool.get_pool_status()

# ═══════════════════════════════════════════
# SYSTEM STATUS
# ═══════════════════════════════════════════

# ═══════════════════════════════════════════
# SECURITY ADMIN ENDPOINTS
# ═══════════════════════════════════════════

ADMIN_SECRET = os.environ.get("STEW_ADMIN_SECRET", "").strip()

@app.get("/security/stats")
async def security_stats(request: Request):
    """View security statistics — admin only."""
    key = request.headers.get("X-Admin-Key", "").strip()
    if not ADMIN_SECRET or key != ADMIN_SECRET:
        raise HTTPException(status_code=403, detail="Admin access required")
    return get_security_stats()

@app.post("/security/block")
async def block_ip_endpoint(request: Request):
    """Manually block an IP — admin only."""
    key = request.headers.get("X-Admin-Key", "").strip()
    if not ADMIN_SECRET or key != ADMIN_SECRET:
        raise HTTPException(status_code=403, detail="Admin access required")
    body = await request.json()
    ip = body.get("ip")
    duration = body.get("duration", -1)
    reason = body.get("reason", "manual admin block")
    if not ip:
        raise HTTPException(status_code=400, detail="IP required")
    manual_block(ip, duration, reason)
    return {"success": True, "message": f"IP {ip} blocked", "duration": duration}

@app.post("/security/unblock")
async def unblock_ip_endpoint(request: Request):
    """Manually unblock an IP — admin only."""
    key = request.headers.get("X-Admin-Key", "").strip()
    if not ADMIN_SECRET or key != ADMIN_SECRET:
        raise HTTPException(status_code=403, detail="Admin access required")
    body = await request.json()
    ip = body.get("ip")
    if not ip:
        raise HTTPException(status_code=400, detail="IP required")
    manual_unblock(ip)
    return {"success": True, "message": f"IP {ip} unblocked"}

@app.get("/security/health")
async def security_health():
    """Public security health check."""
    stats = get_security_stats()
    return {
        "status": "protected",
        "shield": "active",
        "rate_limiting": "enabled",
        "ip_blocking": "enabled",
        "input_sanitization": "enabled",
        "blocked_ips": stats["currently_blocked_ips"],
        "total_requests_served": stats["total_requests"],
    }


@app.get("/playground", response_class=HTMLResponse)
async def playground():
    """S.T.E.W Developer Playground — test all endpoints live"""
    import pathlib
    # Try multiple locations
    base = pathlib.Path(__file__).parent.parent
    candidates = [
        base / "playground.html",
        base / "server" / "playground.html",
        pathlib.Path("/app/playground.html"),
        pathlib.Path("playground.html"),
    ]
    for f in candidates:
        if f.exists():
            return HTMLResponse(content=f.read_text())
    # If file not found, serve embedded playground
    return HTMLResponse(content=PLAYGROUND_HTML)


# ═══════════════════════════════════════════
# AUTH ENDPOINTS
# ═══════════════════════════════════════════

@app.post("/auth/register")
async def register_dev(req: RegisterRequest):
    """Register and get a free API key instantly — no credit card needed"""
    email = req.email.strip().lower()
    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="Valid email required")
    for key, info in _api_keys.items():
        if info.get("email") == email:
            return {"api_key": key, "plan": info["plan"], "calls_limit": info["limit"],
                    "message": "Welcome back! Here is your existing key.", "email": email}
    plan = req.plan if req.plan in ["free", "pro"] else "free"
    limits = {"free": 1000, "pro": 50000}
    api_key = _generate_key()
    _api_keys[api_key] = {
        "email": email, "name": req.name, "plan": plan,
        "calls": 0, "limit": limits.get(plan, 1000),
        "created": datetime.now().isoformat(), "last_used": None
    }
    logger.info(f"🔑 New key: {email} ({plan})")
    return {
        "api_key": api_key, "plan": plan,
        "calls_limit": limits.get(plan, 1000),
        "message": f"✅ Welcome {req.name}! Your STEW API key is ready.",
        "email": email,
        "docs": "https://stew-agent.onrender.com/docs",
        "playground": "https://stew-agent.onrender.com/playground"
    }


@app.get("/auth/usage")
async def auth_usage(api_key: str = ""):
    """Check your API usage — calls used, limit, and remaining"""
    if not api_key:
        raise HTTPException(status_code=400, detail="Provide ?api_key=your_key")
    if api_key == _free_key:
        return {"plan": "playground", "calls_used": 0, "calls_limit": 50, "calls_remaining": 50, "email": "playground@stew.ai"}
    if api_key in _api_keys:
        info = _api_keys[api_key]
        used = info.get("calls", 0)
        limit = info.get("limit", 1000)
        return {
            "plan": info.get("plan", "free"),
            "email": info.get("email", ""),
            "calls_used": used,
            "calls_limit": limit,
            "calls_remaining": max(0, limit - used),
            "created": info.get("created", ""),
            "last_used": info.get("last_used", "never"),
        }
    raise HTTPException(status_code=401, detail="Invalid API key")

@app.post("/auth/validate")
async def validate_dev_key(req: ValidateRequest):
    """Validate an API key"""
    try:
        info = _validate_api_key(req.api_key)
        return {"valid": True, "plan": info["plan"], "calls_used": info.get("calls", 0),
                "calls_limit": info.get("limit", 0), "email": info.get("email", "?")}
    except HTTPException as e:
        return {"valid": False, "detail": e.detail}

@app.get("/auth/stats")
async def auth_stats():
    """Public developer registration stats"""
    return {
        "registered_developers": len(_api_keys),
        "free_key": "stew_free_playground_2026",
        "plans": {"free": "1,000 calls/month", "pro": "50,000 calls/month"}
    }


# ── HUMAN BROWSE: full autonomous web session ──
class HumanBrowseRequest(BaseModel):
    url: str
    actions: list = []  # optional list of actions: [{type: "click", selector: "..."}, {type:"fill",selector:"...",value:"..."}]
    question: str = ""  # optional question to answer from the page

@app.post("/browse/human")
async def browse_human(request: HumanBrowseRequest, req: Request):
    """Browse a website like a human — navigate, optionally perform actions, optionally answer a question from the page"""
    security_middleware(req)
    url = sanitize_input(request.url)
    if not validate_url(url):
        raise HTTPException(status_code=400, detail="Invalid URL")
    pw, browser = await skills._fresh_browser()
    if not browser:
        raise HTTPException(status_code=503, detail="Browser unavailable")
    try:
        context = await browser.new_context(
            viewport={"width": 1280, "height": 900},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            locale="en-US",
        )
        page = await context.new_page()
        await page.goto(url, wait_until="domcontentloaded", timeout=35000)
        await page.wait_for_timeout(2000)
        # Scroll down naturally
        await page.evaluate("window.scrollTo(0, 300)")
        await page.wait_for_timeout(500)
        results = []
        # Perform requested actions
        for action in (request.actions or []):
            atype = action.get("type","")
            sel = action.get("selector","")
            val = action.get("value","")
            try:
                if atype == "click":
                    await page.click(sel, timeout=6000)
                    await page.wait_for_timeout(1200)
                    results.append({"action":"click","selector":sel,"status":"ok"})
                elif atype == "fill":
                    await page.fill(sel, val, timeout=6000)
                    results.append({"action":"fill","selector":sel,"value":val,"status":"ok"})
                elif atype == "scroll":
                    await page.evaluate(f"window.scrollBy(0, {val or 500})")
                    results.append({"action":"scroll","status":"ok"})
                elif atype == "wait":
                    await page.wait_for_timeout(int(val or 1000))
                    results.append({"action":"wait","status":"ok"})
                elif atype == "type":
                    await page.keyboard.type(str(val))
                    results.append({"action":"type","value":val,"status":"ok"})
                elif atype == "press":
                    await page.keyboard.press(str(val or "Enter"))
                    results.append({"action":"press","key":val,"status":"ok"})
            except Exception as e:
                results.append({"action":atype,"selector":sel,"status":"error","error":str(e)})
        # Final state
        title = await page.title()
        final_url = page.url
        text = await page.evaluate("""() => {
            const els = document.querySelectorAll('script,style,nav,footer');
            els.forEach(e => e.remove());
            return document.body ? document.body.innerText.slice(0, 8000) : '';
        }""")
        links = await page.evaluate("""() => {
            return Array.from(document.querySelectorAll('a[href]'))
                .map(a => ({text: a.innerText.trim().slice(0,80), href: a.href}))
                .filter(l => l.href.startsWith('http') && l.text.length > 0)
                .slice(0, 20);
        }""")
        screenshot_path = skills.screenshots_dir / f"human_{int(__import__('time').time())}.png"
        await page.screenshot(path=str(screenshot_path), full_page=False)
        screenshot_b64 = __import__('base64').b64encode(screenshot_path.read_bytes()).decode()
        # Answer question if asked
        ai_answer = None
        if request.question and text:
            try:
                ai_answer = await brain.think(
                    f"Based on this webpage content, answer: {request.question}\n\nPage title: {title}\nURL: {final_url}\n\nContent:\n{text[:4000]}"
                )
            except Exception:
                ai_answer = "Could not analyze page content."
        return {
            "url": url, "final_url": final_url, "title": title,
            "text": text[:4000], "links": links,
            "actions_performed": results,
            "ai_answer": ai_answer,
            "screenshot": screenshot_b64,
            "success": True
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        try:
            await browser.close()
            await pw.stop()
        except Exception:
            pass



@app.get("/slime", response_class=HTMLResponse)
async def slime_page():
    """Slime AI v2.5 Ultra — Full App"""
    import pathlib
    base = pathlib.Path(__file__).parent.parent
    for p in ["slime.html", "server/slime.html", "/app/slime.html"]:
        fp = base / p if not p.startswith('/') else pathlib.Path(p)
        if fp.exists():
            with open(fp, 'r', encoding='utf-8') as f:
                return HTMLResponse(content=f.read())
    return HTMLResponse(content="<h1>Slime AI - Loading...</h1>", status_code=200)

@app.get("/plugins", response_class=HTMLResponse)
async def plugins_page():
    """STEW Plugins & Connectors dashboard"""
    import pathlib
    for p in ["plugins.html", "server/plugins.html", "/app/plugins.html"]:
        try:
            with open(p, "r") as f:
                return HTMLResponse(content=f.read())
        except FileNotFoundError:
            continue
    # Inline fallback
    return HTMLResponse(content="""<!DOCTYPE html><html><head><meta charset=UTF-8><title>STEW Plugins</title></head><body style='background:#050508;color:#e2e8f0;font-family:Inter,sans-serif;display:flex;align-items:center;justify-content:center;height:100vh;'><div style='text-align:center'><div style='font-size:3rem;margin-bottom:1rem'>🔌</div><h1>Plugins Loading...</h1><p style='color:#64748b;margin-top:.5rem'>Redirecting to homepage...</p><script>setTimeout(()=>location.href='/',2000)</script></div></body></html>""")


# ═══ SEO ROUTES ═══════════════════════════════════
@app.get("/sitemap.xml")
async def sitemap():
    from fastapi.responses import Response
    try:
        with open("sitemap.xml", "r") as f:
            return Response(content=f.read(), media_type="application/xml")
    except:
        xml = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://stew-agent.onrender.com/</loc><priority>1.0</priority></url>
  <url><loc>https://stew-agent.onrender.com/playground</loc><priority>0.9</priority></url>
  <url><loc>https://stew-agent.onrender.com/plugins</loc><priority>0.8</priority></url>
  <url><loc>https://stew-agent.onrender.com/slime</loc><priority>0.9</priority></url>
</urlset>"""
        from fastapi.responses import Response
        return Response(content=xml, media_type="application/xml")

@app.get("/robots.txt")
async def robots():
    from fastapi.responses import PlainTextResponse
    content = """User-agent: *
Allow: /
Disallow: /auth/
Disallow: /security/block
Sitemap: https://stew-agent.onrender.com/sitemap.xml
Host: stew-agent.onrender.com"""
    return PlainTextResponse(content=content)

@app.get("/BBHLR0945oVzFsu-C0G8NRNHkvYdCEDCxf3Mpho30A.html", include_in_schema=False)
async def google_verify():
    """Google Search Console HTML file verification"""
    return HTMLResponse(content="google-site-verification: BBHLR0945oVzFsu-C0G8NRNHkvYdCEDCxf3Mpho30A.html")

@app.get("/google4414167ffddc8a5f.html", include_in_schema=False)
async def google_verify2():
    """Google Search Console HTML file verification v2"""
    return HTMLResponse(content="google-site-verification: google4414167ffddc8a5f", media_type="text/html")

@app.get("/heartbeat")
async def heartbeat():
    return {
        "agent": "S.T.E.W — Secret Task Execution Worker",
        "status": "online",
        "beat": heart.beats,
        "version": "4.0.1 ULTRA",
        "timestamp": datetime.now().isoformat()
    }

@app.get("/status")
async def status():
    return {
        "agent": "S.T.E.W",
        "full_name": "Secret Task Execution Worker",
        "version": "4.0.1 ULTRA",
        "creator": "Emmanuel Ene Rejoice Gideon",
        "company": "MUTYINT",
        "stew": "online",
        "brain": brain.get_status(),
        "heart": {"alive": True, "heartbeats": heart.beats, "mood": "determined"},
        "skills_count": 60,
        "agents": 100,
        "browser": "Playwright Chromium",
        "vision": "Tesseract OCR + LLM Vision",
        "files_available": len(list(__import__('pathlib').Path("output").glob("*"))) if __import__('pathlib').Path("output").exists() else 0,
        "timestamp": datetime.now().isoformat()
    }

# ═══════════════════════════════════════════
# FILE DOWNLOAD
# ═══════════════════════════════════════════

@app.get("/download/{filename}")
async def download(filename: str):
    for directory in ["output", "workspace", "screenshots"]:
        fpath = __import__('pathlib').Path(directory) / filename
        if fpath.exists():
            return FileResponse(str(fpath), filename=filename)
    raise HTTPException(status_code=404, detail=f"File '{filename}' not found")

# ═══════════════════════════════════════════
# STARTUP
# ═══════════════════════════════════════════

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    logger.info(f"⚡ S.T.E.W launching on port {port}")
    uvicorn.run("server.main:app", host="0.0.0.0", port=port, reload=False)
