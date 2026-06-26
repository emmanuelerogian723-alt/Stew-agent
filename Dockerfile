# S.T.E.W 3.0 ULTRA — Minimal Render Dockerfile (guaranteed to build)
FROM python:3.11-slim-bookworm

# Core system deps only - minimal and safe
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ curl ca-certificates \
    libpq-dev \
    && apt-get clean

WORKDIR /app

# Install Python dependencies
COPY stew_deploy/requirements.txt .
RUN pip install --upgrade pip --quiet && \
    pip install --no-cache-dir -r requirements.txt --quiet

# Copy all app code
COPY stew_deploy/ .

# Copy landing page
COPY landing.html /app/landing.html

# Runtime directories
RUN mkdir -p memory/data output logs workspace screenshots

EXPOSE 8000

# Bulletproof startup: alembic migration then server
CMD alembic upgrade head; uvicorn server.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1 --log-level info
