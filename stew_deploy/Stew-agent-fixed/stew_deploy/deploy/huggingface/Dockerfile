# S.T.E.W — Hugging Face Spaces Dockerfile
# HF Spaces requires the app to run on port 7860

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
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# HF Spaces requires non-root user
RUN useradd -m -u 1000 stew
WORKDIR /home/stew/app
RUN chown -R stew:stew /home/stew/app

COPY --chown=stew:stew requirements.txt .
RUN pip install --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

COPY --chown=stew:stew server/ ./server/
COPY --chown=stew:stew migrations/ ./migrations/
COPY --chown=stew:stew alembic.ini .
COPY --chown=stew:stew deploy/huggingface/app.py .

USER stew
RUN mkdir -p logs output workspace

# HF Spaces port is 7860
EXPOSE 7860

CMD alembic upgrade head && python app.py
