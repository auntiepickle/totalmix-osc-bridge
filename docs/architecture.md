# Architecture

The bridge is a single Python process: one FastAPI server, one asyncio event loop, one optional MQTT client in a background thread, and a pool of daemon threads for macro execution. All state lives in one singleton, `bridge` in `bridge.py`.

---

## Signal flow

```
MIDI controller -> USB -> Browser (Web MIDI API)
  -> midi.js -> WebSocket -> POST /api/trigger/{name}
  -> daemon thread -> bridge.run_macro()
      +-- workspace/snapshot switch via OSC (if needed)
      +-- operation steps (ramp / LFO, blocking and cancellable)
      +-- osc_client.send_message() -> UDP -> TotalMix FX

TotalMix FX -> MQTT (if configured)
  -> mqtt_handler.on_message()
      +-- bridge.update_workspace/snapshot()
      +-- broadcast -> all WebSocket clients -> browser
```

The browser is not a thin relay. MIDI input, BPM clock detection, LED animation, fire timing, and UI state all run in the browser. Macros already in flight on the server keep running if the tab closes.

---

## File responsibilities

### Python

| File | Owns |
|---|---|
| `bridge.py` | `TotalMixOSCBridge` class, macro execution, OSC client, state singleton |
| `web/web_client.py` | FastAPI app, all REST endpoints, WebSocket endpoint, startup wiring |
| `mqtt_handler.py` | MQTT subscriptions, workspace/snapshot state routing, snapshot map file watcher |
| `operations.py` | `OperationRegistry`: pluggable ramp and LFO implementations |
| `config.py` | Env var loading, `snapshot_num_to_osc_index()` |
| `osc.py` | Shared OSC socket cache — one `SimpleUDPClient` per `(ip, port)`, used by both `bridge.py` and `mqtt_handler`. |
| `osc_listener.py` | Structured OSC feedback listener: parses TotalMix pushes into queryable `DeviceState` (submixes, channel names, fader values). On by default. |
| `discovery.py` | Channel-map discovery walker: `/setSubmix 1..N`, capture feedback, build a `ufx2_channel_map.json` draft. |
| `osc_monitor.py` | Legacy log-only UDP listener. Superseded by `osc_listener.py`; enable with `ENABLE_OSC_MONITOR=true` (conflicts with the listener on the same port). |

### Frontend

Load order: `api.js` -> `app.js` -> `ui.js` -> `midi.js`.

| File | Owns |
|---|---|
| `api.js` | `window.API.*`: centralized fetch layer. All HTTP calls go through here. Nothing else calls `fetch()` directly. |
| `app.js` | Global state, WebSocket handler, macro loading, LED helpers, nav dropdowns, health polling |
| `ui.js` | Card rendering, progress animation, inline editor, macro firing, settings menu, file upload |
| `midi.js` | Web MIDI init, CC/Note message handling, BPM clock detection, device selector |

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
| `main_loop` | event loop | Set by `web_client.startup_event`. Required for thread-safe broadcast. |
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
      /loadQuickWorkspace {slot}  -> sleep 1.0s
      /3/snapshots/{9-n}/1        -> sleep 0.3s
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

`web_client.startup_event()` stores the running asyncio loop as `bridge.main_loop`. Sync threads call `asyncio.run_coroutine_threadsafe(self._do_broadcast(...), self.main_loop)`. Asyncio context creates a task directly. Broadcasts before FastAPI startup are silently dropped.

---

## Device capture and discovery

TotalMix pushes its state over OSC to the configured "Port outgoing" whenever the visible bank changes: `/1/labelSubmix` (selected submix name), `/{row}/trackname{n}` (channel names), `/{row}/volume{n}` (fader positions), plus pan and dB display values. Rows: 1 = hardware input, 2 = software playback, 3 = hardware output.

`osc_listener.OSCListener` receives that stream and maintains a `DeviceState`: channel data scoped per submix (row 3 output faders are submix-independent and stored under `_outputs`), plus a raw address store so unrecognized feedback is inspectable rather than lost. Volume floods are throttled to one WebSocket `device_update` event per 250ms; structural changes (submix/trackname) broadcast immediately.

`discovery.discover_channel_map()` interrogates the device: send `/setSubmix i` for i = 1..N, wait `settle_s` for the feedback burst, record what came back. Empty submixes (`<Empty>`) and repeated labels (TotalMix clamps out-of-range indices to the last submix) are skipped. The result is a `ufx2_channel_map.json`-compatible draft.

API surface:

| Endpoint | Purpose |
|---|---|
| `GET /api/device/state` | Live captured state + raw address dump |
| `POST /api/device/discover` | Start a discovery walk (background thread; `discovery_progress` / `discovery_complete` WS events) |
| `GET /api/device/discovery` | Status and result of the last walk |
| `POST /api/device/discovery/apply` | Promote the draft to the live `ufx2_channel_map.json` (auto-backup first) |

The draft is also written to `discovered_channel_map.json` (git-ignored) for manual inspection.

---

## TotalMix OSC quirks

**Snapshot index reversal.** TotalMix numbers snapshot buttons bottom-to-top in its OSC namespace. Slot 1 is OSC index 8; slot 8 is index 1. The formula is `9 - slot_number`, handled by `config.snapshot_num_to_osc_index()`. The recall command is `/3/snapshots/{index}/1` with value `1.0`.

**Workspace switch sleep.** After `/loadQuickWorkspace`, TotalMix takes roughly one second to finish switching. The bridge sleeps 1.0s before sending snapshot recall and 0.3s after before executing macro steps. Too short and OSC commands land in the wrong workspace.

**`/setSubmix` selects the output bus.** Send `/setSubmix {index}` before adjusting a send level. The level command (`/1/volume{N}`) applies to whichever bus TotalMix has selected. Omit `/setSubmix` and you will adjust the wrong bus.

**Snapshot name matching.** The bridge lowercases and strips whitespace on both sides before comparing `snapshot` from `mappings.json` against `snapshot_map`. Case mismatches are safe. Leading or trailing whitespace is not.

---

## Config fallback

`mappings.json` and `ufx2_channel_map.json` are git-ignored. If missing, the bridge loads from the corresponding `*.example.json` files and sets `mappings_is_example` or `channel_map_is_example` to True. The UI shows an amber indicator in the settings menu.

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

Register with `@OperationRegistry.register("name")` in `operations.py`:

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

## Thread safety

Macro triggers arrive concurrently from three places: the web API thread, the MQTT callback thread, and queued re-fire threads. `bridge._macro_lock` guards the shared trigger state (`_running_macros`, `_cancel_events`, `_queued_params`) — the fire-mode guard, debounce check, and registration run as one atomic block, and cleanup in `finally` takes the same lock.

If OSC is not configured (`OSC_IP` unset), `run_macro()` skips at entry and broadcasts a `macro_skipped` event with reason `osc_not_configured` instead of crashing.

All OSC sends share one UDP socket per (ip, port) target, cached in `osc.py` (`get_client()` / `send_osc()`).
