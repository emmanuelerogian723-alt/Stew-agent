# S.T.E.W Browser Extension API

Free, open-source search and research API for S.T.E.W.
No API keys needed — uses DuckDuckGo + Bing + BeautifulSoup.

## Endpoints

- `GET /api/search?q=<query>&depth=1&fetch=false` — Web search
- `POST /api/research` — Deep research with page content extraction
- `GET /api/fetch?url=<url>` — Fetch and extract content from any URL

## Deploy to Vercel

```bash
vercel --prod
```

## Usage

```bash
# Search
curl "https://your-url.vercel.app/api/search?q=artificial+intelligence"

# Research
curl -X POST "https://your-url.vercel.app/api/research" \
  -H "Content-Type: application/json" \
  -d '{"query": "quantum computing", "depth": 2}'

# Fetch page
curl "https://your-url.vercel.app/api/fetch?url=https://example.com"
```
