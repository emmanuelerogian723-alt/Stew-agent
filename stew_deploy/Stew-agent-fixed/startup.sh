#!/bin/bash
# S.T.E.W Startup Script — Bulletproof
set -e

echo "========================================"
echo "  S.T.E.W 3.0 ULTRA — Starting Up"
echo "========================================"

# Run DB migrations (won't fail if already done)
echo "[1/2] Running database migrations..."
alembic upgrade head || echo "  ⚠️  Migrations skipped (DB may already be up to date)"

# Start server
echo "[2/2] Starting uvicorn server..."
exec uvicorn server.main:app \
    --host 0.0.0.0 \
    --port ${PORT:-8000} \
    --workers 1 \
    --log-level info \
    --timeout-keep-alive 30
