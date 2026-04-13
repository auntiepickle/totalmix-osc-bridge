#!/usr/bin/env python3
import json, os, shutil, datetime, logging
from flask import Flask, jsonify, request
# ... (rest of your existing bridge.py stays exactly the same until the end)

app = Flask(__name__)
macros = {}
channel_map = {}

def backup_json_files():
    timestamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_dir = "backups"
    os.makedirs(backup_dir, exist_ok=True)
    
    for filename in ["mappings.json", "ufx2_channel_map.json"]:
        if os.path.exists(filename):
            shutil.copy2(filename, f"{backup_dir}/{filename}.{timestamp}")
            logging.info(f"✅ Backed up {filename} → {backup_dir}/{filename}.{timestamp}")

def load_data():
    global macros, channel_map
    backup_json_files()                    # ← automatic backup every reload/start
    with open("mappings.json") as f:
        macros = json.load(f)["macros"]
    with open("ufx2_channel_map.json") as f:
        channel_map = json.load(f)
    logging.info(f"✅ Loaded {len(macros)} macros + channel map")

@app.route('/api/reload', methods=['POST'])
def reload():
    try:
        load_data()
        return jsonify({"status": "success", "macros_loaded": len(macros)})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

# Call load_data() at startup (and again on /api/reload)
load_data()

# ... (rest of your existing routes and OSC/MIDI code unchanged)
if __name__ == "__main__":
    logging.info("🚀 Bridge started with Hot-Reload + Auto-Backup")
    app.run(host="0.0.0.0", port=5000)
