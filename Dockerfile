# =============================================
# STAGE 1: Tailwind CSS Builder (Node.js)
# =============================================
FROM node:20-alpine AS tailwind-builder

WORKDIR /build

# Copy everything needed for Tailwind to scan + build
COPY web/static/ ./web/static/
COPY web/ ./web/

# Install Tailwind CLI + create config if missing
RUN npm install -D tailwindcss
RUN test -f tailwind.config.js || npx tailwindcss init

# Build production CSS (this is the exact command you asked for)
RUN npx tailwindcss -i ./web/static/input.css -o ./web/static/output.css --minify

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

# Copy Python backend + the *built* web folder (with output.css)
COPY --from=tailwind-builder /build/web ./web
COPY --from=tailwind-builder /build/*.py ./

# Web port from .env (single source of truth)
ARG WEB_PORT=8088
ENV WEB_PORT=${WEB_PORT}

EXPOSE ${WEB_PORT}

# Run with configurable port
CMD sh -c "uvicorn web.web_client:app --host 0.0.0.0 --port ${WEB_PORT} --log-level info"