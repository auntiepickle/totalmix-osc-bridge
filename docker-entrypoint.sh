#!/bin/sh
set -e

# Dynamic commit from git (works in Docker build)
if [ -d "/app/.git" ]; then
  GIT_COMMIT=$(git rev-parse --short HEAD 2>/dev/null || echo "unknown")
else
  GIT_COMMIT="docker-build"
fi

echo "=== TotalMix OSC Bridge Startup (commit $GIT_COMMIT) ==="

if [ ! -f /app/web/static/style.css ] || [ -d /app/web/static/style.css ]; then
  echo "→ Restoring built style.css from image layers..."
  cp /static-assets/style.css /app/web/static/style.css
  chmod 644 /app/web/static/style.css
  echo "   style.css restored ($(wc -c < /app/web/static/style.css) bytes)"
fi

ls -la /app/web/static/ | grep style.css
exec "$@"