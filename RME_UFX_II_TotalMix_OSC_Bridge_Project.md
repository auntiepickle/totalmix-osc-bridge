# RME UFX II TotalMix OSC Bridge – Project Overview

**Last updated**: April 8, 2026 (commit e4fb5dca)

**Goal**  
Reliable single-slot OSC bridge for Cirklon (and any MQTT client) to control TotalMix FX submixes and FX sends (Orville on AES/AN 7/8, etc.).

**Current Architecture (Centralized MQTT → OSC Server)**
- `bridge.py` – owns everything (macros, state, OSC client)
- Clients (Cirklon, HA, web UI) only publish `totalmix/macro/<name>`
- Dynamic submix selection via macros in `mappings.json`
- Fader sends still use `/<row>/volume<channel>` pattern from `ufx2_channel_map.json`
- Workspaces & snapshots loaded from `ufx2_snapshot_map.json` (dynamic, no more hard-coded WORKSPACE_NAMES)

**Key Files**
- `mappings.json` – data-driven macros (ramp, LFO, setSubmix, volume, etc.)
- `ufx2_channel_map.json` – static channel definitions (AES/AN 7/8 FX routing)
- `ufx2_snapshot_map.json` – workspace/snapshot slot mapping
- `operations.py` – reusable ramp / LFO operations

**Deployment**  
Docker / docker-compose or venv on always-on machine.