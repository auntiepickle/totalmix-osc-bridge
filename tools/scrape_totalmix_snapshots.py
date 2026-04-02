#!/usr/bin/env python3
"""
TotalMix Snapshot Scraper - Clean version for HASS.Agent
- Pure JSON generation (no MQTT code at all)
- Outputs clean JSON to stdout (HASS.Agent captures this)
- Saves local backup file
"""
import re
import json
import os
import getpass
import sys
from pathlib import Path
from collections import defaultdict

TOTALMIX_FOLDER = Path(os.getenv("TOTALMIX_FOLDER", rf"C:\Users\{getpass.getuser()}\AppData\Local\TotalMixFX"))

print("🔍 TotalMix Snapshot Scraper (Grok Home Studio Project — HASS.Agent edition)", file=sys.stderr)

# 1. Workspace names from preferences.xml
pref_file = TOTALMIX_FOLDER / "rme.totalmix.preferences.xml"
workspace_map = {}
if pref_file.exists():
    text = pref_file.read_text(encoding="utf-8")
    for m in re.finditer(r'<val e="PresetName(\d+)" v="([^"]*)"/>', text):
        num = int(m.group(1))
        name = m.group(2).strip()
        if name and name != "<Empty>" and name != "":
            workspace_map[num] = name

# 2. Snapshot names per workspace from presetN.tmws
snapshot_map = defaultdict(dict)
for ws_num, ws_name in workspace_map.items():
    preset_file = TOTALMIX_FOLDER / f"preset{ws_num}.tmws"
    if not preset_file.exists():
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

# Output
if snapshot_map:
    json_output = json.dumps(dict(snapshot_map), indent=2, ensure_ascii=False)
    print(json_output)                    # ← HASS.Agent captures this
    # Local backup
    output_file = TOTALMIX_FOLDER / "ufx2_snapshot_map.json"
    output_file.write_text(json_output, encoding="utf-8")
    print(f"✅ Local backup saved to {output_file}", file=sys.stderr)
else:
    print("{}", file=sys.stderr)