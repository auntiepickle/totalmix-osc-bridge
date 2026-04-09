#!/bin/sh
set -e

echo "=== TotalMix OSC Bridge Startup (commit 91a66c9) ==="
if [ ! -f /app/web/static/style.css ] || [ -d /app/web/static/style.css ]; then
  echo "→ Restoring built style.css from image layers..."
  cp /static-assets/style.css /app/web/static/style.css
  chmod 644 /app/web/static/style.css
  echo "   style.css restored ($(wc -c < /app/web/static/style.css) bytes)"
fi

ls -la /app/web/static/ | grep style.css
exec "$@"
