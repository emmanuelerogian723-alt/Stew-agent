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
"""
import json
import re
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
11. Never say you can't do something — try the tool first.
11b. NEVER output Python code as your response. NEVER tell the user to "pip install" anything. NEVER tell the user to "run this script" or "decode base64". YOU are the agent — YOU run the code, YOU generate the file, and the user gets a downloadable file. If you find yourself writing code as instructions, STOP and use generate_document instead.
12. Be concise in explanations. Show your work when using tools.
13. End with a clear final answer after tool use.
14. After a document is generated and you receive the TOOL_RESULT confirming success, tell the user the file is ready and they can download it. Do NOT repeat the TOOL_CALL.
15. NEVER call web_search more than ONCE per conversation. If the first search returns no results or fails, answer based on your own knowledge instead of searching again.
16. NEVER call browse_url more than ONCE per conversation.
17. For open-ended, multi-step or research-heavy goals, break the goal into smaller steps and chain multiple DIFFERENT tools in sequence (e.g. web_search to find facts, then run_python_code to compute something, then generate_document to produce a deliverable). Think like an autonomous agent completing a real task end-to-end, not a one-shot Q&A bot.
18. For unknown facts, historical/biographical info, or general knowledge lookups — prefer wikipedia_search over web_search (faster, more reliable for encyclopedic facts). Use web_search only for time-sensitive or very recent info.

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


async def execute_tool(call: dict, bot=None, chat_id=None) -> dict:
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
        url = args.get("url", "")
        if not url:
            return {"error": "No URL provided"}
        from server.browser import StewBrowser
        browser = StewBrowser()
        result = await browser.fetch(url)
        content = result.get("content", "")[:8000]
        title = result.get("title", "Unknown")
        return {
            "tool": tool,
            "success": bool(content),
            "output": f"Title: {title}\nURL: {url}\n\n{content}",
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

    # ── TERMINAL SANDBOX TOOLS (owner/admin only) ──────────────────────────────
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


async def run_agent_loop(
    user_text: str,
    bot=None,
    chat_id: int = None,
    max_iterations: int = 5,
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
    messages = [
        {"role": "system", "content": TOOL_SYSTEM_PROMPT},
        {"role": "user", "content": user_text},
    ]

    files = []
    figures = []
    tool_history = []
    tools_used = set()  # Track tools already called to prevent loops

    for iteration in range(max_iterations):
        # Get LLM response
        result = await asyncio.to_thread(llm.chat, messages)
        assistant_text = clean_response(result["content"])

        # Check for tool calls
        tool_calls = extract_tool_calls(assistant_text)

        if not tool_calls:
            # No more tool calls — this is the final answer
            return {
                "response": assistant_text,
                "files": files,
                "figures": figures,
                "tool_calls": tool_history,
            }

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
            new_calls.append(call)
            tools_used.add(tool_name)

        if not new_calls:
            # All tool calls were duplicates — force final answer
            messages.append({"role": "assistant", "content": assistant_text})
            messages.append({
                "role": "user",
                "content": "You have already used all available tools. Please provide your final answer now based on the information you have. Do NOT make any more TOOL_CALL."
            })
            continue

        tool_calls = new_calls

        # Execute each tool call
        for call in tool_calls:
            tool_name = call.get("tool", "unknown")
            if bot and chat_id:
                short = f"Using tool: {tool_name}..."
                await bot.send_message(chat_id, short)

            tool_result = await execute_tool(call, bot, chat_id)
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
                fig_caption = "QR code generated by S.T.E.W" if tool_name == "generate_qr_code" else "Chart generated by S.T.E.W"
                for fig in tool_result["figures"]:
                    try:
                        fig_bytes = _b64.b64decode(fig["base64"])
                        await bot.send_photo(chat_id, fig_bytes, fig_caption)
                    except:
                        pass

            # Add the tool call + result to conversation
            tool_output = tool_result.get("output", tool_result.get("error", "No output"))
            messages.append({"role": "assistant", "content": assistant_text})
            messages.append({
                "role": "user",
                "content": f"TOOL_RESULT for {tool_name}:\n{tool_output[:5000]}\n\n"
                           f"Analyze this result and continue. If you have enough information, "
                           f"provide your final answer (no more TOOL_CALL)."
            })

    # Max iterations reached — get final response
    messages.append({
        "role": "user",
        "content": "You have used all your tool calls. Please provide your final answer now."
    })
    result = await asyncio.to_thread(llm.chat, messages)
    final_text = clean_response(result["content"])

    return {
        "response": final_text,
        "files": files,
        "figures": figures,
        "tool_calls": tool_history,
    }
