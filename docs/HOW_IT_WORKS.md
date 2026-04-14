# How It Works

The bridge is a single Python process: one FastAPI server, one asyncio event loop, one MQTT client in a background thread, and a pool of daemon threads for macro execution. All state routes through one singleton — `bridge` in `bridge.py`.

---

## Signal flow

```
Cirklon → USB → Browser (Web MIDI API)
  → midi.js CC handler → WebSocket → POST /api/trigger/{name}
  → daemon thread → bridge.run_macro()
      ├── workspace/snapshot switch via OSC (if needed)
      ├── operation steps (ramp / LFO, blocking + cancellable)
      └── osc_client.send_message() → UDP → TotalMix FX

TotalMix FX → MQTT (workspace/snapshot state)
  → mqtt_handler.on_message()
      ├── bridge.update_workspace/snapshot()
      └── broadcast → all WebSocket clients → browser UI
```

The browser is a thin relay. No logic lives there. If the browser tab closes, macros already in flight keep running.

---

## Files and what they own

| File | Owns |
|---|---|
| `bridge.py` | The `TotalMixOSCBridge` class, module-level globals (`MAPPINGS`, `SNAPSHOT_MAP`, `osc_client`, `ws_clients`), singleton `bridge` |
| `web/web_client.py` | FastAPI app, all REST endpoints, WebSocket endpoint, startup wiring |
| `mqtt_handler.py` | MQTT subscriptions, workspace/snapshot routing, SMB file watcher |
| `operations.py` | `OperationRegistry` — pluggable ramp and LFO implementations |
| `config.py` | Env var loading, `snapshot_num_to_osc_index()` |
| `osc.py` | Standalone OSC sender used by `mqtt_handler`. Caches one `SimpleUDPClient` per `(ip, port)`. |
| `osc_monitor.py` | UDP listener for discovering OSC addresses from TotalMix. Enable with `ENABLE_OSC_MONITOR=true`. |

Frontend: `app.js` → `ui.js` → `midi.js`, loaded in that order. They share globals (`macros`, `midiConnectedDevice`, etc.) via `app.js`.

---

## bridge.py — key state

| Attribute | Type | What it is |
|---|---|---|
| `current_workspace` | `str \| None` | Last-known workspace name (not slot number) |
| `current_snapshot` | `str \| None` | Last-known snapshot name, lowercased and stripped |
| `mappings` | `dict` | Live macro definitions. Hot-reloadable via the UI. |
| `snapshot_map` | `dict` | Workspace → slot/snapshot lookup. Updated by SMB watcher. |
| `channel_map` | `dict` | OSC address → routing label data |
| `mappings_is_example` | `bool` | `True` when running from `mappings.example.json` |
| `channel_map_is_example` | `bool` | `True` when running from `ufx2_channel_map.example.json` |
| `main_loop` | event loop | Set by `web_client.startup_event`. Required for thread-safe broadcast — see below. |
| `_suppress_handler` | `bool` | Blocks MQTT feedback during macro execution — see below. |
| `_running_macros` | `set[str]` | Names of currently executing macros |
| `_cancel_events` | `dict` | `threading.Event` per running macro, set to cancel on `restart` mode |
| `_queued_params` | `dict` | Pending param saved by `queue` or `restart` mode |

---

## run_macro() — what actually happens

```
1. Is macro name valid? No → log error, return
2. Is it already running?
     ignore  → drop
     queue   → save param, return
     restart → set cancel_event, save param, return
3. Is debounce window active? → drop
4. Set _suppress_handler = True
5. Resolve workspace slot + snapshot index from snapshot_map
6. Already on target? → skip switch
   force_switch=False and another macro running? → emit macro_skipped, return
7. Switch:
     /loadQuickWorkspace {slot} → sleep(1.0)
     /3/snapshots/{9-snap_num}/1 → sleep(0.3)
8. Broadcast macro_start (drives progress bar in browser)
9. Execute steps in order
10. Broadcast macro_complete
11. finally: _suppress_handler = False, _last_macro_end_time = now
12. Fire queued param if one was saved
```

---

## The feedback loop problem

When `run_macro()` sends `/loadQuickWorkspace` and the snapshot OSC, TotalMix publishes its new state back to MQTT (`totalmix/workspace`, `totalmix/snapshot`). Without suppression, `on_message()` would pick those up and overwrite `current_workspace`/`current_snapshot` with raw slot numbers mid-execution — right when the ramp is running.

Two guards prevent this:

1. **`_suppress_handler = True`** during execution. `on_message()` checks this at the top and returns immediately for workspace/snapshot topics.
2. **`_last_macro_end_time` cooldown** — suppresses those topics for 2.5s after the macro completes, catching any delayed MQTT publishes.

---

## Thread-safe broadcast

FastAPI runs in asyncio. MQTT callbacks and macro threads are plain OS threads. `bridge.broadcast_state()` has to work from both contexts.

The solution: `web_client.startup_event()` stores the running asyncio loop as `bridge.main_loop`. When `broadcast_state()` is called from a sync thread, it uses `asyncio.run_coroutine_threadsafe(self._do_broadcast(...), self.main_loop)`. When called from within asyncio, it creates a task directly.

If `main_loop` isn't set yet (the brief window between process start and FastAPI startup), broadcasts are silently dropped.

---

## TotalMix OSC — the non-obvious parts

**Snapshot index reversal.** TotalMix lays out snapshot buttons bottom-to-top in its OSC namespace. Snapshot slot 1 is OSC index 8. Slot 8 is index 1. The formula is `9 - snap_num`, encapsulated in `config.snapshot_num_to_osc_index()`. The OSC address to recall a snapshot is `/3/snapshots/{index}/1` with value `1.0`.

**Workspace switch needs a sleep.** After sending `/loadQuickWorkspace`, TotalMix takes ~1 second to finish switching. The bridge sleeps `1.0s` before sending the snapshot recall, and `0.3s` after that before executing macro steps. These are empirically tuned — too short and OSC commands hit the wrong workspace.

**`/setSubmix` selects the bus.** Before adjusting a send level, the bridge sends `/setSubmix {index}` to tell TotalMix which output bus to edit. The send level command (`/1/volume{N}`) then applies to that bus. If `/setSubmix` is missing from your steps, you'll adjust whichever bus TotalMix has selected at that moment.

**Snapshot names are normalized.** The bridge lowercases and strips both sides before comparing `snapshot` from `mappings.json` against `snapshot_map`. Case mismatches won't cause silent failures — but whitespace will.

---

## Config file fallback chain

Both `mappings.json` and `ufx2_channel_map.json` are git-ignored. If they're missing, the bridge loads from their `*.example.json` counterparts and sets `mappings_is_example` / `channel_map_is_example = True`. The web UI shows an amber indicator in the settings menu when this happens.

`ufx2_snapshot_map.json` is loaded from `/app/config/ufx2_snapshot_map.json` (Docker SMB mount) first, then from the local directory. A background thread polls the SMB path every 5 seconds and syncs `bridge.snapshot_map` on change.

---

## Frontend patterns

**Collapsible grid groups** use `display:contents` on the wrapper div. Children remain direct CSS grid items at any nesting depth. Toggling `none` ↔ `contents` collapses or expands without breaking the grid. Collapse state persists in `localStorage`.

**LED state machine** — four states, never overlap:
- White flash (150ms) — MIDI CC received
- Amber solid — macro executing
- Green flash (600ms) — macro complete
- Red flash (800ms) — macro skipped/dropped

**Inline macro editor** reads every `[data-field]` input in the panel, traverses the path (e.g. `steps.0.operation.bpm`) into a deep clone of the macro object, then `PATCH`es `/api/config/macros/{name}`.

**MIDI clock BPM** — reads `0xF8` timing clock messages (24 per quarter note). BPM = `60000 / (24 × avg_interval_ms)`. Requires 4+ ticks before displaying. Values outside 20–400 BPM are discarded.

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

Use it in `mappings.json`:
```json
{ "osc": "/1/volume2", "value": "{{param}}", "operation": { "type": "hold", "duration": 2.0 } }
```

---

## Known rough edges

**`_running_macros` is unguarded.** Mutated from multiple threads without a lock. Works in CPython due to the GIL, but it's an implementation detail, not a guarantee.

**`start_mqtt` is attached post-class.** Defined as a module-level function in `bridge.py`, then monkey-patched onto `TotalMixOSCBridge`. It works but it's unusual. Historical artifact from adding it after the class was already in production.

**`osc.py` and `bridge.py` use separate OSC sockets.** `bridge.py` has its own `osc_client`; `osc.py` caches a separate one for `mqtt_handler.py`. Functionally fine, slightly wasteful.
