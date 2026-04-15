# TotalMix OSC Bridge

Automate RME TotalMix FX from any MIDI controller. One trigger fires multi-step macros: workspace switches, BPM-synced fader ramps, send level LFOs. Everything is delivered over OSC to TotalMix in real time.

---

## Why this exists

TotalMix FX has OSC built in. What it lacks is automation. The built-in MIDI remote maps one CC to one fader, statically. There is no way to say "ramp the reverb send over 4 bars at current tempo, then load the breakdown snapshot." For hardware-heavy live rigs where TotalMix is the mixer, that gap is a real problem.

This bridge fills it. It sits between a MIDI controller and TotalMix's OSC interface and adds what is missing: multi-step macros, tempo-synced operations, workspace orchestration, and a web UI for monitoring and config.

**On precision:** MIDI CC is 7-bit, 128 steps. A fader ramp over MIDI is 128 discrete jumps, audible on a hardware send effect. OSC carries 32-bit floats. Ramps are smooth at any resolution. OSC over UDP on LAN is also faster and more consistent than MIDI's serial bandwidth under load. For send automation, the difference is real.

---

## Signal chain

```
MIDI controller -> USB -> Browser (Web MIDI API)
  -> WebSocket -> FastAPI server
  -> bridge.run_macro() -> operations (ramp / LFO)
  -> UDP OSC -> TotalMix FX
```

Any standard MIDI controller works. The bridge responds to CC, Note On, and Note Off.

MQTT is optional. If you run Home Assistant or a similar home automation stack, the bridge can publish state and receive macro triggers over MQTT. Without a broker, everything works over the WebSocket and REST API.

---

## Features

- **MIDI triggers** — bind CC, Note On, or Note Off (any number, any channel) to a macro
- **Fire modes** — `ignore`, `queue`, or `restart` when a macro is already running
- **Workspace and snapshot switching** — macros declare target by name; the bridge resolves slot numbers and switches via OSC only when needed
- **BPM-synced ramp and LFO** — smooth fader moves over musical time (`bars x BPM`); use `"bpm": "clock"` to follow live MIDI clock
- **Live web UI** — macro cards with progress bars, five-state LED indicators, real-time WebSocket updates
- **Inline config editing** — edit any macro in the browser; changes write to disk and hot-reload without a restart
- **MIDI clock BPM display** — reads `0xF8` timing clock messages and shows live BPM
- **MQTT integration** — optional; Home Assistant can trigger macros and receive workspace state
- **Auto-backup** — every config save writes a timestamped copy to `backups/`

---

## Quick start

### Option A: Local (laptop, no server required)

```bash
git clone https://github.com/auntiepickle/totalmix-osc-bridge.git
cd totalmix-osc-bridge

python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp mappings.example.json mappings.json
cp ufx2_channel_map.example.json ufx2_channel_map.json
cp ufx2_snapshot_map.example.json ufx2_snapshot_map.json

export OSC_IP=127.0.0.1
uvicorn web.web_client:app --host 0.0.0.0 --port 8080
```

Open `http://localhost:8080`. Point TotalMix OSC output to `127.0.0.1`. MIDI works on localhost without HTTPS.

### Option B: Docker (always-on server)

```bash
cp docker-compose.example.yml docker-compose.yml
# Set OSC_IP at minimum; see docs/setup.md for all options

cp mappings.example.json mappings.json
cp ufx2_channel_map.example.json ufx2_channel_map.json

docker compose build --no-cache && docker compose up -d
docker compose logs -f
```

HTTPS is required for Web MIDI on a real IP. See [docs/setup.md](docs/setup.md#https).

---

## Config files

| File | Purpose |
|---|---|
| `mappings.json` | Macro definitions: steps, MIDI triggers, fire modes, operations |
| `ufx2_snapshot_map.json` | Workspace names to TotalMix Quick Select slots and snapshot names |
| `ufx2_channel_map.json` | OSC address to human name map for routing labels on cards |

All three have `*.example.json` counterparts. The real files are git-ignored so live edits survive `git pull`. Full schema in [docs/config.md](docs/config.md).

---

## Documentation

| Doc | Contents |
|---|---|
| [docs/architecture.md](docs/architecture.md) | Signal flow, component responsibilities, TotalMix OSC gotchas, thread model, frontend patterns |
| [docs/config.md](docs/config.md) | Full schema for all three config files with examples |
| [docs/setup.md](docs/setup.md) | Local and Docker deployment, env vars, HTTPS, MQTT and Home Assistant |
