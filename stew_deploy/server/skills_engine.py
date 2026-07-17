"""
S.T.E.W Skills Engine — 60+ autonomous skills.
Each skill is a self-contained async function.
Skills are discovered automatically and callable by name.
"""
import asyncio
import base64
import hashlib
import json
import logging
import math
import os
import re
import time
import uuid
from datetime import datetime, timedelta
from typing import Any, Optional
from urllib.parse import quote_plus, urlparse

import httpx

logger = logging.getLogger(__name__)

# ─── Registry ────────────────────────────────────────────────────────────────

_SKILLS: dict[str, dict] = {}


def skill(name: str, description: str, category: str = "general"):
    """Decorator to register a skill."""
    def decorator(fn):
        _SKILLS[name] = {
            "fn": fn,
            "name": name,
            "description": description,
            "category": category,
        }
        return fn
    return decorator


async def run_skill(name: str, **kwargs) -> Any:
    """Execute a skill by name."""
    if name not in _SKILLS:
        return {"error": f"Skill '{name}' not found. Available: {list(_SKILLS.keys())}"}
    try:
        return await _SKILLS[name]["fn"](**kwargs)
    except Exception as e:
        logger.error(f"Skill '{name}' error: {e}")
        return {"error": str(e), "skill": name}


def list_skills(category: Optional[str] = None) -> list[dict]:
    skills = [{"name": s["name"], "description": s["description"], "category": s["category"]}
              for s in _SKILLS.values()]
    if category:
        skills = [s for s in skills if s["category"] == category]
    return sorted(skills, key=lambda x: x["name"])


# ─── WEB & SEARCH ─────────────────────────────────────────────────────────────

@skill("web_search", "Search the web using Serper API with DuckDuckGo fallback", "web")
async def web_search(query: str, num: int = 5) -> dict:
    from server.search import get_searcher
    s = get_searcher()
    return await asyncio.to_thread(s.search, query, num)


@skill("web_browse", "Fetch and read any webpage URL", "web")
async def web_browse(url: str, question: str = "") -> dict:
    from server.browser import StewBrowser
    b = StewBrowser()
    page = await b.fetch(url)
    if question and "content" in page:
        page["answer_hint"] = f"Question: {question}\nContent excerpt: {page['content'][:2000]}"
    return page


@skill("duckduckgo_search", "Search DuckDuckGo without API key (fallback)", "web")
async def duckduckgo_search(query: str) -> dict:
    from server.browser import StewBrowser
    b = StewBrowser()
    return await b.search_web_fallback(query)


@skill("fill_form", "Fill and submit a web form automatically", "web")
async def fill_form(url: str, form_data: dict, method: str = "POST") -> dict:
    from server.browser import StewBrowser
    b = StewBrowser()
    return await b.submit_form(url, form_data, method)


@skill("fetch_json", "Fetch JSON from any API endpoint", "web")
async def fetch_json(url: str, headers: dict = None, params: dict = None) -> dict:
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(url, headers=headers or {}, params=params or {})
        return resp.json()


@skill("post_json", "POST JSON to any API endpoint", "web")
async def post_json(url: str, payload: dict, headers: dict = None) -> dict:
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(url, json=payload, headers=headers or {})
        return {"status": resp.status_code, "body": resp.json() if resp.headers.get("content-type","").startswith("application/json") else resp.text}


@skill("check_website_status", "Check if a website is online and measure response time", "web")
async def check_website_status(url: str) -> dict:
    start = time.time()
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            resp = await client.get(url)
            elapsed = round((time.time() - start) * 1000, 2)
            return {"url": url, "status": resp.status_code, "online": True, "response_ms": elapsed}
    except Exception as e:
        return {"url": url, "online": False, "error": str(e)}


@skill("get_page_links", "Extract all links from a webpage", "web")
async def get_page_links(url: str) -> dict:
    from server.browser import StewBrowser
    b = StewBrowser()
    page = await b.fetch(url)
    return {"url": url, "links": page.get("links", []), "count": len(page.get("links", []))}


@skill("get_page_forms", "Extract all forms from a webpage", "web")
async def get_page_forms(url: str) -> dict:
    from server.browser import StewBrowser
    b = StewBrowser()
    page = await b.fetch(url)
    return {"url": url, "forms": page.get("forms", [])}


# ─── DOCUMENT GENERATION ──────────────────────────────────────────────────────

@skill("generate_pdf", "Generate a PDF document", "documents")
async def generate_pdf(title: str, content: str, author: str = "S.T.E.W") -> dict:
    from server.document_generator import generate_pdf as _gen_pdf
    result = await asyncio.to_thread(_gen_pdf, content, title)
    return {"format": "pdf", **result, "filename": f"{title.lower().replace(' ','_')}.pdf"}


@skill("generate_docx", "Generate a Word document (.docx)", "documents")
async def generate_docx(title: str, content: str, author: str = "S.T.E.W") -> dict:
    from server.document_generator import generate_docx as _gen_docx
    result = await asyncio.to_thread(_gen_docx, content, title)
    return {"format": "docx", **result, "filename": f"{title.lower().replace(' ','_')}.docx"}


@skill("generate_xlsx", "Generate an Excel spreadsheet (.xlsx)", "documents")
async def generate_xlsx(title: str, data: list, headers: list = None) -> dict:
    from server.document_generator import generate_xlsx as _gen_xlsx
    result = await asyncio.to_thread(_gen_xlsx, data=data or [], headers=headers or [], title=title)
    return {"format": "xlsx", **result, "filename": f"{title.lower().replace(' ','_')}.xlsx"}


@skill("generate_pptx", "Generate a PowerPoint presentation (.pptx)", "documents")
async def generate_pptx(title: str, slides: list) -> dict:
    from server.document_generator import generate_pptx as _gen_pptx
    result = await asyncio.to_thread(_gen_pptx, slides, title)
    return {"format": "pptx", **result, "filename": f"{title.lower().replace(' ','_')}.pptx"}


@skill("generate_html_report", "Generate an HTML report", "documents")
async def generate_html_report(title: str, content: str, style: str = "professional") -> dict:
    from server.document_generator import generate_html as _gen_html
    result = await asyncio.to_thread(_gen_html, content, title)
    return {"format": "html", **result, "filename": f"{title.lower().replace(' ','_')}.html"}


@skill("generate_csv", "Generate a CSV file from data", "documents")
async def generate_csv(headers: list, rows: list, filename: str = "data") -> dict:
    import csv
    import io
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(headers)
    writer.writerows(rows)
    b64 = base64.b64encode(buf.getvalue().encode()).decode()
    return {"format": "csv", "base64": b64, "filename": f"{filename}.csv"}


@skill("generate_markdown", "Generate a Markdown document", "documents")
async def generate_markdown(title: str, sections: list) -> dict:
    lines = [f"# {title}\n"]
    for sec in sections:
        if isinstance(sec, dict):
            lines.append(f"## {sec.get('heading','')}\n")
            lines.append(sec.get("body","") + "\n")
        else:
            lines.append(str(sec) + "\n")
    md = "\n".join(lines)
    b64 = base64.b64encode(md.encode()).decode()
    return {"format": "md", "base64": b64, "filename": f"{title.lower().replace(' ','_')}.md", "text": md}


# ─── DATA & MATH ──────────────────────────────────────────────────────────────

@skill("calculate", "Evaluate a mathematical expression safely", "math")
async def calculate(expression: str) -> dict:
    allowed = set("0123456789+-*/().% eE")
    clean = expression.replace("^", "**").replace("×","*").replace("÷","/")
    if not all(c in allowed or c.isspace() for c in clean):
        # Allow math functions
        import math as _math
        safe_globals = {k: getattr(_math, k) for k in dir(_math) if not k.startswith("_")}
        safe_globals["__builtins__"] = {}
        result = eval(clean, safe_globals)
    else:
        result = eval(clean, {"__builtins__": {}})
    return {"expression": expression, "result": result}


@skill("unit_convert", "Convert between units (length, weight, temperature, etc.)", "math")
async def unit_convert(value: float, from_unit: str, to_unit: str) -> dict:
    conversions = {
        # length (to meters)
        "m": 1, "km": 1000, "cm": 0.01, "mm": 0.001,
        "ft": 0.3048, "in": 0.0254, "yd": 0.9144, "mi": 1609.344,
        # weight (to kg)
        "kg": 1, "g": 0.001, "lb": 0.453592, "oz": 0.0283495, "t": 1000,
        # time (to seconds)
        "s": 1, "min": 60, "h": 3600, "day": 86400, "week": 604800,
        # data (to bytes)
        "b": 1, "kb": 1024, "mb": 1048576, "gb": 1073741824,
    }
    fu = from_unit.lower()
    tu = to_unit.lower()
    # Temperature special case
    if fu in ("c","celsius") and tu in ("f","fahrenheit"):
        return {"result": value * 9/5 + 32, "from": f"{value}{from_unit}", "to": f"{value * 9/5 + 32}{to_unit}"}
    if fu in ("f","fahrenheit") and tu in ("c","celsius"):
        return {"result": (value - 32) * 5/9, "from": f"{value}{from_unit}", "to": f"{(value-32)*5/9}{to_unit}"}
    if fu in conversions and tu in conversions:
        base = value * conversions[fu]
        result = base / conversions[tu]
        return {"result": result, "from": f"{value} {from_unit}", "to": f"{result} {to_unit}"}
    return {"error": f"Unknown units: {from_unit} → {to_unit}"}


@skill("statistics", "Calculate statistics on a list of numbers", "math")
async def statistics(numbers: list) -> dict:
    if not numbers:
        return {"error": "Empty list"}
    n = len(numbers)
    mean = sum(numbers) / n
    sorted_n = sorted(numbers)
    median = sorted_n[n//2] if n % 2 else (sorted_n[n//2-1] + sorted_n[n//2]) / 2
    variance = sum((x - mean)**2 for x in numbers) / n
    return {
        "count": n, "sum": sum(numbers), "min": min(numbers),
        "max": max(numbers), "mean": round(mean, 4),
        "median": median, "std_dev": round(variance**0.5, 4),
        "range": max(numbers) - min(numbers),
    }


@skill("json_parse", "Parse and pretty-print JSON data", "data")
async def json_parse(data: str) -> dict:
    parsed = json.loads(data)
    return {"parsed": parsed, "pretty": json.dumps(parsed, indent=2), "type": type(parsed).__name__}


@skill("json_to_table", "Convert JSON array to a formatted table string", "data")
async def json_to_table(data: list) -> dict:
    if not data or not isinstance(data[0], dict):
        return {"error": "Input must be a list of objects"}
    headers = list(data[0].keys())
    rows = [[str(row.get(h, "")) for h in headers] for row in data]
    widths = [max(len(h), max((len(r[i]) for r in rows), default=0)) for i, h in enumerate(headers)]
    header_line = " | ".join(h.ljust(widths[i]) for i, h in enumerate(headers))
    separator = "-+-".join("-" * w for w in widths)
    data_lines = [" | ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)) for row in rows]
    return {"table": "\n".join([header_line, separator] + data_lines), "rows": len(data)}


@skill("csv_parse", "Parse CSV text into structured data", "data")
async def csv_parse(csv_text: str) -> dict:
    import csv, io
    reader = csv.DictReader(io.StringIO(csv_text))
    rows = list(reader)
    return {"headers": reader.fieldnames, "rows": rows, "count": len(rows)}


@skill("base64_encode", "Encode text to Base64", "data")
async def base64_encode(text: str) -> dict:
    encoded = base64.b64encode(text.encode()).decode()
    return {"input": text, "encoded": encoded}


@skill("base64_decode", "Decode Base64 to text", "data")
async def base64_decode(encoded: str) -> dict:
    decoded = base64.b64decode(encoded.encode()).decode()
    return {"encoded": encoded, "decoded": decoded}


@skill("hash_text", "Generate MD5/SHA256 hash of text", "data")
async def hash_text(text: str, algorithm: str = "sha256") -> dict:
    h = hashlib.new(algorithm)
    h.update(text.encode())
    return {"text": text[:50], "algorithm": algorithm, "hash": h.hexdigest()}


@skill("generate_uuid", "Generate a random UUID", "data")
async def generate_uuid(count: int = 1) -> dict:
    ids = [str(uuid.uuid4()) for _ in range(min(count, 20))]
    return {"uuids": ids, "count": len(ids)}


# ─── TEXT & LANGUAGE ──────────────────────────────────────────────────────────

@skill("word_count", "Count words, chars, sentences in text", "text")
async def word_count(text: str) -> dict:
    words = text.split()
    sentences = re.split(r'[.!?]+', text)
    paragraphs = [p for p in text.split('\n\n') if p.strip()]
    return {
        "characters": len(text),
        "characters_no_spaces": len(text.replace(" ", "")),
        "words": len(words),
        "sentences": len([s for s in sentences if s.strip()]),
        "paragraphs": len(paragraphs),
        "reading_time_minutes": round(len(words) / 200, 1),
    }


@skill("text_summarize", "Summarize a long text into key points (LLM-powered)", "text")
async def text_summarize(text: str, max_points: int = 5) -> dict:
    # Returns structured data; LLM in main.py will do the actual summarization
    return {
        "action": "summarize",
        "text_length": len(text),
        "text_preview": text[:500],
        "max_points": max_points,
        "instruction": f"Summarize the following text into {max_points} key bullet points:\n\n{text[:4000]}"
    }


@skill("text_extract_emails", "Extract all email addresses from text", "text")
async def text_extract_emails(text: str) -> dict:
    pattern = r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'
    emails = list(set(re.findall(pattern, text)))
    return {"emails": emails, "count": len(emails)}


@skill("text_extract_urls", "Extract all URLs from text", "text")
async def text_extract_urls(text: str) -> dict:
    pattern = r'https?://[^\s<>"{}|\\^`\[\]]+'
    urls = list(set(re.findall(pattern, text)))
    return {"urls": urls, "count": len(urls)}


@skill("text_extract_phones", "Extract phone numbers from text", "text")
async def text_extract_phones(text: str) -> dict:
    pattern = r'(?:\+?234|0)?\s*[789][01]\d{8}'
    phones = list(set(re.findall(pattern, text.replace(" ", ""))))
    return {"phones": phones, "count": len(phones)}


@skill("text_translate_detect", "Detect language of text", "text")
async def text_translate_detect(text: str) -> dict:
    # Basic heuristic detection
    common_langs = {
        "en": ["the","is","and","of","to","in","that","it","was"],
        "fr": ["le","la","les","de","du","et","en","est","que"],
        "es": ["el","la","los","de","y","en","que","es","se"],
        "de": ["der","die","das","und","ist","in","von","zu"],
        "yo": ["ni","mo","ti","se","fun","pe","ati","bi"],
        "ig": ["na","ya","ha","nke","ka","ndi","ọ","a"],
    }
    words = text.lower().split()
    scores = {}
    for lang, keywords in common_langs.items():
        scores[lang] = sum(1 for w in words if w in keywords)
    best = max(scores, key=scores.get)
    return {"detected_language": best, "confidence_scores": scores, "text_preview": text[:100]}


@skill("text_sentiment", "Analyze sentiment of text (positive/negative/neutral)", "text")
async def text_sentiment(text: str) -> dict:
    positive_words = {"good","great","excellent","amazing","wonderful","fantastic","love","happy","best","perfect","awesome","brilliant","outstanding","superb"}
    negative_words = {"bad","terrible","awful","horrible","hate","worst","poor","disappointing","frustrating","useless","broken","failed","wrong","error"}
    words = set(text.lower().split())
    pos = len(words & positive_words)
    neg = len(words & negative_words)
    if pos > neg:
        sentiment = "positive"
    elif neg > pos:
        sentiment = "negative"
    else:
        sentiment = "neutral"
    score = (pos - neg) / max(len(words), 1)
    return {"sentiment": sentiment, "score": round(score, 3), "positive_hits": pos, "negative_hits": neg}


@skill("text_clean", "Clean and normalize text (remove extra spaces, fix encoding)", "text")
async def text_clean(text: str) -> dict:
    cleaned = re.sub(r'\s+', ' ', text).strip()
    cleaned = re.sub(r'[^\x00-\x7F]+', ' ', cleaned)
    cleaned = re.sub(r' +', ' ', cleaned)
    return {"original_length": len(text), "cleaned": cleaned, "cleaned_length": len(cleaned)}


@skill("generate_password", "Generate a strong random password", "security")
async def generate_password(length: int = 16, include_symbols: bool = True) -> dict:
    import random, string
    chars = string.ascii_letters + string.digits
    if include_symbols:
        chars += "!@#$%^&*()-_=+"
    pwd = ''.join(random.choice(chars) for _ in range(min(length, 64)))
    return {"password": pwd, "length": len(pwd), "strength": "strong" if len(pwd) >= 12 else "medium"}


# ─── DATE & TIME ──────────────────────────────────────────────────────────────

@skill("get_current_time", "Get current date and time in multiple timezones", "datetime")
async def get_current_time(timezone: str = "UTC") -> dict:
    now_utc = datetime.utcnow()
    offsets = {
        "UTC": 0, "WAT": 1, "CET": 1, "EET": 2,
        "IST": 5.5, "CST": 8, "JST": 9, "AEST": 10,
        "EST": -5, "CST_US": -6, "MST": -7, "PST": -8,
    }
    tz_upper = timezone.upper()
    offset = offsets.get(tz_upper, 0)
    local = now_utc + timedelta(hours=offset)
    return {
        "utc": now_utc.strftime("%Y-%m-%d %H:%M:%S UTC"),
        "requested_tz": timezone,
        "local": local.strftime("%Y-%m-%d %H:%M:%S"),
        "date": local.strftime("%A, %B %d, %Y"),
        "timestamp": int(time.time()),
    }


@skill("date_diff", "Calculate the difference between two dates", "datetime")
async def date_diff(date1: str, date2: str) -> dict:
    d1 = datetime.fromisoformat(date1)
    d2 = datetime.fromisoformat(date2)
    diff = abs(d2 - d1)
    return {
        "days": diff.days,
        "weeks": diff.days // 7,
        "months": diff.days // 30,
        "years": diff.days // 365,
        "from": date1,
        "to": date2,
    }


@skill("add_days", "Add or subtract days from a date", "datetime")
async def add_days(date: str, days: int) -> dict:
    d = datetime.fromisoformat(date)
    result = d + timedelta(days=days)
    return {"original": date, "days_added": days, "result": result.strftime("%Y-%m-%d"), "day_name": result.strftime("%A")}


# ─── FINANCE & CURRENCY ───────────────────────────────────────────────────────

@skill("currency_rates", "Get live currency exchange rates", "finance")
async def currency_rates(base: str = "USD") -> dict:
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"https://open.er-api.com/v6/latest/{base.upper()}")
            data = resp.json()
            rates = data.get("rates", {})
            key_currencies = ["NGN","USD","EUR","GBP","KES","GHS","ZAR","JPY","CNY","INR"]
            filtered = {k: rates[k] for k in key_currencies if k in rates}
            return {"base": base.upper(), "rates": filtered, "time": data.get("time_last_update_utc","")}
    except Exception as e:
        return {"error": str(e), "note": "Exchange rate API unavailable"}


@skill("compound_interest", "Calculate compound interest", "finance")
async def compound_interest(principal: float, rate: float, years: int, n: int = 12) -> dict:
    r = rate / 100
    amount = principal * (1 + r/n) ** (n * years)
    interest = amount - principal
    return {
        "principal": principal, "rate_pct": rate, "years": years,
        "compounds_per_year": n, "final_amount": round(amount, 2),
        "interest_earned": round(interest, 2),
    }


@skill("loan_calculator", "Calculate monthly loan payments", "finance")
async def loan_calculator(principal: float, annual_rate: float, years: int) -> dict:
    r = annual_rate / 100 / 12
    n = years * 12
    if r == 0:
        payment = principal / n
    else:
        payment = principal * (r * (1+r)**n) / ((1+r)**n - 1)
    total = payment * n
    return {
        "principal": principal, "annual_rate_pct": annual_rate, "years": years,
        "monthly_payment": round(payment, 2),
        "total_payment": round(total, 2),
        "total_interest": round(total - principal, 2),
    }


# ─── AI & GENERATION ──────────────────────────────────────────────────────────

@skill("generate_email", "Generate a professional email draft", "ai")
async def generate_email(purpose: str, recipient: str, tone: str = "professional") -> dict:
    return {
        "action": "generate_email",
        "instruction": f"Write a {tone} email to {recipient} for the following purpose: {purpose}. Include subject line, greeting, body, and closing.",
        "purpose": purpose, "recipient": recipient, "tone": tone,
    }


@skill("generate_cv", "Generate a professional CV/Resume", "ai")
async def generate_cv(name: str, skills: list, experience: list, education: list) -> dict:
    return {
        "action": "generate_cv",
        "instruction": f"Generate a professional CV for {name}. Skills: {skills}. Experience: {experience}. Education: {education}. Format it properly with sections.",
        "name": name,
    }


@skill("generate_cover_letter", "Generate a job application cover letter", "ai")
async def generate_cover_letter(job_title: str, company: str, applicant_name: str, skills: str) -> dict:
    return {
        "action": "generate_cover_letter",
        "instruction": f"Write a compelling cover letter for {applicant_name} applying for {job_title} at {company}. Their key skills: {skills}",
    }


@skill("generate_business_plan", "Generate a business plan outline", "ai")
async def generate_business_plan(business_name: str, industry: str, target_market: str) -> dict:
    return {
        "action": "generate_business_plan",
        "instruction": f"Create a detailed business plan for '{business_name}' in the {industry} industry targeting {target_market}. Include executive summary, market analysis, strategy, and financial projections.",
    }


@skill("generate_social_post", "Generate social media posts", "ai")
async def generate_social_post(topic: str, platform: str = "Twitter", tone: str = "engaging") -> dict:
    return {
        "action": "generate_social_post",
        "instruction": f"Write a {tone} {platform} post about: {topic}. Follow {platform} best practices and character limits.",
        "topic": topic, "platform": platform,
    }


@skill("code_review", "Review code and suggest improvements", "code")
async def code_review(code: str, language: str = "python") -> dict:
    return {
        "action": "code_review",
        "instruction": f"Review this {language} code. Check for: bugs, security issues, performance, style. Suggest improvements:\n\n```{language}\n{code}\n```",
        "language": language,
    }


@skill("code_explain", "Explain what a piece of code does", "code")
async def code_explain(code: str, language: str = "python") -> dict:
    return {
        "action": "code_explain",
        "instruction": f"Explain this {language} code in simple terms. Describe what each part does:\n\n```{language}\n{code}\n```",
    }


@skill("code_debug", "Find and fix bugs in code", "code")
async def code_debug(code: str, error: str, language: str = "python") -> dict:
    return {
        "action": "code_debug",
        "instruction": f"Debug this {language} code. Error: {error}\n\nCode:\n```{language}\n{code}\n```\nFind the bug and provide the fixed version.",
    }


@skill("code_convert", "Convert code from one language to another", "code")
async def code_convert(code: str, from_lang: str, to_lang: str) -> dict:
    return {
        "action": "code_convert",
        "instruction": f"Convert this {from_lang} code to {to_lang}, maintaining all logic and functionality:\n\n```{from_lang}\n{code}\n```",
    }


# ─── SYSTEM & UTILITY ─────────────────────────────────────────────────────────

@skill("system_info", "Get S.T.E.W system info and status", "system")
async def system_info() -> dict:
    import platform
    return {
        "agent": "S.T.E.W 3.0 ULTRA",
        "version": "5.0.0",
        "platform": platform.system(),
        "python": platform.python_version(),
        "skills_loaded": len(_SKILLS),
        "timestamp": datetime.utcnow().isoformat(),
        "status": "operational",
    }


@skill("ping", "Ping a host and check connectivity", "system")
async def ping(host: str) -> dict:
    return await check_website_status(f"https://{host}" if not host.startswith("http") else host)


@skill("random_number", "Generate random numbers", "utility")
async def random_number(min_val: int = 1, max_val: int = 100, count: int = 1) -> dict:
    import random
    nums = [random.randint(min_val, max_val) for _ in range(min(count, 100))]
    return {"numbers": nums, "count": len(nums), "min": min_val, "max": max_val}


@skill("qr_code_url", "Generate a QR code URL for any text", "utility")
async def qr_code_url(text: str, size: int = 200) -> dict:
    encoded = quote_plus(text)
    url = f"https://api.qrserver.com/v1/create-qr-code/?size={size}x{size}&data={encoded}"
    return {"qr_image_url": url, "text": text, "size": f"{size}x{size}"}


@skill("shorten_url", "Shorten a URL using TinyURL", "utility")
async def shorten_url(url: str) -> dict:
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"https://tinyurl.com/api-create.php?url={quote_plus(url)}")
            return {"original": url, "shortened": resp.text.strip(), "provider": "tinyurl"}
    except Exception as e:
        return {"error": str(e), "original": url}


@skill("ip_info", "Get information about an IP address", "network")
async def ip_info(ip: str = "") -> dict:
    try:
        url = f"https://ipapi.co/{ip}/json/" if ip else "https://ipapi.co/json/"
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url)
            data = resp.json()
            return {
                "ip": data.get("ip"), "city": data.get("city"),
                "country": data.get("country_name"), "region": data.get("region"),
                "timezone": data.get("timezone"), "org": data.get("org"),
                "latitude": data.get("latitude"), "longitude": data.get("longitude"),
            }
    except Exception as e:
        return {"error": str(e)}


@skill("weather", "Get current weather for a city", "utility")
async def weather(city: str) -> dict:
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            # Use wttr.in — no API key needed
            resp = await client.get(
                f"https://wttr.in/{quote_plus(city)}?format=j1",
                headers={"Accept": "application/json"}
            )
            data = resp.json()
            current = data["current_condition"][0]
            return {
                "city": city,
                "temp_c": current["temp_C"],
                "temp_f": current["temp_F"],
                "description": current["weatherDesc"][0]["value"],
                "humidity": current["humidity"],
                "wind_kmph": current["windspeedKmph"],
                "feels_like_c": current["FeelsLikeC"],
            }
    except Exception as e:
        return {"error": str(e), "city": city, "note": "Weather service unavailable"}


@skill("timezone_convert", "Convert time between timezones", "datetime")
async def timezone_convert(time_str: str, from_tz: str, to_tz: str) -> dict:
    offsets = {
        "WAT": 1, "UTC": 0, "GMT": 0, "CET": 1, "EET": 2,
        "IST": 5.5, "PKT": 5, "BST_BD": 6, "CST": 8, "JST": 9,
        "AEST": 10, "EST": -5, "EDT": -4, "CST_US": -6, "MST": -7, "PST": -8, "PDT": -7,
    }
    try:
        t = datetime.strptime(time_str, "%H:%M")
        from_offset = offsets.get(from_tz.upper(), 0)
        to_offset = offsets.get(to_tz.upper(), 0)
        diff = to_offset - from_offset
        result = t + timedelta(hours=diff)
        return {
            "input": time_str, "from_tz": from_tz, "to_tz": to_tz,
            "result": result.strftime("%H:%M"), "offset_diff_hours": diff,
        }
    except Exception as e:
        return {"error": str(e)}


@skill("list_skills", "List all available S.T.E.W skills", "system")
async def list_all_skills(category: str = "") -> dict:
    skills = list_skills(category if category else None)
    categories = list(set(s["category"] for s in skills))
    return {
        "total": len(skills),
        "categories": sorted(categories),
        "skills": skills,
    }

