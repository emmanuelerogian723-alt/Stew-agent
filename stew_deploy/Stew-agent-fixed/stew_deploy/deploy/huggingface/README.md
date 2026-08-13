---
title: S.T.E.W Agent API
emoji: 🤖
colorFrom: blue
colorTo: cyan
sdk: docker
pinned: false
license: mit
app_port: 7860
---

# S.T.E.W — Smart Thinking Executive Worker

An autonomous AI agent API with real document generation, multi-provider LLM fallback, web search, and persistent memory.

## Endpoints

- `GET /heartbeat` — health check
- `POST /auth/register` — get API key
- `POST /chat` — chat with web grounding
- `POST /task` — execute tasks
- `POST /generate/pdf` — generate real PDF
- `POST /generate/docx` — generate Word doc
- `POST /generate/xlsx` — generate Excel
- `POST /generate/pptx` — generate PowerPoint
- `GET /docs` — interactive API docs

## Setup

Set these secrets in your HF Space settings:

```
GROQ_API_KEY
OPENROUTER_API_KEY
SERPER_API_KEY
JWT_SECRET_KEY
DATABASE_URL
PAYSTACK_SECRET_KEY
```
