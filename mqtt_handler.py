import paho.mqtt.client as mqtt
from osc import send_osc
from workspaces import publish_workspaces

def setup_mqtt(client, mqtt_broker, mqtt_port, mqtt_user, mqtt_pass, osc_ip, osc_port):
    def on_connect(client, userdata, flags, rc, properties=None):
        print(f"MQTT CONNECTED (code {rc})")
        client.subscribe("totalmix/#")
        print("Subscribed to totalmix/#")
        publish_workspaces(client)  # Send names on connect

    def on_message(client, userdata, msg):
        payload = msg.payload.decode().strip()
        print(f"MQTT IN → {msg.topic} | {payload}")
        try:
            if msg.topic == "totalmix/workspace":
                ws = int(payload)
                send_osc("/loadQuickWorkspace", ws, osc_ip, osc_port)
                print(f"→ WORKSPACE {ws} LOADED")
        except Exception as e:
            print(f"Handler error: {e}")

    client.on_connect = on_connect
    client.on_message = on_message
    client.username_pw_set(mqtt_user, mqtt_pass)
    client.connect(mqtt_broker, mqtt_port, 60)
