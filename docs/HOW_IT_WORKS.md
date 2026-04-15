# How It Works

The bridge is a single Python process: one FastAPI server, one asyncio event loop, one MQTT client in a background thread (if configured), and a pool of daemon threads for macro execution. All state routes through one singleton — `bridge` in `bridge.py`.

---

## Signal flow

```
MIDI controller → USB → Browser (Web MIDI API)
  → midi.js → WebSocket → POST /api/trigger/{name}
  → daemon thread → bridge.run_macro()
      ├── workspace/snapshot switch via OSC (if needed)
      ├── operation steps (ramp / LFO, blocking + cancellable)
      └── osc_client.send_message() → UDP → TotalMix FX

TotalMix FX → MQTT (workspace/snapshot state, if MQTT configured)
  → mqtt_handler.on_message()
      ├── bridge.update_workspace/snapshot()
      └── broadcast → all WebSocket clients → browser UI
```

The browser handles MIDI input, BPM clock detection, LED animation, and UI state. It is not a thin relay — meaningful logic lives there. If the browser tab closes, macros already in flight keep running on the server.

---

## Files and what they own

| File | Owns |
|---|---|
| `bridge.py` | `TotalMixOSCBridge` class, all macro execution logic, OSC client, state singleton |
| `web/web_client.py` | FastAPI app, all REST endpoints, WebSocket endpoint, startup wiring |
| `mqtt_handler.py` | MQTT subscriptions, workspace/snapshot state routing, SMB file watcher |
| `operations.py` | `OperationRegistry` — pluggable ramp and LFO implementations |
| `config.py` | Env var loading, `snapshot_num_to_osc_index()` |
| `osc.py` | Standalone OSC sender used by `mqtt_handler`. Caches one `SimpleUDPClient` per `(ip, port)`. |
| `osc_monitor.py` | UDP listener for discovering OSC addresses from TotalMix. Enable with `ENABLE_OSC_MONITOR=true`. |

Frontend load order: `api.js` → `app.js` → `ui.js` → `midi.js`.

| File | Owns |
|---|---|
| `api.js` | `window.API.*` — centralized fetch layer. All HTTP calls go through here. Nothing else calls `fetch()` directly. |
| `app.js` | Global state, WebSocket handler, macro loading, LED helpers, nav dropdowns, health polling |
| `ui.js` | Card rendering, progress animation, inline editor, macro firing, settings menu, file upload |
| `midi.js` | Web MIDI init, CC/Note message handling, BPM clock detection, device selector |

---

## bridge.py — key state

| Attribute | Type | What it is |
|---|---|---|
| `current_workspace` | `str \| None` | Last-known workspace name (not slot number) |
| `current_snapshot` | `str \| None` | Last-known snapshot name, lowercased and stripped |
| `mappings` | `dict` | Live macro definitions. Hot-reloadable via the UI. |
| `snapshot_map` | `dict` | Workspace → slot/snapshot lookup. Updated by SMB watcher or local file. |
| `channel_map` | `dict` | OSC address → routing label data |
| `mappings_is_example` | `bool` | `True` when running from `mappings.example.json` |
| `channel_map_is_example` | `bool` | `True` when running from `ufx2_channel_map.example.json` |
| `mqtt_connected` | `bool` | `True` when MQTT broker connection is active. `False` if no broker configured. |
| `main_loop` | event loop | Set by `web_client.startup_event`. Required for thread-safe broadcast. |
| `_suppress_handler` | `bool` | Blocks MQTT feedback during macro execution — see below. |
| `_running_macros` | `set[str]` | Names of currently executing macros |
| `_cancel_events` | `dict` | `threading.Event` per running macro, set to cancel on `restart` mode |
| `_queued_params` | `dict` | Pending param saved by `queue` or `restart` mode |

---

## run_macro() — what actually happens

```
1.  Is macro name valid? No → log error, return
2.  Is OSC client configured? No → broadcast macro_skipped, return
3.  Is it already running?
      ignore  → drop
      queue   → save param, return
      restart → set cancel_event, save param, return
4.  Is debounce window active? → drop
5.  Set _suppress_handler = True
6.  Resolve workspace slot + snapshot index from snapshot_map
7.  Already on target? → skip switch
    force_switch=False and another macro running? → emit macro_skipped, return
8.  Switch:
      /loadQuickWorkspace {slot} → sleep(1.0)
      /3/snapshots/{9-snap_num}/1 → sleep(0.3)
9.  Broadcast macro_start (drives progress bar in browser)
10. Execute steps in order
11. Broadcast macro_complete
12. finally: _suppress_handler = False, _last_macro_end_time = now
13. Fire queued param if one was saved
```

---

## The MQTT feedback loop problem

When `run_macro()` sends `/loadQuickWorkspace` and the snapshot OSC, TotalMix publishes its new state back to MQTT (`totalmix/workspace`, `totalmix/snapshot`). Without suppression, `on_message()` would pick those up and overwrite `current_workspace`/`current_snapshot` with raw slot numbers mid-execution — right when a ramp is running.

Two guards prevent this:

1. **`_suppress_handler = True`** during execution. `on_message()` checks this at the top and returns immediately for workspace/snapshot topics.
2. **`_last_macro_end_time` cooldown** — suppresses those topics for 2.5s after the macro completes, catching delayed MQTT publishes.

This problem only exists when MQTT is configured. Without a broker, there is no feedback loop.

---

## Thread-safe broadcast

FastAPI runs in asyncio. MQTT callbacks and macro threads are plain OS threads. `bridge.broadcast_state()` has to work from both contexts.

The solution: `web_client.startup_event()` stores the running asyncio loop as `bridge.main_loop`. When `broadcast_state()` is called from a sync thread, it uses `asyncio.run_coroutine_threadsafe(self._do_broadcast(...), self.main_loop)`. When called from within asyncio, it creates a task directly.

If `main_loop` isn't set yet (the brief window between process start and FastAPI startup), broadcasts are silently dropped.

---

## TotalMix OSC — the non-obvious parts

**Snapshot index reversal.** TotalMix lays out snapshot buttons bottom-to-top in its OSC namespace. Snapshot slot 1 is OSC index 8. Slot 8 is index 1. The formula is `9 - snap_num`, encapsulated in `config.snapshot_num_to_osc_index()`. The OSC address to recall a snapshot is `/3/snapshots/{index}/1` with value `1.0`.

**Workspace switch needs a sleep.** After sending `/loadQuickWorkspace`, TotalMix takes ~1 second to finish switching. The bridge sleeps `1.0s` before sending the snapshot recall, and `0.3s` after that before executing macro steps. These are empirically tuned — too short and OSC commands land in the wrong workspace.

**`/setSubmix` selects the output bus.** Before adjusting a send level, send `/setSubmix {index}` to tell TotalMix which output bus to edit. The send level command (`/1/volume{N}`) then applies to that bus. If `/setSubmix` is missing from your steps, you'll adjust whichever bus TotalMix has selected at that moment.

**Snapshot names are normalized.** The bridge lowercases and strips both sides before comparing `snapshot` from `mappings.json` against `snapshot_map`. Case mismatches won't cause failures — but leading/trailing whitespace will.

---

## Config file fallback chain

Both `mappings.json` and `ufx2_channel_map.json` are git-ignored. If they're missing, the bridge loads from their `*.example.json` counterparts and sets `mappings_is_example` / `channel_map_is_example = True`. The web UI shows an amber indicator in the settings menu.

`ufx2_snapshot_map.json` is loaded from `/app/config/ufx2_snapshot_map.json` (Docker volume mount) first, then from the local directory. A background thread polls the mounted path every 5 seconds and syncs `bridge.snapshot_map` on change.

---

## Frontend — LED state machine

Five states, strictly ordered, never overlapping:

| State | Color | Duration | Trigger |
|---|---|---|---|
| MIDI received | White flash | 150ms | CC/Note arrives in midi.js before macro fires |
| Running | Amber solid | Until complete | `macro_start` WebSocket event |
| Complete | Green flash | 600ms | `macro_complete` WebSocket event |
| Last fired hold | Cyan solid | Until next fire | After green flash fades — peak-hold on the last card fired |
| Skipped | Red flash | 800ms | `macro_skipped` WebSocket event |

The cyan hold also adds a border glow to the card. When a new macro fires, the previous card's cyan hold clears immediately (`_clearLastFired()`).

---

## Frontend — collapsible grid groups

Collapsible workspace and snapshot groups use `display:contents` on the wrapper div. This makes children remain direct CSS grid items at any nesting depth. Toggling `none` ↔ `contents` collapses/expands without breaking the grid. Collapse state persists in `localStorage`.

---

## Frontend — BPM clock

Reads `0xF8` timing clock messages (24 per quarter note). BPM = `60000 / (24 × avg_interval_ms)`. Requires 4+ ticks before displaying. Values outside 20–400 BPM are discarded. Detected BPM stored as `window._detectedBPM` and passed in every `POST /api/trigger` body so the bridge can substitute it when a macro step has `"bpm": "clock"`.

---

## Frontend — inline macro editor

Reads every `[data-field]` input in the panel, traverses the dot-separated path (e.g. `steps.0.operation.bpm`) into a deep clone of the macro object, then `PATCH`es `/api/config/macros/{name}`. All config changes write to disk and hot-reload into the bridge without a restart.

---

## Operations

Register a new operation type with `@OperationRegistry.register("name")` in `operations.py`:

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

Always check `cancel_event` in the loop — it's how `restart` mode interrupts a running operation.

---

## Known rough edges

**`_running_macros` is unguarded.** Mutated from multiple threads without a lock. Works in CPython due to the GIL, but that's an implementation detail, not a guarantee.

**`switch_to()` has a latent UnboundLocalError.** `snap_num` is assigned inside a loop and used after it. If the snapshot name isn't found, `snap_num` is undefined at the point of use. Fix: initialize `snap_num = None` before the loop, guard after.

**`run_macro()` doesn't guard `osc_client is None`.** If OSC isn't configured and a macro fires, it will crash rather than skipping gracefully. Pending fix: check at entry and broadcast `macro_skipped`.

**`start_mqtt` is monkey-patched onto the class.** Defined as a module-level function in `bridge.py`, then attached post-definition. Historical artifact. Works, but unusual.

**`osc.py` and `bridge.py` use separate OSC sockets.** Each caches its own `SimpleUDPClient`. Functionally correct, slightly wasteful.
