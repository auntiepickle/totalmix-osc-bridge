import os

OSC_IP = os.getenv('OSC_IP')
OSC_PORT = int(os.getenv('OSC_PORT', 7001))
MQTT_BROKER = os.getenv('MQTT_BROKER', 'mosquitto')
MQTT_PORT = int(os.getenv('MQTT_PORT', 1883))
MQTT_USER = os.getenv('MQTT_USER')
MQTT_PASS = os.getenv('MQTT_PASS')

def snapshot_num_to_osc_index(snap_num: int) -> int:
    """Convert a 1–8 snapshot slot number to the TotalMix OSC button index.

    TotalMix orders snapshot buttons bottom-to-top in its OSC namespace, so
    slot 1 is index 8 and slot 8 is index 1. The OSC address to recall a
    snapshot is:  /3/snapshots/{index}/1  with value 1.0
    """
    return 9 - int(snap_num)

# === OSC MONITOR SETTINGS (for learning addresses) ===
ENABLE_OSC_MONITOR = os.getenv('ENABLE_OSC_MONITOR', 'False').lower() == 'true'
OSC_MONITOR_PORT = int(os.getenv('OSC_MONITOR_PORT', '9001'))
# === LOGGING SETTINGS (100 KB limit per file) ===
BRIDGE_LOG_FILE = os.getenv('BRIDGE_LOG_FILE', 'bridge.log')
OSC_MONITOR_LOG_FILE = os.getenv('OSC_MONITOR_LOG_FILE', 'osc_monitor.log')
LOG_MAX_BYTES = 100 * 1024          # 100 KB
LOG_BACKUP_COUNT = 1                # Keep 1 old file
OSC_MONITOR_VERBOSE = os.getenv('OSC_MONITOR_VERBOSE', 'True').lower() == 'true'