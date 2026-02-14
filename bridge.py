import os
import time
import paho.mqtt.client as mqtt
import threading
from config import *
from mqtt_handler import setup_mqtt

print("=== TOTALMIX OSC BRIDGE STARTED ===")
print(f"OSC → {OSC_IP}:{OSC_PORT}")
print(f"Loaded {len(WORKSPACE_NAMES)} workspaces")

# Create MQTT client
client = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2)

# Setup MQTT (passes config to handler)
setup_mqtt(client, MQTT_BROKER, MQTT_PORT, MQTT_USER, MQTT_PASS, OSC_IP, OSC_PORT)

# Start MQTT loop
client.loop_start()

print("Bridge is running...")
while True:
    time.sleep(30)
