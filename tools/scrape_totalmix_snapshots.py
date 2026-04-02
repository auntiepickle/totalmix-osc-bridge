#!/usr/bin/env python3
"""
TotalMix Snapshot + Workspace Scraper (public version)
- Runs on any Windows machine via HASS.Agent
- Auto-detects current Windows username
- Publishes full ufx2_snapshot_map.json via MQTT (retained)
- Saves local backup
- Zero hard-coded usernames or paths
"""
import re
import json
import subprocess
import os
import getpass
from pathlib import Path
from collections import defaultdict

# Configurable via environment variables (no hard-coded usernames)
TOTALMIX_FOLDER = Path(
    os.getenv(
        "TOTALMIX_FOLDER",
        rf"C:\Users\{getpass.getuser()}\AppData\Local\TotalMixFX"
    )
)
MQTT_HOST = os.getenv("MQTT_HOST", "127.0.0.1")
MQTT_TOPIC = "totalmix/config/snapshot_map"

print("🔍 TotalMix Snapshot Scraper (Grok Home Studio Project — public edition)")

# 1. Workspace names from rme.totalmix.preferences.xml
pref_file = TOTALMIX_FOLDER / "rme.totalmix.preferences.xml"
workspace_map = {}
if pref_file.exists():
    text = pref_file.read_text(encoding="utf-8")
    for m in re.finditer(r'<val e="PresetName(\d+)" v="([^"]*)"/>', text):
        num = int(m.group(1))
        name = m.group(2).strip()
        if name and name != "<Empty>" and name != "":
            workspace_map[num] = name
    print(f"Found {len(workspace_map)} workspaces: {list(workspace_map.values())}")
else:
    print(f"⚠️ preferences.xml not found at {pref_file}")

# 2. Snapshot names per workspace from presetN.tmws
snapshot_map = defaultdict(dict)
for ws_num, ws_name in workspace_map.items():
    preset_file = TOTALMIX_FOLDER / f"preset{ws_num}.tmws"
    if not preset_file.exists():
        print(f"⚠️ Missing {preset_file.name} for workspace {ws_name}")
        continue
    text = preset_file.read_text(encoding="utf-8")
    snaps = {}
    for m in re.finditer(r'<val e="SnapshotName (\d+)" v="([^"]*)"/>', text):
        idx = int(m.group(1)) + 1
        name = m.group(2).strip()
        snaps[str(idx)] = name if name else f"Empty {idx}"
    for i in range(1, 9):
        if str(i) not in snaps:
            snaps[str(i)] = "Empty"
    snapshot_map[ws_name] = snaps
    print(f"✓ {ws_name} → {snaps}")

# 3. Publish full map via MQTT (retained) + local backup
if snapshot_map:
    json_payload = json.dumps(dict(snapshot_map), indent=2, ensure_ascii=False)
    try:
        subprocess.run([
            "mosquitto_pub",
            "-h", MQTT_HOST,
            "-t", MQTT_TOPIC,
            "-m", json_payload,
            "-r"
        ], check=True, capture_output=True, text=True)
        print(f"✅ Published snapshot map to MQTT → {MQTT_TOPIC}")
    except Exception as e:
        print(f"❌ MQTT publish failed: {e} (install mosquitto or set MQTT_HOST env var)")

    # Local backup (optional — you can .gitignore this if you want)
    output_file = TOTALMIX_FOLDER / "ufx2_snapshot_map.json"
    output_file.write_text(json_payload, encoding="utf-8")
    print(f"✅ Local backup saved to {output_file}")
else:
    print("❌ No snapshot data found — nothing published")

print("Scraper finished.")