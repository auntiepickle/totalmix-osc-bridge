# Mappings Reference

Three JSON files configure the bridge. All three have `*.example.json` counterparts in the repo. The real files are git-ignored — copy the examples and edit. Changes made through the web UI write back to the live files and hot-reload without a restart.

---

## mappings.json

Defines every macro the bridge knows about. The bridge loads this at startup and keeps it in memory as `bridge.mappings`. The live editor in the UI patches it in-place via `PATCH /api/config/macros/{name}`.

### Macro fields

```json
{
  "macros": {
    "an3_to_adat1_send": {
      "description": "AN 3 → ADAT 1 send",
      "workspace": "Pill_setup",
      "snapshot": "lastthingdid",
      "force_switch": false,
      "fire_mode": "ignore",
      "debounce_ms": 0,
      "param_range": [0.0, 1.0],
      "routing_label": "AN 3 → ADAT 1",
      "steps": [ ... ],
      "midi_triggers": [ ... ]
    }
  }
}
```

| Field | Type | Default | Description |
|---|---|---|---|
| `description` | string | — | Shown on the macro card in the web UI |
| `workspace` | string | — | Target workspace name. Must match a key in `ufx2_snapshot_map.json` (case-insensitive). |
| `snapshot` | string | — | Target snapshot name. Must match a value in that workspace's `snapshots` dict. |
| `force_switch` | bool | `false` | When `true`, always switch workspace/snapshot even if another macro is running. When `false`, the switch is blocked if another macro is in flight. |
| `fire_mode` | string | `"ignore"` | Concurrency behaviour — see [Fire modes](#fire-modes). |
| `debounce_ms` | int | `0` | Minimum milliseconds between triggers. Drops triggers that arrive too soon. |
| `param_range` | `[float, float]` | `[0.0, 1.0]` | Clamps the incoming param. Use `[0.2, 0.8]` to prevent extreme values. |
| `routing_label` | string | auto | Overrides the auto-generated label on the macro card. If omitted, the bridge derives it from `ufx2_channel_map.json` by matching OSC addresses. |
| `steps` | array | — | Ordered OSC sends and operations — see [Steps](#steps). |
| `midi_triggers` | array | `[]` | MIDI CC bindings — see [MIDI triggers](#midi-triggers). |

### Fire modes

Controls what happens when a trigger arrives while the macro is already running.

| Mode | Behaviour |
|---|---|
| `ignore` | Drop the new trigger. Default. |
| `queue` | Save the new param and fire once after the current run completes. Only the most recent queued param is kept. |
| `restart` | Cancel the current execution immediately (sends `0.0` on the active OSC address), then re-run with the new param. |

### Steps

Steps execute in order. Each step is either an instant OSC send or a time-based operation.

**Instant send**

```json
{ "osc": "/setSubmix", "value": 14 }
```

Sends the OSC message immediately. `value` is cast to `float`.

**Ramp operation**

```json
{
  "osc": "/1/volume2",
  "value": "{{param}}",
  "operation": {
    "type": "ramp",
    "bars": 2,
    "bpm": 140,
    "curve": "triangle"
  }
}
```

Moves the OSC value smoothly over `bars` bars at `bpm` BPM.

| Field | Default | Description |
|---|---|---|
| `bars` | `2` | Duration in bars |
| `bpm` | `140` | Tempo in BPM |
| `curve` | `"triangle"` | `"triangle"` (up then down) or `"linear"` (0 to param, stays) |

Duration in seconds = `bars × 4 × 60 / bpm`. A 2-bar ramp at 140 BPM lasts 3.43 seconds.

**LFO operation**

```json
{
  "osc": "/1/volume2",
  "value": "{{param}}",
  "operation": {
    "type": "lfo",
    "bars": 4,
    "bpm": 140,
    "depth": 1.0
  }
}
```

Drives a sine wave over musical time. One full cycle per bar.

| Field | Default | Description |
|---|---|---|
| `bars` | `2` | Duration in bars |
| `bpm` | `140` | Tempo in BPM |
| `depth` | `1.0` | Amplitude (0.0–1.0) |

**`{{param}}`** — the `value` string `"{{param}}"` tells the bridge to use the incoming trigger parameter (0.0–1.0 from MIDI CC) as the operation target value.

### MIDI triggers

```json
"midi_triggers": [
  {
    "type": "control_change",
    "number": 44,
    "channel": 1,
    "use_value_as_param": true
  }
]
```

| Field | Description |
|---|---|
| `type` | Always `"control_change"` for now |
| `number` | CC number (0–127) |
| `channel` | MIDI channel (1–16) |
| `use_value_as_param` | When `true`, the CC value (0–127) is scaled to 0.0–1.0 and passed as `param` to the macro |

The browser matches incoming CC messages against every macro's `midi_triggers` list. The first match wins. Matching happens in `midi.js` `handleMIDIMessage()`.

---

## ufx2_snapshot_map.json

Maps workspace names to TotalMix Quick Select slot numbers and snapshot names. The bridge uses this to resolve workspace and snapshot names to OSC slot numbers before switching.

```json
{
  "Pill_setup": {
    "slot": 7,
    "snapshots": {
      "1": "Reset",
      "4": "lastthingdid",
      "8": "lastthingdid"
    }
  }
}
```

| Field | Description |
|---|---|
| Top-level key | Workspace name. Must match `workspace` in `mappings.json` exactly. |
| `slot` | TotalMix Quick Select slot number (1-indexed). Sent as `/loadQuickWorkspace <slot>`. |
| `snapshots` | Dict mapping snapshot number (1–8, as a string) to snapshot name. |
| Snapshot value | Must match `snapshot` in `mappings.json` exactly. |

**Name matching is case-insensitive.** The bridge normalizes both sides to lowercase before comparing.

**OSC snapshot recall:** slot number → OSC index via `9 - snap_num`. Snapshot 1 → index 8, snapshot 8 → index 1. This reversal reflects TotalMix's bottom-to-top button layout in its OSC namespace. The OSC message is `/3/snapshots/{index}/1` with value `1.0`.

**This file lives outside the repo.** In Docker, mount your NAS share at `/app/config/` and place `ufx2_snapshot_map.json` there. The bridge polls for changes every 5 seconds and reloads automatically. See [docs/DEPLOYMENT.md](DEPLOYMENT.md#snapshot-map-live-sync).

---

## ufx2_channel_map.json

Maps submix destinations and send channels to OSC addresses. The bridge uses this to generate the orange routing labels on macro cards — for example, `/1/volume2` becomes `AN 3 → ADAT 1`.

```json
{
  "submixes": {
    "ADAT 1": {
      "index": 1,
      "name": "ADAT 1",
      "sends": {
        "AN 3": {
          "row": 1,
          "channel": 2,
          "osc_address": "/1/volume2",
          "description": "AN 3 send to ADAT 1 output"
        }
      }
    }
  }
}
```

**Top-level:** `submixes` dict, keyed by output bus name.

**Each submix:**
- `index` — TotalMix submix index (used by `/setSubmix`)
- `name` — display name
- `sends` — dict of input channels that feed this submix

**Each send:**
- `osc_address` — the OSC address that controls this send's level (e.g. `/1/volume2`)
- `channel` — channel number within the submix row
- `description` — human-readable note (not displayed in the UI)

**How routing labels are generated:** `bridge.get_routing_label()` walks every send in every submix and checks whether any step in the macro uses that `osc_address`. When it finds a match, it returns `"{send_name} → {submix_name}"`. If no match is found, the card shows `—`.

**If your macros use OSC addresses not in this file**, the routing labels will show `—`. Either add the entries to `ufx2_channel_map.json` or set `routing_label` directly in `mappings.json` to override.

The settings gear shows `(example)` next to the submix count when the bridge is running from `ufx2_channel_map.example.json`. Use **Edit channel map** in the gear menu to set up your own.
