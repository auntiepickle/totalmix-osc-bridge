# Architecture

The bridge is a single Python process — one FastAPI server, one asyncio event loop, one MQTT client running in a background thread, and a pool of daemon threads for macro execution. Everything routes through the `bridge` singleton.

---

## Signal flow

```
Cirklon (MIDI out) → USB → Browser (Web MIDI API)
  → midi.js handleMIDIMessage() → fireMacro() over WebSocket
  → FastAPI POST /api/trigger/{name}
  → threading.Thread → bridge.run_macro()
      ├── workspace/snapshot switch via OSC (if needed)
      ├── operations.py ramp or LFO (blocking, cancellable)
      └── osc_client.send_message() → UDP → TotalMix FX

MQTT broker ← TotalMix FX publishes workspace/snapshot changes
  → mqtt_handler.on_message()
      ├── bridge.update_workspace/snapshot() → broadcasts to WS clients
      └── suppressed when a macro is in flight (see below)
```

The browser is a thin relay. All state and logic lives server-side.

---

## Components

### bridge.py

Central orchestration. Creates the `bridge` singleton at module load. `web_client.py` imports it directly.

**Module globals**

| Name | Purpose |
|---|---|
| `MAPPINGS` | Loaded from `mappings.json` (falls back to `mappings.example.json`). Hot-reloadable. |
| `MAPPINGS_IS_EXAMPLE` | `True` when running from the example file. Drives the setup banner in the UI. |
| `SNAPSHOT_MAP` | Loaded from `/app/config/ufx2_snapshot_map.json` (Docker SMB mount) or local fallback. |
| `osc_client` | `pythonosc.SimpleUDPClient` pointed at `OSC_IP:OSC_PORT`. Created once at startup. |
| `ws_clients` | List of active FastAPI `WebSocket` connections. Shared with `web_client.py` via import. |

**`TotalMixOSCBridge` — key state**

| Attribute | Type | Purpose |
|---|---|---|
| `current_workspace` | `str \| None` | Last-known workspace name (not slot number) |
| `current_snapshot` | `str \| None` | Last-known snapshot name, lowercased and stripped |
| `mappings` | `dict` | Live macro definitions — mutable by the live editor |
| `snapshot_map` | `dict` | Workspace → slot/snapshot lookup — updated by SMB watcher |
| `channel_map` | `dict` | OSC address → submix/send names for routing label generation |
| `channel_map_is_example` | `bool` | `True` when running from the example channel map |
| `mqtt_client` | `paho.Client` | Set after `start_mqtt()`. Used to publish state back to MQTT. |
| `main_loop` | `asyncio.AbstractEventLoop` | Set by `web_client.startup_event`. Required for thread-safe broadcast. |
| `_suppress_handler` | `bool` | Blocks MQTT feedback while a macro is sending OSC (see feedback loop prevention) |
| `_last_macro_end_time` | `float` | `time.time()` at last completion. Drives the 2.5s cooldown window. |
| `_running_macros` | `set[str]` | Names of macros currently executing in daemon threads |
| `_cancel_events` | `dict[str, Event]` | `threading.Event` per running macro — set to cancel on `restart` mode |
| `_queued_params` | `dict[str, float]` | Pending param saved by `queue` or `restart` mode |

**Key methods**

- `run_macro(name, param)` — fire mode gate → workspace/snapshot switch → operation steps → broadcast. Always runs in a daemon thread.
- `broadcast_state(macro_update, macro_event)` — thread-safe WebSocket push. Detects whether called from asyncio (FastAPI) or a sync thread (MQTT) and routes accordingly.
- `update_workspace(name, slot)` / `update_snapshot(name, index)` — update current state and broadcast.
- `get_routing_label(macro_name)` — walks `channel_map` to match OSC addresses in steps, returns `"AN 3 → ADAT 1"` style string.
- `_load_channel_map()` — tries `ufx2_channel_map.json` then example fallback. Sets `channel_map_is_example`.

---

### web_client.py

FastAPI app. Serves the static web UI, REST API, and WebSocket endpoint. Imports the `bridge` singleton.

**Startup:** `startup_event()` calls `bridge.start_mqtt()` and stores `asyncio.get_running_loop()` as `bridge.main_loop`. Without this, MQTT-thread broadcasts are silently dropped.

**REST endpoints**

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/macros` | All macros from live `bridge.mappings` |
| `POST` | `/api/trigger/{name}` | Fire macro in daemon thread |
| `GET` | `/api/status` | Config summary — counts, current WS/SS, example flags |
| `GET/POST` | `/api/config/mappings` | Read or save `mappings.json` |
| `PATCH` | `/api/config/macros/{name}` | Update single macro inline |
| `GET/POST` | `/api/config/channel_map` | Read or save `ufx2_channel_map.json` |
| `GET/POST` | `/api/config/snapshot_map` | Read or save `ufx2_snapshot_map.json` |
| `POST` | `/api/config/mappings/init-from-example` | Copy example → `mappings.json`, clear `mappings_is_example` |
| `POST` | `/api/config/channel_map/init-from-example` | Copy example → `ufx2_channel_map.json`, clear `channel_map_is_example` |
| `POST` | `/api/reload` | Reload `mappings.json` from disk |
| `GET` | `/api/snapshot_map` | Current `bridge.snapshot_map` (for UI validation) |

---

### mqtt_handler.py

MQTT subscriptions, workspace/snapshot routing, and the SMB file watcher.

**Subscriptions**

| Topic | Handler |
|---|---|
| `totalmix/workspace` | Send `/loadQuickWorkspace <slot>` via OSC, update `bridge.current_workspace` |
| `totalmix/snapshot` | Send `/3/snapshots/{index}/1` via OSC, update `bridge.current_snapshot` |
| `totalmix/macro/<name>` | Fire `bridge.run_macro(name, param)` |
| `totalmix/config/snapshot_map` | Update in-memory `SNAPSHOT_MAP` and republish |

**SMB watcher** — `map_watcher()` runs as a daemon thread, polling `/app/config/ufx2_snapshot_map.json` every 5 seconds. On change, it reloads the file, syncs `bridge.snapshot_map`, and republishes the workspace list to MQTT.

---

### operations.py

Pluggable operation system. Register a function with `@OperationRegistry.register("name")` and it becomes available as a step `operation.type` in `mappings.json`.

**Built-in operations**

- **`ramp`** — smooth value change from `param` down to 0 over musical time. Supports `triangle` (up then down) and `linear` curves. Duration = `bars × 4 × 60 / bpm` seconds. Sends OSC at 20 steps/sec.
- **`lfo`** — sine wave at `depth` amplitude over musical time. Duration = same formula. Phase = `t × 2π × (bpm/60) × 4` — one full cycle per bar. Sends at 30 steps/sec.

Both operations check a `threading.Event` before each step. When `restart` mode cancels a macro, the event fires, the operation sends `0.0` and exits immediately.

---

### config.py

Environment variable loading and shared utilities.

**`snapshot_num_to_osc_index(snap_num)`** — converts a 1–8 snapshot slot to the TotalMix OSC button index. TotalMix orders its snapshot buttons bottom-to-top in the OSC namespace: slot 1 is index 8, slot 8 is index 1. The recall address is `/3/snapshots/{index}/1`. Used in both `bridge.py` and `mqtt_handler.py`.

---

### osc.py

Standalone OSC sender used by `mqtt_handler.py`. Maintains a module-level `SimpleUDPClient` cache keyed by `(ip, port)` — one socket per destination, reused on every call. `bridge.py` uses its own `osc_client` directly and does not call `osc.py`.

---

### osc_monitor.py

UDP listener for OSC address discovery. Enable with `ENABLE_OSC_MONITOR=true`. Logs all incoming messages from TotalMix to `osc_monitor.log`. Useful for finding OSC addresses before writing macros.

---

## TotalMix OSC reference

| OSC address | Value | Effect |
|---|---|---|
| `/loadQuickWorkspace` | `float(slot)` | Switch to workspace slot (1-indexed) |
| `/3/snapshots/{index}/1` | `1.0` | Recall snapshot. `index = 9 - snap_num` |
| `/setSubmix` | `float(index)` | Select submix (output bus) for editing |
| `/1/volume{N}` | `0.0–1.0` | Set send level for channel N in the current submix |

The snapshot index reversal (`9 - snap_num`) is a TotalMix GUI artifact. Buttons are laid out bottom-to-top in the OSC namespace. `config.snapshot_num_to_osc_index()` encapsulates this.

---

## Workspace / snapshot state machine

```
macro trigger arrives
  ├── is it already running?
  │     └── fire_mode: ignore → drop | queue → save param | restart → cancel + save
  ├── resolve ws_name + snap_name from mappings.json
  ├── look up slot + snap_num from snapshot_map
  ├── already on target? → skip switch
  ├── force_switch=False and another macro running? → skip, emit macro_skipped
  └── switch needed:
        1. set _suppress_handler = True
        2. send /loadQuickWorkspace → sleep(1.0)
        3. send /3/snapshots/{index}/1 → sleep(0.3)
        4. execute steps
        5. _suppress_handler = False, _last_macro_end_time = now
        6. MQTT suppression window: 2.5s after completion
```

---

## Feedback loop prevention

When `run_macro()` sends `/loadQuickWorkspace` and the snapshot OSC message, TotalMix publishes state back to MQTT (`totalmix/workspace`, `totalmix/snapshot`). Without suppression, `mqtt_handler.on_message()` would re-process those messages and overwrite `current_workspace`/`current_snapshot` with stale slot numbers mid-ramp.

Two guards handle this:
1. `_suppress_handler = True` during execution — drops workspace/snapshot MQTT messages immediately.
2. `_last_macro_end_time` cooldown — suppresses messages for 2.5s after completion, catching any delayed publishes.

---

## Thread safety

`run_macro()` runs in `daemon=True` threads — one per concurrent macro execution. `broadcast_state()` routes through `asyncio.run_coroutine_threadsafe()` when called from an MQTT or macro thread, using `bridge.main_loop` set at FastAPI startup.

`_running_macros`, `_cancel_events`, and `_queued_params` are mutated from multiple threads without explicit locks. In practice, Python's GIL prevents torn writes on dict/set operations, but this is a known risk under heavy concurrency. See [docs/DEVELOPMENT.md](DEVELOPMENT.md) for details.

---

## Frontend

Three JS files load in order: `app.js` → `ui.js` → `midi.js`.

**`app.js`** — WebSocket connection, macro loading, LED state machine, last-fired tracking, example-mappings banner.

**`ui.js`** — Card rendering grouped by workspace → snapshot (both collapsible, state persisted in `localStorage`). Inline macro editor using `data-field` path traversal to build the PATCH payload. Live config editor modal for full JSON editing. Settings menu with status summary.

**`midi.js`** — Web MIDI init, device selector, CC routing to macros, MIDI clock BPM detection from `0xF8` timing messages (24 pulses per quarter note).

**Collapsible grid groups** use `display:contents` on the wrapper div. Children remain direct grid items regardless of nesting depth. Toggling `none` ↔ `contents` collapses or expands without breaking the CSS grid layout.
