import os
import socket
import struct
import time
from dotenv import load_dotenv
import paho.mqtt.client as mqtt

load_dotenv()

OSC_IP = os.getenv('OSC_IP')
OSC_PORT = int(os.getenv('OSC_PORT', 7001))
MQTT_BROKER = os.getenv('MQTT_BROKER', 'mosquitto')
MQTT_PORT = int(os.getenv('MQTT_PORT', 1883))
MQTT_USER = os.getenv('MQTT_USER')
MQTT_PASS = os.getenv('MQTT_PASS')

print("=== BRIDGE STARTED ===")
print(f"OSC Target: {OSC_IP}:{OSC_PORT}")
print(f"MQTT Broker: {MQTT_BROKER}")
print(f"MQTT User: {MQTT_USER}")

def send_osc(address, value=1.0):
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        addr_padded = address + '\0' * ((4 - len(address) % 4) % 4)
        type_tag = ' ,f\0\0'
        value_bytes = struct.pack('>f', float(value))
        message = addr_padded.encode() + type_tag.encode() + value_bytes
        sock.sendto(message, (OSC_IP, OSC_PORT))
        print(f"OSC SENT: {address} = {value}")
        sock.close()
    except Exception as e:
        print(f"OSC ERROR: {e}")

def on_connect(client, userdata, flags, reason_code, properties):
    print(f"CONNECT RESULT: {reason_code} (0 = success)")
    if reason_code == 0:
        client.subscribe("totalmix/#")
        print("SUBSCRIBED to totalmix/# (all topics)")

def on_message(client, userdata, msg):
    print(f"RECEIVED MQTT → Topic: {msg.topic} | Payload: {msg.payload.decode()}")

client = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
client.username_pw_set(MQTT_USER, MQTT_PASS)
client.on_connect = on_connect
client.on_message = on_message
client.connect(MQTT_BROKER, MQTT_PORT, 60)
client.loop_start()

print("Bridge is now listening...")
while True:
    time.sleep(60)