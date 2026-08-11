# S.T.E.W Agent 🥘

**Africa's #1 AI Agent API — Build AI-Powered Apps in Minutes**

[![Status](https://img.shields.io/badge/status-operational-brightgreen)](https://stew-agent.onrender.com/heartbeat)
[![Version](https://img.shields.io/badge/version-6.0.0-blue)](https://stew-agent.onrender.com)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Built in Nigeria](https://img.shields.io/badge/built%20in-Nigeria-%23009739)](https://stew-agent.onrender.com)

> **S.T.E.W** = **S**mart **T**ask **E**xecution **W**orker

One API. Every skill your app needs. From live web research to binary document generation, code execution, OCR, and a 100-agent swarm — S.T.E.W handles the complete AI automation stack. Built by Africans, for Africans. Naira billing. No dollar card required.

🌐 **Live API**: https://stew-agent.onrender.com
📚 **API Docs**: https://stew-agent.onrender.com/docs
🧪 **Playground**: https://stew-agent.onrender.com/playground
💬 **Telegram Bot**: Available with Business plan

---

## Table of Contents

- [Overview](#overview)
- [Key Features](#key-features)
- [AI Providers](#ai-providers)
- [100-Agent Swarm](#100-agent-swarm)
- [59 Skills](#59-skills)
- [12 Domain Personas](#12-domain-personas)
- [Pricing](#pricing)
- [Quick Start](#quick-start)
- [API Reference](#api-reference)
- [Code Execution Sandbox](#code-execution-sandbox)
- [OCR / Vision](#ocr--vision)
- [Telegram Bot](#telegram-bot)
- [Security](#security)
- [Architecture](#architecture)
- [Deployment](#deployment)
- [Roadmap](#roadmap)
- [Built By](#built-by)

---

## Overview

S.T.E.W is a production-grade AI agent API that gives developers a single endpoint for 59+ skills, 12 domain-specific personas, 6 AI model providers with automatic failover, real-time web search, binary document generation (PDF/DOCX/XLSX/PPTX), a restricted Python code execution sandbox, OCR with Tesseract (17+ languages), and a 100-agent swarm for complex multi-step orchestration.

It is **100% Nigerian-built** and designed for African developers first:
- Naira billing via Paystack (no dollar card needed)
- Nigerian Pidgin, Yoruba, Igbo, Hausa language support
- Free tier with 1,500 API calls/month — no credit card
- 12 personas fine-tuned for African use cases

---

## Key Features

| Feature | Description |
|---|---|
| 🌐 **Real-time Web Research** | Grounds responses in live web data with automatic source citations |
| 💬 **Multi-Provider Chat** | 6 AI providers with automatic failover — never goes down |
| 📄 **Binary Document Generation** | Real PDF, DOCX, XLSX, PPTX files — download-ready, not just text |
| 🤖 **100-Agent Swarm** | Spawn up to 100 specialized agents in parallel for complex tasks |
| ⚙️ **Fine-Tune Personas** | 12 domain personas (Medical, Legal, Finance, Startup, etc.) |
| 💻 **Code Execution** | Restricted Python sandbox with numpy, pandas, matplotlib |
| 🔍 **OCR / Vision** | Tesseract OCR in 17+ languages, 95%+ accuracy |
| 📨 **Telegram Bot** | Full Telegram integration with AI-powered responses |
| 🇳🇬 **Naira Billing** | Paystack-powered subscriptions, no dollar card required |
| 🔐 **Enterprise Security** | Device fingerprinting, rate limiting, IP blocking, audit logs |
| 🗃️ **Conversation Memory** | Persistent conversation context per API key |
| 🌐 **Browser Extension** | Chrome extension for web automation and research |
| 🔑 **Bring Your Own Key** | Pro users can plug in their own Mistral AI key |

---

## AI Providers

S.T.E.W uses 6 world-class AI providers with automatic failover. If one provider goes down, the next picks up instantly.

| Provider | Primary Model | Role |
|---|---|---|
| **Groq** | `openai/gpt-oss-120b` | Primary — ultra-fast inference |
| **Mistral AI** | `mistral-large-latest` | Secondary — European, BYOK supported |
| **NVIDIA NIM** | `meta/llama-3.3-70b-instruct` | Tertiary — free tier, reliable |
| **OpenRouter** | `meta-llama/llama-3.3-70b-instruct:free` | Quaternary — multi-model routing |
| **HuggingFace** | `Qwen/Qwen3-235B-A22B` | Fifth — free fallback |
| **OpenAI** | `gpt-4o-mini` | Emergency fallback |

**Fallback chain:** Groq → Mistral → NVIDIA NIM → OpenRouter → HuggingFace → OpenAI

---

## 100-Agent Swarm

S.T.E.W spawns 100 specialized AI agents at startup, each with a unique name, specialty, and skill set. The `/agents/run` endpoint dispatches tasks to the best-matching agents in parallel.

### Agent Categories

| Category | Agents |
|---|---|
| **Research** | Atlas (Web Research), Sage (Academic), Scout (News), Lens (Image), Vox (Social Media), Trace (Data Mining), Oracle (Market Research), Pulse (Trend Analysis), Cipher (Deep Research), Nexus (Knowledge Synthesis) |
| **Engineering** | Bolt (Python), Spark (JavaScript), Pixel (Frontend), Ghost (Mobile), Forge (Backend), Core (Systems), Debug (Code Review), Stack (DevOps), Matrix (Database), Weave (API Specialist) |
| **AI / ML** | Synapse (NLP), Vision (Computer Vision), Echo (Speech AI), Genome (Data Science), Titan (LLM), Qubit (AI Optimizer), Prism (Multimodal), Neural (AI Model Builder), Helix (AI Researcher), Epoch (Training Specialist) |
| **Building** | Craft (Website Builder), Arch (App Architect), Blade (Full Stack), Flux (Dashboard Builder), Presto (Rapid Prototyper), Frame (UI Designer), Nova (Chrome Extension), Titan2 (Enterprise), Circuit (IoT), Hive (API Builder) |
| **Content** | Quill (Writer), Verse (Creative Writer), Brief (Business Writer), Reel (Video Scriptwriter), Brand (Brand Voice), Press (PR Writer), Social (Social Media Manager), Pitch (Sales Copy), Teach (Educational Content), Lingo (Translator) |
| **Business** | Fund (Finance), Deal (Business Analyst), Growth (Growth Hacker), Pitch2 (Investor Relations), CX (Customer Success), Supply (Operations), HR (HR Agent), Legal (Legal Assistant), SEO (SEO Specialist), Ads (Advertising) |
| **Design** | Canvas (Graphic Designer), Motion (Animation), Cut (Video Editor), Snap (Photography), Sonic (Music AI), Space (3D Designer), Color (Color Theory), Font (Typography), Ink (Document Designer), Story (Storyboard) |
| **Automation** | Flow (Workflow Automator), Hook (Webhook Handler), Cron (Scheduler), Bot (Browser Automator), Pipe (Data Pipeline), Watch (Monitor), Sync (Data Sync), Parse (Data Parser), Relay (Integration), Loop (Recursive Agent) |
| **Security** | Guard (Security Analyst), Vault (Encryption), Shield (Privacy), Test (QA Engineer), Speed (Performance), Backup (Backup Agent), Log (Logging Agent), Watch (Monitor), Net (Network Agent), Cloud (Cloud Agent) |
| **Specialized** | Africa (Africa Specialist), Future (Futures Analyst), Green (Sustainability), Edu (Education), Bio (Biotech), Geo (Geospatial), Finance2 (Crypto & Fintech), Meta (Meta-Agent Coordinator), God (Master Orchestrator), Time (Time Intelligence) |

---

## 59 Skills

All skills are available via `GET /skills` and executable via `POST /skills/run`.

| Category | Skills |
|---|---|
| **AI** | generate_business_plan, generate_cover_letter, generate_cv, generate_email, generate_social_post |
| **Code** | code_convert, code_debug, code_explain, code_review |
| **Data** | base64_decode, base64_encode, csv_parse, generate_uuid, hash_text, json_parse, json_to_table |
| **DateTime** | add_days, date_diff, get_current_time, timezone_convert |
| **Documents** | generate_csv, generate_docx, generate_html_report, generate_markdown, generate_pdf, generate_pptx, generate_xlsx |
| **Finance** | compound_interest, currency_rates, loan_calculator |
| **Math** | calculate, statistics, unit_convert |
| **Network** | ip_info |
| **Security** | generate_password |
| **System** | list_skills, ping, system_info |
| **Text** | text_clean, text_extract_emails, text_extract_phones, text_extract_urls, text_sentiment, text_summarize, text_translate_detect, word_count |
| **Utility** | qr_code_url, random_number, shorten_url, weather |
| **Web** | check_website_status, duckduckgo_search, fetch_json, fill_form, get_page_forms, get_page_links, post_json, web_browse, web_search |

---

## 12 Domain Personas

Fine-tune your API key for any industry. Persona settings are saved server-side and applied to every request automatically.

| Persona | Key | Description |
|---|---|---|
| 🤖 General Assistant | `general` | Powerful autonomous AI agent for any task |
| 🩺 Medical Doctor | `doctor` | Evidence-based medical information and clinical documentation |
| 💚 Health & Wellness | `health` | Nutrition, fitness, mental wellness, preventive care |
| 🚀 Startup Co-founder | `startup` | Business strategy, fundraising, YC-mentor style advice |
| ⚖️ Legal Assistant | `legal` | Contract drafting, legal document analysis, regulations |
| 📈 Finance Advisor | `finance` | Financial modeling, investment analysis, budgeting |
| 🎓 AI Tutor | `education` | Explain complex topics, learning plans, quizzes |
| 🛒 E-Commerce Expert | `ecommerce` | Product listings, customer support, pricing strategy |
| 💻 Software Engineer | `developer` | Production-quality code, debugging, system architecture |
| 📣 Growth Marketer | `marketing` | Copywriting, SEO, social media strategy, conversion |
| 👥 HR & People Ops | `hr` | Job descriptions, interview questions, performance reviews |
| 💬 Customer Support | `customer_support` | Empathetic customer query resolution and escalation |

**Response styles:** `concise`, `balanced` (default), `detailed`
**Languages:** English, Nigerian Pidgin, Yoruba, Igbo, Hausa, French

---

## Pricing

Naira billing via Paystack. No dollar card required.

| Plan | Price | API Calls/month | Key Features |
|---|---|---|---|
| **Free** | ₦0 forever | 1,500 | All 59 skills, web research, document generation, fine-tune personas, custom Mistral key |
| **Pro** | ₦9,900/month (~$6) | 10,000 | All skills + priority routing, conversation memory, 100-agent swarm |
| **Business** | ₦29,000/month (~$18) | 100,000 | 100-agent swarm, Telegram bot setup, all 12 personas, custom system prompts |
| **Enterprise** | ₦49,000+/month | Unlimited | Dedicated AI instances, white-label option, SLA guarantee, on-premise deployment |

---

## Quick Start

### 1. Get Your Free API Key

```bash
# Register via API
curl -X POST https://stew-agent.onrender.com/auth/register \
  -H "Content-Type: application/json" \
  -d '{"full_name":"Your Name","email":"you@example.com","password":"yourpassword","plan":"free"}'
```

Or visit https://stew-agent.onrender.com and click **Get Free Key**.

### 2. Send Your First Chat Request

```bash
curl -X POST https://stew-agent.onrender.com/chat \
  -H "Content-Type: application/json" \
  -d '{
    "message": "What are the top 5 Nigerian fintechs in 2026?",
    "api_key": "stew_your_key_here",
    "web_search": true
  }'
```

### 3. Run a Skill

```bash
curl -X POST https://stew-agent.onrender.com/skills/run \
  -H "Content-Type: application/json" \
  -d '{
    "skill": "generate_cv",
    "params": {"name": "Emmanuel Erog", "role": "AI Engineer"},
    "api_key": "stew_your_key_here"
  }'
```

### 4. Generate a PDF Document

```bash
curl -X POST https://stew-agent.onrender.com/generate/pdf \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Quarterly Report",
    "content": "This is a generated PDF document from S.T.E.W Agent API.",
    "api_key": "stew_your_key_here"
  }'
```

### 5. Spawn the 100-Agent Swarm

```bash
curl -X POST https://stew-agent.onrender.com/agents/run \
  -H "Content-Type: application/json" \
  -d '{
    "task": "Research the top 10 AI startups in Africa and create a summary report",
    "api_key": "stew_your_key_here",
    "num_agents": 5,
    "synthesize": true
  }'
```

---

## API Reference

Full Swagger documentation available at https://stew-agent.onrender.com/docs

### Core Endpoints

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/chat` | AI chat with optional web search grounding |
| `POST` | `/search` | Web search with source citations |
| `POST` | `/browse/navigate` | Browse and extract content from any URL |
| `POST` | `/skills/run` | Execute any of the 59 skills |
| `GET` | `/skills` | List all available skills |
| `POST` | `/agents/run` | Dispatch task to 100-agent swarm |
| `GET` | `/agents/status` | Check agent pool status |
| `GET` | `/heartbeat` | System health check |
| `POST` | `/task` | Submit a complex multi-step task |

### Document Generation

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/generate/pdf` | Generate PDF document |
| `POST` | `/generate/docx` | Generate Word document |
| `POST` | `/generate/xlsx` | Generate Excel spreadsheet |
| `POST` | `/generate/pptx` | Generate PowerPoint presentation |
| `POST` | `/generate/html` | Generate HTML report |

### AI and Image

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/generate/image` | Generate AI image |
| `POST` | `/orchestrate/text` | Multi-model text orchestration |
| `POST` | `/orchestrate/image` | Multi-model image orchestration |

### Code and OCR

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/api/code/exec` | Execute Python code in restricted sandbox |
| `GET` | `/api/code/info` | Get code sandbox info |
| `POST` | `/api/ocr` | OCR on uploaded image |
| `POST` | `/api/ocr/analyze` | Advanced image analysis |
| `GET` | `/api/ocr/languages` | List supported OCR languages |
| `GET` | `/api/ocr/info` | Get OCR engine info |

### Authentication and User Management

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/auth/register` | Register and get free API key |
| `POST` | `/auth/login` | Login with email/password |
| `POST` | `/auth/firebase` | Firebase authentication |
| `GET` | `/auth/me` | Get current user info |
| `GET` | `/auth/usage` | Check API usage and quota |
| `POST` | `/auth/regenerate-key` | Regenerate API key |
| `POST` | `/auth/generate-key` | Generate new API key |

### Fine-Tuning

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/finetune` | Set persona and custom instructions |
| `GET` | `/finetune/{api_key}` | Get current fine-tune settings |
| `GET` | `/personas` | List all available personas |

### Payments

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/payments/initialize` | Initialize Paystack payment |
| `POST` | `/payments/verify` | Verify payment |
| `GET` | `/payments/status/{reference}` | Check payment status |
| `POST` | `/payments/webhook` | Paystack webhook handler |

### Memory and Integrations

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/memory/search` | Search conversation memory |
| `GET` | `/memory/{user_id}` | Get user memory |
| `DELETE` | `/memory/{user_id}` | Clear user memory |
| `POST` | `/api/call` | Call external API through Stew |
| `POST` | `/integrations/call` | Call integrated service |
| `POST` | `/upload/document` | Upload document for processing |

### Telegram

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/telegram/setup` | Set up Telegram bot webhook |
| `GET` | `/telegram/status` | Check Telegram bot status |
| `POST` | `/telegram/webhook` | Telegram webhook handler |

### Security

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/security/dashboard` | View security dashboard |
| `POST` | `/security/fingerprint` | Submit device fingerprint |

---

## Code Execution Sandbox

S.T.E.W includes a restricted Python code execution sandbox for running user code safely.

**Allowed modules:** math, json, re, datetime, statistics, collections, itertools, random, string, textwrap, decimal, fractions, hashlib, unicodedata, operator, functools, bisect, copy, pprint, csv, io

**Optional modules:** numpy, matplotlib, pandas (available if installed)

**Security constraints:**
- No network access
- No file system access
- 10-second execution timeout
- Restricted built-ins
- Safe `__import__` wrapper

```bash
curl -X POST https://stew-agent.onrender.com/api/code/exec \
  -H "Content-Type: application/json" \
  -d '{
    "code": "import numpy as np\nprint(np.array([1,2,3]).mean())",
    "api_key": "stew_your_key_here"
  }'
```

---

## OCR / Vision

S.T.E.W includes Tesseract OCR with support for 17+ languages and 95%+ accuracy.

**Supported languages include:** English, French, Arabic, Chinese, German, Spanish, Portuguese, Russian, Japanese, Korean, Hindi, and more.

```bash
# Upload an image for OCR
curl -X POST https://stew-agent.onrender.com/api/ocr \
  -H "Content-Type: application/json" \
  -d '{
    "image": "base64_encoded_image_data",
    "api_key": "stew_your_key_here",
    "languages": ["eng"]
  }'
```

---

## Telegram Bot

S.T.E.W includes a full Telegram bot integration. Business plan users get automatic setup.

**Features:**
- AI-powered chat responses
- Photo/document handling with OCR
- Code execution via tool-calling agent
- Web search grounding
- Document generation on-demand
- Automatic failover to multiple search providers

---

## Security

S.T.E.W implements enterprise-grade security:

- **Device Fingerprinting:** Canvas hash, screen resolution, timezone, language fingerprinting
- **Rate Limiting:** Per-IP and per-endpoint rate limiting (Free: 100 req/min, Pro: 1,000, Business: 5,000)
- **IP Blocking:** Automatic blocking of malicious IPs
- **Security Middleware:** Runs on every request — tracks total requests, blocked requests, malicious attempts
- **Audit Logging:** Every action is logged and exportable
- **API Key Security:** Keys are hashed with bcrypt, stored securely
- **Registration Limits:** Max 3 free accounts per IP
- **Risk Scoring:** Registration risk assessment with security event logging

---

## Architecture

```
S.T.E.W Agent v6.0.0
├── server/
│   ├── main.py            — FastAPI app (56+ endpoints, 65 routes)
│   ├── llm_client.py      — Multi-provider LLM with failover
│   ├── search.py           — Web search (Serper → DuckDuckGo → SearXNG)
│   ├── browser.py          — Web browsing (Playwright → httpx fallback)
│   ├── skills_engine.py    — 59 skill definitions and execution
│   ├── document_generator  — PDF, DOCX, XLSX, PPTX generation
│   ├── document_processor  — PDF, DOCX, CSV, JSON, TXT extraction
│   ├── ocr_engine.py       — Tesseract OCR (17+ languages)
│   ├── code_sandbox.py     — Restricted Python execution
│   ├── tool_agent.py       — Kimi-style agentic loop (4 tools)
│   ├── orchestrator.py     — Multi-model orchestration
│   ├── agent_pool.py       — 100-agent swarm management
│   ├── telegram_bot.py     — Telegram bot integration
│   ├── auth.py             — User authentication (bcrypt + JWT)
│   ├── payments.py         — Paystack payment integration
│   ├── security.py        — Security middleware + rate limiting
│   ├── security_guard.py  — Device fingerprinting + risk scoring
│   ├── config.py           — Settings and configuration
│   ├── database.py         — SQLAlchemy async database
│   ├── models.py           — Database models
│   ├── memory.py           — Conversation memory
│   ├── vector_memory.py    — Vector-based memory search
│   ├── email_service.py    — Email notifications
│   ├── middleware.py       — Custom middleware
│   ├── keepalive.py        — Keep-alive service
│   └── system_prompt.py    — System prompt generation
├── agents/
│   └── agent_pool.py       — 100 specialized AI agents
├── skills/
│   └── skills_engine.py    — Skill definitions
├── memory/
│   └── memory_engine.py    — Memory management
├── migrations/             — Alembic database migrations
├── tests/                  — Test suite
└── Dockerfile              — Lightweight Docker image for Render free tier
```

### Tech Stack

- **Framework:** FastAPI + Uvicorn
- **Database:** SQLite (production) with SQLAlchemy async ORM + Alembic migrations
- **AI Providers:** Groq, Mistral AI, NVIDIA NIM, OpenRouter, HuggingFace, OpenAI
- **OCR:** Tesseract (pytesseract)
- **Document Generation:** ReportLab (PDF), python-docx (DOCX), openpyxl (XLSX), python-pptx (PPTX)
- **Web Search:** Serper API → DuckDuckGo HTML → SearXNG fallback
- **Web Scraping:** BeautifulSoup + lxml, Playwright (optional)
- **Code Sandbox:** Restricted Python with safe built-ins
- **Payments:** Paystack (Naira billing)
- **Auth:** bcrypt + JWT + Firebase
- **Deployment:** Render (Docker, free tier)

---

## Deployment

### Prerequisites

- Python 3.11+
- Docker (for containerized deployment)
- Render account (or any Docker-compatible platform)

### Environment Variables

```env
DATABASE_URL=sqlite:///./stew.db
ENVIRONMENT=production

# AI Provider Keys (at least one required)
GROQ_API_KEY=your_groq_key
MISTRAL_API_KEY=your_mistral_key
NVIDIA_API_KEY=your_nvidia_key
OPENROUTER_API_KEY=your_openrouter_key
HUGGINGFACE_API_KEY=your_hf_key
OPENAI_API_KEY=your_openai_key

# Search
SERPER_API_KEY=your_serper_key

# Payments
PAYSTACK_SECRET_KEY=your_paystack_secret

# Telegram
TELEGRAM_BOT_TOKEN=your_telegram_bot_token

# Optional
STEW_BROWSER_EXTENSION_URL=your_extension_url
```

### Docker Build

```bash
docker build -f stew_deploy/Dockerfile -t stew-agent .
docker run -p 8000:8000 --env-file .env stew-agent
```

### Render Deployment

1. Fork this repository
2. Create a new Web Service on Render, connected to your fork
3. Set the following:
   - **Environment:** Docker
   - **Dockerfile Path:** `stew_deploy/Dockerfile`
   - **Plan:** Free (or higher for production)
4. Add all environment variables from the list above
5. Deploy

### Free Tier Optimization

S.T.E.W is optimized for Render's free tier (512MB RAM):
- Playwright browser binaries excluded (httpx fallback used instead)
- numpy, matplotlib, pandas are lazy-loaded (only when code sandbox is used)
- Single worker process (`--workers 1`)
- SQLite (no external database needed)
- Service sleeps after 15 minutes of inactivity (first request takes ~10-15s to wake)

---

## OpenAI-Compatible API (Use Stew as Your AI Brain)

S.T.E.W now speaks OpenAI's language. Any tool built for OpenAI can use Stew instead — just change the URL and API key.

### Supported Tools
- **OpenCode** — autonomous coding agent
- **Devin** — AI software engineer
- **Cursor** — AI code editor
- **LangChain** — LLM framework
- **AutoGen** — multi-agent framework
- **OpenClaw** — agent orchestration
- **Hermes Agent** — task execution
- **Any tool** that supports custom OpenAI base URLs

### Quick Setup (3 lines)

```bash
# Instead of OpenAI:
# OPENAI_API_KEY=sk-xxx  OPENAI_BASE_URL=https://api.openai.com/v1

# Use Stew:
export OPENAI_API_KEY=stew_your_api_key_here
export OPENAI_BASE_URL=https://stew-agent.onrender.com/v1
```

### Python (OpenAI SDK)

```python
from openai import OpenAI

# Point to Stew instead of OpenAI
client = OpenAI(
    api_key="stew_your_api_key_here",
    base_url="https://stew-agent.onrender.com/v1"
)

response = client.chat.completions.create(
    model="stew-default",
    messages=[{"role": "user", "content": "Hello from Africa!"}]
)
print(response.choices[0].message.content)
```

### JavaScript/Node.js (OpenAI SDK)

```javascript
import OpenAI from "openai";

const client = new OpenAI({
  apiKey: "stew_your_api_key_here",
  baseURL: "https://stew-agent.onrender.com/v1"
});

const response = await client.chat.completions.create({
  model: "stew-default",
  messages: [{ role: "user", content: "Hello from Africa!" }]
});
console.log(response.choices[0].message.content);
```

### Available Models

| Model | Description |
|---|---|
| `stew-default` | Auto-failover across all 6 providers |
| `stew-fast` | Groq (ultra low latency) |
| `stew-mistral` | Mistral Large (flagship) |
| `stew-nvidia` | NVIDIA NIM Llama 3.3 70B (free) |
| `stew-openrouter` | OpenRouter (free) |
| `stew-hf` | HuggingFace Qwen3 235B |
| `stew-openai` | OpenAI GPT-4o-mini |
| `gpt-4o`, `gpt-4o-mini`, etc. | OpenAI names (auto-routed via Stew) |

### Stew Extensions (beyond OpenAI)

Pass these in the request body for Stew-specific features:

```json
{
  "model": "stew-default",
  "messages": [...],
  "web_search": true,      // enable live web search grounding
  "fusion_mode": true       // multi-model fusion (3 providers in parallel)
}
```

### Endpoints

| Endpoint | Method | Description |
|---|---|---|
| `/v1/chat/completions` | POST | Chat completions (streaming + non-streaming) |
| `/v1/models` | GET | List available models |
| `/v1/models/{id}` | GET | Get model details |
| `/v1/embeddings` | POST | Not supported (returns 501) |

### Why use Stew instead of OpenAI directly?

1. **Naira billing** — Pay via Paystack, no dollar card needed
2. **6 providers** — auto-failover means 99.9% uptime
3. **Web search** — built-in web grounding (OpenAI charges extra for this)
4. **12 personas** — doctor, lawyer, finance, startup, etc.
5. **African context** — Pidgin, Yoruba, Igbo, Hausa support
6. **Free tier** — 1,500 calls/month, no credit card
7. **100-agent swarm** — complex multi-step tasks

## Roadmap

- [x] 59 skills across 13 categories
- [x] 100-agent swarm
- [x] 12 domain personas with fine-tuning
- [x] Multi-provider LLM with failover (6 providers)
- [x] Binary document generation (PDF, DOCX, XLSX, PPTX)
- [x] Python code execution sandbox
- [x] OCR / Vision (Tesseract, 17+ languages)
- [x] Telegram bot integration
- [x] Naira billing via Paystack
- [x] Chrome browser extension
- [x] SearXNG search fallback
- [x] Kimi-style tool-calling agent
- [x] npm SDK (`stew-ai` package) — github.com/emmanuelerogian723-alt/stew-ai
- [x] OpenAI-compatible API endpoint (/v1/chat/completions) — works with OpenCode, Devin, Cursor, LangChain, AutoGen
- [ ] WhatsApp Business API integration
- [ ] Stew Skill Marketplace (third-party developer skills)
- [x] Python SDK (`stew-ai` package) — github.com/emmanuelerogian723-alt/stew-python
- [ ] React component library (`<StewChat />`)
- [ ] WordPress plugin
- [ ] Visual agent workflow builder
- [ ] White-label deployment option
- [ ] On-premise enterprise deployment

---

## Built By

**MUTYINT Nigeria**

S.T.E.W — Smart Task Execution Worker

🇳🇬 100% Nigerian-built · Designed for Africa · Made for the world

- **Website:** https://stew-agent.onrender.com
- **API Docs:** https://stew-agent.onrender.com/docs
- **Playground:** https://stew-agent.onrender.com/playground
- **GitHub:** https://github.com/emmanuelerogian723-alt/Stew-agent
- **npm SDK:** https://github.com/emmanuelerogian723-alt/stew-ai
- **Python SDK:** https://github.com/emmanuelerogian723-alt/stew-python
- **Email:** emmanuelerogian723@gmail.com

---

## License

MIT License — see [LICENSE](LICENSE) file for details.

Copyright © 2026 MUTYINT Nigeria. All rights reserved.

---

<p align="center">
  <strong>Africa's most powerful AI agent API.</strong><br>
  Built by Africans, for Africans. Naira billing. No dollar card.<br>
  🇳🇬 🌍
</p>
