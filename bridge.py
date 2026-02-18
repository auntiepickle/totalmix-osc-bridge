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
logger.info(f"Loaded {len(WORKSPACE_NAMES)} workspaces")
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

try:
    while True:
        time.sleep(30)
except KeyboardInterrupt:
    logger.info("\nShutting down...")
    if ENABLE_OSC_MONITOR:
        osc_monitor.stop()
    client.loop_stop()
    logger.info("Bridge stopped.")