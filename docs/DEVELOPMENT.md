# Development

How to run the bridge locally, add macros, add operation types, and understand the known rough edges.

---

## Local setup

### Requirements

- Python 3.12+
- A running MQTT broker (`mosquitto` locally or remote)
- A TotalMix FX instance to send OSC to — or skip `OSC_IP` and let sends fail silently while you work on the UI

### Python environment

```bash
python -m venv .venv
source .venv/bin/activate    # macOS/Linux
# .venv\Scripts\activate     # Windows

pip install -r requirements.txt
```

### Config files

```bash
cp mappings.example.json mappings.json
cp ufx2_channel_map.example.json ufx2_channel_map.json
cp ufx2_snapshot_map.example.json ufx2_snapshot_map.json
```

Edit all three to match your setup before running.

### Environment variables

```bash
export OSC_IP=192.168.1.50
export OSC_PORT=7001
export MQTT_BROKER=localhost
export MQTT_PORT=1883
export WEB_PORT=8088
export ENABLE_OSC_MONITOR=false
```

### Start the server

```bash
uvicorn web.web_client:app --host 0.0.0.0 --port 8088 --reload
```

Navigate to `http://localhost:8088`. MIDI won't work over plain HTTP — see [HTTPS for local dev](#https-for-local-dev) below.

### HTTPS for local dev

The Web MIDI API requires `https://` or `localhost`. For local dev with a real IP, use [mkcert](https://github.com/FiloSottile/mkcert):

```bash
mkcert -install
mkcert 192.168.1.x localhost 127.0.0.1
# then run a simple HTTPS proxy (Caddy, nginx, or stunnel) in front of port 8088
```

Or, access the UI at `http://localhost:8088` directly — `localhost` is a secure context and MIDI will work.

---

## Adding a macro

Macros live in `mappings.json`. The fastest path is the live editor in the UI (gear → **Edit mappings.json**), but here's the full structure:

```json
{
  "macros": {
    "your_macro_name": {
      "description": "What this does",
      "workspace": "YourWorkspaceName",
      "snapshot": "YourSnapshotName",
      "force_switch": false,
      "fire_mode": "ignore",
      "steps": [
        { "osc": "/setSubmix", "value": 14 },
        {
          "osc": "/1/volume2",
          "value": "{{param}}",
          "operation": { "type": "ramp", "bars": 2, "bpm": 140 }
        }
      ],
      "midi_triggers": [
        { "type": "control_change", "number": 44, "channel": 1, "use_value_as_param": true }
      ]
    }
  }
}
```

1. Add the macro to `mappings.json`.
2. Make sure the `workspace` and `snapshot` names exist in `ufx2_snapshot_map.json`.
3. Use the OSC monitor (see below) to find the right OSC address for your send.
4. Reload the server (gear → **Reload mappings from disk**) or save via the editor.

See [docs/MAPPINGS_REFERENCE.md](MAPPINGS_REFERENCE.md) for every field.

---

## Finding OSC addresses

TotalMix FX sends OSC back when you move faders. Enable the OSC monitor to capture them:

```bash
export ENABLE_OSC_MONITOR=true
python bridge.py
```

Move the fader you want in TotalMix. Check `osc_monitor.log` for the address. The address will look like `/1/volume2` — that's the send level for channel 2 in submix row 1.

---

## Adding an operation type

Operations live in `operations.py` and register themselves via decorator:

```python
@OperationRegistry.register("hold")
def hold_op(osc_client, osc_addr: str, param: float, config: dict,
            cancel_event: threading.Event = None):
    """Hold a value for a fixed duration, then snap to zero."""
    duration = config.get("duration", 1.0)
    osc_client.send_message(osc_addr, float(param))
    end_time = time.time() + duration
    while time.time() < end_time:
        if cancel_event and cancel_event.is_set():
            break
        time.sleep(0.05)
    osc_client.send_message(osc_addr, 0.0)
```

Once registered, use `"type": "hold"` in a step's `operation` dict. The `cancel_event` pattern is required — always check it in your loop so `restart` mode can interrupt.

---

## LFO phase math

The LFO phase formula is `t × 2π × (bpm/60) × 4`. Breaking it down:

- `t` = normalized time (0.0 → 1.0 over the full duration)
- `bpm/60` = beats per second
- `× 4` = beats per bar (4/4 time assumed)
- `× 2π` = one full sine cycle per beat

Result: one complete sine cycle per bar at the given BPM. A 4-bar LFO at 140 BPM completes four cycles.

---

## MIDI clock BPM detection

TotalMix doesn't send MIDI clock, but the Cirklon does. The bridge reads `0xF8` timing clock messages in the browser:

- `0xF8` = MIDI timing clock, 24 pulses per quarter note
- BPM = `60000 / (24 × avg_interval_ms)`

The browser keeps the last 25 ticks and averages the inter-tick intervals. Fewer than 4 ticks = no display. Values outside 20–400 BPM are discarded as noise.

---

## Known issues

These are documented for awareness — they're not blocking bugs, but they matter if you're working deep in the code.

**`_running_macros` and friends are unguarded**
`_running_macros`, `_cancel_events`, and `_queued_params` in `bridge.py` are mutated from multiple threads without explicit locks. Python's GIL makes simple `set.add()` / `dict.__setitem__` atomic in CPython, but this is an implementation detail, not a guarantee. Under heavy concurrent load (many macros firing at once) there is a theoretical race window.

**`osc.py` and `bridge.py` duplicate the OSC client**
`bridge.py` creates its own `SimpleUDPClient` at startup. `osc.py` maintains a separate cache for `mqtt_handler.py`. They're functionally identical but distinct sockets. This was a historical split that hasn't been unified.

**Snapshot map polling instead of file watching**
`mqtt_handler.map_watcher()` polls the SMB file every 5 seconds. A proper file watcher (e.g. `watchdog`) would be more responsive, but polling works reliably across SMB mounts where inotify events are unreliable.

**`start_mqtt` is attached as a method after class definition**
In `bridge.py`, `start_mqtt` is defined as a module-level function and then attached to `TotalMixOSCBridge` via `TotalMixOSCBridge.start_mqtt = start_mqtt`. This works but is unusual. It exists because `start_mqtt` was added after the class was already in production and attaching it this way avoided a larger refactor.

**`bridge.main_loop` must be set before MQTT broadcasts work**
`web_client.startup_event()` sets `bridge.main_loop = asyncio.get_running_loop()`. Until that runs, any MQTT-thread broadcast is silently dropped. This only matters during the brief startup window before FastAPI is ready.
