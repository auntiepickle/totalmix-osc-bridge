import os
import socket
import struct
import time
import paho.mqtt.client as mqtt

# ====================== CONFIG FROM DOCKER ENV ======================
OSC_IP = os.getenv('OSC_IP')
OSC_PORT = int(os.getenv('OSC_PORT', 7001))
MQTT_BROKER = os.getenv('MQTT_BROKER', 'mosquitto')
MQTT_PORT = int(os.getenv('MQTT_PORT', 1883))
MQTT_USER = os.getenv('MQTT_USER')
MQTT_PASS = os.getenv('MQTT_PASS')

print("=== TOTALMIX OSC BRIDGE STARTED ===")
print(f"OSC Target → {OSC_IP}:{OSC_PORT}")
print(f"MQTT Broker → {MQTT_BROKER}:{MQTT_PORT} | User: {MQTT_USER}")

if not all([OSC_IP, MQTT_USER, MQTT_PASS]):
    print("ERROR: Missing required env vars (OSC_IP, MQTT_USER, MQTT_PASS)")
    exit(1)

# ====================== OSC SENDER ======================
def send_osc(address, value=1.0):
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        addr_padded = address + '\0' * ((4 - len(address) % 4) % 4)
        type_tag = ' ,f\0\0'
        value_bytes = struct.pack('>f', float(value))
        message = addr_padded.encode() + type_tag.encode() + value_bytes
        sock.sendto(message, (OSC_IP, OSC_PORT))
        print(f"OSC SENT → {address} = {value}")
        sock.close()
    except Exception as e:
        print(f"OSC ERROR: {e}")

# ====================== MQTT ======================
def on_connect(client, userdata, flags, reason_code, properties):
    print(f"MQTT Connected (code {reason_code})")
    client.subscribe("totalmix/#")
    print("Subscribed to totalmix/#")

def on_message(client, userdata, msg):
    payload = msg.payload.decode().strip()
    print(f"MQTT RECEIVED → {msg.topic} | {payload}")

    try:
        if msg.topic == "totalmix/workspace":
            ws = int(payload)
            if 1 <= ws <= 30:
                send_osc("/loadQuickWorkspace", ws)
                print(f"→ LOADED WORKSPACE {ws}")
        elif msg.topic == "totalmix/snapshot":
            snap = int(payload)
            if 1 <= snap <= 8:
                slot = 9 - snap
                send_osc(f"/3/snapshots/{slot}/1")
                print(f"→ LOADED SNAPSHOT {snap}")
    except Exception as e:
        print(f"Message error: {e}")

# Start MQTT
client = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
client.username_pw_set(MQTT_USER, MQTT_PASS)
client.on_connect = on_connect
client.on_message = on_message
client.connect(MQTT_BROKER, MQTT_PORT, 60)
client.loop_start()

print("Bridge is running and listening...")
while True:
    time.sleep(60)