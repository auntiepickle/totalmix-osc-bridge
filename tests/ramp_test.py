#!/usr/bin/env python3
import time
import subprocess
import os
import sys

# ================== AUTO-LOAD .env ==================
def load_dotenv():
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    if not os.path.exists(env_path):
        print(f"❌ .env file not found at {env_path}")
        sys.exit(1)
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            os.environ[key] = value
    print("✅ Loaded .env file")

load_dotenv()
# ===================================================

MQTT_HOST = "127.0.0.1"
MQTT_PORT = 1883
MQTT_USER = os.getenv("MQTT_USER")
MQTT_PASS = os.getenv("MQTT_PASS")
MACRO_TOPIC = "totalmix/macro/an12_to_aes_send"

DURATION = 3.42857      # exact 2 bars @ 140 BPM (8 beats)
STEPS_PER_SECOND = 50   # smooth fader movement

if not MQTT_USER or not MQTT_PASS:
    print("❌ ERROR: MQTT_USER or MQTT_PASS still missing after loading .env")
    print("   Check that your .env file contains:")
    print("   MQTT_USER=your_username")
    print("   MQTT_PASS=your_password")
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