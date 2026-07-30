#!/bin/sh
set -e

# Dynamic commit from git (works in Docker build)
if [ -d "/app/.git" ]; then
  GIT_COMMIT=$(git rev-parse --short HEAD 2>/dev/null || echo "unknown")
else
  GIT_COMMIT="docker-build"
fi

echo "=== TotalMix OSC Bridge Startup (commit $GIT_COMMIT) ==="

# Always restore: /app is bind-mounted, so a style.css from a previous run
# persists on the host and an existence check would let stale CSS shadow a
# freshly rebuilt image. The image's build is authoritative.
echo "→ Restoring built style.css from image layers..."
rm -rf /app/web/static/style.css
cp /static-assets/style.css /app/web/static/style.css
chmod 644 /app/web/static/style.css
echo "   style.css restored ($(wc -c < /app/web/static/style.css) bytes)"

ls -la /app/web/static/ | grep style.css
exec "$@"