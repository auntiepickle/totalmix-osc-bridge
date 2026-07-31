# Changelog

## v0.1.0-alpha — 2026-08-01 · feature-complete pre-alpha

First tagged revision. Everything below is hardware-verified on an RME
UFX II running TotalMix FX unless marked otherwise.

### Macro engine
- Macros with SET / RAMP / LFO steps, fire modes (ignore / queue / restart),
  per-macro debounce, MIDI triggers (CC + notes) with MIDI-learn, MIDI-clock
  BPM sync, MQTT triggers, and a web UI trigger path.
- Ramps park at their destination (linear) or return to start (triangle);
  LFOs are beat-synced (`rate` = cycles per beat), run whole cycles, and end
  exactly where they began.
- Value shaping per step: sweep window (`range`) and gate point
  (`threshold`) for binary params.

### Name-based live resolution (the core idea)
- Macros store channel/submix NAMES; strip indices resolve from live OSC
  feedback at fire time. Stereo re-pairing, renames, and snapshot rotations
  cannot silently retarget a macro — unresolvable targets refuse
  (`refuse > mis-aim`, proven by every hardware round).
- Device state is the source of truth; config files are bootstrap.

### Parameter classes (all modulatable, all name-resolved)
- Channel: volume, mute (global-per-channel), pan (per-submix), on the
  input, playback, or output row.
- Channel EQ: 3 bands (gain / freq / Q, band types Bell / Shelf / High
  Pass / Low Pass), EQ enable, low-cut (enable / freq / slope).
- Channel Dynamics: comp/exp enable, thresholds, ratios, attack, release,
  makeup gain; Auto Level (enable, headroom, max gain, rise time); input
  gain L/R; phase invert L/R. All sliders show hardware-measured unit
  ranges.
- Global FX: reverb + echo (enable, time, volume, width, predelay,
  feedback).

### Page-2 aiming (channel detail)
- Inputs aim via layout-keyed verified width maps; outputs aim via the
  walked submix index (first output clamped to offset 0) — both rules
  hardware-measured, mono and stereo layouts.
- Every page-2 write is confirmed by `/2/trackname` before it fires:
  compute → confirm → act. Mismatch or silence refuses.

### Crash safety
- Out-of-range `/setSubmix` crashes TotalMix FX (root-caused this cycle).
  Every send is bounded: discovery stops at the live output count, macro
  fires require the live output row to match the map, raw steps require an
  exact known map index. The freeze mystery is closed.

### Discovery & device capture
- Crash-safe submix walk with per-index label confirmation and hard abort
  on unconfirmed feedback; playback-row capture; collapse guard and
  layout-keyed width carry on apply; liveness probe (bus-row toggle);
  stale-map and device-dead banners; `validate_capture.sh` with pre/post
  probes.

### Pre-alpha review hardening (this release)
- 28 confirmed findings fixed from a six-dimension multi-agent review plus
  a dedicated OSC-protocol expert review: device-aim serialization,
  output-cache invalidation on layout changes, ordered OSC ingestion,
  freshness-stamped state (ghost strips can no longer win resolution or
  defeat crash guards), guaranteed bank/row restores on all exit paths,
  panVal ingest crash, discovery label confirmation, web API hygiene
  (backups, example-map isolation, boolean validation), frontend escaping
  of device-controlled strings, WebSocket auto-reconnect.

### Tooling
- Browser MIDI emulator (CC / notes / clock) — full pipeline testing with
  no hardware; 137-test pytest suite; GitHub Actions CI.
