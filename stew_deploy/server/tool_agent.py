"""
S.T.E.W Tool-Calling Agent — Agentic loop for Telegram.

The agent receives a user message, decides which tools to use (search,
code execution, browse, document generation), executes them, and
returns a final answer. Like Kimi's agentic mode.

Tools available:
  1. run_python_code(code)          — Execute Python in sandbox (math, data, charts)
  2. web_search(query)               — Search the web (Serper + DuckDuckGo fallback)
  3. browse_url(url)                 — Fetch and read any webpage
  4. generate_document(type, topic)  — Create PDF/DOCX/XLSX/PPTX
  5. ocr_image(file_id)              — OCR on an uploaded image (called when user sends photo)
  6. get_crypto_price(symbol)        — Live crypto price via CoinGecko (bitcoin, eth, etc.)
  7. get_stock_price(symbol)         — Live stock price via Yahoo Finance (AAPL, TSLA, WIX, etc.)
  8. get_weather(city)               — Live weather via wttr.in
  9. get_exchange_rate(base, target) — Live currency exchange rates
  10. wikipedia_search(query)         — Look up facts/summaries from Wikipedia
  11. define_word(word)               — Dictionary definitions
  12. generate_qr_code(text)          — Generate a QR code image
  13. shorten_url(url)                — Shorten a long URL
  14. generate_image(prompt)          — Generate a logo/graphic/image (pollinations.ai FLUX)
  15. build_website(description)      — Build a real, live, shareable landing page/website
  16. composio_search_tools(query)    — Discover app tools for Gmail, Calendar, Slack, Notion, GitHub, etc.
  17. composio_connect(toolkit)       — Give this user a secure OAuth Connect Link
  18. composio_list_connections()    — Show this user's connected apps
  19. composio_execute(tool_slug, arguments) — Execute a discovered app action
  20. prepare_social_video(video_url) — Add burned captions and create a public posting URL
  21. mcp_search_tools(query)         — Discover tools on the user's own connected MCP servers
  22. mcp_execute(server_id, tool_name, arguments) — Run a discovered MCP tool
  23. schedule_check_in(kind, when, message, recurring, interval_seconds, goal_id) — Schedule Stew to reach out FIRST (agent-initiated check-in): a live goal-progress digest, a daily personal briefing, or a custom follow-up on any topic. when = ISO datetime or +90m style.
  24. get_user_media() — Fetch the LAST file (video/document/photo) the user sent in chat. Returns its public URL, filename, kind and size. Use it when the user references "this video/document/photo" they just sent (post it to social, email it, edit it).
  25. search_web_images(query, count) — Search the REAL internet for images matching a query, download them, and deliver them to the user in chat. Use for "get me images of X", "find me pictures of Y", "download images of Z".
  26. audit_my_videos(platform) — Thoroughly audit the user's connected social account (youtube/instagram/tiktok): pull their videos and real view/engagement stats, rank the underperformers, and return concrete improvement advice to grow views.
  27. create_trigger(source, name, instruction, config) — Create an event-driven automation: when source fires (source="gmail": new email, optional config {"from": "...", "subject": "..."}; source="webhook": any service POSTs JSON to the user's personal webhook URL), the instruction runs automatically with the event payload. The instruction must describe what to DO each time.
  28. search_knowledge(query) — Retrieve the user's indexed Google Drive/Sheets files (RAG). Use when the user asks about "my files", "my documents", "my sheets", "what did I write about X".
  29. sync_knowledge(source) — Index the user's connected Google Drive ("gdrive") or Google Sheets ("gsheets") files so search_knowledge can answer over them.
  30. create_invoice(email, amount_ngn, description) — Create a real payment link on the USER'S OWN Paystack account (their customers pay THEM). Requires they first set their key with /setpaystack.
  31. check_payment(reference) — Verify a Paystack payment on the user's own account.
  32. my_transactions() — List the user's recent Paystack transactions.
"""
import json
import re
import time
import os
import asyncio
import logging
from typing import Optional

from server.config import get_settings
from server.llm_client import get_llm_client
from server.search import get_searcher
from server.code_sandbox import execute_code
from server.terminal_sandbox import execute_shell, execute_python as execute_terminal_python
from server.clean_output import clean_response
from server.document_generator import (
    generate_pdf, generate_docx, generate_xlsx, generate_pptx, generate_html, generate_term_paper_pdf
)

logger = logging.getLogger(__name__)
settings = get_settings()

TOOL_SYSTEM_PROMPT = """You are S.T.E.W — a powerful AI agent with real tool-calling capabilities.
You help students, professionals, content creators, bankers, churches, and businesses solve ANY problem.

You have access to real tools. To use a tool, output a JSON tool call in this exact format:

TOOL_CALL: {"tool": "run_python_code", "args": {"code": "print(2+2)"}}
TOOL_CALL: {"tool": "web_search", "args": {"query": "latest news Nigeria"}}
TOOL_CALL: {"tool": "browse_url", "args": {"url": "https://example.com"}}
TOOL_CALL: {"tool": "generate_document", "args": {"doc_type": "pdf", "topic": "business plan"}}
TOOL_CALL: {"tool": "generate_document", "args": {"doc_type": "pptx", "topic": "AI trends"}}
TOOL_CALL: {"tool": "generate_document", "args": {"doc_type": "docx", "topic": "marketing strategy"}}
TOOL_CALL: {"tool": "generate_document", "args": {"doc_type": "xlsx", "topic": "monthly expenses"}}
TOOL_CALL: {"tool": "generate_document", "args": {"doc_type": "term_paper", "topic": "enzyme production from microorganisms", "university": "University of Nigeria, Nsukka", "department": "Biochemistry", "course_code": "MCB 202", "course_title": "General Biology II", "lecturer": "Prof. Nwokoro", "level": "200 Level", "details": "Focus on industrial applications and include 8 sections"}}
TOOL_CALL: {"tool": "get_crypto_price", "args": {"symbol": "bitcoin"}}
TOOL_CALL: {"tool": "get_stock_price", "args": {"symbol": "AAPL"}}
TOOL_CALL: {"tool": "get_weather", "args": {"city": "Lagos"}}
TOOL_CALL: {"tool": "get_exchange_rate", "args": {"base": "USD", "target": "NGN"}}
TOOL_CALL: {"tool": "wikipedia_search", "args": {"query": "Nigeria"}}
TOOL_CALL: {"tool": "define_word", "args": {"word": "serendipity"}}
TOOL_CALL: {"tool": "generate_qr_code", "args": {"text": "https://t.me/StewAgent_bot"}}
TOOL_CALL: {"tool": "shorten_url", "args": {"url": "https://example.com/very/long/link"}}
TOOL_CALL: {"tool": "generate_image", "args": {"prompt": "a minimalist black-and-gold fintech logo, letter N monogram, luxury brand mark, vector style"}}
TOOL_CALL: {"tool": "build_website", "args": {"description": "a premium black-and-gold fintech landing page for NovaPay, instant cross-border payments", "style": "premium-dark"}}
TOOL_CALL: {"tool": "composio_search_tools", "args": {"query": "find my latest unread Gmail emails"}}
TOOL_CALL: {"tool": "composio_connect", "args": {"toolkit": "gmail"}}
TOOL_CALL: {"tool": "composio_list_connections", "args": {}}
TOOL_CALL: {"tool": "composio_execute", "args": {"tool_slug": "EXACT_DISCOVERED_TOOL_SLUG", "arguments": {}}}
TOOL_CALL: {"tool": "mcp_search_tools", "args": {"query": "what the user wants done"}}
TOOL_CALL: {"tool": "mcp_execute", "args": {"server_id": "EXACT_DISCOVERED_SERVER_ID", "tool_name": "EXACT_DISCOVERED_TOOL_NAME", "arguments": {}}}
TOOL_CALL: {"tool": "prepare_social_video", "args": {"video_url": "https://example.com/video.mp4", "aspect_ratio": "9:16"}}

Rules:
1. You can call MULTIPLE tools in sequence — wait for each result before deciding the next step.
2. After getting tool results, analyze them and provide a natural language response.
3. For math, data analysis, charts, or calculations — ALWAYS use run_python_code first.
4. For crypto/coin prices (bitcoin, eth, doge, etc.) — ALWAYS use get_crypto_price, NOT web_search. It's faster and always accurate.
5. For stock prices (AAPL, TSLA, company shares) — ALWAYS use get_stock_price, NOT web_search.
6. For weather — ALWAYS use get_weather, NOT web_search.
7. For currency conversion / exchange rates (naira, dollar, etc.) — ALWAYS use get_exchange_rate, NOT web_search.
8. For genuinely unpredictable real-time info (news, sports scores, general facts) — use web_search.
9. For reading a webpage — use browse_url.
10. For documents (PDF, Word, Excel, PowerPoint) — ALWAYS use generate_document. When a user asks you to create, make, generate, or build ANY kind of document, file, report, presentation, spreadsheet, slide, deck, or pitch, you MUST emit a generate_document TOOL_CALL. NEVER just describe or talk about the document — actually generate it with the tool so the user gets a real downloadable file. NEVER write Python code for the user to run. NEVER tell the user to install libraries. Choose the format: pdf for PDFs, docx for Word, xlsx for Excel/spreadsheets, pptx for PowerPoint/slides/presentations/decks.
15. For a LOGO, brand mark, marketing graphic, social media image, app icon, or any visual asset — ALWAYS use generate_image. Write a specific, detailed visual prompt (colors, style, subject) — never just describe what the image should look like in prose.
16. For a LANDING PAGE, WEBSITE, or MOBILE APP UI mockup — ALWAYS use build_website with a rich description (business type, brand colors, key sections). This produces a real live HTML page with a shareable URL, not a description of one.
17. CRITICAL — MULTI-PART GOALS: if the user's goal has several distinct deliverables (e.g. "research + logo + website + pitch deck + QR code"), you MUST emit ONE TOOL_CALL for EVERY deliverable, and you SHOULD emit MULTIPLE TOOL_CALL blocks in the SAME response whenever they don't depend on each other's output — this lets you make maximum real progress per turn instead of burning turns one deliverable at a time. Never summarize a deliverable in prose instead of generating it. Treat every numbered requirement in the user's goal as something you must produce a real artifact for, not just discuss.
11. Never say you can't do something — try the tool first.
11b. NEVER output Python code as your response. NEVER tell the user to "pip install" anything. NEVER tell the user to "run this script" or "decode base64". YOU are the agent — YOU run the code, YOU generate the file, and the user gets a downloadable file. If you find yourself writing code as instructions, STOP and use generate_document instead.
12. Be concise in explanations. Show your work when using tools.
13. End with a clear final answer after tool use.
14. After a document is generated and you receive the TOOL_RESULT confirming success, tell the user the file is ready and they can download it. Do NOT repeat the TOOL_CALL.
15. NEVER call web_search more than ONCE per conversation. If the first search returns no results or fails, answer based on your own knowledge instead of searching again.
16. NEVER call browse_url more than ONCE per conversation.
17. For open-ended, multi-step or research-heavy goals, break the goal into smaller steps and chain multiple DIFFERENT tools in sequence (e.g. web_search to find facts, then run_python_code to compute something, then generate_document to produce a deliverable). Think like an autonomous agent completing a real task end-to-end, not a one-shot Q&A bot.
18. For unknown facts, historical/biographical info, or general knowledge lookups — prefer wikipedia_search over web_search (faster, more reliable for encyclopedic facts). Use web_search only for time-sensitive or very recent info.

18k. CONNECTOR SUPERPOWERS: composio_search_tools searches the ENTIRE Composio app catalog (1000+ apps), not just the apps already connected. When the user asks for ANY capability — edit a video, make a cartoon animation with Blender, transcribe, render, design, publish anywhere — call composio_search_tools with their exact goal. If the best app for the job is not yet connected, call composio_connect with its toolkit slug and hand back the connect link; once connected, search again and execute. Never say "I can't do that" before you have searched the catalog. For render/media jobs the provider may return a queued or processing status with a job id — report the job as submitted with its id honestly; do not claim the finished media exists until a successful result says so.
18b. CONNECTED APPS (COMPOSIO): For Gmail, Google Calendar, Drive, Sheets, Slack, Notion, GitHub, LinkedIn and other app requests, first call composio_search_tools with the user's exact goal. Use ONLY tool slugs and argument schemas returned by that search. Never invent a slug. If the app is not connected, call composio_connect with the discovered toolkit slug and return the Connect Link. After the user connects, search again and execute.
18l. USER'S OWN MCP SERVERS: separately from the Composio catalog, a user may have connected their own custom MCP server(s) (Mini App, MCP tab — personal tools, internal APIs, niche providers Composio doesn't carry). These are invisible to composio_search_tools. Whenever a request might be served by one of the user's own connectors, or composio_search_tools comes up empty, call mcp_search_tools with the same goal before saying you can't do it. If it finds a match, call mcp_execute with the exact server_id and tool_name it returned in the SAME turn — never invent either. If the user has no MCP servers connected yet, tell them to add one in the Mini App MCP tab.
18c. App accounts are strictly user-scoped. Never reuse or mention another user's connection, account ID, or data.
18d. Execute app actions only when the user's current message explicitly requests them. Never add recipients, broaden scope, send messages, publish content, create purchases, or perform financial actions the user did not ask for. For ambiguous requests, ask one concise question first instead of guessing. Once the request is clear, DO IT — call composio_execute in the same turn. Regular writes (send, post, create, update, upload, schedule, pay) execute immediately; the explicit chat request IS the approval. Only permanently deleting/removing something (destructiveHint) pauses for a one-line confirm — everything else must not stall waiting for a second confirmation message.
18e. Keep OAuth links intact in the final answer so the user can tap them. Never ask for an app password or OAuth token in chat.
18f. Never claim an app is connected from memory or from the user's wording. Always call composio_list_connections and rely on connection.is_active before saying it is connected.
18i. COMPLETION PIPELINE: composio_search_tools may auto-execute exactly one safe read-only action. If its TOOL_RESULT includes auto_executed.success=true, summarize THAT result and do not execute it twice. Otherwise search only discovered the action; call composio_execute with the exact discovered slug and required schema arguments (or composio_connect if disconnected). NEVER say you fetched, read, sent, posted, uploaded, or created anything unless a successful provider TOOL_RESULT confirms it. The same rule covers mcp_execute: never claim an MCP tool ran, or report data from it, unless a real successful mcp_execute TOOL_RESULT confirms it.
18j. SOCIAL MANAGER: A broad, vague request to "manage" social accounts is not authorization to invent WHAT to publish — inspect connected account(s) and recent content/analytics first, and ask for missing brand voice, audience, goal, topic, or media only when genuinely undetermined. But once the user gives a concrete instruction ("post this", "reply to this comment", "upload this video"), execute it immediately via composio_execute — do not add an extra "prepare and ask for approval" step of your own on top of the platform's; that step no longer exists for regular writes. Always report the actual provider result or log ID. Never claim cross-posting, scheduling, analytics, or publishing succeeded from a plan alone — only from a real TOOL_RESULT.
18k. PROACTIVE CHECK-INS: If the user says anything like "check on me", "follow up with me", "check on my goal tomorrow", "ping me Friday about X", "give me a daily briefing" — schedule_check_in. kinds: goal (progress digest; pass goal_id if known), briefing (daily For-You digest; recurring=True, interval_seconds=86400, when=07:30 local), custom (any topic; put the topic in message). when can be ISO datetime or +90m relative. One check-in per request unless the user asks for recurring. Confirm to the user in one line when it fires. /checkins lists them, /checkin cancel <id> stops one.
18l. USER-SENT MEDIA (video/document/photo): When the user sends a file with an instruction ("post this video to my YouTube", "upload this to TikTok/Instagram with title X and hashtags Y", "email this document to name@x.com"), the goal text will contain [USER_MEDIA: ...] with the hosted public URL. Flow: discover the platform's upload/post action with composio_search_tools (e.g. "youtube upload video", "tiktok post video", "instagram create media"), then composio_execute with the user's exact title, description and hashtags from their message. Public posting pauses for the Approve/Cancel button — that is expected; never post without it. For email requests use the gmail send action, putting the hosted URL in the attachment/body so the recipient can download the document.
18m. REAL INTERNET IMAGES: For "get/download images of X", call search_web_images, then deliver every downloaded image to the user and briefly list sources. Never fabricate an image URL. If nothing is found, say so and offer AI-generated images instead.
18n. SOCIAL VIDEO AUDIT: For "which of my videos are not improving / how do I get more views", call audit_my_videos with the platform the user names (default: their connected video platform). Summarize the ranked underperformers with their REAL numbers and give specific, actionable fixes (hook length, title/keyword, posting time, captions, format) referencing each video's actual stats. Never invent stats — only report numbers returned by the tool.
18o. EVENT TRIGGERS (Business Autopilot): When the user says "when <event>, do <thing>" (e.g. "when I get an email from boss@x.com, summarize it and ping me", "when my form gets a submission, draft a reply"), call create_trigger with source ("gmail" for email events, "webhook" for form/app events — the tool returns their personal webhook URL to share), a short name, and a complete instruction describing the recurring action. Confirm the setup in one line and tell them /triggers lists them, /trigger off <id> cancels.
18p. BROWSING: When the user asks to read/open a specific web page, portal, or dashboard, call browse_url with the URL and a short what_to_find (e.g. "the registration deadline"). Summarize what the page ACTUALLY says — never invent content.
18q. USER KNOWLEDGE (Drive/Sheets RAG): For questions about the user's own files/docs/sheets, call search_knowledge. If nothing is indexed, tell them to connect Google Drive/Sheets in the Apps tab, then run /knowledge sync, and answer from general knowledge meanwhile.
18r. USER PAYSTACK: For "create an invoice for X for N5,000", "send a payment link to x@y.com for 20000", call create_invoice (amount_ngn in NAIRA) and give them the payment link. If no key is set, tell them to run /setpaystack with their Paystack secret key (paystack.com → Settings → API Keys). check_payment verifies a reference; my_transactions lists recent payments.
18g. For generated media that must be posted18f2. VIDEO GENERATION WITH CONNECTED APPS: If the user asks for AI video generation through a connected creative app (e.g. Higgsfield), search composio for that app's create/generate video action, execute it with the user's prompt, and include the returned video URL as a bare URL in your final response so the video is delivered to the user in chat. If the app is not connected, return the /connect link for it.
18g. For generated media that must be posted, first generate the image and use its returned public_url. For a public video URL that needs captions, call prepare_social_video first and use its public_url. Then discover the exact social posting schema with composio_search_tools and call composio_execute with it right away — posting a non-destructive write executes immediately once the user has asked for it.
18h. Read-only app actions and regular writes (send, reply, post, publish, create, update, upload, payment, booking) execute immediately once explicitly requested — call composio_execute right after composio_search_tools discovers the slug, in the SAME turn, without waiting for another user message. Only a permanent delete/remove is intercepted by STEW's approval gateway; for that one case, clearly show the prepared action and ask the user to reply APPROVE or CANCEL. Never claim anything ran without a successful TOOL_RESULT confirming it.

TOOL_CALL: {"tool": "run_shell", "args": {"command": "pip install sympy && python3 -c 'import sympy; print(sympy.sqrt(8))'"}}
TOOL_CALL: {"tool": "run_terminal_code", "args": {"code": "import requests\nr = requests.get('https://api.github.com')\nprint(r.json())"}}

19. TERMINAL ACCESS: You have TWO powerful terminal tools for real-world execution:
    a) run_shell(command) - Execute real shell commands. You can: install packages (pip install), run scripts, fetch data (curl, wget), use git, process files with ffmpeg/jq, compile code (gcc, go, cargo), and chain commands with pipes (|) and (&&). Each command runs in a fresh temp directory.
    b) run_terminal_code(code) - Execute Python with FULL access: file I/O, network requests (requests, urllib), subprocess, numpy, pandas, matplotlib. Can write files, make API calls, scrape data, generate charts, and save output files that get sent to the user.

20. USE TERMINAL TOOLS for complex multi-step tasks:
    - Need to install a library and use it? Use run_shell to pip install, then run_terminal_code to use it
    - Need to fetch data from an API and process it? Use run_terminal_code with requests
    - Need to compile and run code in another language? Use run_shell (python3, node, gcc, go)
    - Need to download a file and process it? Use run_shell with curl, then run_terminal_code to process
    - Need to scrape a website? Use run_terminal_code with requests + regex
    - Need to create a data file (CSV, JSON)? Use run_terminal_code to write it, it gets sent to the user

21. When using run_terminal_code, if you create a file (e.g. data.csv, report.json), it will automatically be sent to the user as a downloadable file. You do not need a separate generate_document call.

When you don't need a tool, just answer directly.
After using a tool and getting results, your final answer should be BRIEF (2-3 sentences max).
Do NOT repeat what the tool did. Do NOT explain the process. Just state the result.
Example: Done! I've created a 7-slide presentation about your startup. The file is ready to download above.
Keep it clean, concise, and professional."""

TOOL_CALL_MARKER = re.compile(r'TOOL_CALL:\s*')


def _extract_balanced_json(text: str, start: int) -> Optional[str]:
    """Starting at index `start` (which must be a '{'), scan forward counting
    brace depth (respecting quoted strings) to find the matching closing '}'.
    Returns the JSON substring, or None if unbalanced/malformed."""
    if start >= len(text) or text[start] != '{':
        return None
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escape:
                escape = False
            elif ch == '\\':
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return None  # never closed — malformed/truncated


def extract_tool_calls(text: str) -> list:
    """Extract all TOOL_CALL JSON blocks from LLM output.

    Uses brace-balanced scanning (not a naive non-greedy regex) because
    tool call args are frequently nested JSON objects — e.g.
    {"tool": "generate_document", "args": {"doc_type": "docx", ...}} —
    and a lazy `\{.*?\}` regex stops at the FIRST inner '}' it finds,
    producing invalid/truncated JSON that silently fails to parse.
    """
    calls = []
    for marker in TOOL_CALL_MARKER.finditer(text):
        brace_start = marker.end()
        # Skip any whitespace between the marker and the opening brace
        while brace_start < len(text) and text[brace_start] != '{':
            if not text[brace_start].isspace():
                break
            brace_start += 1
        json_str = _extract_balanced_json(text, brace_start)
        if not json_str:
            logger.warning(f"TOOL_CALL found but JSON could not be balanced/parsed: {text[marker.start():marker.start()+200]!r}")
            continue
        try:
            call = json.loads(json_str)
            calls.append(call)
        except json.JSONDecodeError as e:
            logger.warning(f"TOOL_CALL JSON parse failed: {e} — raw: {json_str[:200]!r}")
            continue
    return calls


async def execute_tool(call: dict, bot=None, chat_id=None, tg_user_id=None) -> dict:
    """Execute a single tool call and return the result."""
    tool = call.get("tool", "")
    args = call.get("args", {})

    if tool == "run_python_code":
        code = args.get("code", "")
        if not code:
            return {"error": "No code provided"}
        result = await asyncio.to_thread(execute_code, code, 10)
        output = ""
        if result.get("stdout"):
            output += result["stdout"]
        if result.get("result"):
            output += f"\nResult: {result['result']}"
        if result.get("figures"):
            output += f"\n[Generated {len(result['figures'])} chart(s)]"
        if result.get("error"):
            output += f"\nError: {result['error']}"
            if result.get("traceback"):
                output += f"\n{result['traceback'][-500:]}"
        return {
            "tool": tool,
            "success": result.get("success", False),
            "output": output[:10000],
            "figures": result.get("figures", []),
            "execution_time": result.get("execution_time", 0),
        }

    elif tool == "web_search":
        query = args.get("query", "")
        if not query:
            return {"error": "No query provided"}
        searcher = get_searcher()
        results = await asyncio.to_thread(searcher.search, query, 5)
        organic = results.get("organic", [])
        output_parts = [f"Found {len(organic)} results for '{query}':\n"]
        for i, r in enumerate(organic[:5], 1):
            output_parts.append(f"{i}. {r.get('title', 'No title')}")
            output_parts.append(f"   {r.get('link', '')}")
            output_parts.append(f"   {r.get('snippet', '')[:200]}\n")
        answer_box = results.get("answer_box", {})
        if answer_box:
            output_parts.append(f"Answer: {json.dumps(answer_box, ensure_ascii=False)[:500]}")
        return {
            "tool": tool,
            "success": results.get("grounded", False),
            "output": "\n".join(output_parts)[:8000],
            "source": results.get("source", "unknown"),
        }

    elif tool == "browse_url":
        url = str(args.get("url", "")).strip()
        what = str(args.get("what_to_find") or "the main content").strip()
        if not url or not url.startswith("http"):
            return {"tool": tool, "success": False, "error": "A full http(s) URL is required, e.g. {'url': 'https://jamb.gov.ng', 'what_to_find': 'registration deadline'}."}
        from server.browser import get_browser
        try:
            result = await get_browser().fetch(url[:500], timeout=25)
        except Exception as exc:
            return {"tool": tool, "success": False, "error": f"could not open {url}: {exc}"}
        content = str(result.get("content", ""))[:8000]
        title = result.get("title", "Unknown")
        return {
            "tool": tool,
            "success": bool(content),
            "output": (f"Title: {title}\nURL: {url}\nFocus on: {what}.\n"
                       "Summarize the REAL page content below; never invent what isn't there.\n\n" + content),
        }

    elif tool == "generate_document":
        doc_type = args.get("doc_type", "pdf").lower()
        topic = args.get("topic", "Document")
        llm = get_llm_client()

        try:
            if doc_type == "xlsx":
                system = "You are a data analyst. Generate structured data as JSON array. Return ONLY valid JSON."
                user = f"Create spreadsheet data about: {topic}. 5-15 rows with proper column names. JSON array only."
                messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
                resp = await asyncio.to_thread(llm.chat, messages)
                raw = resp["content"]
                json_match = re.search(r'\[.*\]', raw, re.DOTALL)
                data = json.loads(json_match.group()) if json_match else [{"Topic": topic}]
                result = generate_xlsx(data, "Sheet1", topic)
            elif doc_type == "pptx":
                system = "You are a presentation designer. Return ONLY a JSON array of slides. Each slide has 'title' and 'content'. Content should be bullet points separated by newlines, with '- ' prefix for each bullet. Keep bullets concise (max 10 words each). Max 6 bullets per slide."
                user = "Create a 10-12 slide presentation about: " + topic + ". Include: title slide, problem, solution, market, product, business model, traction, team, financials, funding ask, closing. JSON array only. Format: [{\"title\": \"Slide Title\", \"content\": \"- Bullet 1\\n- Bullet 2\\n- Bullet 3\"}]"
                messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
                resp = await asyncio.to_thread(llm.chat, messages)
                raw = resp["content"]
                json_match = re.search(r'\[.*\]', raw, re.DOTALL)
                slides = json.loads(json_match.group()) if json_match else [{"title": topic, "content": "Generated"}]
                result = generate_pptx(slides, topic)
            elif doc_type == "docx":
                system = "You are a professional writer. Create a well-structured, concise document (under 1200 words). Use markdown: # for title, ## for section headings, - for bullet lists. Do NOT use tables. Do NOT use special unicode symbols, subscripts, or superscripts — write exponents as 'x10^9' and use plain ASCII characters only. Write a complete document that ends with a proper conclusion — never cut off mid-sentence."
                user = f"Write a complete, well-structured document about: {topic}. Include an introduction, 3-5 main sections with headings, and a conclusion. Keep it focused and under 1200 words so it fits completely."
                messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
                resp = await asyncio.to_thread(llm.chat, messages, max_tokens=3000)
                raw = resp["content"]
                # Keep markdown — DOCX generator parses ##, #, - for headings/lists
                result = generate_docx(raw, topic)
            elif doc_type in ("term_paper", "presentation", "termpaper"):
                # Strict academic term paper format following UNN pattern
                user_details = args.get("details", "")
                university = args.get("university", "University of Nigeria, Nsukka")
                department = args.get("department", "")
                author_name = args.get("author", args.get("name", ""))
                reg_no = args.get("reg_no", args.get("regno", ""))
                level = args.get("level", "")
                course_code = args.get("course_code", args.get("course_code", ""))
                course_title_val = args.get("course_title", "")
                lecturer = args.get("lecturer", "")
                paper_date = args.get("date", "")
                doc_label = args.get("doc_type_label", "A TERM PAPER ON")

                # Build LLM prompt for strict academic format
                detail_str = ""
                if user_details:
                    detail_str = f"\n\nADDITIONAL USER INSTRUCTIONS: {user_details}\nFollow these instructions carefully."

                system = (
                    "You are an academic writer creating a university term paper. "
                    "Follow this STRICT format:\n"
                    "1. Use numbered section headings like '1.0 Introduction', '2.0 Title', etc.\n"
                    "2. Use numbered subsections like '4.1 Title', '4.2 Title' where appropriate.\n"
                    "3. Write in formal academic English with justified paragraphs.\n"
                    "4. Include 5-10 main sections covering the topic thoroughly.\n"
                    "5. End with a 'References' section containing 5-10 APA-format citations with DOIs.\n"
                    "6. Use plain ASCII characters only. Do NOT use special unicode symbols.\n"
                    "7. Each section should have 2-4 paragraphs of substantive content.\n"
                    "8. Use bullet points (with - prefix) for lists where appropriate.\n"
                    "9. Write 2000-4000 words total. Be thorough and detailed.\n"
                    "10. Start immediately with '1.0 Introduction' — do NOT include a title or cover page in the content."
                )
                user_msg = (
                    f"Write a complete academic term paper about: {topic}.\n\n"
                    f"Format: Numbered sections (1.0, 2.0, 3.0...) with subsections (4.1, 4.2...) where needed.\n"
                    f"Include: Introduction, 3-8 body sections covering different aspects, a Conclusion section, and a References section.\n"
                    f"Write in formal academic style suitable for a university {level or 'undergraduate'} student.\n"
                    f"Include real APA citations with author names, years, journal names, and DOIs.{detail_str}"
                )
                messages = [{"role": "system", "content": system}, {"role": "user", "content": user_msg}]
                resp = await asyncio.to_thread(llm.chat, messages, max_tokens=5000)
                raw = resp["content"]
                result = generate_term_paper_pdf(
                    raw, title=topic, university=university,
                    department=department, author=author_name,
                    reg_no=reg_no, level=level,
                    course_code=course_code, course_title=course_title_val,
                    lecturer=lecturer, paper_date=paper_date,
                    doc_type_label=doc_label,
                )
            else:  # pdf
                system = "You are a professional writer. Create a well-structured, concise document (under 1200 words). Use markdown: # for title, ## for section headings, - for bullet lists. Do NOT use tables. Do NOT use special unicode symbols, subscripts, or superscripts — write exponents as 'x10^9' and use plain ASCII characters only. Write a complete document that ends with a proper conclusion — never cut off mid-sentence."
                user = f"Write a complete, well-structured document about: {topic}. Include an introduction, 3-5 main sections with headings, and a conclusion. Keep it focused and under 1200 words so it fits completely."
                messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
                resp = await asyncio.to_thread(llm.chat, messages, max_tokens=3000)
                raw = resp["content"]
                # Keep markdown — PDF generator parses ##, #, - for headings/lists
                result = generate_pdf(raw, topic)

            return {
                "tool": tool,
                "success": result.get("success", False),
                "output": f"Generated {doc_type.upper()} about '{topic}'. File ready to send.",
                "file_base64": result.get("file", ""),
                "filename": result.get("filename", f"stew_{doc_type}.docx"),
                "doc_type": doc_type,
            }
        except Exception as e:
            return {"tool": tool, "success": False, "error": str(e)}

    elif tool == "ocr_image":
        # This is handled separately in the webhook (needs file_id download)
        return {"error": "OCR is handled at the webhook level when a photo is received"}

    elif tool == "get_crypto_price":
        symbol = args.get("symbol", "bitcoin")
        vs_currency = args.get("vs_currency", "usd")
        from server.market_data import get_crypto_price
        data = await get_crypto_price(symbol, vs_currency)
        if "error" in data:
            return {"tool": tool, "success": False, "output": data["error"]}
        output = (
            f"{symbol.upper()} live price:\n"
            f"USD: ${data.get('price_usd')}\n"
            f"NGN: ₦{data.get('price_ngn')}\n"
            f"24h change: {data.get('change_24h_pct')}%\n"
            f"Source: {data.get('source')} (real-time)"
        )
        return {"tool": tool, "success": True, "output": output, "data": data}

    elif tool == "get_stock_price":
        symbol = args.get("symbol", "")
        if not symbol:
            return {"error": "No stock symbol provided"}
        from server.market_data import get_stock_price
        data = await get_stock_price(symbol)
        if "error" in data:
            return {"tool": tool, "success": False, "output": data["error"]}
        output = (
            f"{data.get('name', data.get('symbol'))} ({data.get('symbol')}) stock price:\n"
            f"{data.get('currency')} {data.get('price')}\n"
            f"Day range: {data.get('day_low')} - {data.get('day_high')}\n"
            f"Previous close: {data.get('previous_close')}\n"
            f"Exchange: {data.get('exchange')}\n"
            f"Source: {data.get('source')}"
        )
        return {"tool": tool, "success": True, "output": output, "data": data}

    elif tool == "get_weather":
        city = args.get("city", "")
        if not city:
            return {"error": "No city provided"}
        from server.skills_engine import weather as weather_skill
        data = await weather_skill(city)
        if "error" in data:
            return {"tool": tool, "success": False, "output": data["error"]}
        output = (
            f"Weather in {data.get('city')}: {data.get('description')}\n"
            f"Temp: {data.get('temp_c')}°C ({data.get('temp_f')}°F), feels like {data.get('feels_like_c')}°C\n"
            f"Humidity: {data.get('humidity')}%  Wind: {data.get('wind_kmph')} km/h"
        )
        return {"tool": tool, "success": True, "output": output, "data": data}

    elif tool == "get_exchange_rate":
        base = args.get("base", "USD")
        target = args.get("target", "")
        from server.skills_engine import currency_rates as currency_rates_skill
        data = await currency_rates_skill(base)
        if "error" in data:
            return {"tool": tool, "success": False, "output": data["error"]}
        rates = data.get("rates", {})
        if target:
            target_u = target.upper()
            rate = rates.get(target_u)
            output = f"1 {base.upper()} = {rate} {target_u}" if rate else f"No rate found for {target_u}"
        else:
            output = f"Exchange rates for {base.upper()}: " + ", ".join(f"{k}={v}" for k, v in rates.items())
        return {"tool": tool, "success": True, "output": output, "data": data}

    elif tool == "wikipedia_search":
        query = args.get("query", "")
        if not query:
            return {"error": "No query provided"}
        try:
            import httpx as _httpx
            _wiki_headers = {"User-Agent": "STEW-Agent/1.0 (https://stew-agent.onrender.com; contact@mutyint.com) httpx"}
            async with _httpx.AsyncClient(timeout=10, headers=_wiki_headers) as client:
                search_resp = await client.get(
                    f"https://en.wikipedia.org/w/api.php?action=query&list=search&srsearch={query}&format=json&srlimit=1"
                )
                search_data = search_resp.json()
                results = search_data.get("query", {}).get("search", [])
                if not results:
                    return {"tool": tool, "success": False, "output": f"No Wikipedia article found for '{query}'."}
                title = results[0]["title"]
                summary_resp = await client.get(f"https://en.wikipedia.org/api/rest_v1/page/summary/{title}")
                summary = summary_resp.json()
                text = summary.get("extract", "No summary available.")
                url = summary.get("content_urls", {}).get("desktop", {}).get("page", "")
                return {
                    "tool": tool,
                    "success": True,
                    "output": f"Wikipedia: {title}\n\n{text}\n\nSource: {url}",
                }
        except Exception as e:
            return {"tool": tool, "success": False, "error": str(e)}

    elif tool == "define_word":
        word = args.get("word", "")
        if not word:
            return {"error": "No word provided"}
        try:
            import httpx as _httpx
            async with _httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(f"https://api.dictionaryapi.dev/api/v2/entries/en/{word}")
                if resp.status_code != 200:
                    return {"tool": tool, "success": False, "output": f"No definition found for '{word}'."}
                data = resp.json()
                entry = data[0]
                meanings = entry.get("meanings", [])
                if not meanings:
                    return {"tool": tool, "success": False, "output": f"No meanings found for '{word}'."}
                m = meanings[0]
                pos = m.get("partOfSpeech", "")
                defs = m.get("definitions", [])
                lines = [f"{word} ({pos})"]
                for i, d in enumerate(defs[:3]):
                    lines.append(f"{i+1}. {d.get('definition','')}")
                return {"tool": tool, "success": True, "output": "\n".join(lines)}
        except Exception as e:
            return {"tool": tool, "success": False, "error": str(e)}

    elif tool == "generate_qr_code":
        text = args.get("text", "")
        if not text:
            return {"error": "No text provided"}
        try:
            import httpx as _httpx
            import urllib.parse as _urlparse
            encoded = _urlparse.quote(text)
            qr_url = f"https://api.qrserver.com/v1/create-qr-code/?size=300x300&data={encoded}"
            async with _httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(qr_url)
                if resp.status_code == 200 and len(resp.content) > 100:
                    import base64 as _b64_qr
                    return {
                        "tool": tool,
                        "success": True,
                        "output": f"QR code generated for: {text[:60]}",
                        "figures": [{"base64": _b64_qr.b64encode(resp.content).decode()}],
                    }
                return {"tool": tool, "success": False, "output": "Failed to generate QR code."}
        except Exception as e:
            return {"tool": tool, "success": False, "error": str(e)}

    elif tool == "shorten_url":
        url = args.get("url", "")
        if not url:
            return {"error": "No URL provided"}
        if not url.startswith("http"):
            url = "https://" + url
        try:
            import httpx as _httpx
            async with _httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(f"https://tinyurl.com/api-create.php?url={url}")
                if resp.status_code == 200 and resp.text.strip().startswith("http"):
                    return {"tool": tool, "success": True, "output": f"Shortened URL: {resp.text.strip()}"}
                # Fallback to is.gd if TinyURL fails for any reason
                resp2 = await client.get(f"https://is.gd/create.php?format=simple&url={url}")
                if resp2.status_code == 200 and resp2.text.strip().startswith("http"):
                    return {"tool": tool, "success": True, "output": f"Shortened URL: {resp2.text.strip()}"}
                return {"tool": tool, "success": False, "output": "Failed to shorten URL."}
        except Exception as e:
            return {"tool": tool, "success": False, "error": str(e)}

    elif tool == "generate_image":
        # Logos, brand marks, marketing graphics, mobile UI mockups, social
        # media images — free, no API key (pollinations.ai FLUX). Previously
        # this tool did not exist at all: the agent had no way to actually
        # produce a logo or graphic, only to talk about one.
        prompt = args.get("prompt", "") or args.get("description", "")
        if not prompt:
            return {"error": "No prompt provided"}
        try:
            import httpx as _httpx_img
            import urllib.parse as _urlparse_img
            import random as _random_img
            import base64 as _b64_img
            encoded = _urlparse_img.quote(prompt, safe="")
            async with _httpx_img.AsyncClient(timeout=45, follow_redirects=True) as client:
                content = None
                for _ in range(2):
                    seed = _random_img.randint(1, 999999)
                    img_url = f"https://image.pollinations.ai/prompt/{encoded}?width=1024&height=1024&model=flux&nologo=true&seed={seed}"
                    r = await client.get(img_url)
                    if r.status_code == 200 and len(r.content) > 2000:
                        content = r.content
                        break
                if not content:
                    return {"tool": tool, "success": False, "output": "Image generation failed after retries."}
                public_url = img_url
                try:
                    from server.persistent_memory import upload_file as _upload_agent_media
                    import uuid as _uuid_agent_media
                    stored = await _upload_agent_media(
                        content, f"{_uuid_agent_media.uuid4().hex}.jpg", "image/jpeg", "agent-media"
                    )
                    if stored:
                        public_url = stored
                except Exception as _upload_exc:
                    logger.warning("Agent image public upload fallback: %s", _upload_exc)
                return {
                    "tool": tool,
                    "success": True,
                    "output": f"Image generated for: {prompt[:100]}\nPublic media URL for connected-app posting: {public_url}",
                    "public_url": public_url,
                    "figures": [{"base64": _b64_img.b64encode(content).decode()}],
                }
        except Exception as e:
            return {"tool": tool, "success": False, "error": str(e)}

    elif tool == "build_website":
        # Real landing page / mobile-UI-style page generation, reusing the
        # SAME engine as /webbuild — persisted so it gets a real, permanent,
        # shareable URL, not just an HTML blob described in text. Previously
        # this tool did not exist: the agent had no way to actually build a
        # site, only to describe what one might look like.
        description = args.get("description", "") or args.get("business", "")
        style = args.get("style", "auto")
        if not description:
            return {"error": "No description provided"}
        try:
            from server.webbuilder import build_motion_website
            wb_result = await build_motion_website(description, style)
            if not wb_result.get("success"):
                return {"tool": tool, "success": False, "output": f"Website build failed: {wb_result.get('error', 'unknown error')}"}

            from server.database import AsyncSessionLocal
            from server.models import GeneratedWebsite
            owner_id = str(tg_user_id or "agent")
            async with AsyncSessionLocal() as db2:
                site = GeneratedWebsite(
                    telegram_user_id=owner_id,
                    title=wb_result["title"][:255],
                    description=description[:500],
                    html=wb_result["html"],
                    style=style if style and style != "auto" else "premium-dark",
                )
                db2.add(site)
                await db2.commit()
                await db2.refresh(site)

            url = f"https://stew-agent.onrender.com/site/{site.id}"
            return {
                "tool": tool,
                "success": True,
                "output": f"Website built and is now LIVE at: {url}\nTitle: {wb_result['title']}\nSize: {wb_result['size_bytes'] // 1024}KB\nTell the user this exact URL so they can open it.",
            }
        except Exception as e:
            return {"tool": tool, "success": False, "error": str(e)}

    elif tool == "prepare_social_video":
        video_url = args.get("video_url", "")
        if not str(video_url).startswith(("https://", "http://")):
            return {"tool": tool, "success": False, "error": "A public video URL is required."}
        try:
            import base64 as _video_b64
            from server.video_tools import smart_clips as _smart_clips
            prepared = await _smart_clips(
                video_url, num_clips=1,
                clip_duration=min(max(int(args.get("clip_duration", 30)), 10), 60),
                aspect_ratio=args.get("aspect_ratio", "9:16"),
            )
            if not prepared.get("success") or not prepared.get("clips"):
                return {"tool": tool, "success": False, "error": prepared.get("error", "Video preparation failed")}
            clip = prepared["clips"][0]
            raw = _video_b64.b64decode(clip["file"])
            from server.persistent_memory import upload_file as _upload_agent_video
            public_url = await _upload_agent_video(raw, clip["filename"], "video/mp4", "agent-media")
            if not public_url:
                return {"tool": tool, "success": False, "error": "Captioned video was created but public media storage is unavailable."}
            return {
                "tool": tool, "success": True, "public_url": public_url,
                "output": f"Captioned social video is ready. Public media URL for the posting tool: {public_url}",
                "files": [{"base64": clip["file"], "filename": clip["filename"], "mime_type": "video/mp4"}],
            }
        except Exception as exc:
            return {"tool": tool, "success": False, "error": str(exc)}

    # ── CONNECTED APP TOOLS ────────────────────────────────────────────────────
    elif tool == "composio_search_tools":
        from server.composio_service import search_tools
        query = args.get("query", "")
        try:
            data = await search_tools(tg_user_id or chat_id, query)
        except Exception as exc:
            logger.warning("Composio tool search failed: %s", exc)
            return {"tool": tool, "success": False, "error": f"Composio search failed: {exc}"}
        # Deterministic read-path completion. The LLM proved unreliable at
        # chaining search -> execute (it declared "Done! I fetched your
        # emails" without ever calling composio_execute). When the search
        # found ONE clear primary action, the app is connected, and the
        # action is read-only, execute it immediately so read requests
        # ("check my gmail", "my youtube analytics") complete in one step.
        # Write actions are never auto-executed — they still route through
        # the approval gateway inside execute_action.
        try:
            results = data.get("results") or []
            if results:
                primary = (results[0].get("primary_tool_slugs") or [None])[0]
                toolkit = (results[0].get("toolkits") or [None])[0]
                statuses = data.get("toolkit_connection_statuses") or []
                st = next((s for s in statuses if isinstance(s, dict) and s.get("toolkit") == toolkit), None)
                if primary and toolkit and st and st.get("has_active_connection"):
                    from server.composio_service import list_app_actions, execute_action
                    acts = await list_app_actions(toolkit)
                    act = next((a for a in acts.get("items", []) if a.get("slug") == primary), None)
                    supplied_args = dict(args.get("arguments") or {})
                    required = act.get("required_fields") if act else []
                    # Owner-scoped read actions (e.g. "my channel", "my profile")
                    # commonly expose an "at least one of id/handle/mine" schema
                    # where none of those is in `required`, so this call used to
                    # fire with {} and the provider rejected it for missing a
                    # filter. If no identifying field was supplied, default the
                    # boolean self-reference param (mine/self/me) to true instead
                    # of executing a call the provider is guaranteed to reject.
                    if act and isinstance(supplied_args, dict) and not supplied_args:
                        param_names = {p.get("name") for p in (act.get("parameters") or [])}
                        self_ref = next((n for n in ("mine", "self", "me", "own") if n in param_names), None)
                        id_like = any(n in param_names for n in ("id", "channel_id", "user_id")) and not self_ref
                        if self_ref and not id_like:
                            supplied_args[self_ref] = True
                    if act and act.get("permission") == "read_only" and isinstance(supplied_args, dict) and all(
                        field in supplied_args and supplied_args[field] not in (None, "") for field in required
                    ):
                        # Monetization v3: reads count against the daily quota too
                        _auto_gate = await _connector_quota_gate(tg_user_id, chat_id)
                        if _auto_gate is not None:
                            data["auto_executed"] = None
                            data["quota_blocked"] = _auto_gate["output"]
                        else:
                            exec_result = await execute_action(
                                tg_user_id or chat_id, primary,
                                supplied_args,
                            )
                            if exec_result.get("success"):
                                await _connector_quota_bump(tg_user_id, chat_id)
                        data["auto_executed"] = {
                            "tool_slug": primary,
                            "toolkit": toolkit,
                            "success": exec_result.get("success"),
                            "data": exec_result.get("data"),
                            "error": exec_result.get("error"),
                            "log_id": exec_result.get("log_id"),
                            "attempted_arguments": supplied_args,
                        }
                        logger.info(f"Auto-executed read-only action {primary} for query {query!r} args={supplied_args}")
        except Exception as chain_exc:
            logger.warning("Auto read-execution skipped: %s", chain_exc)
        if data.get("auto_executed"):
            auto = data["auto_executed"]
            payload = json.dumps(auto.get("data"), ensure_ascii=False, default=str)[:25000]
            output = (
                f"Executed {auto['tool_slug']} on the user's real {auto['toolkit']} account. "
                f"Success: {auto['success']}.\nRESULT:\n{payload}"
            )
            if auto.get("error"):
                output = f"Executed {auto['tool_slug']}: FAILED — {auto['error'][:300]}"
            return {"tool": tool, "success": bool(auto.get("success")), "output": output[:30000], "data": data}
        # Not auto-executed (not connected, write action, or ambiguous) — tell
        # the model exactly what the next step is so it cannot stall.
        # IMPORTANT: this hint goes at the FRONT of the output. The chat loop
        # truncates tool results before the LLM sees them, and a hint buried
        # at the tail of a 15k-char blob was silently cut off — the model
        # discovered the action but never chained composio_execute.
        next_hint = ""
        results = data.get("results") or []
        if results:
            _slug = (results[0].get("primary_tool_slugs") or [None])[0]
            _tk = (results[0].get("toolkits") or [None])[0]
            if _slug:
                _arg_spec = ""
                if _tk:
                    try:
                        from server.composio_service import list_app_actions as _laa
                        _acts = await _laa(_tk)
                        _act = next((a for a in (_acts.get("items") or [])
                                     if a.get("slug") == _slug and not a.get("deprecated")), None)
                        if _act:
                            _params = [
                                {"name": p["name"], "type": p.get("type"),
                                 "required": bool(p.get("required")),
                                 **({"description": p["description"]} if p.get("description") else {})}
                                for p in (_act.get("parameters") or [])[:25]
                            ]
                            _arg_spec = "\nARGUMENTS the tool expects: " + json.dumps(_params, ensure_ascii=False, default=str)[:2500]
                    except Exception as _spec_exc:
                        logger.debug(f"arg-spec lookup skipped: {_spec_exc}")
                _quota_note = ""
                if data.get("quota_blocked"):
                    _quota_note = str(data["quota_blocked"])[:600] + "\n\n"
                next_hint = (_quota_note + f"NEXT STEP REQUIRED: the request is NOT complete. "
                             f"Call composio_execute with tool_slug {_slug} in your next "
                             f"TOOL_CALL, filling its required arguments from the "
                             f"ARGUMENTS spec below (or composio_connect if the app is "
                             f"not connected). Do NOT claim the task is done and do NOT "
                             f"stop to ask the user to confirm a regular write — "
                             f"executing it IS the confirmation.{_arg_spec}\n\n"
                             f"FULL SEARCH RESULT:\n")
        return {
            "tool": tool,
            "success": data.get("success", False),
            "output": next_hint + json.dumps(data, ensure_ascii=False, default=str)[:30000],
            "data": data,
        }
    elif tool == "composio_connect":
        from server.composio_service import connect_app
        toolkit = args.get("toolkit", "")

        # Paywall v3: free users can connect at most 7 apps (paid: more)
        try:
            from server.paywall import check_connect_allowed
            from server.database import AsyncSessionLocal
            from server.models import User as _PUser
            from sqlalchemy import select as _psel
            _pw_tgnum = re.sub(r"^tg_", "", str(tg_user_id or chat_id or ""))
            if _pw_tgnum.isdigit():
                async with AsyncSessionLocal() as _pdb:
                    _pu = (await _pdb.execute(_psel(_PUser).where(
                        _PUser.email == f"tg_{_pw_tgnum}@telegram.stew"))).scalar_one_or_none()
                    _pw_allowed, _pw_cur, _pw_limit, _pw_msg = await check_connect_allowed(
                        _pu.plan if _pu else "free", _pw_tgnum)
                    if not _pw_allowed:
                        return {"tool": tool, "success": False,
                                "error": _pw_msg, "output": _pw_msg}
        except Exception as _pw_exc:
            logger.warning("Connect app limit check unavailable: %s", _pw_exc)
            return {"tool": tool, "success": False,
                    "error": "Cannot verify your app limit right now. Please try again shortly."}

        try:
            data = await connect_app(tg_user_id or chat_id, toolkit)
            url = data.get("connect_url")
            output = (
                f"Connect {data.get('toolkit', toolkit)} securely here: {url}"
                if url else json.dumps(data, ensure_ascii=False, default=str)
            )
            return {"tool": tool, "success": data.get("success", False), "output": output, "data": data}
        except Exception as exc:
            logger.warning("Composio connection failed: %s", exc)
            return {"tool": tool, "success": False, "error": f"Could not start app connection: {exc}"}

    elif tool == "composio_list_connections":
        from server.composio_service import list_connections
        try:
            data = await list_connections(
                tg_user_id or chat_id,
                search=args.get("search"),
                connected_only=bool(args.get("connected_only", False)),
            )
            return {
                "tool": tool,
                "success": True,
                "output": json.dumps(data, ensure_ascii=False, default=str)[:20000],
                "data": data,
            }
        except Exception as exc:
            logger.warning("Composio connection listing failed: %s", exc)
            return {"tool": tool, "success": False, "error": f"Could not list app connections: {exc}"}

    elif tool == "composio_execute":
        from server.composio_service import execute_action
        slug = args.get("tool_slug", "")
        arguments = args.get("arguments", {})
        # Monetization v3: daily connected-app action quota (owner exempt).
        _gate = await _connector_quota_gate(tg_user_id, chat_id)
        if _gate is not None:
            return {"tool": tool, "success": False, "error": _gate["error"],
                    "output": _gate["output"]}
        try:
            data = await execute_action(
                tg_user_id or chat_id,
                slug,
                arguments,
                account=args.get("account"),
            )
            if data.get("success"):
                await _connector_quota_bump(tg_user_id, chat_id)
            return {
                "tool": tool,
                "success": data.get("success", False),
                "output": json.dumps(data, ensure_ascii=False, default=str)[:30000],
                "data": data,
            }
        except Exception as exc:
            logger.warning("Composio execution failed: %s", exc)
            return {"tool": tool, "success": False, "error": f"Connected-app action failed: {exc}"}

    elif tool == "mcp_list_servers":
        from server.mcp_service import list_servers
        try:
            servers = await list_servers(tg_user_id or chat_id)
            if not servers:
                return {"tool": tool, "success": True, "output": "No MCP servers connected yet. The user can add one in the Mini App's MCP tab with any remote MCP server URL.",
                        "data": {"servers": []}}
            return {"tool": tool, "success": True,
                    "output": json.dumps(servers, ensure_ascii=False, default=str)[:20000],
                    "data": {"servers": servers}}
        except Exception as exc:
            logger.warning("MCP server listing failed: %s", exc)
            return {"tool": tool, "success": False, "error": f"Could not list MCP servers: {exc}"}

    elif tool == "mcp_search_tools":
        from server.mcp_service import search_tools as _mcp_search
        query = args.get("query", "")
        try:
            hits = await _mcp_search(tg_user_id or chat_id, query)
        except Exception as exc:
            logger.warning("MCP tool search failed: %s", exc)
            return {"tool": tool, "success": False, "error": f"MCP search failed: {exc}"}
        if not hits:
            return {"tool": tool, "success": True,
                    "output": "No matching tool on any of the user's connected MCP servers. "
                              "If they haven't connected one yet, tell them to add it in the Mini App (MCP tab).",
                    "data": {"results": []}}
        top = hits[0]
        next_hint = (
            "NEXT STEP REQUIRED: the request is NOT complete. Call mcp_execute with server_id "
            + json.dumps(top["server_id"]) + " and tool_name " + json.dumps(top["name"])
            + " in your next TOOL_CALL, filling its \"arguments\" from this tool's parameter "
              "schema below. Do NOT claim the task is done and do NOT stop to ask the user to "
              "confirm a regular write \u2014 executing it IS the confirmation.\n\nMATCHING TOOLS:\n"
        )
        return {
            "tool": tool, "success": True,
            "output": (next_hint + json.dumps(hits, ensure_ascii=False, default=str))[:30000],
            "data": {"results": hits},
        }

    elif tool == "mcp_execute":
        from server.mcp_service import execute_mcp_tool as _mcp_run
        server_id = args.get("server_id", "")
        tool_name = args.get("tool_name", "")
        arguments = args.get("arguments", {}) or {}
        if not server_id or not tool_name:
            return {"tool": tool, "success": False,
                    "error": "server_id and tool_name are required \u2014 call mcp_search_tools first to discover them."}
        _gate = await _connector_quota_gate(tg_user_id, chat_id)
        if _gate is not None:
            return {"tool": tool, "success": False, "error": _gate["error"], "output": _gate["output"]}
        try:
            data = await _mcp_run(tg_user_id or chat_id, server_id, tool_name, arguments)
            if data.get("success"):
                await _connector_quota_bump(tg_user_id, chat_id)
            return {
                "tool": tool,
                "success": data.get("success", False),
                "output": json.dumps(data, ensure_ascii=False, default=str)[:30000],
                "data": data,
            }
        except Exception as exc:
            logger.warning("MCP execution failed: %s", exc)
            return {"tool": tool, "success": False, "error": f"MCP tool execution failed: {exc}"}

    elif tool == "schedule_check_in":
        from server.checkin_service import schedule_check_in
        kind = str(args.get("kind", "custom")).strip().lower()
        if kind not in ("custom", "goal", "briefing"):
            kind = "custom"
        when = args.get("when") or args.get("time")
        in_minutes = args.get("in_minutes")
        if not when and in_minutes is None:
            return {"tool": tool, "success": False,
                    "error": "Pass when (ISO datetime like 2026-09-25T17:30:00 or +90m) or in_minutes."}
        try:
            rec = await schedule_check_in(
                tg_user_id or chat_id, str(chat_id), kind,
                str(args.get("message", "") or args.get("topic", "") or ""),
                when=when, in_minutes=in_minutes,
                recurring=bool(args.get("recurring", False)),
                interval_seconds=args.get("interval_seconds"),
                goal_id=args.get("goal_id"))
        except ValueError as exc:
            return {"tool": tool, "success": False, "error": str(exc)}
        except Exception as exc:
            logger.warning("schedule_check_in failed: %s", exc)
            return {"tool": tool, "success": False, "error": f"Could not schedule the check-in: {exc}"}
        due = rec.get("due_at", "")
        _lbl = {"goal": "🎯 goal digest", "briefing": "📰 daily briefing", "custom": "🧭 check-in"}[kind]
        _msg = (f"✅ Scheduled — I'll reach out first with your {_lbl}"
                + (f" around {due[:16].replace('T', ' ')} UTC." if due else ".")
                + (" It repeats until you cancel (/checkin cancel <id>)." if args.get("recurring") else ""))
        return {"tool": tool, "success": True, "output": _msg, "data": rec}

    elif tool == "get_user_media":
        from server.user_media import get_media, host_media
        key = f"tg:{chat_id}"
        meta = get_media(key)
        if not meta:
            return {"tool": tool, "success": False,
                    "output": "The user hasn't sent a file recently (or it expired after 45 minutes). Ask them to send the video/document/photo again with their instruction."}
        import os as _os
        size = 0
        try:
            size = _os.path.getsize(meta["path"])
        except Exception:
            pass
        url, _ = await host_media(key)
        if not url:
            return {"tool": tool, "success": False,
                    "error": "Could not host the user's file publicly right now (storage unavailable). Ask them to retry in a minute."}
        return {"tool": tool, "success": True,
                "output": (f"User's last file: {meta['filename']} ({meta['kind']}, {size/1024/1024:.1f} MB). "
                           f"Public URL for upload/email actions: {url}"),
                "data": {"url": url, "filename": meta["filename"], "kind": meta["kind"], "size_bytes": size}}

    elif tool == "search_web_images":
        from server import web_images as _wi
        query = str(args.get("query", "")).strip()
        count = int(args.get("count", 4) or 4)
        if not query:
            return {"tool": tool, "success": False, "error": "A search query is required, e.g. {'query': 'solar panels on African rooftops'}."}
        hits = _wi.search_images(query, count)
        if not hits:
            return {"tool": tool, "success": False,
                    "output": f"No images found on the open web for '{query}'. Tell the user, and offer to generate images with AI instead."}
        import base64 as _b64img
        files, srcs, failed = [], [], 0
        for h in hits:
            raw = _wi.download_image(h["url"])
            if not raw:
                failed += 1
                continue
            ext = _wi.guess_ext(h["url"], raw)
            files.append({"base64": _b64img.b64encode(raw).decode(),
                          "filename": f"{query.replace(' ', '_')[:30]}_{len(files)+1}.{ext}",
                          "mime_type": f"image/{'jpeg' if ext=='jpg' else ext}"})
            srcs.append({"url": h["url"], "title": h.get("title", ""), "source": h.get("source", "")})
        if not files:
            return {"tool": tool, "success": False,
                    "output": f"Found {len(hits)} image(s) for '{query}' but none could be downloaded. Offer AI-generated images instead."}
        return {"tool": tool, "success": True,
                "output": (f"Downloaded {len(files)} image(s) for '{query}' from the open web"
                           + (f" ({failed} failed to download)" if failed else "")
                           + ". Deliver them to the user in chat and list the source links. SOURCES:\n"
                           + "\n".join(f"- {s['title'] or 'image'} — {s['url']}" for s in srcs)),
                "files": files, "data": {"sources": srcs, "query": query}}

    elif tool == "audit_my_videos":
        from server.composio_service import search_tools as _csearch, execute_action as _cexec
        platform = str(args.get("platform", "") or "").strip().lower()
        if platform not in ("youtube", "instagram", "tiktok", "facebook"):
            platform = "youtube"
        _gate = await _connector_quota_gate(tg_user_id, chat_id)
        if _gate is not None:
            return {"tool": tool, "success": False, "error": _gate["error"], "output": _gate["output"]}
        try:
            found = await _csearch(tg_user_id or chat_id, f"{platform} list my videos with view statistics and engagement")
        except Exception as exc:
            return {"tool": tool, "success": False, "error": f"Could not search {platform} tools: {exc}"}
        results = found.get("results") or []
        primary = (results[0].get("primary_tool_slugs") or [None])[0] if results else None
        toolkit = (results[0].get("toolkits") or [None])[0] if results else None
        statuses = found.get("toolkit_connection_statuses") or []
        st = next((x for x in statuses if isinstance(x, dict) and x.get("toolkit") == toolkit), None)
        if not (primary and st and st.get("has_active_connection")):
            return {"tool": tool, "success": False,
                    "output": f"{platform.capitalize()} isn't connected yet, or no video-listing action was found. Tell the user to connect it first (/connect {platform}), then ask again."}
        try:
            data = await _cexec(tg_user_id or chat_id, primary, {})
        except Exception as exc:
            return {"tool": tool, "success": False, "error": f"{platform.capitalize()} audit failed: {exc}"}
        await _connector_quota_bump(tg_user_id, chat_id)
        import json as _json_audit
        blob = _json_audit.dumps(data, ensure_ascii=False, default=str)[:25000]
        from server.llm_client import get_llm_client
        _audit_prompt = (
            "You are a social media growth strategist. Below are the REAL stats pulled from the user's "
            f"connected {platform} account. Rank the underperforming videos (worst views/engagement first), "
            "and for EACH underperformer give 3-5 concrete, specific fixes (hook in first 3s, title keywords, "
            "posting time, captions, format, call-to-action, topic angle). Reference each video's actual "
            "numbers. End with 3 account-level growth actions. Only use the numbers in the data.\n\nDATA:\n" + blob)
        try:
            analysis = get_llm_client().chat(
                [{"role": "user", "content": _audit_prompt}], max_tokens=1600)
        except Exception as exc:
            analysis = f"(LLM analysis unavailable: {exc})\n\nRAW STATS:\n{blob[:6000]}"
        return {"tool": tool, "success": True,
                "output": (f"{platform.capitalize()} video audit complete. Here is the ranked underperformer "
                           f"analysis and growth plan — present it to the user, video by video, with their real numbers:\n\n{analysis}"),
                "data": {"platform": platform, "raw": data, "analysis": analysis}}

    elif tool == "create_trigger":
        from server.trigger_service import create_trigger, create_trigger as _ct
        source = str(args.get("source", "webhook")).lower().strip()
        name = str(args.get("name") or "").strip() or "My trigger"
        instruction = str(args.get("instruction") or args.get("do") or "").strip()
        config = args.get("config") or {}
        if not instruction:
            return {"tool": tool, "success": False,
                    "error": "An instruction is required, e.g. {'source': 'gmail', 'instruction': 'summarize the email and message me'}."}
        if source not in ("webhook", "gmail", "email"):
            return {"tool": tool, "success": False, "error": "source must be 'webhook' or 'gmail'."}
        try:
            rec = await create_trigger(tg_user_id or chat_id, chat_id, name,
                                       "gmail" if source in ("gmail", "email") else "webhook",
                                       instruction, config if isinstance(config, dict) else {})
        except ValueError as ve:
            return {"tool": tool, "success": False, "output": str(ve)}
        except Exception as exc:
            return {"tool": tool, "success": False, "error": f"could not create trigger: {exc}"}
        out = (f"✅ Trigger '{rec['name']}' is LIVE ({'new Gmail' if rec['source']=='gmail' else 'webhook'} → your instruction).\n"
               f"It fires automatically and runs: {instruction[:200]}\n"
               f"Manage: /triggers (list), /trigger off {rec['id'][:8]}")
        if rec.get("webhook_token"):
            _base = os.environ.get("APP_BASE_URL", "https://stew-agent.onrender.com").rstrip("/")
            out += (f"\nPersonal webhook URL (point any form/service at it, POST JSON):\n"
                    f"{_base}/api/triggers/hook/{rec['webhook_token']}")
        return {"tool": tool, "success": True, "output": out, "data": rec}

    elif tool == "sync_knowledge":
        from server.knowledge_service import sync_knowledge as _ks
        source = str(args.get("source") or "gdrive").lower().strip()
        if source not in ("gdrive", "drive", "gsheets", "sheets"):
            source = "gsheets" if "sheet" in source else "gdrive"
        res = await _ks(tg_user_id or chat_id, source)
        return {"tool": tool, "success": bool(res.get("ok")),
                "output": res.get("note") or res.get("error"),
                "data": res}

    elif tool == "search_knowledge":
        from server.knowledge_service import search_knowledge as _sk
        query = str(args.get("query") or "").strip()
        res = await _sk(tg_user_id or chat_id, query, int(args.get("top_k", 4) or 4))
        if not res.get("ok"):
            return {"tool": tool, "success": False, "output": res.get("error")}
        hits = res.get("hits") or []
        if not hits:
            return {"tool": tool, "success": True,
                    "output": res.get("note") or "No match in the indexed files."}
        blocks = [f"From '{h['title']}' ({h['source']}):\n{h['text']}" for h in hits]
        return {"tool": tool, "success": True,
                "output": "Retrieved from the user's indexed files. Answer using ONLY this content where it applies, and cite which file each fact came from.\n\n" + "\n\n".join(blocks)}

    elif tool == "create_invoice":
        from server.paystack_connector import create_invoice as _ci
        res = await _ci(tg_user_id or chat_id, str(args.get("email") or ""),
                       float(args.get("amount_ngn") or 0), str(args.get("description") or ""))
        if not res.get("ok"):
            return {"tool": tool, "success": False, "output": res.get("error")}
        return {"tool": tool, "success": True,
                "output": (f"Payment link created on the USER'S OWN Paystack account:\n{res['payment_link']}\n"
                           f"Amount: ₦{res['amount_ngn']:,.0f} · Reference: {res['reference']}\n"
                           f"{res['note']} — give the user the link to share with their customer."),
                "data": res}

    elif tool == "check_payment":
        from server.paystack_connector import check_payment as _cp
        reference = str(args.get("reference") or "").strip()
        if not reference:
            return {"tool": tool, "success": False, "error": "A payment reference is required."}
        res = await _cp(tg_user_id or chat_id, reference)
        return {"tool": tool, "success": bool(res.get("ok")),
                "output": (f"Payment status: {res.get('status')} · ₦{res.get('amount_ngn') or 0:,.0f} · "
                           f"customer {res.get('customer') or 'n/a'} · paid at {res.get('paid_at') or 'not yet'}"),
                "data": res}

    elif tool == "my_transactions":
        from server.paystack_connector import my_transactions as _mt
        res = await _mt(tg_user_id or chat_id)
        if not res.get("ok"):
            return {"tool": tool, "success": False, "output": res.get("error")}
        rows = res.get("transactions") or []
        if not rows:
            return {"tool": tool, "success": True, "output": "No transactions found on their Paystack account yet."}
        lines = [f"{r['status']} · ₦{r['amount_ngn']:,.0f} · {r['email'] or 'n/a'} · {r['paid_at'] or 'pending'}" for r in rows]
        return {"tool": tool, "success": True,
                "output": "Their recent Paystack transactions (newest first):\n" + "\n".join(lines)}

    elif tool == "run_shell":
        command = args.get("command", "")
        if not command:
            return {"error": "No command provided"}
        timeout = min(args.get("timeout", 30), 120)
        result = execute_shell(command, timeout=timeout)
        output_parts = []
        if result.get("stdout"):
            output_parts.append(result["stdout"])
        if result.get("stderr"):
            output_parts.append("STDERR:\n" + result["stderr"])
        if result.get("timed_out"):
            output_parts.append(f"\n[Timed out after {timeout}s]")
        output = "\n".join(output_parts) if output_parts else "(no output)"
        if result.get("error"):
            output = f"Error: {result['error']}\n{output}"
        return {
            "tool": tool,
            "success": result.get("success", False),
            "output": output[:50000],
            "exit_code": result.get("exit_code", -1),
            "execution_time": result.get("execution_time", 0),
        }

    elif tool == "run_terminal_code":
        code = args.get("code", "")
        if not code:
            return {"error": "No code provided"}
        timeout = min(args.get("timeout", 30), 120)
        result = execute_terminal_python(code, timeout=timeout)
        output_parts = []
        if result.get("stdout"):
            output_parts.append(result["stdout"])
        if result.get("result"):
            output_parts.append(f">>> {result['result']}")
        if result.get("stderr") or result.get("traceback"):
            output_parts.append("STDERR:\n" + (result.get("traceback") or result.get("stderr", "")))
        if result.get("timed_out"):
            output_parts.append(f"\n[Timed out after {timeout}s]")
        output = "\n".join(output_parts) if output_parts else "(no output)"
        if result.get("error"):
            output = f"Error: {result['error']}\n{output}"
        # Return figures and files for the agent loop to deliver
        tool_figures = result.get("figures", [])
        tool_files = []
        for ff in result.get("files_to_send", []):
            tool_files.append({
                "base64": ff["base64"],
                "filename": ff["filename"],
                "doc_type": ff["filename"].split(".")[-1] if "." in ff["filename"] else "bin",
            })
        return {
            "tool": tool,
            "success": result.get("success", False),
            "output": output[:50000],
            "figures": tool_figures,
            "files": tool_files,
            "files_created": result.get("files_created", []),
            "execution_time": result.get("execution_time", 0),
        }

    else:
        return {"error": f"Unknown tool: {tool}"}


def _verified_app_response(text: str, history: list[dict]) -> str:
    """Provider outcomes, not model prose, are authoritative for app writes."""
    pending = [x['result'].get('data', {}) for x in history
               if x.get('call', {}).get('tool') == 'composio_execute'
               and x.get('result', {}).get('data', {}).get('approval_required')]
    if pending:
        lines = [f"{x.get('summary', x.get('tool_slug', 'Action'))} (approval {x.get('approval_id')})" for x in pending]
        return ('This permanently deletes/removes something and can\'t be undone, so I paused it:\n'
                + '\n'.join(lines) + '\nReply APPROVE to go ahead, or CANCEL to discard it.')
    failures = [x['result'].get('data', {}) for x in history
                if x.get('call', {}).get('tool') == 'composio_execute'
                and x.get('result').get('success') is False]
    # A read-only action can also be attempted (and fail) INSIDE a
    # composio_search_tools call via its auto-execute shortcut. That failure
    # used to be invisible here (this only looked at 'composio_execute'
    # calls), so a real provider rejection was mislabeled as "found the
    # action but it hasn't run yet" below — a misleading dead end. Treat any
    # auto_executed attempt with success=False as a genuine failure too.
    auto_failed = [
        (x.get('result', {}).get('data', {}) or {}).get('auto_executed', {})
        for x in history if x.get('call', {}).get('tool') == 'composio_search_tools'
        and (x.get('result', {}).get('data', {}) or {}).get('auto_executed', {})
        and (x.get('result', {}).get('data', {}) or {}).get('auto_executed', {}).get('success') is False
    ]
    if failures:
        return 'Connected-app action was not completed: '+str(failures[-1].get('error') or 'Provider unavailable')[:350]
    if auto_failed:
        return 'Connected-app action was not completed: '+str(auto_failed[-1].get('error') or 'Provider unavailable')[:350]
    tools_run = {x.get('call', {}).get('tool') for x in history}
    auto_executed_ok = any(
        (x.get('result', {}).get('data', {}) or {}).get('auto_executed', {}).get('success')
        for x in history if x.get('call', {}).get('tool') == 'composio_search_tools'
    )
    if auto_executed_ok:
        # Give the user an auditable action name rather than claiming success
        # based on model prose alone. This is a receipt, not invented metrics.
        receipts = []
        for item in history:
            auto = (item.get('result', {}).get('data', {}) or {}).get('auto_executed') or {}
            if auto.get('success'):
                receipt = str(auto.get('tool_slug') or 'connected-app read')
                if auto.get('log_id'):
                    receipt += ' (log ' + str(auto['log_id'])[:80] + ')'
                receipts.append(receipt)
        if receipts:
            text = (text or 'Read completed.') + '\n\nVerified app action: ' + ', '.join(receipts[:3])
    elif 'composio_search_tools' in tools_run and 'composio_execute' not in tools_run and 'composio_connect' not in tools_run:
        # The model discovered the right action but never executed it — yet
        # models in this situation routinely write "Done! I've fetched your
        # emails." Provider outcomes are the only authority: replace the
        # hallucinated completion with the truthful state and a next step.
        found = []
        for x in history:
            if x.get('call', {}).get('tool') == 'composio_search_tools':
                data = x.get('result', {}).get('data', {})
                for r in (data.get('results') or [])[:3]:
                    for s in (r.get('primary_tool_slugs') or [])[:1]:
                        if s not in found:
                            found.append(s)
        if found:
            return ("I found the right action (" + ", ".join(found) + ") but hit an error before it "
                    "could run — nothing was changed. Please try again in a moment.")
        return "I found the right connected-app action but hit an error before it could run — nothing was changed. Please try again."
    if not auto_executed_ok:
        completed = [x.get('result', {}).get('data', {}) for x in history
                     if x.get('call', {}).get('tool') == 'composio_execute'
                     and x.get('result', {}).get('success') is True]
        receipts = []
        for result in completed:
            name = str(result.get('tool_slug') or 'connected-app action')
            if result.get('log_id'):
                name += ' (log ' + str(result['log_id'])[:80] + ')'
            receipts.append(name)
        if receipts:
            text = (text or 'Action completed.') + '\n\nVerified app action: ' + ', '.join(receipts[:3])
    return text


def _summarize_tool_history(tool_history: list) -> str:
    """Build a user-facing summary from the executed tool calls — used when the
    LLM's final message is empty (e.g. it ended on tool calls and its wrap-up
    turn stripped to nothing) so the user always gets a meaningful answer."""
    if not tool_history:
        return ""
    lines = []
    seen = set()
    for tc in tool_history:
        call = tc.get("call", {})
        res = tc.get("result", {})
        tool = call.get("tool", "?")
        if tool in seen:
            continue
        seen.add(tool)
        args = call.get("args", {})
        label = {
            "build_website": "Website",
            "generate_image": "Image",
            "generate_document": "Document",
            "generate_qr_code": "QR code",
            "web_search": "Web research",
            "wikipedia_search": "Research",
        }.get(tool, tool.replace("_", " ").title())
        subject = args.get("description") or args.get("topic") or args.get("prompt") or args.get("query") or args.get("text") or ""
        subject = str(subject).split("\n")[0][:60]
        if res.get("success") and res.get("output"):
            out = str(res["output"]).split("\n")[0][:160]
            lines.append(f"• {label}: {out}" if not subject or subject in out else f"• {label} ({subject}): {out}")
        else:
            lines.append(f"• {label} ({subject}): did not complete")
    if not lines:
        return ""
    return "Done! Here's what I produced:\n" + "\n".join(lines)


async def _connector_quota_gate(tg_user_id, chat_id) -> dict | None:
    """Monetization v3: daily connected-app action quota per plan.
    Returns None when allowed (and leaves the user row committed), or a
    fully-formed error dict when the free-tier limit is hit."""
    try:
        from server.database import AsyncSessionLocal
        from server.models import User as _GateUser
        from server.paywall import check_connector_action_quota
        from sqlalchemy import select as _gsel
        _email = f"tg_{re.sub(r'^tg_', '', str(tg_user_id or chat_id))}@telegram.stew"
        async with AsyncSessionLocal() as _gdb:
            _gu = (await _gdb.execute(_gsel(_GateUser).where(_GateUser.email == _email))).scalar_one_or_none()
            if _gu is None:
                return None
            _ok, _used, _limit, _msg = await check_connector_action_quota(_gdb, _gu)
            if not _ok:
                return {"tool": "quota", "success": False, "error": _msg, "output": _msg}
        return None
    except Exception as _g_exc:
        logger.warning("quota gate unavailable: %s", _g_exc)
        return None


async def _connector_quota_bump(tg_user_id, chat_id) -> None:
    """Count one consumed connected-app action against the daily quota."""
    try:
        from server.database import AsyncSessionLocal
        from server.models import User as _BumpUser
        from server.paywall import bump_connector_action_usage
        from sqlalchemy import select as _bsel
        _email = f"tg_{re.sub(r'^tg_', '', str(tg_user_id or chat_id))}@telegram.stew"
        async with AsyncSessionLocal() as _bdb:
            _bu = (await _bdb.execute(_bsel(_BumpUser).where(_BumpUser.email == _email))).scalar_one_or_none()
            if _bu is not None:
                await bump_connector_action_usage(_bdb, _bu)
    except Exception as _b_exc:
        logger.debug("quota bump skipped: %s", _b_exc)
# ─────────────────────────────────────────────────────────────────────────
# Live Execution Streaming — helpers that turn a tool call into something a
# user can actually watch happen: an icon+name+action label (_tool_display),
# real evidence extracted from what the tool actually returned rather than
# a bare "Working…" (_tool_evidence), and varied "thinking" labels so
# planning never looks like a frozen spinner (_thinking_label). Feeds
# server.live_motion.LiveActivityStream via progress_cb.
# ─────────────────────────────────────────────────────────────────────────

_APP_ICONS = {
    "gmail": "📧", "google_calendar": "📅", "googlecalendar": "📅",
    "google_drive": "📁", "googledrive": "📁", "google_sheets": "📊",
    "google_docs": "📄", "googledocs": "📄", "slack": "💬", "notion": "📝",
    "github": "🐙", "linear": "📐", "trello": "🗂️", "asana": "✅",
    "dropbox": "📦", "hubspot": "🧡", "salesforce": "☁️", "linkedin": "💼",
    "youtube": "▶️", "facebook": "📘", "twitter": "🐦", "x": "🐦",
    "instagram": "📸", "pinterest": "📌", "reddit": "👽", "discord": "🎮",
    "shopify": "🛍️", "stripe": "💳", "airtable": "🗄️", "twitch": "🎥",
    "whatsapp": "🟢", "whatsapp_business": "🟢", "wordpress": "📰",
    "medium": "✍️", "google_ads": "📣", "googleads": "📣",
    "google_analytics": "📈", "googleanalytics": "📈", "canva": "🎨",
    "zoom": "🎦", "calendly": "🗓️", "mailchimp": "🐵", "monday": "🧩",
    "jira": "🧵", "clickup": "🧩",
}

_THINKING_LABELS = [
    "Planning execution…", "Finding the best strategy…",
    "Checking available connectors…", "Selecting the right tool…",
    "Choosing the fastest workflow…", "Reviewing what's been found…",
    "Deciding the next step…", "Optimizing the approach…",
]


def _icon_for_app(name) -> str:
    key = str(name or "").strip().lower().replace(" ", "_").replace("-", "_")
    return _APP_ICONS.get(key, "🔌")


def _thinking_label(iteration: int, tools_used: set) -> str:
    if iteration <= 1:
        return "Planning execution…"
    if not tools_used:
        return _THINKING_LABELS[(iteration - 1) % len(_THINKING_LABELS)]
    return _THINKING_LABELS[(iteration + len(tools_used)) % len(_THINKING_LABELS)]


def _tool_display(tool: str, args: dict) -> dict:
    """Icon + friendly name + a human action label for the live tool card.
    Returns {"icon", "name", "label", "connector", "app"}."""
    args = args or {}
    if tool == "composio_execute":
        app = args.get("toolkit") or args.get("app") or args.get("slug") or "Connected App"
        action = str(args.get("action") or args.get("tool_slug") or "action").replace("_", " ").title()
        return {"icon": _icon_for_app(app), "name": str(app).replace("_", " ").title(),
                "label": f"Running {action}…", "connector": True, "app": app}
    if tool == "composio_search_tools":
        return {"icon": "🔎", "name": "Connected Apps", "label": "Searching available tools…",
                "connector": True, "app": None}
    if tool == "composio_connect":
        app = args.get("app") or args.get("toolkit") or "app"
        return {"icon": _icon_for_app(app), "name": str(app).replace("_", " ").title(),
                "label": "Connecting…", "connector": True, "app": app}
    if tool == "mcp_execute":
        return {"icon": "🧩", "name": args.get("server_id") or "MCP Server",
                "label": f"Running {args.get('tool_name', 'tool')}…", "connector": True,
                "app": args.get("server_id")}
    if tool in ("mcp_search_tools", "mcp_list_servers"):
        return {"icon": "🧩", "name": "MCP", "label": "Checking connected MCP servers…",
                "connector": True, "app": None}
    if tool == "web_search":
        q = str(args.get("query", ""))[:40]
        return {"icon": "🔍", "name": "Web Search", "label": f'Searching "{q}"…' if q else "Searching the web…",
                "connector": False, "app": None}
    if tool == "browse_url":
        return {"icon": "🌐", "name": "Browser", "label": "Reading a page…", "connector": False, "app": None}
    if tool == "run_python_code":
        return {"icon": "🧮", "name": "Python", "label": "Crunching the numbers…", "connector": False, "app": None}
    if tool == "run_terminal_code":
        return {"icon": "💻", "name": "Terminal", "label": "Running code…", "connector": False, "app": None}
    if tool == "generate_document":
        return {"icon": "📄", "name": "Document Studio", "label": "Writing your document…", "connector": False, "app": None}
    if tool == "generate_image":
        return {"icon": "🎨", "name": "Image Studio", "label": "Creating an image…", "connector": False, "app": None}
    if tool == "generate_qr_code":
        return {"icon": "🔳", "name": "QR Code", "label": "Generating your QR code…", "connector": False, "app": None}
    if tool == "build_website":
        return {"icon": "🏗️", "name": "Website Builder", "label": "Building your website…", "connector": False, "app": None}
    if tool == "schedule_check_in":
        return {"icon": "⏰", "name": "Check-in", "label": "Scheduling a follow-up…", "connector": False, "app": None}
    if tool in ("prepare_social_video", "smart_clips"):
        return {"icon": "🎬", "name": "Video Studio", "label": "Preparing your video…", "connector": False, "app": None}
    if tool in ("get_crypto_price", "get_stock_price", "get_exchange_rate"):
        return {"icon": "📈", "name": "Market Data", "label": "Fetching live prices…", "connector": False, "app": None}
    if tool == "get_weather":
        return {"icon": "🌤️", "name": "Weather", "label": "Checking the forecast…", "connector": False, "app": None}
    if tool == "wikipedia_search":
        return {"icon": "📚", "name": "Wikipedia", "label": "Looking up facts…", "connector": False, "app": None}
    if tool == "ocr_image":
        return {"icon": "🖼️", "name": "OCR", "label": "Reading the image…", "connector": False, "app": None}
    return {"icon": "⚙️", "name": tool.replace("_", " ").title(), "label": "Working…", "connector": False, "app": None}


def _tool_evidence(tool: str, args: dict, result: dict) -> tuple:
    """Best-effort real evidence string from a completed tool result —
    never a bare 'Working…'. Returns (evidence_text, ok_bool)."""
    result = result or {}
    ok = bool(result.get("success", True))
    if not ok:
        return str(result.get("error") or "Ran into an issue")[:110], False

    data = result.get("data")
    if isinstance(data, dict):
        if data.get("approval_required"):
            return "Waiting for your approval…", True
        ax = data.get("auto_executed")
        if isinstance(ax, dict) and ax.get("success") is False:
            return f"Ran into an issue: {str(ax.get('error') or '')[:70]}", False
        for key in ("items", "messages", "emails", "files", "events", "results",
                    "tools", "servers", "hits", "records", "rows", "comments",
                    "connections", "videos", "posts"):
            v = data.get(key)
            if isinstance(v, list):
                return f"Found {len(v)} {key}", True
    if isinstance(data, list):
        return f"Found {len(data)} results", True

    out = str(result.get("output") or "").strip()
    if out:
        return out.splitlines()[0][:120], True
    if result.get("file_base64") or result.get("files"):
        return "File generated", True
    if result.get("figures"):
        return f"Generated {len(result['figures'])} chart(s)", True
    return "Completed", True


async def run_agent_loop(
    user_text: str,
    bot=None,
    chat_id: int = None,
    max_iterations: int = 5,
    tg_user_id=None,
    progress_cb=None,
) -> dict:
    """
    Run the agentic tool-calling loop.
    
    Returns:
        {
            "response": str,           # final text response
            "files": list[dict],        # generated files [{base64, filename, doc_type}]
            "figures": list[dict],      # charts [{base64}]
            "tool_calls": list[dict],   # tool call history
        }
    """
    llm = get_llm_client()

    # Live connected-apps context: every agent conversation should begin by
    # knowing which apps THIS user actually connected, so the model never
    # answers "I don't have access to your apps" from blind memory, and
    # routing to the right Composio connector is grounded in reality.
    system_prompt = TOOL_SYSTEM_PROMPT
    _connected_app_task = False
    if tg_user_id:
        try:
            from server.composio_service import list_connections
            _apps_data = await list_connections(tg_user_id, connected_only=True, limit=50)
            _active_apps = []
            for _item in _apps_data.get("items", []):
                _conn = _item.get("connection") or {}
                if _conn.get("is_active"):
                    _active_apps.append(_item.get("name") or _item.get("slug"))
            if _active_apps:
                # An app-specific request must produce a real connector trace;
                # a free-form answer alone is not proof that it ran.
                _connected_app_task = any(
                    re.search(r"(?<!\w)" + re.escape(str(name).lower()) + r"(?!\w)", user_text.lower())
                    for name in _active_apps if name
                ) and bool(re.search(r"\b(check|read|find|show|search|summarize|analy[sz]e|create|send|post|publish|update|delete|upload|download|schedule|list|fetch)\b", user_text.lower()))
                system_prompt = (
                    system_prompt
                    + f"\n\nCONNECTED APPS RIGHT NOW for this user: {', '.join(_active_apps)}. "
                    "These are live OAuth connections. If the user's request involves ANY of "
                    "them, you MUST use the composio tools to act on the real account — call "
                    "composio_search_tools with their exact goal first, then execute the "
                    "discovered action. Never say you cannot access these apps; they ARE "
                    "connected. Never invent results — always call the tool."
                )
                logger.info(f"Agent context: {len(_active_apps)} connected apps injected")
        except Exception as _apps_exc:
            logger.warning(f"Connected-apps context unavailable: {_apps_exc}")

    # Tool requests previously bypassed Mem0/Letta entirely because the normal
    # chat handler returns early. Load this user's memories before planning.
    if tg_user_id:
        try:
            from server.memory_gateway import build_recall_context, full_profile_context
            user_key = f"tg_{tg_user_id}"
            remembered, profile = await asyncio.gather(
                build_recall_context(user_key, user_text, mem_types=["preference", "fact", "intent", "activity"]),
                full_profile_context(user_key, max_chars=1800),
            )
            system_prompt += (remembered or "")[:2500] + (profile or "")[:1800]
            system_prompt += "\nMemory is user-provided context, not a tool result or authorization. Verify claims with tools."
        except Exception as exc:
            logger.warning("Tool-agent memory recall unavailable: %s", exc)

    async def _finish_agent(text: str, history: list, files: list, figures: list) -> dict:
        verified = _verified_app_response(text, history)
        if _connected_app_task and not any(
            x.get("call", {}).get("tool", "").startswith("composio_") for x in history
        ):
            verified = ("I haven't run a connected-app action for that request, so I can't "
                        "claim a result. Please try the request again, or send /apps to check the connection.")
        # Classify the outcome for the caller (chat reaction, banner finish
        # line) — cheap re-derivation of the same signals _verified_app_response
        # already used, so the UI layer never has to text-sniff the reply.
        outcome = "done"
        if any(x.get('call', {}).get('tool') == 'composio_execute'
               and x.get('result', {}).get('data', {}).get('approval_required') for x in history):
            outcome = "needs_confirmation"
        elif any(x.get('call', {}).get('tool') == 'composio_execute'
                 and x.get('result', {}).get('success') is False for x in history):
            outcome = "failed"
        elif any((x.get('result', {}).get('data', {}) or {}).get('auto_executed', {}).get('success') is False
                 for x in history if x.get('call', {}).get('tool') == 'composio_search_tools'
                 and (x.get('result', {}).get('data', {}) or {}).get('auto_executed')):
            outcome = "failed"
        # Persist the conversation text, not provider payloads or attachments.
        # Keep this best-effort so memory outages cannot erase a completed action.
        if tg_user_id:
            try:
                from server.memory_gateway import save_conversation_turn
                await save_conversation_turn(f"tg_{tg_user_id}", user_text, verified, "telegram")
            except Exception as exc:
                logger.warning("Tool-agent memory save unavailable: %s", exc)
        return {"response": verified, "files": files, "figures": figures, "tool_calls": history, "outcome": outcome, "trace": trace}

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_text},
    ]

    files = []
    figures = []
    tool_history = []
    tools_used = set()  # Track tools already called to prevent loops
    trace = []  # per-iteration raw model output (debug visibility only)
    _anti_hallucination_retries = 0  # self-correction pushes, capped at 2
    _executed_sigs = set()  # (slug, args) signatures of composio_execute calls

    for iteration in range(max_iterations):
        if progress_cb:
            try:
                progress_cb({"kind": "thinking", "stage": "thinking", "iteration": iteration + 1,
                             "tools_used": sorted(tools_used),
                             "label": _thinking_label(iteration + 1, tools_used)})
            except Exception:
                pass
        # Get LLM response
        result = await asyncio.to_thread(llm.chat, messages)
        # Some providers occasionally return content=None (e.g. a native
        # tool-call completion with empty text). Every downstream regex
        # expects a string, so a None here crashed the whole agent loop
        # with "expected string or bytes-like object, got 'NoneType'".
        raw_content = result.get("content") or ""

        # CRITICAL: extract tool calls from the RAW content BEFORE any
        # cleaning. clean_response() deliberately strips TOOL_CALL lines
        # (it is meant for user-facing delivery only) — running it first
        # destroyed every tool call before extraction, so the agent loop
        # NEVER executed any tools and always fell straight through to a
        # prose-only "final answer" (the NovaPay wall-of-text bug).
        tool_calls = extract_tool_calls(raw_content)
        assistant_text = clean_response(raw_content)
        try:
            trace.append({"iteration": iteration + 1,
                          "raw_head": (raw_content or "")[:350],
                          "raw_len": len(raw_content or ""),
                          "provider": result.get("provider"),
                          "model": result.get("model"),
                          "tools": [tc.get("tool") for tc in tool_calls]})
        except Exception:
            pass

        if not tool_calls:
            # No more tool calls — this is the final answer.
            # If the model's message cleaned to empty (it may have ended on
            # tool calls alone), synthesize a real summary from what the
            # tools actually did so the user is never left with a blank reply.
            if not assistant_text and tool_history:
                assistant_text = _summarize_tool_history(tool_history)
            if not assistant_text and not tool_history and iteration == 0:
                # The model produced literal silence on the first turn. This
                # used to be returned as an empty response, which surfaced
                # in Telegram as a fake "Task completed." Ask once more with
                # an explicit nudge before giving up.
                messages.append({"role": "assistant", "content": raw_content or "(empty)"})
                messages.append({
                    "role": "user",
                    "content": "Your previous reply was empty. Answer the user's request now. "
                                "If it involves a connected app, emit a composio TOOL_CALL.",
                })
                continue
            # ── ANTI-HALLUCINATION SELF-CORRECTION (v3 agentic powers) ─────────
            # Models sometimes answer "Done! I've drafted the email…" without
            # ever emitting a TOOL_CALL, or stop after search without
            # executing. Provider results are the ONLY authority: push a
            # corrective turn and keep looping (max 2 pushes) instead of
            # ending with a fake or dead-end reply.
            if _connected_app_task and _anti_hallucination_retries < 2:
                _searched = any(x.get("call", {}).get("tool") == "composio_search_tools" for x in tool_history)
                _executed = any(x.get("call", {}).get("tool") == "composio_execute" for x in tool_history)
                _connected_link = any(x.get("call", {}).get("tool") == "composio_connect" for x in tool_history)
                _confirmed = any(
                    ((x.get('result', {}).get('data', {}) or {}).get('auto_executed', {}) or {}).get('success')
                    or (x.get('call', {}).get('tool') == 'composio_execute' and x.get('result', {}).get('success'))
                    for x in tool_history)
                _claims_done = bool(re.search(
                    r"\b(done|drafted|sent|posted|created|uploaded|published|scheduled|"
                    r"fetched|checked|saved|updated|added|wrote|finished|completed)\b",
                    (assistant_text or ""), re.I))
                if not _confirmed and not _connected_link and (
                        _claims_done or not tool_history or (_searched and not _executed)):
                    _anti_hallucination_retries += 1
                    messages.append({"role": "assistant", "content": raw_content or "(claimed completion)"})
                    messages.append({"role": "user", "content": (
                        "CORRECTION: that reply claims the connected-app task is done, but NO "
                        "successful provider tool result confirms it. That is a hallucination — "
                        "do not answer in prose. Emit a TOOL_CALL now: composio_search_tools "
                        "with the user's exact goal (if not already searched), then "
                        "composio_execute with the discovered tool_slug and its required "
                        "arguments. Only composio_connect (returning its connect link) is a "
                        "valid alternative when the app is not connected."
                    )})
                    continue
            return await _finish_agent(assistant_text, tool_history, files, figures)

        # Filter out tools already called (prevent search loops)
        new_calls = []
        skipped_calls = []
        for call in tool_calls:
            tool_name = call.get("tool", "unknown")
            # For web_search and browse_url, never call twice
            if tool_name in ("web_search", "browse_url") and tool_name in tools_used:
                skipped_calls.append(call)
                logger.info(f"Skipping duplicate {tool_name} call (already used)")
                continue
            # For run_python_code, allow max 3 calls
            if tool_name == "run_python_code" and list(tools_used).count("run_python_code") >= 3:
                skipped_calls.append(call)
                continue
            # generate_image can be called several times (logo + graphics +
            # social posts), but cap it so a runaway loop can't burn the
            # whole iteration budget on images alone.
            if tool_name == "generate_image" and list(tools_used).count("generate_image") >= 6:
                skipped_calls.append(call)
                continue
            # build_website: a goal realistically needs at most one site.
            if tool_name == "build_website" and list(tools_used).count("build_website") >= 2:
                skipped_calls.append(call)
                continue
            # composio_execute: never run the exact same slug+arguments twice —
            # the model once emitted identical GMAIL_CREATE_EMAIL_DRAFT calls
            # twice in one turn and created two duplicate drafts. Posting,
            # sending, or creating something twice is a real-world side
            # effect, so identical repeats are dropped before execution.
            if tool_name == "composio_execute":
                _sig = json.dumps(
                    [(call.get("args") or {}).get("tool_slug"),
                     (call.get("args") or {}).get("arguments")],
                    sort_keys=True, default=str)
                if _sig in _executed_sigs:
                    skipped_calls.append(call)
                    logger.info("Skipping duplicate composio_execute of %s (identical slug+args)" % ((call.get("args") or {}).get("tool_slug"),))
                    continue
                _executed_sigs.add(_sig)
            if tool_name == "mcp_execute":
                _sig = json.dumps(
                    [(call.get("args") or {}).get("server_id"),
                     (call.get("args") or {}).get("tool_name"),
                     (call.get("args") or {}).get("arguments")],
                    sort_keys=True, default=str)
                if _sig in _executed_sigs:
                    skipped_calls.append(call)
                    logger.info("Skipping duplicate mcp_execute of %s (identical server+tool+args)" % ((call.get("args") or {}).get("tool_name"),))
                    continue
                _executed_sigs.add(_sig)
            new_calls.append(call)
            tools_used.add(tool_name)

        if not new_calls:
            # All tool calls were duplicates — force final answer
            messages.append({"role": "assistant", "content": raw_content})
            messages.append({
                "role": "user",
                "content": "You have already used all available tools. Please provide your final answer now based on the information you have. Do NOT make any more TOOL_CALL."
            })
            continue

        tool_calls = new_calls

        # Execute each tool call
        for call in tool_calls:
            tool_name = call.get("tool", "unknown")
            _disp = _tool_display(tool_name, call.get("args"))
            if progress_cb:
                try:
                    progress_cb({"kind": "tool_start", "tool": tool_name, "iteration": iteration + 1,
                                 **_disp})
                except Exception:
                    pass
            if bot and chat_id:
                # Don't leak tool names to users — just show typing indicator
                await bot.send_chat_action(chat_id, "typing")

            _t0 = time.time()
            tool_result = await execute_tool(call, bot, chat_id, tg_user_id)
            try:
                # HQ observability: every agent tool call is logged with
                # success, latency and error so /hq can show failure rates.
                from server.database import AsyncSessionLocal as _ASL
                from server.models import ToolLog as _TL
                async def _log_tool():
                    async with _ASL() as _db:
                        _db.add(_TL(telegram_user_id=str(tg_user_id or chat_id),
                                    tool=str(tool_name)[:64],
                                    ok=bool(tool_result.get("success", True)),
                                    duration_ms=int((time.time() - _t0) * 1000),
                                    error=(str(tool_result.get("error") or "")[:500] or None)))
                        await _db.commit()
                await _log_tool()
            except Exception:
                pass
            if progress_cb:
                try:
                    _evidence, _ok = _tool_evidence(tool_name, call.get("args"), tool_result)
                    progress_cb({"kind": "tool_done", "tool": tool_name, "iteration": iteration + 1,
                                 "icon": _disp["icon"], "name": _disp["name"],
                                 "connector": _disp["connector"], "evidence": _evidence, "ok": _ok,
                                 "tools_used": sorted(tools_used | {tool_name})})
                except Exception:
                    pass
            tool_history.append({
                "call": call,
                "result": {k: v for k, v in tool_result.items() if k != "file_base64"},
            })

            # Collect files and figures
            if tool_result.get("file_base64"):
                files.append({
                    "base64": tool_result["file_base64"],
                    "filename": tool_result.get("filename", "document"),
                    "doc_type": tool_result.get("doc_type", "pdf"),
                })
            if tool_result.get("figures"):
                figures.extend(tool_result["figures"])
            # Collect files from terminal sandbox (run_terminal_code)
            if tool_result.get("files"):
                files.extend(tool_result["files"])

            # Send figures to chat
            if bot and chat_id and tool_result.get("figures"):
                import base64 as _b64
                if tool_name == "generate_qr_code":
                    fig_caption = "QR code generated by S.T.E.W"
                elif tool_name == "generate_image":
                    fig_caption = "Image generated by S.T.E.W"
                else:
                    fig_caption = "Chart generated by S.T.E.W"
                for fig in tool_result["figures"]:
                    try:
                        fig_bytes = _b64.b64decode(fig["base64"])
                        await bot.send_photo(chat_id, fig_bytes, fig_caption)
                    except:
                        pass

            # Add the tool call + result to conversation
            # (append the RAW assistant content — with its TOOL_CALL blocks —
            # so the model sees its own tool invocations in context)
            tool_output = tool_result.get("output", tool_result.get("error", "No output"))
            # Head+tail truncation: keep the instructions at the front AND the
            # tail of long payloads (where provider guidance often lives). A
            # flat [:5000] once hid a 15k-char result's entire next-step hint.
            if len(tool_output) > 9000:
                tool_output = tool_output[:6000] + "\n...[truncated middle]...\n" + tool_output[-2500:]
            messages.append({"role": "assistant", "content": raw_content})
            messages.append({
                "role": "user",
                "content": f"TOOL_RESULT for {tool_name}:\n{tool_output}\n\n"
                           f"Analyze this result. If the user's request is NOT yet fully "
                           f"completed, continue with the next TOOL_CALL. Only give a "
                           f"final answer once the request is genuinely satisfied — and "
                           f"never claim work was done unless a TOOL_RESULT confirms it."
            })

    # Max iterations reached — get final response
    messages.append({
        "role": "user",
        "content": "You have used all your tool calls. Please provide your final answer now."
    })
    result = await asyncio.to_thread(llm.chat, messages)
    final_text = clean_response(result["content"])
    if not final_text and tool_history:
        final_text = _summarize_tool_history(tool_history)

    return await _finish_agent(final_text, tool_history, files, figures)
