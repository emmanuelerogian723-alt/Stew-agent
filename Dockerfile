# S.T.E.W 3.0 ULTRA — Bulletproof Render Dockerfile
FROM python:3.11-slim-bookworm

# System dependencies including libpq for asyncpg
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ wget curl ca-certificates \
    libpq-dev \
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

# Install Python dependencies
COPY stew_deploy/requirements.txt .
RUN pip install --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Skip Playwright in production (saves 300MB and avoids hang)
# Browser skills run in fallback/API mode

# Copy all app code
COPY stew_deploy/ .

# Copy landing page
COPY landing.html /app/landing.html

# Runtime directories
RUN mkdir -p memory/data output logs workspace screenshots

# Copy startup script
COPY startup.sh /app/startup.sh
RUN chmod +x /app/startup.sh

EXPOSE 8000

CMD ["/app/startup.sh"]
