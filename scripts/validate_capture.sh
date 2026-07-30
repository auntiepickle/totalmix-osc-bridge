#!/bin/bash
# validate_capture.sh — end-to-end check of OSC device capture + discovery.
# Run on the server that hosts the bridge container.
#
#   ./scripts/validate_capture.sh            # validate + run discovery walk
#   ./scripts/validate_capture.sh --apply    # ...and promote the result to
#                                            # the live ufx2_channel_map.json
#
# Exits non-zero at the first failed step with a diagnosis.

set -u
WEB_PORT="${WEB_PORT:-8088}"
BASE="http://localhost:${WEB_PORT}"
CONTAINER="${CONTAINER:-totalmix-osc-bridge}"

say()  { printf '\n=== %s ===\n' "$1"; }
fail() { printf 'FAIL: %s\n' "$1"; exit 1; }

json() {  # pretty-print stdin if python3 exists, else pass through
  if command -v python3 >/dev/null; then python3 -m json.tool; else cat; fi
}

say "1/5 Container is up"
docker ps --format '{{.Names}}' | grep -qx "$CONTAINER" \
  || fail "container '$CONTAINER' is not running (docker compose up -d?)"
echo "ok: $CONTAINER running"

say "2/5 OSC listener bound"
if docker logs "$CONTAINER" 2>&1 | grep -i "OSC listener" | tail -3 | grep -q "started"; then
  docker logs "$CONTAINER" 2>&1 | grep -i "OSC listener started" | tail -1
else
  docker logs "$CONTAINER" 2>&1 | grep -i "OSC listener" | tail -3
  fail "listener did not start — port conflict? (check ENABLE_OSC_MONITOR, ss -ulnp | grep 9001)"
fi

say "3/5 TotalMix feedback is arriving"
STATE=$(curl -sf "$BASE/api/device/state") \
  || fail "GET /api/device/state failed — is the web UI on port $WEB_PORT?"
COUNT=$(echo "$STATE" | { python3 -c 'import json,sys; print(len(json.load(sys.stdin)["raw_addresses"]))' 2>/dev/null || echo 0; })
if [ "$COUNT" -eq 0 ]; then
  echo "$STATE" | json
  fail "no OSC feedback captured yet.
  -> Wiggle a fader in TotalMix FX, then re-run.
  -> Still nothing? TotalMix: Options > Settings > OSC:
     'In Use' checked, Port outgoing = 9001, IP = this server's IP."
fi
echo "ok: $COUNT distinct OSC addresses captured"
echo "$STATE" | { python3 -c 'import json,sys; d=json.load(sys.stdin); print("current submix:", d["current_submix"]); print("submixes seen:", list(d["submixes"]))' 2>/dev/null || true; }

say "4/5 Discovery walk (~20s — TotalMix will flip through submixes)"
curl -sf -X POST "$BASE/api/device/discover" \
  -H 'Content-Type: application/json' -d '{"submix_count": 16, "settle_s": 1.0}' | json \
  || fail "could not start discovery (409 = already running; wait and retry)"
for _ in $(seq 1 30); do
  sleep 2
  STATUS=$(curl -sf "$BASE/api/device/discovery" | { python3 -c 'import json,sys; print(json.load(sys.stdin)["status"])' 2>/dev/null || echo unknown; })
  [ "$STATUS" != "running" ] && break
done
RESULT=$(curl -sf "$BASE/api/device/discovery")
[ "$STATUS" = "done" ] || { echo "$RESULT" | json; fail "discovery ended with status '$STATUS'"; }

say "5/5 Result"
echo "$RESULT" | { python3 -c '
import json, sys
d = json.load(sys.stdin)
print(f"submixes discovered: {d[\"submixes\"]}")
for e in d["walk_log"]:
    mark = f"SKIPPED ({e[\"skipped\"]})" if "skipped" in e else "ok"
    print(f"  index {e[\"index\"]:>2}: {str(e.get(\"label\")):<20} {mark}")
fallback = sum(1 for s in d["channel_map"]["submixes"].values()
               for name in s["sends"] if name.startswith("Row "))
total = sum(len(s["sends"]) for s in d["channel_map"]["submixes"].values())
print(f"sends: {total} total, {fallback} with fallback names")
if fallback:
    print("NOTE: fallback names mean tracknames were not parsed — share")
    print("      /api/device/state raw_addresses so the parser can be adapted.")
' 2>/dev/null || echo "$RESULT" | json | head -60; }
echo
echo "Draft saved to discovered_channel_map.json"

if [ "${1:-}" = "--apply" ]; then
  say "Applying to live ufx2_channel_map.json (auto-backup first)"
  curl -sf -X POST "$BASE/api/device/discovery/apply" | json || fail "apply failed"
else
  echo "Happy with it? Promote it:  ./scripts/validate_capture.sh --apply"
  echo "                       or:  curl -X POST $BASE/api/device/discovery/apply"
fi
