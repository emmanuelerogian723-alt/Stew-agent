# S.T.E.W 5.0 — Fixed Render Dockerfile (lightweight for free tier)
FROM python:3.11-slim-bookworm

# System dependencies (no Playwright browser deps — too heavy for free tier)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ curl wget ca-certificates \
    libpq-dev \
    libpango-1.0-0 libpangoft2-1.0-0 libpangocairo-1.0-0 \
    libgdk-pixbuf2.0-0 libcairo2 libffi-dev \
    libxml2-dev libxslt1-dev shared-mime-info \
    tesseract-ocr tesseract-ocr-eng \
    fonts-liberation fontconfig fonts-dejavu-core \
    ffmpeg \
    && apt-get clean

WORKDIR /app

COPY stew_deploy/requirements.txt .
RUN pip install --upgrade pip --quiet && \
    pip install --no-cache-dir -r requirements.txt --quiet && \
    pip install --no-cache-dir --upgrade yt-dlp --quiet

# stew_deploy/ is the SINGLE SOURCE OF TRUTH for all HTML/static assets.
# Do NOT add COPY lines from the repo root — that previously caused stale
# root-level landing.html/dashboard.html (missing Google Sign-In + device
# fingerprinting) to silently overwrite the correct files on every deploy.
COPY stew_deploy/ .
COPY stew_deploy/playground.html /app/stew_playground.html

RUN mkdir -p memory/data output logs workspace screenshots uploads

EXPOSE 8000

CMD uvicorn server.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1 --log-level info --loop asyncio & sleep 2; (alembic upgrade head 2>&1 || true) >> /app/logs/alembic.log; wait
