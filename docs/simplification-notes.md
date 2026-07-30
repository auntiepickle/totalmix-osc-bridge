# Simplification notes — toward the walk-up "simple patch" mode

Decision (2026-07-30): ship the **advanced** editor first (full step lists,
raw OSC visible) and collect notes here on what a simplified patch model
should look like. Revisit once the advanced manager has real usage.

## Observations from real macros

Every macro written so far has the exact same shape:

```
/setSubmix {index}                      <- pick output bus
/{page}/volume{ch} = {{param}} + op     <- drive one send
```

plus one CC trigger with `use_value_as_param: true`. Three macros, zero
exceptions. A "simple patch" is therefore: **routing (submix + send) x
behavior (set/ramp/LFO + timing) x trigger (CC#/note + channel)** — three
dropdown groups, no OSC strings, no JSON.

## Concrete simplifications to make later

- **Patch = routing + behavior + trigger.** Store as the same mappings.json
  format (the simple editor is a projection, not a new schema). Advanced
  JSON stays as the escape hatch — already true of the current editor.
- **Derive `routing_label` at read time** from the channel map instead of
  persisting it into mappings.json. Persisted copies go stale (see
  `an3_to_adat1_send` — label still says "ADAT 1", device says "RE-150 In").
- **Stop persisting runtime fields.** `run_macro` merges `value`, `progress`,
  `last_trigger`, `osc_preview`, `midi_trigger`, `name` into live state, and
  UI saves have written them into mappings.json. The UI already strips them
  when duplicating (`RUNTIME_FIELDS` in ui.js); the server should strip them
  on every save so config stays config.
- **MIDI-learn for triggers.** The browser already sees every CC (midi.js);
  a "learn" button that captures the next CC beats typing numbers.
- **Channel test pulse.** When picking a send, a "wiggle" button that sends a
  short fader blip so the user can hear/see which channel they grabbed.
  Inverse: highlight the send in the picker when its physical fader moves
  (needs live volume feedback from the OSC listener — already captured).
- **`/setSubmix` values are strings in some macros, ints in others.**
  Both work (`float()` coercion) but the schema should settle on one.
- **Stereo-pair awareness.** Discovery records the first index of each pair;
  the picker never shows the second half. Fine — but if a user hand-types the
  odd index, nothing warns them. The simple mode should only offer known-good
  indices.

## Full-surface control (user note, 2026-07-30)

The RME reports essentially *all* signals over OSC feedback — 177 addresses
on a UFX II: mutes, solos, mic gains, phantom power, cue, talkback, the whole
FX section (reverb/echo params), record state, snapshots, master volume.
Implications:

- Macros can target far more than volume sends. The step editor already
  accepts any OSC address; the *picker* only covers sends. A future picker
  could offer categories (mute, gain, FX param...) sourced from the live
  raw-address capture rather than only the channel map.
- Live mixer state in the UI (fader positions, mute states per card) is
  feasible today from `DeviceState` — the listener already holds it.
