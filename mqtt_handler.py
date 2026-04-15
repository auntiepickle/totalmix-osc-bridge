import logging
import json
import time
import threading
import paho.mqtt.client as mqtt
from pathlib import Path

from osc import send_osc
from config import snapshot_num_to_osc_index

logger = logging.getLogger(__name__)

# ── Snapshot map — loaded from SMB-mounted config share ───────────────────────
# The snapshot map lives outside the repo so it can be updated on a NAS without
# a git pull. The map_watcher thread checks every 5 seconds for file changes.
SNAPSHOT_MAP: dict = {}
MAP_PATH = Path("/app/config/ufx2_snapshot_map.json")
LAST_MTIME: float = 0


def load_snapshot_map() -> bool:
    """Load the snapshot map from the SMB-mounted config path.

    Returns True if the file changed (new data loaded), False otherwise.
    Silently ignores missing files — the bridge runs without a snapshot map,
    just without workspace/snapshot resolution.
    """
    global SNAPSHOT_MAP, LAST_MTIME
    try:
        mtime = MAP_PATH.stat().st_mtime
        if mtime == LAST_MTIME:
            return False
        with open(MAP_PATH, "r", encoding="utf-8-sig") as f:
            SNAPSHOT_MAP = json.load(f)
        LAST_MTIME = mtime
        logger.info(f"Snapshot map reloaded — {len(SNAPSHOT_MAP)} workspaces from {MAP_PATH}")
        return True
    except FileNotFoundError:
        return False
    except Exception as e:
        logger.warning(f"Could not load snapshot map from {MAP_PATH}: {e}")
        return False


def publish_snapshot_map(client):
    if not SNAPSHOT_MAP:
        return
    try:
        client.publish("totalmix/snapshot_map", json.dumps(SNAPSHOT_MAP), retain=True)
        logger.info(f"Published snapshot map ({len(SNAPSHOT_MAP)} workspaces) → totalmix/snapshot_map")
    except Exception as e:
        logger.error(f"publish_snapshot_map failed: {e}")


def publish_dynamic_workspaces(client):
    """Publish workspace list sorted by slot index.

    Publishes to totalmix/workspaces as a JSON array of {name, index} objects.
    Home Assistant input_select entities consume this to build dropdowns.
    """
    if not SNAPSHOT_MAP:
        logger.debug("No snapshot map loaded — skipping workspace publish")
        return

    workspace_list = []
    for name, data in SNAPSHOT_MAP.items():
        if not isinstance(data, dict):
            continue
        slot = data.get("slot")
        if slot is None:
            logger.warning(f"Workspace '{name}' has no 'slot' — skipping")
            continue
        if name and name.strip() and name != "<Empty>":
            workspace_list.append({"name": name, "index": int(slot)})

    workspace_list.sort(key=lambda x: x["index"])

    try:
        client.publish("totalmix/workspaces", json.dumps(workspace_list), retain=True, qos=1)
        logger.info(
            f"Published {len(workspace_list)} workspaces → totalmix/workspaces "
            f"({[w['name'] for w in workspace_list[:5]]}...)"
        )
    except Exception as e:
        logger.error(f"publish_dynamic_workspaces failed: {e}")


def map_watcher(client, bridge=None):
    """Daemon thread — polls the SMB snapshot map every 5 seconds.

    On change: reloads, syncs bridge.snapshot_map, and republishes to MQTT.
    """
    logger.info(f"Snapshot map watcher started (polling {MAP_PATH} every 5s)")
    while True:
        time.sleep(5)
        if load_snapshot_map():
            if bridge is not None:
                bridge.snapshot_map = SNAPSHOT_MAP
                logger.info(f"bridge.snapshot_map synced from SMB ({len(SNAPSHOT_MAP)} workspaces)")
            publish_snapshot_map(client)
            publish_dynamic_workspaces(client)


def setup_mqtt(client, mqtt_broker, mqtt_port, mqtt_user, mqtt_pass, osc_ip, osc_port, bridge):
    """Wire up MQTT callbacks and connect.

    Registers on_connect and on_message handlers, then connects and starts the
    background map_watcher thread. Called once from bridge.start_mqtt().
    """

    def on_connect(client, userdata, flags, rc, properties=None):
        logger.info(f"MQTT connected (rc={rc})")
        bridge.mqtt_connected = True
        client.subscribe("totalmix/#")
        client.subscribe("totalmix/macro/#")

        load_snapshot_map()
        if SNAPSHOT_MAP:
            bridge.snapshot_map = SNAPSHOT_MAP
            logger.info(f"bridge.snapshot_map synced on connect ({len(SNAPSHOT_MAP)} workspaces)")

        publish_snapshot_map(client)
        publish_dynamic_workspaces(client)

    def on_message(client, userdata, msg):
        global SNAPSHOT_MAP
        payload = msg.payload.decode().strip()

        # ── Suppress feedback loops while a macro is executing ────────────────
        # run_macro() publishes to totalmix/workspace and totalmix/snapshot as
        # part of the switch sequence. Without suppression, those publishes
        # would trigger on_message again and overwrite current_workspace/snapshot
        # with stale slot numbers mid-ramp.
        if msg.topic in ("totalmix/workspace", "totalmix/snapshot"):
            if getattr(bridge, "_suppress_handler", False):
                logger.debug(f"Suppressed {msg.topic} (macro in progress)")
                return
            since_last = time.time() - getattr(bridge, "_last_macro_end_time", 0)
            if since_last < 2.5:
                logger.debug(f"Suppressed {msg.topic} (cooldown {since_last:.1f}s < 2.5s)")
                return

        if msg.topic.startswith("totalmix/macro/") or msg.topic in ("totalmix/workspace", "totalmix/snapshot"):
            logger.info(f"MQTT ← {msg.topic} | {payload}")

        try:
            if msg.topic == "totalmix/workspace":
                try:
                    ws_slot = int(payload)
                    ws_name = next(
                        (name for name, data in SNAPSHOT_MAP.items()
                         if isinstance(data, dict) and data.get("slot") == ws_slot),
                        None,
                    )
                    if ws_name and ws_name == getattr(bridge, "current_workspace", None):
                        logger.debug(f"Workspace slot {ws_slot} already active — skipping OSC")
                        bridge.update_workspace(name=ws_name)
                        return
                    send_osc("/loadQuickWorkspace", ws_slot, osc_ip, osc_port)
                    bridge.update_workspace(name=ws_name or f"slot_{ws_slot}")
                except ValueError:
                    logger.warning(f"Non-integer workspace payload ignored: {payload!r}")

            elif msg.topic == "totalmix/snapshot":
                try:
                    snap_num = int(payload)
                    if 1 <= snap_num <= 8:
                        osc_addr = f"/3/snapshots/{snapshot_num_to_osc_index(snap_num)}/1"
                        send_osc(osc_addr, 1.0, osc_ip, osc_port)
                        logger.info(f"Snapshot #{snap_num} recalled ({osc_addr})")
                        client.publish("totalmix/snapshot/status", f"loaded_{snap_num}", retain=True)

                        ws = getattr(bridge, "current_workspace", None)
                        snap_name = None
                        if ws and ws in SNAPSHOT_MAP:
                            snap_name = SNAPSHOT_MAP[ws].get("snapshots", {}).get(str(snap_num))
                        bridge.update_snapshot(name=snap_name or f"snap_{snap_num}")
                except ValueError:
                    logger.warning(f"Non-integer snapshot payload ignored: {payload!r}")

            elif msg.topic == "totalmix/config/snapshot_map":
                SNAPSHOT_MAP = json.loads(payload)
                publish_snapshot_map(client)
                publish_dynamic_workspaces(client)

            elif msg.topic.startswith("totalmix/macro/"):
                macro_name = msg.topic.split("/")[-1]
                if macro_name in bridge.mappings.get("macros", {}):
                    try:
                        param = float(payload)
                        logger.info(f"MQTT macro trigger: '{macro_name}' param={param:.3f}")
                        bridge.run_macro(macro_name, param)
                    except ValueError:
                        logger.warning(f"Invalid param for macro '{macro_name}': {payload!r}")
                else:
                    logger.warning(f"Macro '{macro_name}' not found in mappings")

        except Exception as e:
            logger.error(f"on_message error on {msg.topic}: {e}", exc_info=True)

    def on_disconnect(client, userdata, disconnect_flags, reason_code, properties=None):
        logger.warning(f"MQTT disconnected (rc={reason_code})")
        bridge.mqtt_connected = False

    client.on_connect    = on_connect
    client.on_disconnect = on_disconnect
    client.on_message    = on_message
    client.username_pw_set(mqtt_user, mqtt_pass)
    client.connect(mqtt_broker, mqtt_port, 60)

    threading.Thread(target=map_watcher, args=(client, bridge), daemon=True).start()
