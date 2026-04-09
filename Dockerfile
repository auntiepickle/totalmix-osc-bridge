# =============================================
# STAGE 1: Tailwind v4 Builder (fully automated, no more npx errors)
# =============================================
FROM node:20 AS tailwind-builder

WORKDIR /build

# Copy web files + config (exact structure from commit e12ddbad1dc8e764d7b22979124c774db9f7e7b2)
COPY web/static/ ./web/static/
COPY web/ ./web/
COPY tailwind.config.js ./

# Setup + install BOTH packages needed for v4
RUN npm init -y
RUN npm install -D tailwindcss@latest @tailwindcss/cli

# Build with the CORRECT v4 CLI (this is the fix for the "could not determine executable" error)
RUN npx @tailwindcss/cli -i ./web/static/input.css -o ./web/static/output.css --minify

# Debug output so you can see the file was created
RUN echo "=== TAILWIND v4 BUILD COMPLETE ===" && ls -la ./web/static/

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

# Run
CMD sh -c "uvicorn web.web_client:app --host 0.0.0.0 --port ${WEB_PORT} --log-level info"