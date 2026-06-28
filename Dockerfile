# S.T.E.W 5.0 — Fixed Render Dockerfile
FROM python:3.11-slim-bookworm

# ALL required system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ curl ca-certificates \
    libpq-dev \
    libpango-1.0-0 libpangoft2-1.0-0 libpangocairo-1.0-0 \
    libgdk-pixbuf2.0-0 libcairo2 libffi-dev \
    libxml2-dev libxslt1-dev shared-mime-info \
    tesseract-ocr tesseract-ocr-eng \
    libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 \
    libcups2 libxkbcommon0 libxcomposite1 libxdamage1 \
    libxfixes3 libxrandr2 libgbm1 libasound2 \
    libx11-6 libx11-xcb1 libxcb1 libxext6 \
    fonts-liberation fontconfig \
    && apt-get clean

WORKDIR /app

COPY stew_deploy/requirements.txt .
RUN pip install --upgrade pip --quiet && \
    pip install --no-cache-dir -r requirements.txt --quiet

RUN playwright install chromium

COPY stew_deploy/ .
COPY landing.html /app/landing.html

RUN mkdir -p memory/data output logs workspace screenshots uploads

EXPOSE 8000

CMD alembic upgrade head; uvicorn server.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1 --log-level info
