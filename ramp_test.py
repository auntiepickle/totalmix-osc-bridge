#!/usr/bin/env python3
import time
import subprocess
import os
import sys

# ================== CONFIG (reads your existing shell variables) ==================
MQTT_HOST = "127.0.0.1"
MQTT_PORT = 1883
MQTT_USER = os.getenv("MQTT_USER")
MQTT_PASS = os.getenv("MQTT_PASS")
MACRO_TOPIC = "totalmix/macro/an12_to_aes_send"

DURATION = 3.42857      # exact 2 bars @ 140 BPM (8 beats)
STEPS_PER_SECOND = 50   # smooth fader movement
# =================================================================================

if not MQTT_USER or not MQTT_PASS:
    print("❌ ERROR: MQTT_USER or MQTT_PASS environment variables are not set!")
    print("   Make sure you have run: export MQTT_USER=youruser && export MQTT_PASS=yourpass")
    print("   Or source your .env / credentials file before running this script.")
    sys.exit(1)

def publish_param(value: float):
    payload = f"{value:.4f}"
    cmd = [
        "mosquitto_pub",
        "-h", MQTT_HOST,
        "-p", str(MQTT_PORT),
        "-u", MQTT_USER,
        "-P", MQTT_PASS,
        "-t", MACRO_TOPIC,
        "-m", payload
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as e:
        print(f"❌ Publish failed (exit {e.returncode}): {e.stderr.strip()}")
        sys.exit(1)
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        sys.exit(1)

print("🚀 Starting 0 → 1 → 0 ramp test (AN 1/2 → AES send slider)")
print(f"   Duration: {DURATION:.4f}s  |  Updates: ~{int(DURATION * STEPS_PER_SECOND)}")
print("   Watch submix 12 in Pill_setup/Reset — fader should glide smoothly.")

start_time = time.time()

for step in range(int(DURATION * STEPS_PER_SECOND) + 1):
    t = (time.time() - start_time) / DURATION
    if t > 1.0:
        break
    # Triangle ramp: 0 → 1 at end of bar 1, then 1 → 0 at end of bar 2
    if t < 0.5:
        param = t * 2.0
    else:
        param = 2.0 - (t * 2.0)

    publish_param(param)
    time.sleep(1.0 / STEPS_PER_SECOND)

# Final exact zero
publish_param(0.0)
print("✅ Ramp test complete — slider back to 0.0")