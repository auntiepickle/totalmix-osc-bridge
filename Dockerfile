# =============================================
# STAGE 1: Tailwind CSS Builder (Node.js)
# =============================================
FROM node:20-alpine AS tailwind-builder

WORKDIR /build

# Copy web assets so Tailwind can scan .html/.js files
COPY web/static/ ./web/static/
COPY web/ ./web/

# Install Tailwind (classic v3 for full compatibility)
RUN npm install -D tailwindcss

# Create tailwind.config.js manually (bypasses npx init bug in Alpine)
RUN cat > tailwind.config.js << 'EOF'
/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    "./web/static/**/*.html",
    "./web/static/**/*.js",
  ],
  theme: {
    extend: {},
  },
  plugins: [],
}
EOF

# Build production CSS (exact command you asked for, now reliable)
RUN ./node_modules/.bin/tailwindcss -i ./web/static/input.css -o ./web/static/output.css --minify

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

# Copy the *built* web folder (with output.css) + Python backend
COPY --from=tailwind-builder /build/web ./web
COPY --from=tailwind-builder /build/*.py ./

# Web port from .env (single source of truth)
ARG WEB_PORT=8088
ENV WEB_PORT=${WEB_PORT}

EXPOSE ${WEB_PORT}

# Run with configurable port
CMD sh -c "uvicorn web.web_client:app --host 0.0.0.0 --port ${WEB_PORT} --log-level info"