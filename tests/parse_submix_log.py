import re
import glob
from collections import OrderedDict

print("=== SUBMIX + CHANNEL LISTING TOOL ===")
print("Reading all osc_monitor.log* files...\n")

log_files = sorted(glob.glob("osc_monitor.log*"), reverse=True)
full_log = ""
for f in log_files:
    with open(f, "r", encoding="utf-8") as file:
        full_log += file.read()

submix_order = OrderedDict()

for line in full_log.splitlines():
    line = line.strip()
    if "/1/labelSubmix" in line:
        match = re.search(r'/1/labelSubmix → (.*?) (←|$)', line)
        if match:
            name = match.group(1).strip()
            if name not in submix_order:
                submix_order[name] = []

# Collect all unique volume channels per submix
for name in submix_order:
    hw = set()
    sw = set()
    # Look for volume lines after each labelSubmix
    for line in full_log.splitlines():
        if f"/1/labelSubmix → {name}" in line:
            # Find the next 200 lines after this submix change
            start = full_log.find(f"/1/labelSubmix → {name}")
            chunk = full_log[start:start+8000]
            for m in re.finditer(r'/([12])/volume(\d+)', chunk):
                row = m.group(1)
                ch = int(m.group(2))
                if row == '1':
                    hw.add(ch)
                else:
                    sw.add(ch)
    submix_order[name] = {"hardware": sorted(hw), "software": sorted(sw)}

print("\n=== ALL SUBMIXES & CHANNELS FOUND ===")
for name, channels in submix_order.items():
    print(f"\nSubmix: {name}")
    print(f"  Hardware Input channels: {channels['hardware'] or 'none'}")
    print(f"  Software Playback channels: {channels['software'] or 'none'}")

with open("ufx2_channel_map.json", "w") as f:
    import json
    json.dump(dict(submix_order), f, indent=2)

print("\nSaved to ufx2_channel_map.json – this is your per-unit definition file.")