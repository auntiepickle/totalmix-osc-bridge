import paho.mqtt.client as mqtt
import json
import time
import threading
from osc import send_osc
from workspaces import publish_workspaces
from pathlib import Path

# ========================================================
# SNAPSHOT MAP — loaded from mounted SMB share + live watcher
# ========================================================
SNAPSHOT_MAP = {}
MAP_PATH = Path("/app/config/ufx2_snapshot_map.json")
LAST_MTIME = 0

def load_snapshot_map():
    global SNAPSHOT_MAP, LAST_MTIME
    try:
        mtime = MAP_PATH.stat().st_mtime
        if mtime == LAST_MTIME:
            return False  # no change

        with open(MAP_PATH, "r", encoding="utf-8-sig") as f:
            SNAPSHOT_MAP = json.load(f)
        LAST_MTIME = mtime
        print(f"✅ Loaded snapshot map with {len(SNAPSHOT_MAP)} workspaces from SMB mount")
        return True
    except Exception as e:
        print(f"⚠️ Could not load snapshot map from SMB: {e}")
        return False

def publish_snapshot_map(client):
    if SNAPSHOT_MAP:
        try:
            client.publish("totalmix/snapshot_map", json.dumps(SNAPSHOT_MAP), retain=True)
            print(f"Published snapshot map with {len(SNAPSHOT_MAP)} workspaces to totalmix/snapshot_map")
        except Exception as e:
            print(f"Error publishing snapshot map: {e}")

def publish_dynamic_workspaces(client):
    """Publish workspace list dynamically from SNAPSHOT_MAP keys (preserves JSON key order = slot order)"""
    if not SNAPSHOT_MAP:
        print("⚠️ No snapshot map loaded yet — skipping dynamic workspaces")
        return
    workspace_names = list(SNAPSHOT_MAP.keys())
    try:
        client.publish("totalmix/workspaces", json.dumps(workspace_names), retain=True, qos=1)
        clean_names = [name for name in workspace_names if name and name != "<Empty>"]
        print(f"✅ Published DYNAMIC workspaces: {len(workspace_names)} total slots, {len(clean_names)} named")
    except Exception as e:
        print(f"Error publishing dynamic workspaces: {e}")

def map_watcher(client):
    """Background thread that watches the SMB file and republishes on change"""
    global LAST_MTIME
    print("Started background snapshot map watcher (checks every 5s)")
    while True:
        time.sleep(5)
        if load_snapshot_map():
            publish_snapshot_map(client)
            publish_dynamic_workspaces(client)   # ← this is what updates the dropdown live

def setup_mqtt(client, mqtt_broker, mqtt_port, mqtt_user, mqtt_pass, osc_ip, osc_port):
    def on_connect(client, userdata, flags, rc, properties=None):
        print(f"MQTT CONNECTED (code {rc})")
        client.subscribe("totalmix/#")
        print("Subscribed to totalmix/#")
        
        publish_workspaces(client)           # legacy fallback (now safe)
        load_snapshot_map()                  # initial load
        publish_snapshot_map(client)         # initial publish
        publish_dynamic_workspaces(client)   # ← initial dynamic workspaces

    def on_message(client, userdata, msg):
        payload = msg.payload.decode().strip()
        print(f"MQTT IN → {msg.topic} | {payload}")

        try:
            if msg.topic == "totalmix/workspace":
                ws = int(payload)
                send_osc("/loadQuickWorkspace", ws, osc_ip, osc_port)
                print(f"→ WORKSPACE {ws} LOADED")

            elif msg.topic == "totalmix/snapshot":
                snap_num = int(payload)
                if 1 <= snap_num <= 8:
                    index = 9 - snap_num
                    address = f"/3/snapshots/{index}/1"
                    send_osc(address, 1.0, osc_ip, osc_port)
                    print(f"→ SNAPSHOT #{snap_num} RECALLED (OSC: {address})")
                    client.publish("totalmix/snapshot/status", f"loaded_{snap_num}", retain=True)
                else:
                    print(f"⚠️ Invalid snapshot number: {payload}")

            elif msg.topic == "totalmix/config/snapshot_map":
                global SNAPSHOT_MAP
                SNAPSHOT_MAP = json.loads(payload)
                print(f"✅ Snapshot map updated from scraper ({len(SNAPSHOT_MAP)} workspaces)")
                publish_snapshot_map(client)
                publish_dynamic_workspaces(client)

        except Exception as e:
            print(f"Handler error: {e}")

    client.on_connect = on_connect
    client.on_message = on_message
    client.username_pw_set(mqtt_user, mqtt_pass)
    client.connect(mqtt_broker, mqtt_port, 60)

    # Start background watcher thread
    watcher_thread = threading.Thread(target=map_watcher, args=(client,), daemon=True)
    watcher_thread.start()