import os
import time
from config import *
from mqtt_handler import setup_mqtt
from osc_monitor import osc_monitor   # ← NEW

print("=== TOTALMIX OSC BRIDGE STARTED ===")
print(f"OSC → {OSC_IP}:{OSC_PORT}")
print(f"Loaded {len(WORKSPACE_NAMES)} workspaces")

# Create MQTT client
client = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2)

# Setup MQTT
setup_mqtt(client, MQTT_BROKER, MQTT_PORT, MQTT_USER, MQTT_PASS, OSC_IP, OSC_PORT)

# Start OSC Monitor if enabled (perfect for learning mappings)
if ENABLE_OSC_MONITOR:
    osc_monitor.start()

# Start MQTT loop
client.loop_start()

print("Bridge is running... (Ctrl+C to stop)")

try:
    while True:
        time.sleep(30)
except KeyboardInterrupt:
    print("\nShutting down...")
    if ENABLE_OSC_MONITOR:
        osc_monitor.stop()
    print("Bridge stopped.")