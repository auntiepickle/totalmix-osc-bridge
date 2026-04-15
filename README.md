# TotalMix OSC Bridge

Nobody wants to move a fader unless they mean to. This bridge makes that a deliberate choice rather than a constant task.

---

## Why this exists

If you run analog hardware through RME TotalMix, your sends are configured and left alone. You patch a synth into a reverb, dial in the level, move on. But sometimes the send itself is part of the performance: a synth that blooms into the room on a specific bar, a delay return that opens for a breakdown and closes again. TotalMix has no way to do that. You either reach for the fader or you skip it.

This bridge adds the missing layer. One MIDI trigger fires a macro: load the right workspace and snapshot, then ramp a send over N bars in time with your sequencer clock. No fader touch required. The signal path becomes something you compose with, not just configure.

**On OSC vs MIDI for this:** MIDI CC is 7-bit, 128 steps. A ramp over MIDI is 128 discrete jumps, audible on a hardware send. OSC carries 32-bit floats. Ramps are smooth at any resolution. For automation into analog hardware the difference is real and audible.

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
