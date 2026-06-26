# S.T.E.W Root Dockerfile — Render Production
FROM python:3.11-slim-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ wget curl ca-certificates \
    tesseract-ocr tesseract-ocr-eng \
    libglib2.0-0 libnss3 libnspr4 \
    libatk1.0-0 libatk-bridge2.0-0 \
    libcups2 libdrm2 libxkbcommon0 \
    libxcomposite1 libxdamage1 libxfixes3 \
    libxrandr2 libgbm1 libpango-1.0-0 libcairo2 \
    libatspi2.0-0 libx11-6 libx11-xcb1 libxcb1 \
    libxext6 libxi6 libxtst6 libasound2 \
    fonts-liberation libfreetype6 libxml2 \
    && apt-get clean

WORKDIR /app

# Install Python dependencies (cached layer)
COPY stew_deploy/requirements.txt .
RUN pip install --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Playwright browser (optional — fallback if fails)
RUN pip install --no-cache-dir playwright==1.44.0 && \
    (playwright install chromium --with-deps || echo "Playwright optional")

# Copy all app code
COPY stew_deploy/ .

# Copy landing page to root level
COPY landing.html /app/landing.html

# Runtime directories
RUN mkdir -p memory/data output logs workspace screenshots

EXPOSE 8000

CMD alembic upgrade head && uvicorn server.main:app --host 0.0.0.0 --port 8000 --workers 1 --log-level info
