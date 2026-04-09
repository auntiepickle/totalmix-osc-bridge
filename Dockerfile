# =============================================
# STAGE 1: Tailwind v4 Builder (long-term clean name)
# =============================================
FROM node:20 AS tailwind-builder

WORKDIR /build

COPY web/static/ ./web/static/
COPY web/ ./web/
COPY tailwind.config.js ./

RUN npm init -y
RUN npm install -D tailwindcss@latest @tailwindcss/cli

# Defensive cleanup + build to style.css
RUN rm -rf ./web/static/style.css
RUN npx @tailwindcss/cli --cwd /build \
    -i ./web/static/input.css \
    -o ./web/static/style.css \
    --minify \
    --config ./tailwind.config.js

# Debug
RUN echo "=== BUILDER FINAL CHECK ===" && ls -la ./web/static/ && wc -c ./web/static/style.css || echo "style.css MISSING IN BUILDER"

# =============================================
# STAGE 2: Python Runtime
# =============================================
FROM python:3.12-slim

RUN apt-get update && apt-get install -y libasound2-dev && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt

# Explicit static copy
COPY --from=tailwind-builder /build/web/static ./web/static

# Final debug
RUN echo "=== FINAL IMAGE STATIC FILES ===" && ls -la /app/web/static/ && echo "=== END FINAL DEBUG ==="

COPY --from=tailwind-builder /build/*.py ./

ARG WEB_PORT=8088
ENV WEB_PORT=${WEB_PORT}

EXPOSE ${WEB_PORT}

CMD sh -c "uvicorn web.web_client:app --host 0.0.0.0 --port ${WEB_PORT} --log-level info"
