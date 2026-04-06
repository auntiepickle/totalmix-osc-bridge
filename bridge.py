import os
import time
import logging
import logging.handlers
import json
import mido
import threading
import paho.mqtt.client as mqtt
from pythonosc import udp_client
from config import *
from mqtt_handler import setup_mqtt
from osc_monitor import osc_monitor

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
    logger.info(f"✅ Loaded ufx2_snapshot_map.json — workspaces: {list(SNAPSHOT_MAP.keys())}")
except Exception as e:
    logger.error(f"Failed to load ufx2_snapshot_map.json: {e}")
    SNAPSHOT_MAP = {}

# Load mappings
try:
    with open("mappings.json", "r", encoding="utf-8") as f:
        MAPPINGS = json.load(f)
    logger.info("✅ Loaded mappings.json — macros fully data-driven")
except Exception as e:
    logger.error(f"Failed to load mappings.json: {e}")
    MAPPINGS = {"macros": {}}

# OSC client
osc_client = udp_client.SimpleUDPClient(OSC_IP, OSC_PORT) if OSC_IP and OSC_PORT else None
logger.info(f"OSC Client ready → {OSC_IP}:{OSC_PORT}")

class TotalMixOSCBridge:
    def __init__(self, osc_client, mappings, snapshot_map):
        self.osc_client = osc_client
        self.mappings = mappings
        self.snapshot_map = snapshot_map
        self.current_workspace = None   # ← state tracking
        self.current_snapshot = None    # ← state tracking

    def run_macro(self, macro_name: str, param: float = 0.5):
        if macro_name not in self.mappings.get("macros", {}):
            logger.error(f"Macro '{macro_name}' not found")
            return

        macro = self.mappings["macros"][macro_name]
        value = max(macro.get("param_range", [0.0, 1.0])[0],
                    min(macro.get("param_range", [0.0, 1.0])[1], float(param)))

        ws_name = macro.get("workspace")
        snap_name = macro.get("snapshot")

        logger.info(f"🚀 Running macro '{macro_name}' → {ws_name}/{snap_name} param={value:.4f}")

        # === STATE-AWARE SWITCH (only if needed) ===
        if ws_name and self.current_workspace != ws_name:
            if ws_name in self.snapshot_map:
                ws_slot = self.snapshot_map[ws_name].get("slot")
                if ws_slot is not None:
                    self.osc_client.send_message("/loadQuickWorkspace", float(ws_slot))
                    self.current_workspace = ws_name
                    logger.info(f"   → Switched workspace to '{ws_name}' (slot {ws_slot})")
                    time.sleep(0.3)

        if snap_name and ws_name and self.current_snapshot != snap_name:
            if ws_name in self.snapshot_map:
                snapshots = self.snapshot_map[ws_name].get("snapshots", {})
                if snap_name in snapshots:
                    snap_index = snapshots[snap_name].get("index") or 1
                    osc_addr = f"/3/snapshots/{9 - int(snap_index)}/1"
                    self.osc_client.send_message(osc_addr, 1.0)
                    self.current_snapshot = snap_name
                    logger.info(f"   → Switched snapshot to '{snap_name}'")
                    time.sleep(0.3)

        # === MACRO STEPS (always run) ===
        for step in macro.get("steps", []):
            osc_addr = step["osc"]
            step_val = value if step.get("value") == "{{param}}" else step["value"]
            try:
                self.osc_client.send_message(osc_addr, float(step_val))
                logger.info(f"   → {osc_addr} = {step_val}")
            except Exception as e:
                logger.error(f"OSC send failed: {e}")

        logger.info(f"✅ Macro '{macro_name}' complete")

bridge = TotalMixOSCBridge(osc_client, MAPPINGS, SNAPSHOT_MAP)

logger.info("=== TOTALMIX OSC BRIDGE LOADED ===")
logger.info("✅ State-aware workspace/snapshot switching (no repeated switches)")

# === MIDI INPUT LISTENER (Cirklon → macro) ===


def midi_listener():
    try:
        inport = mido.open_input("IAC Driver Bus 1")   # ← change port name if your IAC bus is different
        logger.info("MIDI listener started — listening on IAC Driver Bus 1 (all macros in mappings.json)")
        
        for msg in inport:
            if msg.type != "control_change":
                continue
            
            # Scan every macro’s midi_triggers array
            for macro_name, macro in bridge.mappings.get("macros", {}).items():
                for trigger in macro.get("midi_triggers", []):
                    if (trigger.get("type") == "control_change" and
                        trigger.get("number") == msg.control and
                        trigger.get("channel") == msg.channel + 1):   # mido channel 0 = MIDI ch 1
                    
                        param = msg.value / 127.0
                        if trigger.get("use_value_as_param", False):
                            logger.info(f"MIDI CC {msg.control} ch {msg.channel+1} → macro '{macro_name}' param={param:.3f}")
                            bridge.run_macro(macro_name, param)
                        else:
                            logger.info(f"MIDI trigger '{macro_name}' (no param)")
                            bridge.run_macro(macro_name)
                        break  # one trigger per message is enough

    except Exception as e:
        logger.error(f"MIDI listener failed: {e}")

# === BRIDGE STARTUP (centralized server — no local MIDI) ===
if __name__ == "__main__":
    logger.info("=== TOTALMIX OSC BRIDGE STARTING (centralized mode) ===")
    logger.info(f"OSC target: {OSC_IP}:{OSC_PORT}")
    logger.info("MQTT macro namespace: totalmix/macro/<name>  (clients publish here)")

    client = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
    
    # Pass the bridge instance so mqtt_handler can call run_macro
    setup_mqtt(client, MQTT_BROKER, MQTT_PORT, MQTT_USER, MQTT_PASS, OSC_IP, OSC_PORT, bridge)

    if ENABLE_OSC_MONITOR:
        osc_monitor.start()

    client.loop_start()

    try:
        while True:
            time.sleep(30)
    except KeyboardInterrupt:
        logger.info("\nShutting down bridge...")
        if ENABLE_OSC_MONITOR:
            osc_monitor.stop()
        client.loop_stop()
        logger.info("Bridge stopped cleanly.")