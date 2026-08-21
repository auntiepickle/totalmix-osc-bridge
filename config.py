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

# === OSC LISTENER (structured TotalMix feedback — device state + discovery) ===
# Point TotalMix's OSC "Port outgoing" at this server on this port.
# Defaults to the monitor port so existing TotalMix configs keep working;
# the monitor and the listener cannot share the port simultaneously.
ENABLE_OSC_LISTENER = os.getenv('ENABLE_OSC_LISTENER', 'True').lower() == 'true'
OSC_LISTEN_PORT = int(os.getenv('OSC_LISTEN_PORT', os.getenv('OSC_MONITOR_PORT', '9001')))
# === GLOBAL OSC TRANSPORT (#25 — TotalMix FX 2.1+ 'Global OSC' remote) ===
# OSC_TRANSPORT selects the macro write path: 'classic' (default, the
# proven aim-and-write path) or 'global' (absolute addressing, no aiming).
# The Global remote is a SECOND remote in TotalMix (Remote 2) with its own
# port pair — classic stays configured either way (workspace switching has
# no Global equivalent).
OSC_TRANSPORT = os.getenv('OSC_TRANSPORT', 'classic').strip().lower()
GLOBAL_OSC_IP = os.getenv('GLOBAL_OSC_IP', os.getenv('OSC_IP') or '127.0.0.1')
GLOBAL_OSC_PORT = int(os.getenv('GLOBAL_OSC_PORT', '7002'))
GLOBAL_OSC_LISTEN_PORT = int(os.getenv('GLOBAL_OSC_LISTEN_PORT', '9002'))
# Shadow mode: run the Global listener for observation/name-learning even
# while the classic transport still does the writing.
ENABLE_GLOBAL_OSC_LISTENER = (
    os.getenv('ENABLE_GLOBAL_OSC_LISTENER', 'False').lower() == 'true'
    or OSC_TRANSPORT == 'global')
GLOBAL_HEARTBEAT_TIMEOUT_S = float(os.getenv('GLOBAL_HEARTBEAT_TIMEOUT_S', '5'))

# === LOGGING SETTINGS (100 KB limit per file) ===
BRIDGE_LOG_FILE = os.getenv('BRIDGE_LOG_FILE', 'bridge.log')
OSC_MONITOR_LOG_FILE = os.getenv('OSC_MONITOR_LOG_FILE', 'osc_monitor.log')
LOG_MAX_BYTES = 100 * 1024          # 100 KB
LOG_BACKUP_COUNT = 1                # Keep 1 old file
OSC_MONITOR_VERBOSE = os.getenv('OSC_MONITOR_VERBOSE', 'True').lower() == 'true'