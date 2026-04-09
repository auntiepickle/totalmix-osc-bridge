import os
import time
import logging
import logging.handlers
import json
import mido
import threading
import paho.mqtt.client as mqtt
import re
import asyncio  # ← NEW: required for async-safe WebSocket broadcast
from pythonosc import udp_client
from config import *
from mqtt_handler import setup_mqtt
from osc_monitor import osc_monitor
from operations import OperationRegistry

# === CENTRAL LOGGING ===
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s',
    datefmt='%H:%M:%S',
    handlers=[
        logging.StreamHandler(),
        logging.handlers.RotatingFileHandler(
            BRIDGE_LOG_FILE,
            maxBytes=LOG_MAX_BYTES,
            backupCount=LOG_BACKUP_COUNT,
            encoding='utf-8'
        )
    ]
)
logger = logging.getLogger(__name__)

# Load snapshot map
try:
    with open("ufx2_snapshot_map.json", "r", encoding="utf-8") as f:
        SNAPSHOT_MAP = json.load(f)
    logger.info(f"Loaded ufx2_snapshot_map.json — workspaces: {list(SNAPSHOT_MAP.keys())}")
except Exception as e:
    logger.error(f"Failed to load ufx2_snapshot_map.json: {e}")
    SNAPSHOT_MAP = {}

# Load mappings
try:
    with open("mappings.json", "r", encoding="utf-8") as f:
        MAPPINGS = json.load(f)
    logger.info("Loaded mappings.json — macros fully data-driven")
except Exception as e:
    logger.error(f"Failed to load mappings.json: {e}")
    MAPPINGS = {"macros": {}}

# OSC client
osc_client = udp_client.SimpleUDPClient(OSC_IP, OSC_PORT) if OSC_IP and OSC_PORT else None
logger.info(f"OSC Client ready → {OSC_IP}:{OSC_PORT}")

# === WEBSOCKET HOOK FOR WEB CLIENT v1 ===
ws_clients = []  # list of active FastAPI WebSocket connections

def broadcast_state(bridge_instance, macro_update=None):
    """Broadcast current state + optional macro live data (still event-driven, non-chatty)"""
    if not ws_clients:
        return

    payload = {
        "type": "state",
        "workspace": getattr(bridge_instance, "current_workspace", None),
        "snapshot": getattr(bridge_instance, "current_snapshot", None),
        "macros": getattr(bridge_instance, "macro_live_state", {})   # rich per-macro data for cards
    }

    if macro_update:
        payload["macro_update"] = macro_update

    for client in list(ws_clients):
        try:
            asyncio.create_task(client.send_text(json.dumps(payload)))
        except Exception:
            if client in ws_clients:
                ws_clients.remove(client)

class TotalMixOSCBridge:
    def __init__(self, osc_client, mappings, snapshot_map):
        self._suppress_handler = False   
        self._last_macro_end_time = 0.0
        self.osc_client = osc_client
        self.mappings = mappings
        self.snapshot_map = snapshot_map
        self.current_workspace = None
        self.current_snapshot = None
        self.mqtt_client = None
        # WebSocket hook (live UI updates)
        self.broadcast_state = lambda macro_update=None: broadcast_state(self, macro_update=macro_update)
        self.macro_live_state = {}          # live macro state for polished cards
        self.channel_map = None             # loaded once for routing labels
        self._load_channel_map()
    
    def _load_channel_map(self):
        """Load ufx2_channel_map.json once for human-readable routing labels"""
        try:
            with open("ufx2_channel_map.json", "r", encoding="utf-8") as f:
                self.channel_map = json.load(f)
            logger.info("✅ Loaded ufx2_channel_map.json for card routing labels")
        except Exception as e:
            logger.warning(f"Could not load ufx2_channel_map.json: {e}")
            self.channel_map = {}

    def get_routing_label(self, macro_name: str) -> str:
        """Return human-readable routing line for UI cards"""
        if not self.channel_map:
            self._load_channel_map()
        # Simple match against macro steps (expandable later)
        for submix_name, submix_data in self.channel_map.get("submixes", {}).items():
            for send_name, send_data in submix_data.get("sends", {}).items():
                if any(step.get("osc") == send_data.get("osc_address")
                       for step in self.mappings.get("macros", {}).get(macro_name, {}).get("steps", [])):
                    return f"{send_name} → {submix_name} {send_data.get('description', '')}"
        return "—"
    
    def update_workspace(self, name: str = None, slot: int = None):
        if name:
            self.current_workspace = name
        elif slot is not None and self.snapshot_map:
            for ws_name, data in self.snapshot_map.items():
                if data.get("slot") == slot:
                    self.current_workspace = ws_name
                    break
        logger.info(f"BRIDGE STATE → workspace = {self.current_workspace or 'None'}")
        self.broadcast_state()  # ← live web update

    def update_snapshot(self, name: str = None, index: int = None, workspace: str = None):
        if name:
            self.current_snapshot = name
        elif index is not None and (workspace or self.current_workspace):
            ws = workspace or self.current_workspace
            if ws and ws in self.snapshot_map:
                snapshots = self.snapshot_map[ws].get("snapshots", {})
                for snap_key, snap_value in snapshots.items():
                    if str(snap_key) == str(index) or snap_value == name:
                        self.current_snapshot = snap_value
                        break
        logger.info(f"BRIDGE STATE → snapshot = {self.current_snapshot or 'None'}")
        self.broadcast_state()  # ← live web update

    def run_macro(self, macro_name: str, param: float = 0.5):
        if macro_name not in self.mappings.get("macros", {}):
            logger.error(f"Macro '{macro_name}' not found")
            return

        macro = self.mappings["macros"][macro_name]
        value = max(macro.get("param_range", [0.0, 1.0])[0],
                    min(macro.get("param_range", [0.0, 1.0])[1], float(param)))

        # === ROBUST NAME EXTRACTION ===
        ws_name = macro.get("workspace")
        snap_name = macro.get("snapshot")
        if isinstance(snap_name, dict):
            snap_name = snap_name.get("name") or list(snap_name.values())[0] if snap_name else None
        if isinstance(ws_name, dict):
            ws_name = ws_name.get("name") or list(ws_name.values())[0] if ws_name else None

        if snap_name:
            snap_name = re.sub(r'^\d+\s*-\s*', '', str(snap_name)).strip().title()

        force_switch = macro.get("force_switch", False)

        logger.info(f"DEBUG — running macro from GitHub commit 'fixing state sync' — state now: ws={self.current_workspace} snap={self.current_snapshot}")
        logger.info(f"Running macro '{macro_name}' → {ws_name}/{snap_name} param={value:.4f} (force_switch={force_switch})")

        # === DEBOUNCE ===
        current_time = time.time()
        if hasattr(self, '_last_macro_time') and current_time - self._last_macro_time < 1.5 and getattr(self, '_last_macro_name', None) == macro_name:
            logger.info(f"   → Debounced duplicate macro trigger (ignored)")
            return
        self._last_macro_time = current_time
        self._last_macro_name = macro_name

        self._suppress_handler = True

        try:
            # === ALWAYS RESOLVE SLOTS/INDICES ===
            ws_slot = None
            snap_num = None

            if ws_name and ws_name in self.snapshot_map:
                ws_slot = self.snapshot_map[ws_name].get("slot")

            if snap_name and ws_name in self.snapshot_map:
                snapshots = self.snapshot_map[ws_name].get("snapshots", {})
                for snap_key, snap_data in snapshots.items():
                    if isinstance(snap_data, dict):
                        candidate_name = snap_data.get("name") or snap_key
                        candidate_index = snap_data.get("index")
                    else:
                        candidate_name = snap_data
                        candidate_index = snap_key
                    if str(candidate_name).title() == str(snap_name).title():
                        snap_num = candidate_index or snap_key
                        break

            # === STATE-AWARE SWITCH ===
            already_on_target = (
                self.current_workspace == ws_name and
                self.current_snapshot == snap_name and
                ws_name is not None and snap_name is not None
            )

            if force_switch or not already_on_target:
                logger.info(f"   → Need to switch (force={force_switch} or state mismatch)")

                if ws_name and ws_slot is not None:
                    self.osc_client.send_message("/loadQuickWorkspace", float(ws_slot))
                    logger.info(f"   → Switched workspace to '{ws_name}' (slot {ws_slot})")
                    self.current_workspace = ws_name
                    if self.mqtt_client:
                        self.mqtt_client.publish("totalmix/workspace", str(ws_slot), retain=True)
                        logger.info(f"   → Published to HA → totalmix/workspace = {ws_slot}")
                    time.sleep(1.0)

                if snap_name and snap_num is not None:
                    osc_addr = f"/3/snapshots/{9 - int(snap_num)}/1"
                    self.osc_client.send_message(osc_addr, 1.0)
                    logger.info(f"   → Switched snapshot to '{snap_name}' (OSC {osc_addr} = 1.0)")
                    self.current_snapshot = snap_name
                    if self.mqtt_client:
                        self.mqtt_client.publish("totalmix/snapshot", str(snap_num), retain=True)
                        logger.info(f"   → Published to HA → totalmix/snapshot = {snap_num}")
                    time.sleep(0.3)
            else:
                logger.info(f"   → Already on target {ws_name}/{snap_name} — skipping ws/ss switch (force_switch=False)")

            # === MACRO STEPS WITH OPERATION LIBRARY ===
            for step in macro.get("steps", []):
                osc_addr = step["osc"]

                if "operation" in step and step.get("value") == "{{param}}":
                    op_config = step["operation"]
                    OperationRegistry.execute(
                        op_config["type"],
                        self.osc_client,
                        osc_addr,
                        value,
                        op_config
                    )
                    continue

                # === NORMAL STATIC STEP ===
                step_val = value if step.get("value") == "{{param}}" else step.get("value")
                try:
                    self.osc_client.send_message(osc_addr, float(step_val))
                    logger.info(f"   → {osc_addr} = {step_val}")
                except Exception as e:
                    logger.error(f"OSC send failed: {e}")

            # === GUARANTEED HA SYNC ===
            if self.mqtt_client:
                if ws_slot is not None:
                    self.mqtt_client.publish("totalmix/workspace", str(ws_slot), retain=True)
                if snap_num is not None:
                    self.mqtt_client.publish("totalmix/snapshot", str(snap_num), retain=True)
                    self.mqtt_client.publish("totalmix/snapshot/status", f"loaded_{snap_num}", retain=True)

            logger.info(f"Macro '{macro_name}' complete")
            self.broadcast_state()  # ← live web update after macro runs
            
            # === RICH MACRO UPDATE FOR CARDS (still only once per trigger) ===
            macro_data = self.mappings["macros"][macro_name]
            live_data = {
                "name": macro_name,
                "description": macro_data.get("description", ""),
                "value": float(value),
                "progress": 100,
                "lfo_active": False,
                "last_trigger": time.time(),
                "osc_preview": f"{macro_data.get('steps', [{}])[0].get('osc', '')} = {value:.3f}",
                "routing_label": self.get_routing_label(macro_name),
                "midi_trigger": macro_data.get("midi_triggers", [{}])[0] if macro_data.get("midi_triggers") else None
            }
            self.macro_live_state[macro_name] = live_data
            self.broadcast_state(self, macro_update=live_data)   # one clean broadcast

        finally:
            self._suppress_handler = False
            self._last_macro_end_time = time.time()


bridge = TotalMixOSCBridge(osc_client, MAPPINGS, SNAPSHOT_MAP)

logger.info("=== TOTALMIX OSC BRIDGE LOADED ===")
logger.info("State-aware workspace/snapshot switching (NO force) + OperationRegistry + WebSocket live updates for Web Client v1")

# === NEW: MQTT STARTUP METHOD (works in BOTH standalone + web mode) ===
def start_mqtt(self):
    logger.info("=== TOTALMIX OSC BRIDGE STARTING MQTT (web or standalone mode) ===")
    logger.info(f"OSC target → {OSC_IP}:{OSC_PORT}")
    logger.info("MQTT macro namespace → totalmix/macro/<name>")

    client = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
    setup_mqtt(client, MQTT_BROKER, MQTT_PORT, MQTT_USER, MQTT_PASS, OSC_IP, OSC_PORT, self)
    self.mqtt_client = client

    if ENABLE_OSC_MONITOR:
        osc_monitor.start()

    client.loop_start()
    logger.info("MQTT client loop started — macro subscriptions ACTIVE")

# Attach the method to the bridge instance
TotalMixOSCBridge.start_mqtt = start_mqtt

# === BRIDGE STARTUP — CENTRALIZED MODE (for python bridge.py) ===
if __name__ == "__main__":
    bridge.start_mqtt()   # re-uses the same function

    try:
        while True:
            time.sleep(30)
    except KeyboardInterrupt:
        logger.info("\nShutting down bridge...")
        if ENABLE_OSC_MONITOR:
            osc_monitor.stop()
        if bridge.mqtt_client:
            bridge.mqtt_client.loop_stop()
        logger.info("Bridge stopped cleanly.")