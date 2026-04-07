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
    """Publish workspace list + REAL physical slot indices.
    NOW SORTED BY SLOT INDEX so HA dropdown always matches TotalMix Quick Select order."""
    if not SNAPSHOT_MAP:
        print("No snapshot map loaded yet — skipping dynamic workspaces")
        return

    workspace_list = []
    for name, data in SNAPSHOT_MAP.items():
        if not isinstance(data, dict):
            continue
        slot = data.get("slot")
        if slot is None:
            print(f"⚠️  Workspace '{name}' missing 'slot' — falling back to list order (temporary)")
            slot = len(workspace_list) + 1
        if name and name.strip() and name != "<Empty>":
            workspace_list.append({
                "name": name,
                "index": int(slot)
            })

    workspace_list.sort(key=lambda x: x["index"])

    try:
        payload = json.dumps(workspace_list)
        client.publish("totalmix/workspaces", payload, retain=True, qos=1)
        print(f"✅ Published DYNAMIC workspaces (SORTED by slot): {len(workspace_list)} named slots")
        print(f"   Order: {[item['name'] for item in workspace_list[:8]]} ...")
    except Exception as e:
        print(f"Error publishing dynamic workspaces: {e}")
        
def map_watcher(client):
    global LAST_MTIME
    print("Started background snapshot map watcher (checks every 5s)")
    while True:
        time.sleep(5)
        if load_snapshot_map():
            publish_snapshot_map(client)
            publish_dynamic_workspaces(client)

def setup_mqtt(client, mqtt_broker, mqtt_port, mqtt_user, mqtt_pass, osc_ip, osc_port, bridge):
    def on_connect(client, userdata, flags, rc, properties=None):
        print(f"MQTT CONNECTED (code {rc})")
        client.subscribe("totalmix/#")
        client.subscribe("totalmix/macro/#")
        print("Subscribed to totalmix/# and totalmix/macro/#")

        publish_workspaces(client)
        load_snapshot_map()
        publish_snapshot_map(client)
        publish_dynamic_workspaces(client)

    def on_message(client, userdata, msg):
        global SNAPSHOT_MAP
        payload = msg.payload.decode().strip()

        # === TIME-BASED COOLDOWN + SUPPRESSION (kills retained-message feedback loop) ===
        if getattr(bridge, '_last_macro_end_time', 0) > 0 and time.time() - bridge._last_macro_end_time < 2.5:
            if msg.topic in ("totalmix/workspace", "totalmix/snapshot"):
                print(f"→ Suppressed handler for {msg.topic} (cooldown after macro)")
                return

        # === CLEAN LOGGING ===
        if msg.topic.startswith("totalmix/macro/") or "workspace" in msg.topic or "snapshot" in msg.topic:
            print(f"MQTT → {msg.topic} | {payload}")

        try:
            # === HA WORKSPACE / SNAPSHOT → resolve friendly name + sync bridge state ===
            if msg.topic == "totalmix/workspace":
                try:
                    ws_slot = int(payload)
                    # Skip OSC if already on this workspace (prevents double-fire)
                    if getattr(bridge, 'current_workspace', None) and bridge.current_workspace != "unknown":
                        # We still publish state but skip re-load if already correct
                        ws_name = next(
                            (name for name, data in SNAPSHOT_MAP.items()
                             if isinstance(data, dict) and data.get("slot") == ws_slot),
                            None
                        )
                        if ws_name == bridge.current_workspace:
                            print(f"→ WORKSPACE slot {ws_slot} already current — skipping OSC")
                            bridge.update_workspace(name=ws_name)
                            return
                    send_osc("/loadQuickWorkspace", ws_slot, osc_ip, osc_port)
                    print(f"→ WORKSPACE slot {ws_slot} LOADED")

                    ws_name = next(
                        (name for name, data in SNAPSHOT_MAP.items()
                         if isinstance(data, dict) and data.get("slot") == ws_slot),
                        f"slot_{ws_slot}"
                    )
                    bridge.update_workspace(name=ws_name)
                except ValueError:
                    print(f"→ Ignored non-integer workspace payload: {payload} (safe)")

            elif msg.topic == "totalmix/snapshot":
                try:
                    snap_num = int(payload)
                    if 1 <= snap_num <= 8:
                        # Skip OSC if already on this snapshot
                        if getattr(bridge, 'current_snapshot', None) == "Reset" and snap_num == 4:
                            print(f"→ SNAPSHOT #{snap_num} already current — skipping OSC")
                            client.publish("totalmix/snapshot/status", f"loaded_{snap_num}", retain=True)
                            return

                        index = 9 - snap_num
                        address = f"/3/snapshots/{index}/1"
                        send_osc(address, 1.0, osc_ip, osc_port)
                        print(f"→ SNAPSHOT #{snap_num} RECALLED")
                        client.publish("totalmix/snapshot/status", f"loaded_{snap_num}", retain=True)

                        ws = bridge.current_workspace
                        snap_name = None
                        if ws and ws in SNAPSHOT_MAP:
                            snapshots = SNAPSHOT_MAP[ws].get("snapshots", {})
                            snap_name = snapshots.get(str(snap_num))
                        if not snap_name:
                            snap_name = f"snap_{snap_num}"

                        bridge.update_snapshot(name=snap_name)
                except ValueError:
                    print(f"→ Ignored non-integer snapshot payload: {payload} (safe)")

            elif msg.topic == "totalmix/config/snapshot_map":
                SNAPSHOT_MAP = json.loads(payload)
                publish_snapshot_map(client)
                publish_dynamic_workspaces(client)

            # === MACRO HANDLER ===
            elif msg.topic.startswith("totalmix/macro/"):
                macro_name = msg.topic.split("/")[-1]
                if macro_name in bridge.mappings.get("macros", {}):
                    try:
                        param = float(payload)
                        print(f"→ Client triggered macro '{macro_name}' param={param:.3f}")
                        bridge.run_macro(macro_name, param)
                    except ValueError:
                        print(f"Invalid param for macro {macro_name}: {payload}")
                else:
                    print(f"WARNING: macro '{macro_name}' not found in mappings.json")

        except Exception as e:
            print(f"Handler error: {e}")

    client.on_connect = on_connect
    client.on_message = on_message
    client.username_pw_set(mqtt_user, mqtt_pass)
    client.connect(mqtt_broker, mqtt_port, 60)

    watcher_thread = threading.Thread(target=map_watcher, args=(client,), daemon=True)
    watcher_thread.start()