# Config Reference

Three JSON files configure the bridge. All three have `*.example.json` counterparts in the repo. The real files are git-ignored — copy and edit. Changes saved through the web UI write back to disk and hot-reload instantly without a restart.

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
      "routing_label": "AN 3 → Reverb Bus",
      "steps": [ ... ],
      "midi_triggers": [ ... ]
    }
  }
}
```

### Macro fields

| Field | Default | Description |
|---|---|---|
| `description` | — | Shown on the macro card in the UI |
| `workspace` | — | Target workspace name. Must match a key in `ufx2_snapshot_map.json`. Case-insensitive. |
| `snapshot` | — | Target snapshot name. Must match a value in that workspace's `snapshots` dict. Case-insensitive. |
| `force_switch` | `false` | When `true`, always switch workspace/snapshot even if another macro is running. When `false`, the switch is skipped if another macro is in flight. |
| `fire_mode` | `"ignore"` | What to do when triggered while already running — see below. |
| `debounce_ms` | `0` | Drop triggers that arrive within this many milliseconds of the previous one. Useful for noisy CC sources. |
| `param_range` | `[0.0, 1.0]` | Clamp the incoming param before passing to operations. `[0.2, 0.8]` prevents extreme values. |
| `routing_label` | auto | Override the routing label shown on the card. If omitted, derived from `ufx2_channel_map.json`. |
| `steps` | — | Ordered list of OSC sends and operations. Executed in sequence. |
| `midi_triggers` | `[]` | MIDI bindings that fire this macro. |

### fire_mode

| Value | Behaviour |
|---|---|
| `ignore` | Drop the incoming trigger. The running macro finishes uninterrupted. |
| `queue` | Save the param and fire once after the current run finishes. Overwrites any previously queued param. |
| `restart` | Cancel the running execution immediately (sends `0.0` to the OSC address), then re-run with the new param. |

---

### Steps — instant send

```json
{ "osc": "/setSubmix", "value": 14 }
```

Sends immediately. `value` is cast to `float`. Use this to select the output bus before adjusting a send level.

---

### Steps — ramp

```json
{
  "osc": "/1/volume2",
  "value": "{{param}}",
  "operation": { "type": "ramp", "bars": 2, "bpm": 140, "curve": "triangle" }
}
```

Smooth value change over musical time. Duration = `bars × 4 × 60 / bpm` seconds.

OSC carries 32-bit floats — ramps are smooth at any resolution, not limited to the 128 steps of MIDI CC.

| Field | Default | Description |
|---|---|---|
| `bars` | `2` | Length in bars |
| `bpm` | `140` | Tempo in BPM. Use `"clock"` to sync to live MIDI clock tempo detected by the browser. |
| `curve` | `"triangle"` | `"triangle"` = up then back to zero. `"linear"` = zero up to the param value and hold. |

**Using `"bpm": "clock"`** — the browser reads `0xF8` MIDI timing clock messages (24 per beat) and computes live BPM. When a macro fires, that BPM is sent in the trigger body and substituted for `"clock"` at execution time. If no clock is detected, falls back to 140.

---

### Steps — LFO

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
| `depth` | `1.0` | Amplitude (0.0–1.0) |

---

### `"{{param}}"` — dynamic value

The string `"{{param}}"` tells the bridge to use the incoming trigger value (0.0–1.0, from the MIDI CC or the FIRE button) as the operation target. Use it as the `value` field on any step with an operation.

---

### MIDI triggers

```json
"midi_triggers": [
  { "type": "control_change", "number": 44, "channel": 1, "use_value_as_param": true },
  { "type": "note_on",        "note": 60,   "channel": 1 },
  { "type": "note_off",       "note": 60,   "channel": 1 }
]
```

A macro can have multiple triggers. Any one of them fires the macro.

| Field | Description |
|---|---|
| `type` | `"control_change"`, `"note_on"`, or `"note_off"` |
| `number` | CC number (0–127). Used when `type` is `control_change`. |
| `note` | MIDI note number (0–127). Used when `type` is `note_on` or `note_off`. |
| `channel` | MIDI channel (1–16) |
| `use_value_as_param` | CC only. When `true`, the CC value (0–127) is scaled to 0.0–1.0 and passed as `param`. |

Note On and Note Off triggers always fire with `param = 1.0` unless overridden via the web UI or MQTT.

---

## ufx2_snapshot_map.json

Maps workspace names to TotalMix Quick Select slots and their snapshot names. The bridge uses this to resolve human-readable names to the slot numbers and OSC indices it needs to switch.

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
| `snapshots` | Dict of snapshot number string (1–8) → snapshot name. Must match `snapshot` in `mappings.json`. |

**OSC snapshot recall:** the bridge converts the snapshot slot number to an OSC index with `9 - slot` (slot 1 → index 8, slot 8 → index 1). TotalMix orders snapshot buttons bottom-to-top in its OSC namespace. The recall command is `/3/snapshots/{index}/1` with value `1.0`.

**In Docker:** if you want live sync without redeploying, mount a config directory at `/app/config/` and place `ufx2_snapshot_map.json` there. The bridge polls for changes every 5 seconds.

---

## ufx2_channel_map.json

Maps OSC addresses to human-readable routing names. Used to generate the routing labels shown on macro cards (e.g. `/1/volume2` → `AN 3 → ADAT 1`). Not required for macro execution — only affects card display.

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
    },
    "Reverb Bus": {
      "index": 3,
      "name": "Reverb Bus",
      "sends": {
        "AN 3": {
          "channel": 2,
          "osc_address": "/3/volume2"
        }
      }
    }
  }
}
```

`bridge.get_routing_label()` walks every send and checks whether any macro step uses that `osc_address`. On a match it returns `"{send_name} → {submix_name}"`. If no match, the card shows `—`.

If your macros use OSC addresses not in this file, set `routing_label` directly in `mappings.json` to override.

The settings gear shows `(example)` next to the submix count when running from the example file.
