import os
import time
import logging
import logging.handlers
import paho.mqtt.client as mqtt
from config import *
from mqtt_handler import setup_mqtt
from osc_monitor import osc_monitor

# === CENTRAL LOGGING WITH SIZE LIMIT ===
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s',
    datefmt='%H:%M:%S',
    handlers=[
        logging.StreamHandler(),  # console (clean)
        logging.handlers.RotatingFileHandler(
            BRIDGE_LOG_FILE,
            maxBytes=LOG_MAX_BYTES,
            backupCount=LOG_BACKUP_COUNT,
            encoding='utf-8'
        )
    ]
)
logger = logging.getLogger(__name__)

logger.info("=== TOTALMIX OSC BRIDGE STARTED ===")
logger.info(f"OSC → {OSC_IP}:{OSC_PORT}")
logger.info("✅ Workspaces are now FULLY DYNAMIC from ufx2_snapshot_map.json (no static list)")
logger.info(f"Logs → {BRIDGE_LOG_FILE} (max 100 KB) | OSC: {OSC_MONITOR_LOG_FILE} (max 100 KB)")

# Create MQTT client
client = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2)

# Setup MQTT
setup_mqtt(client, MQTT_BROKER, MQTT_PORT, MQTT_USER, MQTT_PASS, OSC_IP, OSC_PORT)

# Start OSC Monitor if enabled
if ENABLE_OSC_MONITOR:
    osc_monitor.start()

# Start MQTT loop
client.loop_start()

logger.info("Bridge is running... (Ctrl+C or docker compose down to stop)")

try:aimport os
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
        logging.StreamHandler(),  # console (clean)
        logging.handlers.RotatingFileHandler(
            BRIDGE_LOG_FILE,
            maxBytes=LOG_MAX_BYTES,
            backupCount=LOG_BACKUP_COUNT,
            encoding='utf-8'
        )
    ]
)
logger = logging.getLogger(__name__)

# Load your updated channel map (we'll use it more in future steps)
try:
    with open("ufx2_channel_map.json", "r", encoding="utf-8") as f:
        CHANNEL_MAP = json.load(f)
    logger.info("✅ Loaded ufx2_channel_map.json (AES + AN 1/2 now available)")
except Exception as e:
    logger.error(f"Failed to load ufx2_channel_map.json: {e}")
    CHANNEL_MAP = {}

# OSC client pointed at BONE
osc_client = udp_client.SimpleUDPClient(OSC_IP, OSC_PORT)
logger.info(f"OSC Client ready → {OSC_IP}:{OSC_PORT}")

class TotalMixOSCBridge:
    def __init__(self, client, channel_map):
        self.osc_client = client
        self.channel_map = channel_map

    def set_an12_to_aes_send(self, value: float = 0.5):
        """AN 1/2 Hardware Input → AES Hardware Output send (0.0–1.0)
        This is the exact command pair we just tested live."""
        try:
            value = max(0.0, min(1.0, float(value)))
            self.osc_client.send_message("/setSubmix", 1.0)   # select AES submix
            self.osc_client.send_message("/1/volume1", value) # AN 1/2 send fader
            logger.info(f"✅ AN 1/2 → AES send set to {value:.4f}")
        except Exception as e:
            logger.error(f"OSC send failed: {e}")

# Instantiate the bridge (ready for use)
bridge = TotalMixOSCBridge(osc_client, CHANNEL_MAP)

logger.info("=== TOTALMIX OSC BRIDGE STARTED ===")
logger.info(f"OSC → {OSC_IP}:{OSC_PORT}")
logger.info("✅ AN 1/2 → AES send control is now LIVE in bridge.set_an12_to_aes_send()")

# Create MQTT client
client = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2)

# Setup MQTT (unchanged for this step — we'll wire the new command next)
setup_mqtt(client, MQTT_BROKER, MQTT_PORT, MQTT_USER, MQTT_PASS, OSC_IP, OSC_PORT)

# Start OSC Monitor if enabled
if ENABLE_OSC_MONITOR:
    osc_monitor.start()

# Start MQTT loop
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
    while True:
        time.sleep(30)
except KeyboardInterrupt:
    logger.info("\nShutting down...")
    if ENABLE_OSC_MONITOR:
        osc_monitor.stop()
    client.loop_stop()
    logger.info("Bridge stopped.")