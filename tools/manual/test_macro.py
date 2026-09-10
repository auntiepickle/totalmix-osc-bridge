#!/usr/bin/env python3
# test_macro.py - reliable macro trigger from host (uses real .env credentials)
import paho.mqtt.client as mqtt
import time
import os
import sys

if len(sys.argv) < 2:
    print("Usage: python3 test_macro.py <macro_name> [param]")
    print("Example: python3 test_macro.py an12_to_aes_send 0.75")
    sys.exit(1)

macro_name = sys.argv[1]
param = sys.argv[2] if len(sys.argv) > 2 else "0.5"

# Load credentials exactly like the bridge does
os.environ.setdefault("MQTT_USER", "unknown")
os.environ.setdefault("MQTT_PASS", "unknown")
# If .env exists, source it (works in shell too)
if os.path.exists(".env"):
    with open(".env") as f:
        for line in f:
            if line.strip() and not line.startswith("#"):
                key, value = line.strip().split("=", 1)
                os.environ[key.strip()] = value.strip().strip('"').strip("'")

client = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
client.username_pw_set(os.getenv("MQTT_USER"), os.getenv("MQTT_PASS"))
client.connect("127.0.0.1", 1883)

client.loop_start()
time.sleep(0.5)

client.publish(f"totalmix/macro/{macro_name}", param)
print(f"📤 Triggered → totalmix/macro/{macro_name} | param={param}")

time.sleep(1)
client.loop_stop()