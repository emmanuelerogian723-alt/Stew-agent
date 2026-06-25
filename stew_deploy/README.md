# STEW AI — Strategic Thinking Execution Workbench

100 parallel AI agents. 50+ built-in skills. Built by Emmanuel Ene Rejoice Gideon — MUTYINT 🇳🇬

## Deploy to Render

1. Go to https://dashboard.render.com
2. New → Web Service → Connect your GitHub repo `Stew-agent`
3. Settings:
   - Runtime: Python 3
   - Build Command: `pip install -r requirements.txt`
   - Start Command: `python server/main.py`
   - Health Check Path: `/heartbeat`
4. Add Environment Variables:
   - `GROQ_API_KEY` — your Groq API key (get one at https://console.groq.com/keys)
   - `PORT` — 8000 (Render sets this automatically)
5. Deploy!

## API Endpoints

POST /chat — Talk to STEW
POST /task — Give STEW a task to execute
POST /search — Web search
POST /code — Write and run code
POST /build/website — Build a website
POST /build/document — Create PDF
POST /agents/run — Run specific agents
POST /agents/all — Deploy all 100 agents
GET /agents/status — All agent statuses
POST /memory/save — Save a memory
GET /memory/stats — Memory statistics
GET /status — Full system status
GET /soul — STEW's identity
GET /heartbeat — Health check
GET /skills — List all skills
GET /docs — Interactive API docs (Swagger UI)

## Get API Keys

Groq (free tier available): https://console.groq.com/keys
OpenAI (optional): https://platform.openai.com/api-keys

## Tech Stack

Python 3.11, FastAPI, Uvicorn, Groq/OpenAI LLMs, DuckDuckGo Search

© 2026 MUTYINT — Built by Emmanuel Ene Rejoice Gideon