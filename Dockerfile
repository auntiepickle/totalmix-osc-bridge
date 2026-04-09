# =============================================
# STAGE 1: Tailwind CSS Builder (Node.js) – v3 pinned
# =============================================
FROM node:20-alpine AS tailwind-builder

WORKDIR /build

# Copy all web assets + Tailwind config (exact structure from commit e12ddbad1dc8e764d7b22979124c774db9f7e7b2)
COPY web/static/ ./web/static/
COPY web/ ./web/
COPY tailwind.config.js ./

# Create minimal package.json + install Tailwind v3 (this fixes the npx executable error)
RUN npm init -y
RUN npm install -D tailwindcss@3

# Build production CSS with v3 CLI
RUN npx tailwindcss@3 -i ./web/static/input.css -o ./web/static/output.css --minify

# =============================================
# STAGE 2: Python Runtime (final slim image)
# =============================================
FROM python:3.12-slim

# System deps for MIDI/ALSA
RUN apt-get update && apt-get install -y libasound2-dev && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Python dependencies
COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt

# Copy built web folder (now includes output.css) + Python backend
COPY --from=tailwind-builder /build/web ./web
COPY --from=tailwind-builder /build/*.py ./

# Web port from .env (single source of truth)
ARG WEB_PORT=8088
ENV WEB_PORT=${WEB_PORT}

EXPOSE ${WEB_PORT}

# Run with configurable port
CMD sh -c "uvicorn web.web_client:app --host 0.0.0.0 --port ${WEB_PORT} --log-level info"
