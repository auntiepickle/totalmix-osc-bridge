import os
import socket
import struct
import time
import paho.mqtt.client as mqtt

print("=== BRIDGE STARTING ===")

OSC_IP = os.getenv('OSC_IP')
OSC_PORT = int(os.getenv('OSC_PORT', 7001))
MQTT_BROKER = os.getenv('MQTT_BROKER', 'mosquitto')
MQTT_PORT = int(os.getenv('MQTT_PORT', 1883))
MQTT_USER = os.getenv('MQTT_USER')
MQTT_PASS = os.getenv('MQTT_PASS')

print(f"OSC → {OSC_IP}:{OSC_PORT}")
print(f"MQTT → {MQTT_BROKER}:{MQTT_PORT} (user: {MQTT_USER})")

if not OSC_IP or not MQTT_USER or not MQTT_PASS:
    print("ERROR: Missing env vars!")
    exit(1)

def send_osc(address, value=1.0):
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        addr_padded = address + '\0' * ((4 - len(address) % 4) % 4)
        msg = addr_padded.encode() + b',f\0\0' + struct.pack('>f', float(value))
        sock.sendto(msg, (OSC_IP, OSC_PORT))
        print(f"OSC SENT → {address} = {value}")
    except Exception as e:
        print(f"OSC FAIL: {e}")

def on_connect(client, userdata, flags, rc, properties=None):
    print(f"MQTT CONNECTED (code {rc})")
    client.subscribe("totalmix/#")
    print("Subscribed to totalmix/#")

def on_message(client, userdata, msg):
    payload = msg.payload.decode().strip()
    print(f"MQTT IN → {msg.topic} | {payload}")
    try:
        if msg.topic == "totalmix/workspace":
            ws = int(payload)
            send_osc("/loadQuickWorkspace", ws)
            print(f"→ WORKSPACE {ws} TRIGGERED")
        elif msg.topic == "totalmix/snapshot":
            snap = int(payload)
            slot = 9 - snap
            send_osc(f"/3/snapshots/{slot}/1")
            print(f"→ SNAPSHOT {snap} TRIGGERED")
    except Exception as e:
        print(f"Handler error: {e}")

client = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
client.username_pw_set(MQTT_USER, MQTT_PASS)
client.on_connect = on_connect
client.on_message = on_message

print("Connecting to MQTT...")
client.connect(MQTT_BROKER, MQTT_PORT, 60)
client.loop_start()

print("Bridge is now running...")
while True:
    time.sleep(30)