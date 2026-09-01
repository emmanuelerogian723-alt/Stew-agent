# 🧠 S.T.E.W Agent — Smart Thinking Executive Worker

> *The AI API built for Africa. Multi-model LLM access, 60+ skills, 100-agent swarm, document generation, web search, and Naira billing via Paystack — no dollar card needed.*

**Live API:** https://stew-agent-r3m7.onrender.com  
**Dashboard:** https://stew-agent-r3m7.onrender.com/dashboard  
**Docs (Swagger):** https://stew-agent-r3m7.onrender.com/docs  
**Built by:** Emmanuel Ene Rejoice Gideon (MUTYINT) 🇳🇬

---

## What is S.T.E.W Agent?

S.T.E.W Agent is an OpenAI-compatible AI API that gives you access to 6 LLM providers (Groq, OpenRouter, Mistral, NVIDIA, Hugging Face, OpenAI) through a single endpoint, with automatic failover. It's designed specifically for the African market:

- **Naira billing** via Paystack — no dollar card required
- **60+ built-in skills** — document generation, web search, code execution, OCR, image generation
- **100-agent swarm** — run parallel AI agents for complex tasks
- **Telegram bot** with tool-calling and voice note support
- **Chrome extension** with Jina AI page reading and open-source search

Any tool that supports custom OpenAI base URLs (Cursor, LangChain, AutoGen, Acode, etc.) can point at S.T.E.W and just work.

---

## Quick Start — Python SDK

### Install

```bash
pip install openai
```

### Use

```python
from openai import OpenAI

# Point the OpenAI SDK at S.T.E.W instead of OpenAI
client = OpenAI(
    api_key="stew_YOUR_API_KEY",           # Get yours at the dashboard
    base_url="https://stew-agent-r3m7.onrender.com/v1"
)

# Chat completion
response = client.chat.completions.create(
    model="stew-default",
    messages=[
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "What is S.T.E.W Agent?"}
    ]
)
print(response.choices[0].message.content)
print(f"Model: {response.model}")
print(f"Tokens used: {response.usage.total_tokens}")
```

### Streaming

```python
stream = client.chat.completions.create(
    model="stew-default",
    messages=[{"role": "user", "content": "Write a poem about Lagos."}],
    stream=True
)
for chunk in stream:
    if chunk.choices[0].delta.content:
        print(chunk.choices[0].delta.content, end="")
```

### List Models

```python
models = client.models.list()
for m in models.data:
    print(m.id, "-", m.description)
```

### Environment Variables (alternative)

Instead of hardcoding, set env vars and the SDK picks them up automatically:

```bash
export OPENAI_API_KEY=stew_YOUR_API_KEY
export OPENAI_BASE_URL=https://stew-agent-r3m7.onrender.com/v1
```

```python
from openai import OpenAI
client = OpenAI()  # reads from env automatically
```

---

## Quick Start — NPM SDK (Node.js)

### Install

```bash
npm install openai
```

### Use

```javascript
import OpenAI from "openai";

const client = new OpenAI({
  apiKey: "stew_YOUR_API_KEY",
  baseURL: "https://stew-agent-r3m7.onrender.com/v1",
});

// Chat completion
const response = await client.chat.completions.create({
  model: "stew-default",
  messages: [
    { role: "system", content: "You are a helpful assistant." },
    { role: "user", content: "What is S.T.E.W Agent?" }
  ],
});

console.log(response.choices[0].message.content);
console.log(`Model: ${response.model}`);
console.log(`Tokens used: ${response.usage.total_tokens}`);
```

### Streaming

```javascript
const stream = await client.chat.completions.create({
  model: "stew-default",
  messages: [{ role: "user", content: "Write a poem about Lagos." }],
  stream: true,
});

for await (const chunk of stream) {
  const delta = chunk.choices[0]?.delta?.content;
  if (delta) process.stdout.write(delta);
}
```

### List Models

```javascript
const models = await client.models.list();
for (const m of models.data) {
  console.log(m.id, "-", m.description);
}
```

### Environment Variables (alternative)

```bash
export OPENAI_API_KEY=stew_YOUR_API_KEY
export OPENAI_BASE_URL=https://stew-agent-r3m7.onrender.com/v1
```

```javascript
import OpenAI from "openai";
const client = new OpenAI(); // reads from env automatically
```

---

## Quick Start — cURL

No SDK needed. Test directly from any terminal:

```bash
# Chat completion
curl -X POST https://stew-agent-r3m7.onrender.com/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer stew_YOUR_API_KEY" \
  -d '{
    "model": "stew-default",
    "messages": [{"role": "user", "content": "Hello Stew!"}]
  }'

# List models
curl https://stew-agent-r3m7.onrender.com/v1/models \
  -H "Authorization: Bearer stew_YOUR_API_KEY"
```

---

## Installing in Acode Terminal (Android)

Acode is a code editor for Android with a built-in terminal. Here's how to use S.T.E.W Agent from it:

### Step 1: Install Termux (for package support)

Acode's built-in terminal is limited. For full pip/npm support, install Termux from F-Droid:
https://f-droid.org/packages/com.termux/

### Step 2: Set up Python in Termux

```bash
# In Termux
pkg update && pkg install python
pip install openai
```

### Step 3: Create your test script

Create a file `stew_test.py` in Acode:

```python
from openai import OpenAI

client = OpenAI(
    api_key="stew_YOUR_API_KEY",
    base_url="https://stew-agent-r3m7.onrender.com/v1"
)

response = client.chat.completions.create(
    model="stew-default",
    messages=[{"role": "user", "content": "Hello from Acode on Android!"}]
)

print("Response:", response.choices[0].message.content)
print("Model:", response.model)
print("Tokens:", response.usage.total_tokens)
```

### Step 4: Run it

```bash
# In Termux (navigate to your Acode workspace)
python stew_test.py
```

### Alternative: Node.js in Termux

```bash
pkg install nodejs
npm install openai
```

Create `stew_test.mjs` in Acode:

```javascript
import OpenAI from "openai";

const client = new OpenAI({
  apiKey: "stew_YOUR_API_KEY",
  baseURL: "https://stew-agent-r3m7.onrender.com/v1",
});

const res = await client.chat.completions.create({
  model: "stew-default",
  messages: [{ role: "user", content: "Hello from Acode on Android!" }],
});

console.log("Response:", res.choices[0].message.content);
console.log("Model:", res.model);
console.log("Tokens:", res.usage.total_tokens);
```

Run it:

```bash
node stew_test.mjs
```

### Alternative: cURL in Acode Terminal

If you just want a quick test without installing anything:

```bash
curl -X POST https://stew-agent-r3m7.onrender.com/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer stew_YOUR_API_KEY" \
  -d '{"model":"stew-default","messages":[{"role":"user","content":"Hello from Acode!"}]}'
```

---

## Available Models

| Model ID | Description |
|---|---|
| `stew-default` | Auto-failover across all 6 providers (recommended) |
| `stew-fast` | Groq — ultra low latency |
| `stew-mistral` | Mistral Large |
| `stew-nvidia` | NVIDIA NIM (Llama 3.3 70B — free) |
| `stew-openrouter` | OpenRouter (free Llama 3.3 70B) |
| `stew-hf` | Hugging Face inference |
| `stew-openai` | OpenAI GPT-4o-mini route |
| `gpt-4o` | OpenAI GPT-4o (alias) |
| `gpt-4o-mini` | OpenAI GPT-4o-mini (alias) |
| `gpt-4-turbo` | OpenAI GPT-4 Turbo (alias) |
| `gpt-3.5-turbo` | OpenAI GPT-3.5 Turbo (alias) |

---

## API Key Registration

1. Go to https://stew-agent-r3m7.onrender.com/dashboard
2. Click **Register** and create an account
3. Your `stew_` API key will be displayed in the dashboard
4. Free plan includes 50 API calls per month
5. Upgrade to Pro (₦5,000/mo) or Business (₦15,000/mo) for higher limits

### Register via API

```bash
curl -X POST https://stew-agent-r3m7.onrender.com/auth/register \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Your Name",
    "email": "you@example.com",
    "password": "yourpassword"
  }'
```

The response includes your `api_key` immediately.

---

## Full API Reference

### OpenAI-Compatible Endpoints

| Method | Path | Description |
|---|---|---|
| POST | `/v1/chat/completions` | Chat completion (streaming & non-streaming) |
| GET | `/v1/models` | List available models |
| GET | `/v1/models/{model_id}` | Get model details |
| POST | `/v1/embeddings` | Text embeddings |

### S.T.E.W Native Endpoints

| Method | Path | Description |
|---|---|---|
| POST | `/chat` | Talk to S.T.E.W |
| POST | `/task` | Execute a task |
| POST | `/search` | Web search |
| POST | `/code` | Write and run Python code |
| POST | `/build/website` | Build a website |
| POST | `/build/document` | Create PDF/DOCX/XLSX/PPTX |
| POST | `/agents/run` | Run specific agents |
| POST | `/agents/all` | Deploy all 100 agents |
| GET | `/agents/status` | Agent statuses |
| POST | `/memory/save` | Save a memory |
| GET | `/memory/stats` | Memory statistics |
| GET | `/status` | Full system status |
| GET | `/soul` | S.T.E.W identity |
| GET | `/heartbeat` | Health check |
| GET | `/skills` | List all 60+ skills |
| GET | `/docs` | Interactive Swagger UI |

---

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `GROQ_API_KEY` | Yes | Groq API key (https://console.groq.com/keys) |
| `OPENROUTER_API_KEY` | Yes | OpenRouter API key (https://openrouter.ai/keys) |
| `PAYSTACK_PUBLIC_KEY` | Yes | Paystack public key |
| `PAYSTACK_SECRET_KEY` | Yes | Paystack secret key |
| `HF_TOKEN` | Optional | Hugging Face token for HF models |
| `STEW_ADMIN_SECRET` | Yes | Admin access secret |
| `ENVIRONMENT` | Yes | Set to `production` |
| `PORT` | Auto | Set by hosting platform (default 8000) |
| `APP_BASE_URL` | Yes | Your deployment URL |
| `TELEGRAM_BOT_TOKEN` | Optional | For Telegram bot features |

---

## Deploy Your Own Instance

### Render (recommended)

1. Go to https://dashboard.render.com
2. New → Web Service → Connect your GitHub repo `Stew-agent`
3. Settings:
   - Runtime: Python 3
   - Build Command: `pip install -r requirements.txt`
   - Start Command: `python server/main.py`
   - Health Check Path: `/heartbeat`
4. Add environment variables (see above)
5. Deploy!

### Other Platforms

S.T.E.W Agent runs anywhere Python 3.11+ is available:
- Railway, Fly.io, Hugging Face Spaces, Vercel (serverless), Docker

See `DEPLOY.md` for platform-specific instructions.

---

## Tech Stack

- **Backend:** Python 3.11, FastAPI, Uvicorn
- **AI:** Groq (Llama 3.3 70B), OpenRouter, Mistral, NVIDIA NIM, Hugging Face, OpenAI
- **Database:** SQLite (development) / PostgreSQL via Supabase (production)
- **Payments:** Paystack (Naira billing)
- **Search:** SearXNG, DuckDuckGo, Jina AI
- **Image Generation:** Pollinations.ai
- **Documents:** PDF, DOCX, XLSX, PPTX generation
- **Bot:** Telegram with tool-calling and voice transcription
- **Extension:** Chrome extension with Jina AI page reading

---

## License

© 2026 MUTYINT — Built by Emmanuel Ene Rejoice Gideon 🇳🇬  
GitHub: https://github.com/emmanuelerogian723-alt/Stew-agent
