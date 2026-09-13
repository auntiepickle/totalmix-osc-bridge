#!/bin/sh
set -e

# Commit of the bind-mounted checkout, read straight from .git: the slim
# runtime image has no git binary, so `git rev-parse` would always say unknown.
# HEAD -> ref file -> packed-refs (a detached HEAD holds the SHA itself).
if [ -d "/app/.git" ]; then
  GIT_COMMIT="unknown"
  head=$(cat /app/.git/HEAD 2>/dev/null || true)
  case "$head" in
    ref:*)
      ref=${head#ref: }
      if [ -f "/app/.git/$ref" ]; then
        GIT_COMMIT=$(cut -c1-7 "/app/.git/$ref")
      elif [ -f /app/.git/packed-refs ]; then
        GIT_COMMIT=$(grep " $ref\$" /app/.git/packed-refs | cut -c1-7)
      fi
      ;;
    *)
      [ -n "$head" ] && GIT_COMMIT=$(printf '%s' "$head" | cut -c1-7)
      ;;
  esac
  [ -n "$GIT_COMMIT" ] || GIT_COMMIT="unknown"
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