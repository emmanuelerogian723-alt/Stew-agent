# Deploy S.T.E.W on Railway

## Steps

1. Go to https://railway.app and create a new project
2. Click "Deploy from GitHub repo"
3. Select your repository
4. Railway will auto-detect the Dockerfile

## Required Environment Variables

Set these in Railway's Variables panel:

| Variable | Description |
|---|---|
| GROQ_API_KEY | From console.groq.com |
| OPENROUTER_API_KEY | From openrouter.ai/keys |
| OPENAI_API_KEY | Optional fallback |
| SERPER_API_KEY | From serper.dev |
| DATABASE_URL | Add a Railway PostgreSQL plugin — it auto-sets this |
| JWT_SECRET_KEY | Any long random string |
| PAYSTACK_SECRET_KEY | From dashboard.paystack.com |
| PAYSTACK_PUBLIC_KEY | From dashboard.paystack.com |

## Add PostgreSQL

In your Railway project:
1. Click "New" → "Database" → "PostgreSQL"
2. Railway will automatically inject DATABASE_URL into your service

## Health Check

Railway will ping `/heartbeat` to verify deployment success.
