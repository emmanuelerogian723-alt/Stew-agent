FROM python:3.11-slim-bookworm

# System dependencies (lighter — no Playwright browser deps)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ wget curl ca-certificates \
    tesseract-ocr tesseract-ocr-eng \
    libxml2 libxslt1.1 \
    libglib2.0-0 \
    ffmpeg \
    fonts-dejavu-core fonts-liberation \
    && apt-get clean

WORKDIR /app

# Install Python dependencies first (layer cache)
COPY requirements.txt .
RUN pip install --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt && \
    pip install --no-cache-dir yt-dlp

# Copy application code
COPY server/ ./server/
COPY dashboard.html .
COPY landing.html .
COPY playground.html .
COPY plugins.html .
COPY admin.html .
COPY robots.txt .
COPY sitemap.xml .
COPY migrations/ ./migrations/
COPY alembic.ini .

# Create runtime directories
RUN mkdir -p memory/data output logs workspace screenshots

EXPOSE 8000

# Run DB migrations then start server
CMD alembic upgrade head 2>/dev/null || true; uvicorn server.main:app --host 0.0.0.0 --port 8000 --workers 2
