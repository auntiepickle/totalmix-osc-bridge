import os
import socket
import struct
import time
from dotenv import load_dotenv
import paho.mqtt.client as mqtt

load_dotenv()

# ================== CONFIG ==================
OSC_IP = os.getenv('OSC_IP')
OSC_PORT = int(os.getenv('OSC_PORT', 7001))

MQTT_BROKER = os.getenv('MQTT_BROKER', 'mosquitto')
MQTT_PORT = int(os.getenv('MQTT_PORT', 1883))
MQTT_USER = os.getenv('MQTT_USER', 'mosquitto')

# Read password from mosquitto's pwfile
PWFILE = "/mosquitto/config/pwfile"

def get_password(username):
    try:
        with open(PWFILE, 'r') as f:
            for line in f:
                if line.startswith(username + ':'):
                    return line.split(':', 1)[1].strip()
        raise ValueError(f"User '{username}' not found in {PWFILE}")
    except Exception as e:
        print(f"Failed to read password from {PWFILE}: {e}")
        return None

MQTT_PASS = get_password(MQTT_USER)

if not MQTT_PASS:
    raise ValueError("Could not load MQTT password from pwfile")

MQTT_TOPIC_WORKSPACE = os.getenv('MQTT_TOPIC_WORKSPACE', 'totalmix/workspace')
MQTT_TOPIC_SNAPSHOT  = os.getenv('MQTT_TOPIC_SNAPSHOT',  'totalmix/snapshot')

SNAPSHOT_SLOTS = {1:8, 2:7, 3:6, 4:5, 5:4, 6:3, 7:2, 8:1}

print(f"Loaded MQTT user: {MQTT_USER}")
print(f"OSC Target: {OSC_IP}:{OSC_PORT}")

def send_osc(address: str, value: float = 1.0):
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        addr_padded = address + '\0' * ((4 - len(address) % 4) % 4)
        type_tag = ' ,f\0\0'
        value_bytes = struct.pack('>f', float(value))
        message = addr_padded.encode() + type_tag.encode() + value_bytes
        sock.sendto(message, (OSC_IP, OSC_PORT))
        sock.close()
        time.sleep(0.01)
    except Exception as e:
        print(f"OSC send error: {e}")

def on_connect(client, userdata, flags, reason_code, properties):
    if reason_code == 0:
        print(f"Connected to MQTT broker: {MQTT_BROKER}")
        client.subscribe(MQTT_TOPIC_WORKSPACE)
        client.subscribe(MQTT_TOPIC_SNAPSHOT)
    else:
        print(f"Failed to connect, reason: {reason_code}")

def on_message(client, userdata, msg):
    try:
        payload = msg.payload.decode().strip()
        topic = msg.topic
        value = int(payload)

        if topic == MQTT_TOPIC_WORKSPACE and 1 <= value <= 30:
            send_osc("/loadQuickWorkspace", value)
            print(f"→ Loaded Workspace {value}")
        elif topic == MQTT_TOPIC_SNAPSHOT and 1 <= value <= 8:
            slot = SNAPSHOT_SLOTS.get(value, 8)
            send_osc(f"/3/snapshots/{slot}/1")
            print(f"→ Loaded Snapshot {value}")
    except Exception as e:
        print(f"Message error: {e}")

client = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
client.username_pw_set(MQTT_USER, MQTT_PASS)
client.on_connect = on_connect
client.on_message = on_message
client.connect(MQTT_BROKER, MQTT_PORT, 60)
client.loop_start()

print("TotalMix OSC Bridge started successfully")
while True:
    time.sleep(60)