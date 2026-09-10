# Architecture

The bridge is one Python process: a FastAPI app on one asyncio loop, a handful
of daemon threads (OSC feedback listeners, macro runs, the knob watcher, the
duck supervisor, MQTT), and one shared singleton, `bridge` in
`tmosc/bridge.py`. The browser UI and the native tray agent are clients of its
REST + WebSocket API.

---

## Signal flow

```
MIDI controller --+-- tray agent (agent/, native)  -- POST /api/knob, /api/trigger --+
                  +-- browser (web/static/midi.js) -- WebSocket {"type":"knob"} -----+
                                                                                    v
Home Assistant -- MQTT totalmix/macro/<name>, totalmix/knob/<name> -->  bridge.run_macro / knob_set
                                                                                    |
                                 Global OSC writers (tmosc/global_transport.py)     | UDP 7002
                                                                                    v
                                                                               TotalMix FX
                                                                                    | UDP 9002 feedback
                                 tmosc/global_listener.py (device state)  <---------+
                                    |                     |
                        WebSocket broadcasts       MQTT retained state (knob / workspace / snapshot)
                                    v
                                 web UI
```

Two OSC transports exist. **Global** (TotalMix FX 2.1+ "Remote 2", absolute
addresses such as `/output/0/volume`) is the standard: knobs, name-targeted
macro steps, ducking, meters and device sync all go through it. **Classic**
(Remote 1, relative page addresses, aim-then-write) is legacy and remains only
for workspace/snapshot switching, the physical-table sweep and the liveness
probe. Several sections further down describe classic-era mechanics; they are
still accurate for those remaining paths.

---

## Modules

| Module | Owns |
|---|---|
| `tmosc/bridge.py` | `TotalMixOSCBridge`: macro runner, knob engine (device sync, hold, VCA groups, MQTT knob state), workspace/snapshot switching, MIDI-owner state, WebSocket broadcast plumbing, classic aiming, sweep and probe |
| `tmosc/api/app.py` | FastAPI app: every REST endpoint, `/ws`, config persistence (atomic writes + auto-backup), uploads, startup wiring. Run with `uvicorn tmosc.api.app:app`; `web/web_client.py` is a compatibility shim for older Docker images |
| `tmosc/__main__.py` | `python -m tmosc` and the frozen exe entry: `app_paths.prepare()`, then in-process uvicorn |
| `tmosc/global_transport.py` | Global OSC writers: name to address resolution through the physical table, unit transforms, heartbeat/liveness |
| `tmosc/global_listener.py` | Global OSC feedback into `GlobalDeviceState` (params, names, mix sends, levels, snapshots, human-change log) |
| `tmosc/global_units.py` | Per-parameter unit map and the RME fader law (`fader_lin` / `fader_db`) |
| `tmosc/osc.py`, `osc_listener.py`, `osc_monitor.py` | Classic client cache, classic feedback `DeviceState`, legacy log-only monitor |
| `tmosc/physical_table.py` | Measured strip / hardware-offset alias table shared by both transports |
| `tmosc/operations.py` | `OperationRegistry` (ramp, LFO) and `shape_value` / `unshape_value` range mapping |
| `tmosc/duck_engine.py` | Sidechain duck supervisor (25 Hz) with restore-on-exit |
| `tmosc/mqtt_handler.py` | MQTT subscriptions (macros, knobs, workspace/snapshot) and the snapshot-map watcher |
| `tmosc/discovery.py` | LAN auto-discovery UDP responder (`TMOSC-DISCOVER?` answered with `TMOSC-BRIDGE <port>`) |
| `tmosc/config.py` | Environment into settings; `snapshot_num_to_osc_index()` |
| `tmosc/app_paths.py` | Where state lives (repo root from source and Docker, `%APPDATA%/tmosc-bridge` when frozen) and the `config.env` loader |

### Frontend

Load order: `api.js` -> `app.js` -> `ui.js` -> `midi.js` (plus `modul/knob.js`, `modul/graph.js` and vendored uPlot).

| File | Owns |
|---|---|
| `api.js` | `window.API.*`: the fetch layer for `/api/...` |
| `app.js` | Global state, WebSocket handler, knob stream, health polling, skins |
| `ui.js` | Card / rack / MODUL rendering, macro editor, routing picker, MIDI matrix, knob curves |
| `midi.js` | Web MIDI, learn, BPM clock, emulator, tray coexistence (yield and relay) |

Server/client mirrors that must change together: `RUNTIME_FIELDS` and
`MACRO_NAME_RE` (`tmosc/api/app.py` and `ui.js`), the fader law
(`global_units.py` and `ui.js`), and the trigger matcher in the native agent
(a port of `midi.js`).

---

## bridge.py state

| Attribute | Type | Description |
|---|---|---|
| `current_workspace` | `str or None` | Last-known workspace name, not slot number |
| `current_snapshot` | `str or None` | Last-known snapshot name, lowercased and stripped |
| `mappings` | `dict` | Live macro definitions. Hot-reloadable via the UI. |
| `snapshot_map` | `dict` | Workspace to slot/snapshot lookup |
| `channel_map` | `dict` | OSC address to routing label data |
| `mappings_is_example` | `bool` | True when running from `mappings.example.json` |
| `channel_map_is_example` | `bool` | True when running from `ufx2_channel_map.example.json` |
| `mqtt_connected` | `bool` | True when MQTT broker connection is active. False if no broker is configured. |
| `main_loop` | event loop | Set by `startup_event()` in `tmosc/api/app.py`. Required for thread-safe broadcast. |
| `_suppress_handler` | `bool` | Blocks MQTT feedback during macro execution |
| `_running_macros` | `set[str]` | Names of currently executing macros |
| `_cancel_events` | `dict` | One `threading.Event` per running macro, set to cancel on `restart` |
| `_queued_params` | `dict` | Pending param saved by `queue` or `restart` mode |

---

## run_macro() execution path

```
1.  Macro name valid?            No  -> log error, return
2.  OSC client configured?       No  -> broadcast macro_skipped, return
3.  Already running?
      ignore  -> drop
      queue   -> save param, return
      restart -> set cancel_event, save param, return
4.  Debounce window active?           -> drop
5.  Set _suppress_handler = True
6.  Resolve workspace slot + snapshot index from snapshot_map
7.  Already on target workspace?      -> skip switch
    force_switch=False, other macro running? -> emit macro_skipped, return
8.  Switch:
      /loadQuickWorkspace {slot}  -> wait for feedback confirmation (2 s timeout; fixed 1.0 s only without a listener)
      /3/snapshots/{9-n}/1        -> wait for confirmation (1 s timeout; 0.3 s fallback)
9.  Broadcast macro_start
10. Execute steps in order
11. Broadcast macro_complete
12. finally: _suppress_handler = False, record _last_macro_end_time
13. Fire queued param if saved
```

---

## MQTT feedback loop

When `run_macro()` sends `/loadQuickWorkspace`, TotalMix publishes its new state back over MQTT. Without suppression, `on_message()` would overwrite `current_workspace` and `current_snapshot` with raw slot numbers mid-execution.

Two guards prevent this. `_suppress_handler = True` is set for the duration of execution; `on_message()` returns immediately for workspace and snapshot topics when it is set. A 2.5-second cooldown after completion catches delayed MQTT publishes.

This issue only exists when MQTT is configured. Without a broker there is no feedback loop.

---

## Thread safety

FastAPI runs in asyncio. MQTT callbacks and macro threads are OS threads. `bridge.broadcast_state()` must work from both.

`startup_event()` in `tmosc/api/app.py` stores the running asyncio loop as `bridge.main_loop`. Sync threads call `asyncio.run_coroutine_threadsafe(self._do_broadcast(...), self.main_loop)`. Asyncio context creates a task directly. Broadcasts before FastAPI startup are silently dropped.

---

## Device capture and discovery

TotalMix pushes its state over OSC to the configured "Port outgoing" whenever the visible bank changes: `/1/labelSubmix` (selected submix name), `/{row}/trackname{n}` (channel names), `/{row}/volume{n}` (fader positions), plus pan and dB display values. Rows: 1 = hardware input, 2 = software playback, 3 = hardware output.

`osc_listener.OSCListener` receives that stream and maintains a `DeviceState`: channel data scoped per submix (row 3 output faders are submix-independent and stored under `_outputs`), plus a raw address store so unrecognized feedback is inspectable rather than lost. Volume floods are throttled to one WebSocket `device_update` event per 250ms; structural changes (submix/trackname) broadcast immediately.

`bridge.run_sweep()` (`POST /api/device/sweep`) measures the **physical table**: it walks the bank with `/setBankStart` and a row-mirror nudge, reads each hardware channel's name at every fixed position on both rows, and persists the result into `ufx2_channel_map.json` under `physical_table`. It never sends `/setSubmix` and never writes a parameter. Under the Global transport, names are additionally learned live from feedback (`/input|playback|output/N/name`) and merged into the table by the name-sync thread, so the table does not go stale between sweeps.

Page-1 channel feedback (`/1/volume{n}`, `/1/trackname{n}`) refers to whichever *row* is selected via `/1/busInput` / `/1/busPlayback` / `/1/busOutput` — the listener tracks the active row and files channel data accordingly (verified against a UFX II capture: 177 distinct feedback addresses including mutes, solos, mic gains, phantom, snapshots, and the FX section).

API surface:

| Endpoint | Purpose |
|---|---|
| `GET /api/device/state` | Classic listener state + raw address dump |
| `POST /api/device/sweep`, `GET /api/device/sweep` | Measure the physical table (background thread; `sweep_progress` WS events) / status of the last sweep |
| `GET /api/device/physical_table` | The measured table |
| `GET /api/device/global` | Global transport and listener status (heartbeat, names, snapshots) |
| `GET /api/device/activity` | Human-change log from Global feedback (wiggle-to-learn) |
| `POST /api/device/pulse`, `POST /api/device/probe` | Channel identify pulse / liveness probe |
| `GET /api/device/picker` | Live names for the routing picker |

Macro management builds on this: `POST`/`PATCH /api/config/macros/{name}` upserts a single macro (name validated `[A-Za-z0-9_-]{1,64}`), `DELETE /api/config/macros/{name}` removes one. Both auto-backup `mappings.json`, hot-reload the bridge, and broadcast `macro_created` / `macro_updated` / `macro_deleted` WebSocket events so every open tab re-syncs its cards.

### Name-based target resolution

`/1/volume{N}` indexes visible fader *strips*, not hardware channels — stereo-linked pairs collapse into one strip, and link state is snapshot-dependent. Any statically captured strip index goes stale the moment the mixer state differs (this bit us: a map captured with AN 1/AN 2 unlinked mis-addressed every send in snapshots where they are linked).

Steps can therefore carry a name-based target instead of trusting a stored address:

```json
{ "target": {"submix": "RE-150 In", "channel": "AN 3"},
  "osc": "/1/volume3",
  "value": "{{param}}", "operation": {"type": "ramp", "bars": 2, "bpm": "clock"} }
```

At fire time `bridge._resolve_target()` looks up the submix index by name (channel map), sends `/setSubmix`, waits for the listener to confirm via `/1/labelSubmix` (1.5s timeout), then matches the channel *name* against the live bank's tracknames to find today's strip index. The stored `osc` address is only a fallback for when feedback is unavailable. `get_routing_label()` prefers target names, so card labels can't go stale either. The editor's routing picker writes targets; legacy raw-address macros (explicit `/setSubmix` steps) still execute unchanged.

---

## TotalMix OSC quirks

**Snapshot index reversal.** TotalMix numbers snapshot buttons bottom-to-top in its OSC namespace. Slot 1 is OSC index 8; slot 8 is index 1. The formula is `9 - slot_number`, handled by `config.snapshot_num_to_osc_index()`. The recall command is `/3/snapshots/{index}/1` with value `1.0`.

**Workspace switch timing.** After `/loadQuickWorkspace`, TotalMix takes roughly one second to finish switching. The bridge waits for the classic listener to confirm the switch (2 s / 1 s timeouts) and only falls back to fixed 1.0 s / 0.3 s sleeps when no listener is running. Too short and OSC commands land in the wrong workspace.

**`/setSubmix` selects the output bus.** Send `/setSubmix {index}` before adjusting a send level. The level command (`/1/volume{N}`) applies to whichever bus TotalMix has selected. Omit `/setSubmix` and you will adjust the wrong bus.

**Snapshot name matching.** The bridge lowercases and strips whitespace on both sides before comparing `snapshot` from `mappings.json` against `snapshot_map`. Case mismatches are safe. Leading or trailing whitespace is not.

---

## Config fallback

`mappings.json` and `ufx2_channel_map.json` are git-ignored. If missing, the bridge loads from the corresponding `examples/*.example.json` templates and sets `mappings_is_example` or `channel_map_is_example` to True. The UI shows an amber indicator in the settings menu.

`ufx2_snapshot_map.json` loads from `/app/config/ufx2_snapshot_map.json` first, then from the local directory. A background thread polls the mounted path every 5 seconds and reloads on change.

---

## Frontend: LED state machine

Five states, strictly ordered, never overlapping:

| State | Color | Duration | Trigger |
|---|---|---|---|
| MIDI received | White flash | 150ms | CC or Note arrives before macro fires |
| Running | Amber solid | Until complete | `macro_start` WebSocket event |
| Complete | Green flash | 600ms | `macro_complete` WebSocket event |
| Last fired hold | Cyan solid | Until next fire | Replaces green after it fades; peak-hold on the last card fired |
| Skipped | Red flash | 800ms | `macro_skipped` WebSocket event |

The cyan hold also adds a border glow to the card. When a new macro fires, the previous card's hold clears.

---

## Frontend: collapsible groups

Collapsible workspace and snapshot groups use `display:contents` on the wrapper div. Children remain direct CSS grid items at any nesting depth. Toggling `none` and `contents` collapses or expands without breaking the grid. Collapse state persists in `localStorage`.

---

## Frontend: BPM clock

`midi.js` reads `0xF8` timing clock messages (24 per quarter note) and computes BPM as `60000 / (24 x avg_interval_ms)`. Requires 4 ticks before displaying. Values outside 20-400 BPM are discarded. The result is stored as `window._detectedBPM` and included in every `POST /api/trigger` body so the bridge can substitute it when a macro step uses `"bpm": "clock"`.

---

## Frontend: inline macro editor

The editor reads every `[data-field]` input in the panel, traverses the dot-separated path (e.g. `steps.0.operation.bpm`) into a deep clone of the macro object, then PATCHes `/api/config/macros/{name}`. Changes write to disk and hot-reload into the bridge without a restart.

---

## Adding an operation type

Register with `@OperationRegistry.register("name")` in `tmosc/operations.py`:

```python
@OperationRegistry.register("hold")
def hold_op(osc_client, osc_addr, param, config, cancel_event=None):
    duration = config.get("duration", 1.0)
    osc_client.send_message(osc_addr, float(param))
    end = time.time() + duration
    while time.time() < end:
        if cancel_event and cancel_event.is_set():
            break
        time.sleep(0.05)
    osc_client.send_message(osc_addr, 0.0)
```

Always check `cancel_event` in the loop. It is how `restart` mode interrupts a running operation.

Use in `mappings.json`:

```json
{ "osc": "/1/volume2", "value": "{{param}}", "operation": { "type": "hold", "duration": 2.0 } }
```

---

## Macro trigger concurrency

Macro triggers arrive concurrently from three places: the web API thread, the MQTT callback thread, and queued re-fire threads. `bridge._macro_lock` guards the shared trigger state (`_running_macros`, `_cancel_events`, `_queued_params`) — the fire-mode guard, debounce check, and registration run as one atomic block, and cleanup in `finally` takes the same lock.

If OSC is not configured (`OSC_IP` unset), `run_macro()` skips at entry and broadcasts a `macro_skipped` event with reason `osc_not_configured` instead of crashing.

All OSC sends share one UDP socket per (ip, port) target, cached in `osc.py` (`get_client()` / `send_osc()`).
