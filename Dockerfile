# S.T.E.W 5.0 — Fixed Render Dockerfile (lightweight for free tier)
FROM python:3.11-slim-bookworm

# System dependencies (no Playwright browser deps — too heavy for free tier)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ curl ca-certificates \
    libpq-dev \
    libpango-1.0-0 libpangoft2-1.0-0 libpangocairo-1.0-0 \
    libgdk-pixbuf2.0-0 libcairo2 libffi-dev \
    libxml2-dev libxslt1-dev shared-mime-info \
    tesseract-ocr tesseract-ocr-eng \
    fonts-liberation fontconfig \
    && apt-get clean

WORKDIR /app

COPY stew_deploy/requirements.txt .
RUN pip install --upgrade pip --quiet && \
    pip install --no-cache-dir -r requirements.txt --quiet

# No Playwright — using BeautifulSoup + trafilatura for web scraping instead

COPY stew_deploy/ .
COPY landing.html /app/landing.html
COPY dashboard.html /app/dashboard.html
COPY robots.txt /app/robots.txt
COPY sitemap.xml /app/sitemap.xml
COPY stew_playground.html /app/stew_playground.html

RUN mkdir -p memory/data output logs workspace screenshots uploads

EXPOSE 8000

CMD alembic upgrade head 2>&1 || true; uvicorn server.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1 --log-level info
