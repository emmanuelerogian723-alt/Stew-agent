FROM python:3.11-slim-bookworm

# Install system dependencies in one layer
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

# Install Python packages first
COPY requirements.txt .
RUN pip install --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Install Playwright with graceful fallback
RUN pip install --no-cache-dir playwright==1.44.0 && \
    (playwright install chromium || echo "Chromium install failed - browser in fallback mode")

# Copy all application code
COPY . .

# Create runtime directories
RUN mkdir -p memory/data output logs workspace screenshots
RUN touch __init__.py server/__init__.py core/__init__.py \
    agents/__init__.py skills/__init__.py \
    memory/__init__.py soul/__init__.py

EXPOSE 8000

CMD ["python", "server/main.py"]
