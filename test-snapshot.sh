#!/bin/bash
# test-snapshot.sh - NOW WORKING (uses the exact raw pythonosc method that just succeeded)

if [ -z "$1" ]; then
  echo "Usage: ./test-snapshot.sh <1-8>"
  exit 1
fi

SNAP=$1
if ! [[ "$SNAP" =~ ^[1-8]$ ]]; then
  echo "Error: Snapshot must be 1-8"
  exit 1
fi

INDEX=$((9 - SNAP))
ADDRESS="/3/snapshots/${INDEX}/1"

echo "=== Loading .env ==="
set -a
source <(grep -v '^#' .env | grep -v '^$' | sed 's/\r$//')
set +a

IP=${OSC_IP:-127.0.0.1}

docker exec -it totalmix-osc-bridge python3 -c "
from pythonosc.udp_client import SimpleUDPClient
import os
ip = os.getenv('OSC_IP')
client = SimpleUDPClient(ip, 7001)
client.send_message('${ADDRESS}', 1.0)
print(f'✅ SENT ${ADDRESS} = 1.0 → Snapshot ${SNAP}')
"