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

# Load channel map (non-fatal if missing)
try:
    with open("ufx2_channel_map.json", "r", encoding="utf-8") as f:
        CHANNEL_MAP = json.load(f)
    logger.info("✅ Loaded ufx2_channel_map.json (AES + AN 1/2 ready)")
except FileNotFoundError:
    logger.error("❌ ufx2_channel_map.json not found in project root! (you may have named it channel_map.json)")
    CHANNEL_MAP = {}
except Exception as e:
    logger.error(f"Failed to load ufx2_channel_map.json: {e}")
    CHANNEL_MAP = {}

# OSC client (with safety check)
if OSC_IP and OSC_PORT:
    osc_client = udp_client.SimpleUDPClient(OSC_IP, OSC_PORT)
    logger.info(f"OSC Client ready → {OSC_IP}:{OSC_PORT}")
else:
    logger.warning("⚠️  OSC_IP is None — check your .env or config.py (should be 192.168.1.61)")
    osc_client = None

class TotalMixOSCBridge:
    def __init__(self, client, channel_map):
        self.osc_client = client
        self.channel_map = channel_map

    def set_an12_to_aes_send(self, value: float = 0.5):
        """AN 1/2 Hardware Input → AES Hardware Output send (0.0–1.0)
        Now using correct submix index 12."""
        if not self.osc_client:
            logger.error("No OSC client — check OSC_IP")
            return
        try:
            value = max(0.0, min(1.0, float(value)))
            self.osc_client.send_message("/setSubmix", 12.0)   # ← corrected index
            self.osc_client.send_message("/1/volume1", value)
            logger.info(f"✅ AN 1/2 → AES send set to {value:.4f}")
        except Exception as e:
            logger.error(f"OSC send failed: {e}")

# Instantiate bridge (safe to import now)
bridge = TotalMixOSCBridge(osc_client, CHANNEL_MAP)

logger.info("=== TOTALMIX OSC BRIDGE LOADED ===")
logger.info("✅ bridge.set_an12_to_aes_send(value) is ready (MQTT only starts when you run the full bridge)")

# === FULL BRIDGE STARTUP ONLY WHEN RUN DIRECTLY ===
if __name__ == "__main__":
    logger.info("Starting full bridge with MQTT...")
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

    try:
        while True:
            time.sleep(30)
    except KeyboardInterrupt:
        logger.info("\nShutting down...")
        if ENABLE_OSC_MONITOR:
            osc_monitor.stop()
        client.loop_stop()
        logger.info("Bridge stopped.")