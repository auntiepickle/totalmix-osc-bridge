# Config Reference

Three JSON files configure the bridge. All have `*.example.json` counterparts in the repo. The real files are git-ignored so live edits survive `git pull`. Changes saved through the web UI write to disk and hot-reload without a restart.

---

## mappings.json

Every macro the bridge knows about.

```json
{
  "macros": {
    "reverb_send_ramp": {
      "description": "Ramp reverb send over 4 bars",
      "workspace": "Live_set",
      "snapshot": "breakdown",
      "force_switch": false,
      "fire_mode": "ignore",
      "debounce_ms": 0,
      "param_range": [0.0, 1.0],
      "routing_label": "AN 3 -> Reverb Bus",
      "steps": [ ... ],
      "midi_triggers": [ ... ]
    }
  }
}
```

### Macro fields

| Field | Default | Description |
|---|---|---|
| `description` | unset | Shown on the macro card |
| `workspace` | unset | Target workspace name. Must match a key in `ufx2_snapshot_map.json`. Case-insensitive. |
| `snapshot` | unset | Target snapshot name. Must match a value in that workspace's `snapshots` dict. Case-insensitive. |
| `force_switch` | `false` | When `true`, always switch workspace and snapshot even if another macro is running. |
| `fire_mode` | `"ignore"` | Behavior when triggered while already running. See below. |
| `debounce_ms` | `0` | Drop triggers that arrive within this many milliseconds of the previous one. |
| `param_range` | `[0.0, 1.0]` | Clamp the incoming param before passing to operations. `[0.2, 0.8]` prevents extreme values. |
| `routing_label` | auto | Override the routing label on the card. If omitted, derived from `ufx2_channel_map.json`. |
| `steps` | required | Ordered list of OSC sends and operations, executed in sequence. |
| `midi_triggers` | `[]` | MIDI bindings that fire this macro. |

### fire_mode

| Value | Behavior |
|---|---|
| `ignore` | Drop the trigger. The running macro finishes uninterrupted. |
| `queue` | Save the param and fire once the current run finishes. Overwrites any previously queued param. |
| `restart` | Cancel the running execution immediately (sends `0.0` to the OSC address), then re-run with the new param. |

---

### Steps: instant send

```json
{ "osc": "/setSubmix", "value": 14 }
```

Sends immediately. `value` is cast to `float`. Use this to select the output bus before adjusting a send level.

---

### Steps: ramp

```json
{
  "osc": "/1/volume2",
  "value": "{{param}}",
  "operation": { "type": "ramp", "bars": 2, "bpm": 140, "curve": "triangle" }
}
```

Smooth value change over musical time. Duration = `bars x 4 x 60 / bpm` seconds. OSC carries 32-bit floats, so ramps are smooth at any resolution, not limited to MIDI's 128 steps.

| Field | Default | Description |
|---|---|---|
| `bars` | `2` | Length in bars |
| `bpm` | `140` | Tempo in BPM. Set to `"clock"` to sync to live MIDI clock. |
| `curve` | `"triangle"` | `"triangle"` ramps up then back to zero. `"linear"` ramps from zero to the param value and holds. |

**Using `"bpm": "clock"`:** the browser reads `0xF8` MIDI timing clock messages and computes live BPM. That value is sent with every trigger and substituted at execution time. Falls back to 140 if no clock is detected.

---

### Steps: LFO

```json
{
  "osc": "/1/volume2",
  "value": "{{param}}",
  "operation": { "type": "lfo", "bars": 4, "bpm": 140, "depth": 1.0 }
}
```

Sine wave oscillation. One full cycle per bar.

| Field | Default | Description |
|---|---|---|
| `bars` | `2` | Total duration |
| `bpm` | `140` | Tempo. Accepts `"clock"` same as ramp. |
| `depth` | `1.0` | Amplitude (0.0-1.0) |

---

### `"{{param}}"` — dynamic value

The string `"{{param}}"` tells the bridge to use the incoming trigger value (0.0-1.0) as the operation target. Use it as the `value` on any step that has an operation.

---

### MIDI triggers

```json
"midi_triggers": [
  { "type": "control_change", "number": 44, "channel": 1, "use_value_as_param": true },
  { "type": "note_on",        "note": 60,   "channel": 1 },
  { "type": "note_off",       "note": 60,   "channel": 1 }
]
```

A macro can have multiple triggers. Any one fires it.

| Field | Description |
|---|---|
| `type` | `"control_change"`, `"note_on"`, or `"note_off"` |
| `number` | CC number (0-127). Used with `control_change`. |
| `note` | MIDI note number (0-127). Used with `note_on` and `note_off`. |
| `channel` | MIDI channel (1-16) |
| `use_value_as_param` | CC only. When `true`, CC value (0-127) is scaled to 0.0-1.0 and passed as `param`. |

Note On and Note Off triggers fire with `param = 1.0` unless overridden via the web UI or MQTT.

---

## ufx2_snapshot_map.json

Maps workspace names to TotalMix Quick Select slots and their snapshot names. The bridge resolves names here before switching.

```json
{
  "Live_set": {
    "slot": 3,
    "snapshots": {
      "1": "intro",
      "2": "verse",
      "4": "breakdown",
      "8": "outro"
    }
  },
  "Studio": {
    "slot": 7,
    "snapshots": {
      "1": "tracking",
      "2": "mixing"
    }
  }
}
```

| Field | Description |
|---|---|
| Top-level key | Workspace name. Must match `workspace` in `mappings.json`. |
| `slot` | TotalMix Quick Select slot (1-indexed). Sent as `/loadQuickWorkspace {slot}`. |
| `snapshots` | Snapshot number string (1-8) to snapshot name. Must match `snapshot` in `mappings.json`. |

**OSC snapshot index:** TotalMix numbers snapshots bottom-to-top in its OSC namespace. Slot 1 is index 8; slot 8 is index 1. The formula is `9 - slot_number`, handled internally by `config.snapshot_num_to_osc_index()`. The recall command is `/3/snapshots/{index}/1` with value `1.0`.

**In Docker:** mount a config directory at `/app/config/` and place `ufx2_snapshot_map.json` there for live sync without redeploy. The bridge polls for changes every 5 seconds.

---

## ufx2_channel_map.json

Maps OSC addresses to human-readable routing names. Used only to generate routing labels on macro cards (e.g. `/1/volume2` -> `AN 3 -> ADAT 1`). Not required for macro execution.

```json
{
  "submixes": {
    "ADAT 1": {
      "index": 1,
      "name": "ADAT 1",
      "sends": {
        "AN 3": {
          "channel": 2,
          "osc_address": "/1/volume2"
        }
      }
    }
  }
}
```

`bridge.get_routing_label()` walks every send and checks whether any macro step uses that `osc_address`. On a match it returns `"{send_name} -> {submix_name}"`. If no match, the card shows `—`.

Set `routing_label` directly in `mappings.json` to override for any macro.

The settings gear shows `(example)` next to the submix count when running from the example file.
