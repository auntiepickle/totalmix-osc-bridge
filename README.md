# TotalMix OSC Bridge

Automate RME TotalMix FX from any MIDI controller. One trigger fires multi-step macros — workspace switches, BPM-synced fader ramps, LFOs — delivered over OSC to TotalMix in real time.

---

## Why this exists

TotalMix FX has OSC built in and it's excellent. What it doesn't have is automation: the built-in MIDI remote is one CC → one fader, static, no timing. There's no way to say "when I hit this note, smoothly ramp the reverb send over 4 bars at current tempo, then switch to the breakdown snapshot." That workflow — common in hardware-heavy live rigs where TotalMix *is* the mixer — simply wasn't a product anywhere.

This bridge fills that gap. It sits between your MIDI controller and TotalMix's OSC interface and adds the layer that's missing: multi-step macros, tempo-synced operations, workspace orchestration, and a web UI to monitor and configure everything without touching JSON.

**Why OSC instead of more MIDI:** MIDI CC is 7-bit (128 steps). A fader ramp over MIDI is 128 discrete jumps — audible on a hardware send effect. OSC carries 32-bit floats. Ramps are smooth at any resolution. OSC over UDP on LAN is also orders of magnitude more responsive than MIDI's serial bandwidth under load.

---

## Signal chain

```
MIDI controller → USB → Browser (Web MIDI API)
  → WebSocket → FastAPI server
  → bridge.run_macro() → operations (ramp / LFO)
  → UDP OSC → TotalMix FX on RME UFX II
```

Any standard MIDI controller works. The bridge responds to CC, Note On, and Note Off messages.

Optional: MQTT (Home Assistant / Mosquitto) for home automation integration — trigger macros from HA automations, or react to bridge state in your home environment. Not required for core functionality.

---

## Features

- **MIDI triggers** — bind CC, Note On, or Note Off (any number, any channel) to a macro
- **Fire modes** — `ignore`, `queue`, or `restart` when a macro is already running
- **Workspace + snapshot switching** — macros declare target by name; bridge resolves slot numbers and switches via OSC only when needed
- **BPM-synced ramp and LFO** — smooth fader moves over musical time (`bars × BPM`); use `"bpm": "clock"` to sync to live MIDI clock tempo
- **Live web UI** — macro cards with progress bars, five-state LED indicators, real-time WebSocket updates
- **Inline config editing** — edit any macro in the browser; changes write to disk and hot-reload instantly
- **MIDI clock BPM display** — reads `0xF8` timing clock and shows live BPM in the header
- **OSC address discovery** — run the OSC monitor, move a fader in TotalMix, see the address *(M4)*
- **MQTT integration** — optional; Home Assistant can trigger macros and receive live workspace state
- **Auto-backup** — every config save creates a timestamped copy in `backups/`

---

## Quick start

### Option A — Local (laptop, no server needed)

```bash
git clone https://github.com/auntiepickle/totalmix-osc-bridge.git
cd totalmix-osc-bridge

python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp mappings.example.json mappings.json
cp ufx2_channel_map.example.json ufx2_channel_map.json
cp ufx2_snapshot_map.example.json ufx2_snapshot_map.json

export OSC_IP=127.0.0.1   # TotalMix is on the same machine
uvicorn web.web_client:app --host 0.0.0.0 --port 8080
```

Open `http://localhost:8080`. MIDI works on localhost without HTTPS.
Point TotalMix OSC output to `127.0.0.1`.

### Option B — Docker (always-on server)

```bash
cp docker-compose.example.yml docker-compose.yml
# Edit docker-compose.yml — set OSC_IP at minimum

cp mappings.example.json mappings.json
cp ufx2_channel_map.example.json ufx2_channel_map.json

docker compose build --no-cache && docker compose up -d
docker compose logs -f
```

HTTPS is required for Web MIDI on a real IP — see [docs/SETUP.md](docs/SETUP.md#https).

---

## Config files

| File | Purpose |
|---|---|
| `mappings.json` | Macro definitions — steps, MIDI triggers, fire modes, operations |
| `ufx2_snapshot_map.json` | Workspace names → TotalMix Quick Select slots + snapshot names |
| `ufx2_channel_map.json` | OSC address → human name map for routing labels on cards |

All three have `*.example.json` counterparts. The real files are git-ignored so live edits survive `git pull`. Full schema: [docs/CONFIG_REFERENCE.md](docs/CONFIG_REFERENCE.md).

---

## Documentation

| Doc | What's in it |
|---|---|
| [docs/HOW_IT_WORKS.md](docs/HOW_IT_WORKS.md) | Signal flow, every component, state machine, TotalMix OSC gotchas, thread safety, frontend architecture |
| [docs/CONFIG_REFERENCE.md](docs/CONFIG_REFERENCE.md) | Full schema for all three config files with examples |
| [docs/SETUP.md](docs/SETUP.md) | Local and Docker deployment, env vars, HTTPS, MQTT / Home Assistant |
