# Config Reference

Three JSON files configure the bridge. All three have `*.example.json` counterparts in the repo. The real files are git-ignored — copy and edit. Changes saved through the web UI write back to disk and hot-reload instantly.

---

## mappings.json

Every macro the bridge knows about.

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

**Macro fields**

| Field | Default | Description |
|---|---|---|
| `description` | — | Shown on the macro card |
| `workspace` | — | Target workspace name. Must match a key in `ufx2_snapshot_map.json`. Case-insensitive. |
| `snapshot` | — | Target snapshot name. Must match a value in that workspace's `snapshots` dict. Case-insensitive. |
| `force_switch` | `false` | When `true`, always switch workspace/snapshot even if another macro is running. When `false`, the switch is blocked if another macro is in flight. |
| `fire_mode` | `"ignore"` | What to do when triggered while already running — see below. |
| `debounce_ms` | `0` | Drop triggers that arrive within this many milliseconds of the previous one. |
| `param_range` | `[0.0, 1.0]` | Clamp the incoming param. `[0.2, 0.8]` prevents extreme values from reaching operations. |
| `routing_label` | auto | Override the orange label on the card. If omitted, derived from `ufx2_channel_map.json`. |
| `steps` | — | Ordered list of OSC sends and operations. |
| `midi_triggers` | `[]` | MIDI CC bindings. |

**fire_mode**

| Value | Behaviour |
|---|---|
| `ignore` | Drop the incoming trigger. |
| `queue` | Save the param and fire once after the current run finishes. Overwrites any previously queued param. |
| `restart` | Cancel the running execution immediately (sends `0.0`), then re-run with the new param. |

**Steps — instant send**

```json
{ "osc": "/setSubmix", "value": 14 }
```

Sends immediately. `value` is cast to `float`.

**Steps — ramp**

```json
{
  "osc": "/1/volume2",
  "value": "{{param}}",
  "operation": { "type": "ramp", "bars": 2, "bpm": 140, "curve": "triangle" }
}
```

Smooth value change over musical time. Duration = `bars × 4 × 60 / bpm` seconds.

| Field | Default | Description |
|---|---|---|
| `bars` | `2` | Length in bars |
| `bpm` | `140` | Tempo |
| `curve` | `"triangle"` | `"triangle"` = up then down to zero. `"linear"` = zero up to param value. |

**Steps — LFO**

```json
{
  "osc": "/1/volume2",
  "value": "{{param}}",
  "operation": { "type": "lfo", "bars": 4, "bpm": 140, "depth": 1.0 }
}
```

Sine wave. One full cycle per bar.

| Field | Default | Description |
|---|---|---|
| `bars` | `2` | Duration |
| `bpm` | `140` | Tempo |
| `depth` | `1.0` | Amplitude (0.0–1.0) |

`"{{param}}"` — the string literal that tells the bridge to use the incoming trigger value (0.0–1.0) as the operation target.

**MIDI triggers**

```json
"midi_triggers": [
  { "type": "control_change", "number": 44, "channel": 1, "use_value_as_param": true }
]
```

| Field | Description |
|---|---|
| `number` | CC number (0–127) |
| `channel` | MIDI channel (1–16) |
| `use_value_as_param` | When `true`, CC value (0–127) is scaled to 0.0–1.0 and passed as `param` |

---

## ufx2_snapshot_map.json

Maps workspace names to TotalMix Quick Select slots and snapshot names. The bridge uses this to resolve names to slot numbers before switching.

```json
{
  "Pill_setup": {
    "slot": 7,
    "snapshots": {
      "1": "Reset",
      "4": "lastthingdid"
    }
  }
}
```

| Field | Description |
|---|---|
| Top-level key | Workspace name. Must match `workspace` in `mappings.json`. |
| `slot` | TotalMix Quick Select slot (1-indexed). Sent as `/loadQuickWorkspace {slot}`. |
| `snapshots` | Dict of snapshot number (1–8, as a string key) → snapshot name. Must match `snapshot` in `mappings.json`. |

**OSC snapshot recall:** the bridge converts slot number to OSC index with `9 - snap_num` (slot 1 → index 8, slot 8 → index 1). TotalMix orders snapshot buttons bottom-to-top in its OSC namespace. The recall command is `/3/snapshots/{index}/1` with value `1.0`.

**This file lives outside the repo.** In Docker, mount your config share at `/app/config/` and place `ufx2_snapshot_map.json` there. The bridge polls for changes every 5 seconds.

---

## ufx2_channel_map.json

Maps OSC addresses to human-readable routing names. Used solely to generate the orange routing labels on macro cards — e.g. `/1/volume2` → `AN 3 → ADAT 1`.

```json
{
  "submixes": {
    "ADAT 1": {
      "index": 1,
      "name": "ADAT 1",
      "sends": {
        "AN 3": {
          "channel": 2,
          "osc_address": "/1/volume2",
          "description": "AN 3 send to ADAT 1 output"
        }
      }
    }
  }
}
```

`bridge.get_routing_label()` walks every send and checks whether any macro step uses that `osc_address`. On a match, it returns `"{send_name} → {submix_name}"`. If no match, the card shows `—`.

If your macros use OSC addresses not in this file, set `routing_label` directly in `mappings.json` instead.

The settings gear shows `(example)` next to the submix count when the bridge is running from the example file.
