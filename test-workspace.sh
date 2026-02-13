#!/bin/bash

echo "=== .env Loading Debug ==="

# Load .env safely
set -a
source <(grep -v '^#' .env | grep -v '^$' | sed 's/\r$//')
set +a

# Redact password automatically
if [ -n "$MQTT_PASS" ]; then
  REDACTED_PASS="${MQTT_PASS:0:4}****"
else
  REDACTED_PASS="EMPTY"
fi

echo "MQTT_USER     = '${MQTT_USER:-EMPTY}'"
echo "MQTT_PASS     = '${REDACTED_PASS}'"
echo "OSC_IP        = '${OSC_IP:-EMPTY}'"
echo "MQTT_BROKER   = '${MQTT_BROKER:-EMPTY}'"

if [ -z "$MQTT_USER" ] || [ -z "$MQTT_PASS" ] || [ -z "$OSC_IP" ]; then
  echo "ERROR: One or more required variables are missing!"
  exit 1
fi

echo "=== Sending test command ==="
mosquitto_pub -h 127.0.0.1 \
  -u "$MQTT_USER" \
  -P "$MQTT_PASS" \
  -t totalmix/workspace \
  -m "$1"

echo "→ Sent workspace request: $1"
