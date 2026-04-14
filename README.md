# TotalMix OSC Bridge

Route MIDI CC from a hardware sequencer through a browser to an RME UFX II — without touching TotalMix's built-in MIDI learn.

A Cirklon (or any MIDI controller) sends CC messages. The browser reads them via the Web MIDI API and fires macros over WebSocket to a persistent server. The server handles workspace switching, BPM-synced ramps, and OSC delivery to TotalMix FX. Everything — state, logic, config — lives in one place.

---

## Signal chain

```
Cirklon → USB → Browser (Web MIDI API)
  → WebSocket → FastAPI server
  → bridge.run_macro() → operations (ramp / LFO)
  → UDP OSC → TotalMix FX on RME UFX II
```

MQTT (Home Assistant / mosquitto) carries workspace and snapshot state in both directions.

---

## Features

- **MIDI CC triggers** — bind any CC number + channel to a macro in `mappings.json`
- **Fire modes** — `ignore`, `queue`, or `restart` when a macro is already running
- **Workspace + snapshot switching** — macros declare their target by name; the bridge resolves slot numbers and switches via OSC only when needed
- **BPM-synced ramp and LFO** — smooth fader moves over musical time (bars × BPM), cancellable mid-execution
- **Live web UI** — macro cards with progress bars, four-state LED indicators, real-time WebSocket updates
- **Inline config editing** — edit any macro in the browser; changes write to disk and hot-reload instantly
- **MIDI clock BPM display** — reads `0xF8` timing clock from the Cirklon and shows live BPM in the header
- **MQTT integration** — Home Assistant can trigger macros and receive live state
- **Auto-backup** — every config save creates a timestamped copy in `backups/`

---

## Quick start

**1. Deploy**

```bash
cp docker-compose.example.yml docker-compose.yml
# Edit docker-compose.yml — set OSC_IP, MQTT_BROKER, SMB paths
docker compose build && docker compose up -d
```

**2. Set up config files**

```bash
cp mappings.example.json mappings.json
cp ufx2_channel_map.example.json ufx2_channel_map.json
# Edit both to match your TotalMix routing
```

`ufx2_snapshot_map.json` lives outside the repo (on your NAS/SMB share). See [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).

**3. Open the UI**

Navigate to `https://YOUR-SERVER-IP` (HTTPS required for Web MIDI). Select your MIDI input device in the header. Macro cards load automatically from `mappings.json`.

If `mappings.json` is missing the UI shows a setup banner — click **Use as my mappings.json** to initialize from the example.

---

## Config files

| File | Purpose |
|---|---|
| `mappings.json` | Macro definitions — steps, MIDI triggers, fire modes, operations |
| `ufx2_snapshot_map.json` | Workspace names → TotalMix Quick Select slots + snapshot names |
| `ufx2_channel_map.json` | OSC address → human name map used to generate routing labels on cards |

All three have `*.example.json` counterparts in the repo. The real files are git-ignored so live edits survive `git pull`. Full schema: [docs/CONFIG_REFERENCE.md](docs/CONFIG_REFERENCE.md).

---

## Documentation

| Doc | What's in it |
|---|---|
| [docs/HOW_IT_WORKS.md](docs/HOW_IT_WORKS.md) | Signal flow, every component, state machine, TotalMix OSC gotchas, thread safety, frontend patterns |
| [docs/CONFIG_REFERENCE.md](docs/CONFIG_REFERENCE.md) | Full schema for all three config files |
| [docs/SETUP.md](docs/SETUP.md) | Docker deployment, local dev, env vars, HTTPS, Home Assistant |
