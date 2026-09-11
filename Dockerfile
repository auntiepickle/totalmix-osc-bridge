# =============================================
# STAGE 1: Tailwind v4 Builder (long-term clean name)
# =============================================
FROM node:20 AS tailwind-builder

WORKDIR /build

COPY web/static/ ./web/static/
COPY web/ ./web/

RUN npm init -y
# Pinned: an unpinned @latest made style.css differ from build to build
RUN npm install -D tailwindcss@4.3.3 @tailwindcss/cli@4.3.3

# Defensive cleanup + build to style.css
RUN rm -rf ./web/static/style.css
RUN npx @tailwindcss/cli --cwd /build \
    -i ./web/static/input.css \
    -o ./web/static/style.css \
    --minify

# Debug
RUN echo "=== BUILDER FINAL CHECK ===" && ls -la ./web/static/ && wc -c ./web/static/style.css || echo "style.css MISSING IN BUILDER"

# =============================================
# STAGE 2: Python Runtime
# =============================================
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt

# Explicit static copy
COPY --from=tailwind-builder /build/web/static ./web/static

# Final debug
RUN echo "=== FINAL IMAGE STATIC FILES ===" && ls -la /app/web/static/ && echo "=== END FINAL DEBUG ==="

# Server code (the live server bind-mounts the repo over /app anyway)
COPY tmosc ./tmosc
COPY web/__init__.py web/web_client.py ./web/
COPY examples ./examples

ARG WEB_PORT=8088
ENV WEB_PORT=${WEB_PORT}

EXPOSE ${WEB_PORT}

# Protect built assets for dev volume mounts
COPY --from=tailwind-builder /build/web/static/style.css /static-assets/style.css

COPY deploy/docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh
ENTRYPOINT ["/docker-entrypoint.sh"]

CMD ["sh", "-c", "uvicorn tmosc.api.app:app --host 0.0.0.0 --port ${WEB_PORT} --log-level info"]
