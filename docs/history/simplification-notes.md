# Simplification notes — toward the walk-up "simple patch" mode

## Guiding principle (user, 2026-07-30)

> "Things were all hardcoded before because we were learning the protocol.
> Ideally we just know the info from the machine and its current state."

The config files are scaffolding from the protocol-learning era. The device
broadcasts its entire state; live feedback is the source of truth and files
are at most a bootstrap/cache. Concretely: snapshots re-pair and rename
strips (hardware-observed), so ANY stored strip data goes stale — resolution
already trusts the live bank over the stored address (refusing the fallback
when they contradict), and the routing picker should eventually read live
device state per submix instead of the discovered file. Next steps down this
road: refresh per-submix bank data opportunistically on snapshot/workspace
feedback, and derive the submix list itself live.

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

- ~~Patch = routing + behavior + trigger~~ **DONE (2026-08-20, #9): simple
  patch mode shipped.** Same mappings.json schema (projection, not a new
  format); simple opens by default for simple-representable macros, advanced
  is one toggle away and the automatic fallback.
- ~~Derive `routing_label` at read time~~ **DONE (2026-08-20):**
  `GET /api/macros` derives it per request; saves strip any persisted copy.
- ~~Stop persisting runtime fields~~ **DONE (2026-08-20):** all three save
  paths (`upsert_macro`, whole-file POST, upload) strip `RUNTIME_FIELDS`
  server-side; the editor also strips before sending.
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
- ~~Static strip indices~~ **DONE (2026-07-30): name-based live resolution.**
  Strip indices proved snapshot-dependent (stereo links collapse strips);
  macros now store `target: {submix, channel}` names and the bridge resolves
  the strip from live feedback at fire time. The simple mode should ONLY ever
  deal in names. Remaining idea: re-run discovery automatically per snapshot
  switch so fallback addresses stay fresh too.

## Next targets beyond sends (user, 2026-07-30)

> "We are focused on sends atm but I'd like to tackle mutes and modulating
> the EQ params."

The capture already shows the raw material arriving as feedback: per-strip
mutes (`/1/mute/1/{n}`), solos, mic gains (`/1/micgain{n}`), phantom, and the
FX section (`/3/reverb*`, `/3/echo*`). Channel EQ params likely appear on the
channel-strip page when a channel is selected (same select-then-read pattern
as submixes/rows). Design direction: the routing picker grows a *parameter*
dimension (send volume / mute / gain / EQ band ...), each with its own
resolve rule — mutes are strip-scoped like volumes (live-resolvable by name);
FX/EQ params are global or selected-channel-scoped. Steps likely become
`target: {submix?, channel?, param: "mute"|"volume"|...}`.

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
