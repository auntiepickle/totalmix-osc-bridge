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
- **2D curve handle** (user request): dragging the dot on an EQ-band
  graph now sets frequency (horizontal) AND the vertical axis - Q on
  resonant low/high-pass (the dot's height IS the resonance peak in dB)
  or band gain on bell/shelf (place the dot at the dB you want). Bell,
  shelf (band-correct direction) and resonant high-pass curves now
  render (RBJ analog prototypes) instead of a flat line.
- **Fixed: 2D handle jumped while dragging** (user report). Vertical is
  now RELATIVE to the grab point (touching the graph no longer teleports
  Q/gain; only deliberate up/down movement applies a dB delta), and sync
  updates leave a module alone while it is being dragged (stale echoes
  were re-morphing the curve mid-gesture). Graph cursor is a pointer.
- **Graphs in every theme** (user request): the live filter curves and
  ramp/LFO wave plots now render in all skins, not just MODUL - new
  base graphs.css look, same engine, same 2D drag. Toggle under gear
  menu -> Design -> "Graphs" (default on).
- **MIN/MAX/gate readouts are typeable** (user request): tap the value
  next to a bounds or gate slider in the editors and type real units
  ("150", "8k", "75%"); the paired slider follows and saves normally.
- **Tap-to-place on the graphs** (user request): a tap (no movement)
  places the dot AT the tap point - frequency and Q/gain both, absolute.
  Drags keep the relative, no-teleport behavior.
- **Fixed: type switch flashed the old curve** (user report: Low Pass ->
  High Pass showed Low Pass momentarily). The dropdown now applies
  locally the instant you pick (optimistic), and a companion echo ledger
  holds your choice against stale write echoes until the server echoes
  it back - authoritative reads (readback/device) still win. Same
  ordering pattern as the knob jump-back fix.
- **Fixed: typed bounds quantized** (user report: 30/200 came back as
  31/203). The editor's MIN/MAX/gate sliders snapped values to a 0.01
  grid in param-norm space; they now take any value, so typed numbers
  land exactly.
- **Fixed: dot drag felt laggy** (user report). Two causes: the curve
  never followed the horizontal axis during a drag (the anti-jump sync
  skip left rendering to 10 Hz server echoes), and every update ran
  through the 170ms morph. Local writes (knobInput/companionInput) now
  refresh the curve directly on every tick, morph-free - the finger IS
  the animation. The morph remains for non-drag transitions.
- **EQ-correct graph scale** (user report: scale issues; researched
  against FabFilter Pro-Q and RME's own EQ). The dB axis is now
  SYMMETRIC +/-24 centered on an emphasized 0 dB line (the old -30..+12
  clipped boosts and resonance off the top - RME gain is +/-20 dB and
  Q 9.9 peaks at +19.9 dB, neither displayable before). Frequency grid
  follows the 1-2-5 convention (50/100/200/500/1k/2k/5k/10k) with
  decade labels. Bonus: graph-drag Q now reaches the full 9.9 (the +12
  ceiling had silently capped it near 4).
- **Per-module frequency windows** (user point: a lo-cut never goes
  above 500 Hz, so displaying 20 Hz-20 kHz wasted two thirds of the
  plot). Each graph now shows the range its parameter can IMPACT plus
  one octave of shoulder: the lo-cut displays 20 Hz-1 kHz with its
  rolloff filling the plot; EQ bands keep the full spectrum they can
  reach. Gridlines/labels adapt per window (1-2-5, denser labels on
  narrow windows).
- **Knob limits drawn on the graphs** (user request): the regions outside
  a knob's bounds are dimmed with dashed hairlines at the limits - the
  reachable lane reads bright, the locked zones read dark. Drawn only
  when the bounds actually bite inside the module's window.
- **MODUL is the default design**: a browser that never chose a skin now
  boots into MODUL; explicit choices (including Default) are respected
  and persist.
- **RACK layout** (user-chosen from a three-direction design council:
  RACK / DESK / FOCUS mockups): under MODUL, every control is now a
  full-width 1U rack unit - identity flank (name, routing, type/slope
  plates, power switch) | hero graph stretching the page | control
  flank (56px knob, 20px readout, range, device line, CC badge, minis,
  DETAILS). Unit addresses (U1, U2...) on the left rail; the nav is a
  thin machined bar; one vertical scan reads the whole chain. Mobile
  stacks each unit head/graph/controls.
- **Every knob gets a display** (user request): gain knobs draw their
  band's real bell/shelf curve (freq/Q from the minis, gain from the
  knob - axes swapped: horizontal drag moves the band freq, vertical
  the gain); volume knobs get a fader-law level strip (dB axis, unity
  tick, bounds dimming, drag-to-set); pan knobs a center-anchored
  strip (L/C/R axis).
- **Half-rack units** (user report: full rows for single values wasted
  the page): simple level/pan knobs now sit three-up as compact
  fractional units; filter/EQ units keep the full-width hero row.
- **Drag modules to reorder** (user request): grab the grip on any rack
  unit and drag it to a new slot; the order persists (new
  POST /api/config/macros-order, in-place reorder like rename).
- **Uniform modules + Eurorack sizes** (user direction): no more
  special-casing EQs - every module is the same kind of unit, flowing
  and packing into rack rows as you drag. Each unit has a STATIC size
  variant you set with its HP chip - 8HP (1/3 row), 12HP (1/2), 24HP
  (full) - persisted per module, adaptive breakpoints built against
  the fixed sizes.
- **Fixed: routing picker missed known channel names** (user report:
  only ADAT 9 / ADAT 10 offered while TotalMix showed the 9/10 pair).
  The picker now builds from the physical table's accumulated aliases -
  every stereo AND mono form a channel has ever worn - grouped
  stereo-first (Inputs/Playback/Outputs x stereo/mono). The bridge
  already resolves any alias at write time.
- **Per-size module designs** (three-agent size council): 8HP compacts
  cut everything that duplicates the graph axis or readout (84/40/32px
  wells, values kept on the gain minis); 12HP is the typographic canon;
  24HP returns the 3-zone hero frame (236px identity | 180px calibrated
  plot | 320px control with a 34px readout and 72px knob; level units
  become 120px console lanes). Well height now encodes dimensionality
  per size; changing a unit's HP re-inits its display at the right
  scale.
- **Master fader** (user request, guarded): a Main output fader knob
  capped at unity (every input path clamps - drag, tap, typed, MIDI),
  double-tap parks at -12 dB, and it never re-asserts after snapshot
  recalls. CC89.
- **Fixed: link/split in TotalMix not picked up** (user report: splitting
  AN 1/2 to mono left the warn icon stale). The listener now counts
  every name/link change; the activity poll carries the counter and any
  tick refreshes the picker + validity icons - the old burst threshold
  only caught snapshot switches.
- **Live peak meters on the level strips** (user request; user enabled
  Level Meter data in TotalMix): the Global feed's /level frames (dB,
  wire-observed) flow through the listener into GET /api/meters - each
  knob reads its meter source (sends: the source channel; row-3 knobs:
  the output), stereo pairs report the louder member. The strips draw
  a slim ink peak bar above the value lane, mapped through the same
  fader law so 0 dB signal sits at the unity tick. ~6 Hz, pauses in
  hidden tabs.
- **HP size chip made discoverable** (user missed it): bigger, brighter,
  with a resize glyph.
- **Full Eurorack HP range** (user: adjustable down to 2HP): sizes are
  now 2/4/6/8/12/16/24HP on a 12-column grid (numeric, legacy s/m/l
  read as 8/12/24). 2HP is the one-knob utility tile (name, knob,
  value, warn LED); 4HP keeps a tiny sparkline well; 6HP drops minis
  and badge; wells below 8HP lose axis labels. Narrow desks bump each
  size up; phones stack everything.
- **Vertical density pass** (user: "more graph than subtext"): routing
  rides the same line as the module name instead of burning a grey row;
  head/ctrl chrome compresses to single lines; the 1-D wells grow to
  dominate (level strips 56/72/120px by size, pan 44/56/96) - the
  signal is the module now.
- **Meter freshness window 2s -> 8s**: TotalMix resends unchanged
  (quiet/floor) levels only every 2-4s, so the 2s window made quiet
  meters blink in and out; changing values stream continuously, so the
  wider window adds no decay lag.
- **Master/output meters fixed at the source**: a raw-wire dump (spare
  TotalMix OSC controller pointed at a local listener) showed the
  output row streams as `/level/out/<hw>` - the listener only knew
  `output`, so every output meter was dropped on arrival. Token added.
  Also drove TotalMix's Global OSC bandwidth limit from 500kByte/s to
  None: all 30 input + 30 playback + output channels now stream
  (was 16 inputs + 2 playback under the cap).
- **Scroll-wheel Q** (user request): mouse wheel over a filter/EQ graph
  narrows or widens the peak (multiplicative per notch - Q reads
  logarithmically); plain 6dB high/low-pass wells ignore it (no Q axis).
- **Drag-to-resize modules + HP dropdown** (user request): grab a
  module's right edge and drag - it snaps live between the static HP
  stops and persists on release; the HP chip is now a dropdown for
  direct picks.
- **Meter fixes** (user report: no live levels on echo send / master):
  TotalMix streams only input-row meters with the current option, so
  send knobs fall back to the playback twin of their source channel;
  a metered channel at silence now shows a floor presence tick (an
  empty lane means NO METER, not no signal); /api/debug/levels reports
  per-row frame counts. Output-row meters (master) need the matching
  TotalMix option before they can display.
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
