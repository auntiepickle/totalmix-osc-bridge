# Changelog

## Unreleased — 2026-08-24 · MODUL: the instrument layout

- New **MODUL** design (gear menu → Design → Modul): not a repaint — the one
  skin with its own layout, from a design sprint over Rams/Braun (ET66), the
  Ulm school, Teenage Engineering, FabFilter, and Berlin club hardware
  (docs/design-modul.md — the ten rules).
- Knob macros render as **instrument modules**: live filter-response curve
  on a log axis (drag the curve = set the cutoff, clamped to the knob's
  bounds), 270° rotary knob (vertical drag, shift = fine, wheel, dblclick =
  snap to device), section power switch with green LED, slope/type plates
  (click cycles), gain/Q mini-knobs. Everything writes through the existing
  coalesced knob/companion pipeline; device→browser sync drives the same
  curves and LEDs.
- Realtime **graphing engine**: uPlot 1.6.32 vendored (MIT) behind a thin
  `ModulGraph` wrapper — filter magnitude plots today, LFO/ramp waveform
  plots ready for wiring. Filter math (Butterworth orders, resonant biquad
  LP) and macro→model derivation are pure functions, kept DOM-free as the
  portable core for a future C-compiled server (see design doc).
- Motion system per the design tokens (140ms operations / 260ms settle,
  log-space curve morphs); `prefers-reduced-motion` disables all of it.
- **Waveforms on macro cards**: RAMP and LFO steps render their shape as a
  live plot on the card (triangle / one-way ramps, LFO cycle trains from
  bars x rate x depth), with a playhead riding the curve in sync with the
  progress bar during a run. MODUL skin only; reduced-motion skips the
  playhead.
- **Wave readouts**: each ramp/LFO plot shows its travel at rest and the
  live value (real units when the param is known, percent otherwise) while
  the playhead runs.
- **Fixed: knob jumps back after a drag.** Three causes, all ordering:
  the 10 Hz broadcast throttle dropped a drag's final value (server now
  arms a trailing-edge flush so the last value always broadcasts); stale
  echoes of the client's own write stream landed after release (the client
  now holds its last-sent value until the server echoes it back or an
  authoritative read supersedes it - value matching, no timers); and the
  curve display preferred device_value, which is only fresh after the
  400 ms readback (knob-norm value is now the single display authority,
  with device-side changes inverse-mapped through the knob range).
- **Mobile ergonomics (#22, ergonomics-first for live playback)**: added the
  missing viewport meta (real phones were rendering the desktop layout
  scaled down - tiny controls); new base mobile.css for ALL skins with
  thumb-scale targets (FIRE keys 56px, chips/switches/sliders 44px, no
  iOS focus-zoom, nav wraps, panels fit the viewport). MODUL mobile is a
  performance surface: 72px main knobs (thumb-pad scale), 44px minis,
  132px curve drag lanes, 17px value readouts.
- **Fixed: empty page when the WebSocket is down.** Card rendering was
  gated on ws.onopen - a wss outage (seen on the HTTPS/Caddy origin)
  left CONTROLS and MACROS completely blank while every REST-driven
  element looked healthy. Cards now load from REST at page init, and a
  visible amber banner replaces the title-only offline signal while the
  socket reconnects.
- **Knob ergonomics round**: double-tap (touch) or double-click any knob or
  slider resets it to the param's default; tap any value readout to TYPE a
  value ("150", "2.5k", "-6", "Q1.4", "L30", "38%") with unit-aware parsing;
  the type/slope cyclers are now dropdowns with curve glyphs per option
  (bell, shelf, low/high pass shapes).
- **Fixed: double-tap reset target** (user report: hi-cut reset to its
  LOWEST value). The generic param default clamped against the knob's
  bounds. New priority: explicit `operation.default` (settable in DETAILS
  with real units - "8k", "Q0.7") > the switch-off end for cut-filter
  knobs (NEUTRAL: hi-cut opens fully, lo-cut parks at its floor) > the
  param default only if it lies inside the bounds > mid-travel. Q minis
  reset to RME-neutral Q0.7.
- **Fixed: typed frequency guessed the wrong magnitude** (user report:
  "20" on the 5k-20k hi-cut parked at the floor). A bare number now
  prefers the reading that lands inside the knob's bounds ("20" -> 20k,
  "8" -> 8k on a hi-cut); explicit units ("20hz", "12k") stay literal.
- **Unity by design** (user design note: 100% of a gain slider was +6 dB
  overgain). Volume/send controls now read in REAL dB via RME's own fader
  law (CalcFaderDB, wire-verified, mirrored client-side): readouts,
  companion minis, ramp/LFO wave ranges ("-inf <-> +6.0dB") and live
  playhead values. New volume knobs default their range to [0, unity] -
  full travel tops out at 0.0 dB; widen the bounds in DETAILS to opt back
  into the +6 dB headroom. Typed entry on volume takes dB ("-12", "0",
  "u"/"unity", "-inf", or "82%"); double-tap resets to unity. Legacy
  /1/volumeN ramp steps read in dB too. Existing knob configs unchanged.
- Mobile: modules go full-width at ≤480px, knobs grow to thumb size,
  targets ≥44px. Fixed: module wiring is synchronous, so graphs initialize
  even when the page loads in a background tab.

## v0.2.0-alpha — 2026-08-21 · the fixed-table architecture

Everything below is hardware-verified on the UFX II via a four-round
validation matrix (TASK 6–9, 8/8 standing) unless noted.

### The physical hardware-channel table (#24 — the architecture pivot)
- One measured per-device table replaces width maps, the layout library,
  snapshot layout memory, and the discovery walk (all DELETED, ~-1100
  lines). `/setSubmix` and `/setBankStart` are hardware-mono indexed
  (RME-documented, sweep-proven): strip STARTS never move across
  snapshots — only display names merge and split.
- `POST /api/device/sweep` (~34s, read-only, never sends /setSubmix) is
  the learning mechanism and how a fresh install bootstraps. Aliases
  accumulate per hw channel ("AN 1" and "AN 1/2" both live at 0);
  aim confirmations keep teaching the table incrementally.
- Resolution: names resolve to measured hw starts; a vanished name
  (absorbed into a pair) resolves to its covering channel or refuses
  only itself; measured-table membership is the /setSubmix crash guard.
- Snapshot switches are non-events: no walks, no banners, no map work.
  The one remaining banner is a one-time "Measure channels" setup prompt.
- Live-fed picker (#6): channel inventory comes from provoked, settled
  row dumps — never the outgoing snapshot's row; nothing left to go stale.
- Device facts captured on hardware: TotalMix dumps stream VALUES FIRST
  and TRACKNAMES LAST (~200ms apart) — settling to quiescence is the
  load-bearing freshness primitive (two races found and regression-
  locked); /setSubmix is 0-based (Main = 0); bank sweeps past the
  hardware end saturate harmlessly.

### Global OSC enabled (groundwork for #25)
- TotalMix FX 2.1 beta runs on the rig with Global OSC live on OSC
  Remote 2 (classic untouched on Remote 1): absolute hw-channel
  addressing, per-channel state dumps in real units, ~1/sec status
  heartbeat, "Receive on hidden channels" on. Wire-verified; the bridge
  transport is the next milestone.

### Simple patch mode (#9)
- New default editor for simple-representable macros (one routed step, at
  most one MIDI trigger): three labeled groups — What (routing), How
  (behavior: SET / RAMP / LFO cards with waveform glyphs and one-line
  descriptions), When (trigger) — no raw steps, no JSON. A projection over
  the same mappings.json schema; the advanced editor stays one toggle away
  and is the automatic fallback for multi-step / raw-OSC macros.
- Routing changes in simple mode apply immediately (no "Set routing" click).
- New macros and duplicates of simple macros open in simple mode.

### Ramp/LFO editor controls (#19, UI half)
- Ramp `curve` (one-way linear vs up-and-back triangle) and LFO `rate`
  (cycles per beat: ¼ ½ 1 2 4) are now editable in both editors; read-only
  view shows the LFO rate. Mode changes seed the matching control and drop
  the other so stored JSON stays canonical.
- `use_value_as_param` ("use value") is now visible and editable per trigger
  (it was always saved as true but never shown).

### Config hygiene
- Every save path strips runtime fields (name, value, progress, lfo_active,
  last_trigger, osc_preview, midi_trigger, routing_label) from macros —
  the raw-JSON editor will show them disappear on save; that is intended.
- `routing_label` is derived at read time from the current channel map,
  never persisted (persisted labels rotted when the device renamed outputs).

### Strip-count "drift" reclassified as normal (architecture review 2026-08-20)
- Input strip counts change with every snapshot (pairing is per-snapshot
  state). The strip-count banner and the auto-walk trigger on that
  comparison are REMOVED — snapshot switches no longer cause banners or
  90-second walks. Direction: per-device fixed hardware-channel table
  (RME documents /setSubmix and /setBankStart as hardware-mono indexed)
  and, longer-term, TotalMix FX 2.1 Global OSC.

### Retained MQTT belief stays current
- A device-CONFIRMED MQTT-driven workspace/snapshot switch now republishes
  the retained `totalmix/workspace` / `totalmix/snapshot` topics (macro
  switches always did) — restarts no longer restore a belief as old as the
  last macro. The bridge's own echo is dropped per-topic per-payload, once;
  unconfirmed switches never refresh the retained value.

### Pre-flight validity (#22 seed)
- Macro cards show an amber warning icon, and the detail/editors a red
  strip, when a macro's stored names (channel, submix, output, workspace,
  snapshot) are missing from the loaded maps — the same refusals the bridge
  enforces at fire time, made visible before firing. Advisory only.

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
