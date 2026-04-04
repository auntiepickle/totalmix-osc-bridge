import os
import time
import logging
import logging.handlers
import json
import paho.mqtt.client as mqtt
from pythonosc import udp_client
from config import *
from mqtt_handler import setup_mqtt
from osc_monitor import osc_monitor

# === CENTRAL LOGGING WITH SIZE LIMIT ===
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

# Load channel map
try:
    with open("ufx2_channel_map.json", "r", encoding="utf-8") as f:
        CHANNEL_MAP = json.load(f)
    logger.info("✅ Loaded ufx2_channel_map.json")
except Exception as e:
    logger.error(f"Failed to load ufx2_channel_map.json: {e}")
    CHANNEL_MAP = {}

# Load mappings (NEW macro system — fully configurable)
try:
    with open("mappings.json", "r", encoding="utf-8") as f:
        MAPPINGS = json.load(f)
    logger.info("✅ Loaded mappings.json — macros are now fully data-driven")
except Exception as e:
    logger.error(f"Failed to load mappings.json: {e}")
    MAPPINGS = {"macros": {}}

# OSC client
if OSC_IP and OSC_PORT:
    osc_client = udp_client.SimpleUDPClient(OSC_IP, OSC_PORT)
    logger.info(f"OSC Client ready → {OSC_IP}:{OSC_PORT}")
else:
    osc_client = None
    logger.warning("⚠️ OSC_IP not set")

class TotalMixOSCBridge:
    def __init__(self, client, mappings):
        self.osc_client = client
        self.mappings = mappings

    def run_macro(self, macro_name: str, param: float = 0.5):
        """Generic runner — reads the entire macro from mappings.json (no constants in code)"""
        if macro_name not in self.mappings.get("macros", {}):
            logger.error(f"Macro '{macro_name}' not found in mappings.json")
            return

        macro = self.mappings["macros"][macro_name]
        value = max(macro.get("param_range", [0.0, 1.0])[0],
                    min(macro.get("param_range", [0.0, 1.0])[1], float(param)))

        logger.info(f"🚀 Running macro '{macro_name}' (workspace: {macro.get('workspace')}, snapshot: {macro.get('snapshot')}) param={value:.4f}")

        for step in macro.get("steps", []):
            osc_addr = step["osc"]
            step_val = value if step.get("value") == "{{param}}" else step["value"]
            try:
                self.osc_client.send_message(osc_addr, float(step_val))
                logger.info(f"   → {osc_addr} = {step_val}")
            except Exception as e:
                logger.error(f"OSC send failed for {osc_addr}: {e}")

        logger.info(f"✅ Macro '{macro_name}' complete")

# Instantiate bridge
bridge = TotalMixOSCBridge(osc_client, MAPPINGS)

logger.info("=== TOTALMIX OSC BRIDGE LOADED ===")
logger.info("✅ New macro system active → bridge.run_macro('an12_to_aes_send', value)")

# === FULL BRIDGE STARTUP ONLY WHEN RUN DIRECTLY ===
if __name__ == "__main__":
    logger.info("Starting full bridge with MQTT...")
    client = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
    setup_mqtt(client, MQTT_BROKER, MQTT_PORT, MQTT_USER, MQTT_PASS, OSC_IP, OSC_PORT)

    if ENABLE_OSC_MONITOR:
        osc_monitor.start()

    client.loop_start()

    logger.info("Bridge is running... (Ctrl+C or docker compose down to stop)")

    try:
        while True:
            time.sleep(30)
    except KeyboardInterrupt:
        logger.info("\nShutting down...")
        if ENABLE_OSC_MONITOR:
            osc_monitor.stop()
        client.loop_stop()
        logger.info("Bridge stopped.")