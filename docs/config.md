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
  "operation": { "type": "lfo", "bars": 4, "bpm": 140, "rate": 1.0, "depth": 1.0 }
}
```

Beat-synced wave, starting and ending at the sweep floor. `bars` sets how long it
runs, `rate` how fast it cycles.

| Field | Default | Description |
|---|---|---|
| `bars` | `2` | Total duration |
| `bpm` | `140` | Tempo. Accepts `"clock"` same as ramp. |
| `rate` | `1.0` | Cycles per beat. Total cycles = `round(bars x 4 x rate)`, minimum 1 — always a whole number so the wave ends where it began. |
| `depth` | `1.0` | Amplitude (0.0-1.0) |
| `range` | param full range | `[lo, hi]` sweep window, same as ramp |
| `threshold` | — | Gate point for toggle params (mute), same as ramp |

---

### Steps: KNOB — continuous MIDI control

```json
{
  "target": { "channel": "Main", "row": 3, "param": "lowcut_freq" },
  "value": "{{param}}",
  "operation": { "type": "knob", "hold": true, "range": [0.0, 0.6] }
}
```

A KNOB step makes the macro **follow a MIDI control live**: every value the
trigger carries (CC, 14-bit CC, pitch bend, aftertouch — with
`use_value_as_param` on) is written straight to the device. Nothing is
"fired": no timing, no device lock, no per-tick events — the browser
coalesces the stream (last value wins, ~40/s) over its WebSocket and the
bridge resolves the name and writes through the Global transport, sub-ms.
Knobs live in their own **Controls** section above the macros (they are a
different kind of object: nothing fires, the control IS the parameter), as
fader strips with a live slider (draggable — the mouse is a knob too), the
value the device actually reports, and the section switch. **New Knob** in
that header creates one.

| Field | Default | Description |
|---|---|---|
| `range` | param full range | `[lo, hi]` — maps the knob's full travel onto a window of the parameter (a lo-cut knob that only spans 20–200 Hz) |
| `threshold` | — | Gate point for toggle params (mute): knob past it = on |
| `auto_enable` | `true` | **Turn on with knob move:** if the parameter lives behind a section switch (low cut, EQ, dynamics, auto-level, reverb, echo) and that switch is off, flip it on before the first write — a lo-cut knob is inaudible otherwise. The strip also shows the switch as an ON/OFF chip you can click (`POST /api/knob/<name>/enable {"on": true}`). |
| `hold` | `true` | **Snapshot-agnostic:** after every confirmed snapshot or workspace switch, re-assert this knob's last value so a recall can't yank it back to what the snapshot stored |

Why knobs are snapshot-agnostic: under Global OSC a target resolves by
name to a fixed hardware channel in every snapshot, so the same knob works
in all of them with no switch on the macro (leave workspace/snapshot empty);
`hold` then keeps the knob's value authoritative across recalls.

Other ways in: the FIRE button, `POST /api/trigger/<name>` and
`totalmix/macro/<name>` (MQTT, payload 0–1 — a Home Assistant slider) all
set the knob to the given value; `POST /api/knob/<name> {"value": 0.4}` is
the direct HTTP form. KNOB requires the Global transport (classic refuses
with `knob_needs_global`).

---

### `"{{param}}"` — dynamic value

The string `"{{param}}"` tells the bridge to use the incoming trigger value (0.0-1.0) as the operation target. Use it as the `value` on any step that has an operation.

---

### MIDI triggers

```json
"midi_triggers": [
  { "type": "control_change",    "number": 44, "channel": 1, "use_value_as_param": true },
  { "type": "control_change_14", "number": 1,  "channel": 1, "use_value_as_param": true },
  { "type": "note_on",           "note": 60,   "channel": 1, "use_value_as_param": true },
  { "type": "note_off",          "note": 60,   "channel": 1 },
  { "type": "program_change",    "number": 5,  "channel": 1 },
  { "type": "pitch_bend",        "channel": 1, "use_value_as_param": true },
  { "type": "aftertouch",        "channel": 1, "use_value_as_param": true }
]
```

A macro can have multiple triggers. Any one fires it. All types are
learn-capable (the learn button captures whatever you send — a 14-bit CC
pair is auto-detected when its fine CC follows the coarse one within 80 ms).

| Field | Description |
|---|---|
| `type` | `control_change` · `control_change_14` · `note_on` · `note_off` · `program_change` · `pitch_bend` · `aftertouch` |
| `number` | `control_change`: CC 0-127 · `control_change_14`: the COARSE (MSB) CC 0-31 — the fine CC is `number+32` per the MIDI spec · `program_change`: program 0-127 |
| `note` | MIDI note number (0-127). Used with `note_on` and `note_off`. |
| `channel` | MIDI channel (1-16) |
| `use_value_as_param` | When `true` the message's value feeds `param` scaled to 0.0-1.0: CC value /127, 14-bit pair /16383, note velocity /127, bend /16383 (8192 = center = 0.5), aftertouch pressure /127. Ignored for `program_change` (a discrete event — always fires with `param = 1.0`). |

Notes: a `control_change_14` trigger claims BOTH of its CC numbers — plain
`control_change` triggers on the same coarse/fine numbers will not fire.
`pitch_bend` and `aftertouch` have no number: they match on channel alone.
Program Change is the scene-selection type: point the macro at a
workspace/snapshot and a PC from your sequencer or foot controller switches
the whole mixer scene.

The bridge listens to **all connected MIDI inputs at once** by default
(clock from a sequencer and knobs from a controller can coexist); pick a
single device in the header dropdown to filter to it.

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
