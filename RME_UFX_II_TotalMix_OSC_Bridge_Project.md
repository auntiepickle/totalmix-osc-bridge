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

### Web UI + Client-Side MIDI (M1 — April 2026)

Modern dashboard at `https://192.168.1.41.nip.io:9445` with:
- Live macro cards + per-card MIDI badges showing source device/channel/value
- Manual MIDI device selector in top bar
- Secure context required by Web MIDI API (Caddy + nip.io)
## UI Overhaul Plan - Macro Cards & Top Bar (M2 Phase - April 2026)

**Status**: Documented & ready for implementation (this commit).  
**Branch**: M2_branch  
**Goal**: Fix awkward spacing, buried JSON, MIDI connect bug, poor button alignment/styling, and make the dashboard feel like a professional Cirklon → TotalMix control surface.

### Current State of All .md Files (Audit - April 9, 2026)
- **RME_UFX_II_TotalMix_OSC_Bridge_Project.md**: Defines single-slot OSC (port 7001), dynamic `/setSubmix`, fader sends via `ufx2_channel_map.json`, workspaces/snapshots via `ufx2_snapshot_map.json`, M1 Web UI (macro cards + MIDI badges).
- **MIDI_to_OSC_Mapping_System.md**: 100% data-driven. Macros live in `mappings.json` with steps, OSC paths, ramp/LFO ops. No code changes needed for new macros.
- **README.md**: Quick start + philosophy (centralized, reusable for any TotalMix user).
- **M2_gaps_and_first_tasks.md** & **MIDI_SUPPORT_AND_UI_ENHANCEMENT_PLAN.md**: High-level gaps; this new section supersedes them for UI work.

### Everything We Should Tackle (Comprehensive Issue List)

1. **Top Bar / MIDI Device Section (High Priority Bug + UX)**
   - Auto-connect works but “MIDI Connected: U6MIDI Pro” only appears after manual **Connect** click.
   - “Connect” button stays active post-auto-connect.
   - No Disconnect / Rescan options. Layout cramped.

2. **Card Layout & Spacing**
   - Uneven padding, too much dead space around progress bars/buttons.
   - Side-by-side cards feel squished.

3. **Information Hierarchy & Details**
   - JSON buried behind **DETAILS ▼** (janky expansion).
   - Key info (OSC Preview, Routing, Duration, MIDI Triggers, BPM) not glanceable.

4. **Command Badge Alignment**
   - Green “U6MIDI Pro • CC42 • ch1” badge floats awkwardly.

5. **FIRE & RAMP Button Styling**
   - FIRE orange is good direction but no hover/active states, no polish.
   - RAMP label looks like disabled text.

6. **General Polish**
   - No loading feedback during fire/ramp.
   - No last-triggered timestamp or quick-copy OSC.

### Proposed Overhaul Plan (Step-by-Step)

**Phase 1: Quick Wins & Bug Fixes (1-2 hours)**
- Fix MIDI auto-connect → instant status update on load.
- Redesign top bar: Workspace | Snapshot | MIDI Controller [dropdown] [green pill] [Disconnect] [Rescan].

**Phase 2: Card Redesign (Main Lift)**
New card wireframe:
[Header]  name                          [perfectly aligned MIDI badge]
Description + routing_label
[Progress bar — thin, outer border only, reversed gradient]
[FIRE] (full-width, hero orange, hover lift, 🔥 icon)
[RAMP] (secondary, side-by-side or toggle)
[Always-visible info row]
OSC: /setSubmix = 0.500
Routing: AN 1/2 → AES …
Duration: 3.4s   BPM: 148
[Advanced ▼] → inline panel with Full macro JSON (syntax-highlighted + copy)
text**Phase 3: Polish**
- Loading spinners, focus states, smooth Tailwind transitions.
- Optional: last-triggered timestamp, copy OSC button.

**Implementation Notes**
- All changes in web/static/ (ui.js + Tailwind).
- No backend changes needed (leverages existing `mappings.json`, `operations.py`, OSC preview logic).
- Keep using current progress-bar animation from last commit.

**Next Step After Commit**
Reply with “Go ahead and generate the mockup” or “Start coding the MIDI bar fix first”.

This documentation is now committed and lives forever in the repo.

