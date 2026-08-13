# S.T.E.W 3.0 ULTRA — Complete Deployment Guide

Deploy anywhere. The backend is the same for every platform.
The landing page HTML files are NEVER touched.

---

## STEP 0 — Get your API Keys

You need at least ONE LLM key and optionally the others.

*LLM Providers (get at least Groq — it's free)*

1. GROQ_API_KEY
   Sign up at: https://console.groq.com
   Free tier: 100k tokens/day. Very fast.

2. OPENROUTER_API_KEY
   Sign up at: https://openrouter.ai
   Pay-per-use. Many models available.

3. OPENAI_API_KEY (optional)
   Sign up at: https://platform.openai.com
   Last resort fallback.

*Web Search (for real results, not hallucinations)*

4. SERPER_API_KEY
   Sign up at: https://serper.dev
   Free tier: 2,500 searches/month.

*Payments (Nigerian Naira — for plan upgrades)*

5. PAYSTACK_SECRET_KEY + PAYSTACK_PUBLIC_KEY
   Sign up at: https://dashboard.paystack.com
   Free to register.

*Auto-generated (you don't need to provide these)*

6. JWT_SECRET_KEY — generate with: openssl rand -hex 32
7. DATABASE_URL — provided by the hosting platform
8. STEW_ADMIN_SECRET — generate with: openssl rand -hex 16

---

## PLATFORM 1 — Render (Recommended — closest to Nigeria)

BEST FOR: Production. Free PostgreSQL included.
URL after deploy: https://stew-agent.onrender.com

Steps:
1. Push your code to GitHub
2. Go to https://dashboard.render.com
3. Click "New" → "Web Service"
4. Connect your GitHub repo
5. Set:
   - Name: stew-agent
   - Runtime: Docker
   - Dockerfile Path: ./stew_deploy/Dockerfile
   - Health Check Path: /heartbeat
6. Add environment variables (one by one):
   - GROQ_API_KEY = your key
   - OPENROUTER_API_KEY = your key
   - SERPER_API_KEY = your key
   - PAYSTACK_SECRET_KEY = your key
   - PAYSTACK_PUBLIC_KEY = your key
   - JWT_SECRET_KEY = (generate random)
   - ENVIRONMENT = production
7. Add PostgreSQL:
   - Click "New" → "PostgreSQL" → Free tier
   - Name it: stew-db
   - In your Web Service, add env var:
     DATABASE_URL = (copy Internal Database URL from PostgreSQL dashboard)
8. Click "Create Web Service"
9. Wait ~5 minutes. Check: https://stew-agent.onrender.com/heartbeat

---

## PLATFORM 2 — Hugging Face Spaces

BEST FOR: Demos, sharing with the AI community. Free GPU option available.
URL after deploy: https://huggingface.co/spaces/YOUR_USERNAME/stew-agent

Steps:
1. Go to https://huggingface.co/new-space
2. Space name: stew-agent
3. SDK: Docker
4. Visibility: Public (or Private)
5. Click "Create Space"
6. In your Space, go to Settings → Repository secrets
7. Add these secrets:
   - GROQ_API_KEY
   - OPENROUTER_API_KEY
   - SERPER_API_KEY
   - JWT_SECRET_KEY
   - PAYSTACK_SECRET_KEY
   - DATABASE_URL (use Render's external DB URL, or Supabase free tier)
8. Upload these files to your Space:
   - stew_deploy/deploy/huggingface/Dockerfile → rename to Dockerfile
   - stew_deploy/deploy/huggingface/README.md → rename to README.md
   - stew_deploy/requirements.txt
   - stew_deploy/server/ (entire folder)
   - stew_deploy/migrations/ (entire folder)
   - stew_deploy/alembic.ini

OR push via git:
   git remote add space https://huggingface.co/spaces/YOUR_USERNAME/stew-agent
   git push space main

---

## PLATFORM 3 — Railway

BEST FOR: Simple, fast deploys. Automatic PostgreSQL.
URL after deploy: https://stew-agent.up.railway.app

Steps:
1. Go to https://railway.app and sign in with GitHub
2. Click "New Project" → "Deploy from GitHub repo"
3. Select your repo
4. Railway auto-detects Dockerfile
5. Click "Add Plugin" → "PostgreSQL"
   DATABASE_URL is automatically injected
6. Go to Variables and add:
   - GROQ_API_KEY
   - OPENROUTER_API_KEY
   - SERPER_API_KEY
   - JWT_SECRET_KEY
   - PAYSTACK_SECRET_KEY
   - PAYSTACK_PUBLIC_KEY
   - ENVIRONMENT = production
7. Deploy happens automatically.
8. Check: https://YOUR-APP.up.railway.app/heartbeat

---

## PLATFORM 4 — Fly.io

BEST FOR: Global edge deployment, low latency.

Steps:
1. Install flyctl: curl -L https://fly.io/install.sh | sh
2. fly auth login
3. cd stew_deploy
4. fly launch --name stew-agent --no-deploy
5. Set secrets:
   fly secrets set GROQ_API_KEY=your_key
   fly secrets set OPENROUTER_API_KEY=your_key
   fly secrets set SERPER_API_KEY=your_key
   fly secrets set JWT_SECRET_KEY=$(openssl rand -hex 32)
   fly secrets set PAYSTACK_SECRET_KEY=your_key
6. Add database:
   fly postgres create --name stew-db --region lhr
   fly postgres attach stew-db
7. fly deploy

---

## PLATFORM 5 — Docker (any VPS / DigitalOcean / AWS EC2)

BEST FOR: Full control. Your own server.

Steps:
1. Copy .env.example to .env, fill in keys
2. docker compose -f stew_deploy/deploy/docker/docker-compose.yml up -d
3. App runs at http://YOUR_SERVER_IP:8000
4. Add Nginx + Certbot for HTTPS (optional)

---

## GitHub Actions (Auto-deploy on every push)

Copy stew_deploy/deploy/github-actions/ci.yml to .github/workflows/ci.yml

Add these GitHub Secrets (Settings → Secrets):
- RENDER_DEPLOY_HOOK = (from Render → your service → Deploy Hook URL)
- HF_TOKEN = (from huggingface.co → Settings → Access Tokens)
- HF_USERNAME = your HF username
- HF_SPACE_NAME = stew-agent

After this, every push to main:
1. Runs all 28 tests
2. Deploys to Render
3. Pushes to Hugging Face Spaces

---

## Free PostgreSQL Options (if your platform doesn't include one)

1. Supabase — https://supabase.com (free 500MB)
   Get connection string from: Project → Settings → Database
   Format: postgresql://postgres:PASSWORD@db.xxx.supabase.co:5432/postgres

2. Neon — https://neon.tech (free serverless PostgreSQL)
   Format: postgresql://user:pass@ep-xxx.us-east-2.aws.neon.tech/neondb

3. Railway PostgreSQL (free $5 credit/month)

---

## Verifying Your Deployment

After deploying anywhere, test these:

curl https://YOUR-DOMAIN/heartbeat
→ Should return: {"status":"ok","version":"5.0.0",...}

curl -X POST https://YOUR-DOMAIN/auth/register \
  -H "Content-Type: application/json" \
  -d '{"name":"Test","email":"test@example.com","password":"test123","plan":"free"}'
→ Should return an api_key starting with "stew_"

curl -X POST https://YOUR-DOMAIN/generate/pdf \
  -H "Content-Type: application/json" \
  -d '{"content":"Hello World","title":"Test","api_key":"YOUR_KEY"}'
→ Should return base64-encoded PDF

---

## Summary — Which platform to choose?

Free + Nigeria-friendly → Render (has free tier, fast deploys)
AI community visibility → Hugging Face Spaces
Simplest setup → Railway
Cheapest VPS → Fly.io
Full control → Docker on your own server
All of the above → Use GitHub Actions to auto-deploy everywhere
