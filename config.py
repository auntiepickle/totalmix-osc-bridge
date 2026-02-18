import os

OSC_IP = os.getenv('OSC_IP')
OSC_PORT = int(os.getenv('OSC_PORT', 7001))
MQTT_BROKER = os.getenv('MQTT_BROKER', 'mosquitto')
MQTT_PORT = int(os.getenv('MQTT_PORT', 1883))
MQTT_USER = os.getenv('MQTT_USER')
MQTT_PASS = os.getenv('MQTT_PASS')

# Your real workspace names (edit here)
WORKSPACE_NAMES = [
    "Blank",           # 1
    "Music",           # 2
    "Work",            # 3
    "techno",          # 4
    "techno_7",        # 5
    "<Empty>",         # 6
    "Pill_setup",      # 7
    "<Empty>",         # 8
    "<Empty>",         # 9
    "<Empty>",         # 10
    "<Empty>",         # 11
    "<Empty>",         # 12
    "<Empty>",         # 13
    "<Empty>",         # 14
    "<Empty>",         # 15
    "<Empty>",         # 16
    "<Empty>",         # 17
    "<Empty>",         # 18
    "<Empty>",         # 19
    "<Empty>",         # 20
    "<Empty>",         # 21
    "<Empty>",         # 22
    "<Empty>",         # 23
    "<Empty>",         # 24
    "<Empty>",         # 25
    "<Empty>",         # 26
    "<Empty>",         # 27
    "<Empty>",         # 28
    "<Empty>",         # 29
    "<Empty>"          # 30
]
# === OSC MONITOR SETTINGS (for learning addresses) ===
ENABLE_OSC_MONITOR = os.getenv('ENABLE_OSC_MONITOR', 'False').lower() == 'true'
OSC_MONITOR_PORT = int(os.getenv('OSC_MONITOR_PORT', '9001'))