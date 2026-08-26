# MODUL — design sprint record

One radical design, done with intention, replacing decoration with instrument
design. Direction converged from a research pass over the philosophies the
brief named ("think germany. think techno. think form and function").

## Research → principles

**Dieter Rams / Braun (ET66, TP1, SK4):** good design is as little design as
possible; honest materials; a product is understandable by its form alone.
The ET66 calculator is the anchor artifact: matte black field, round
convex keys, one yellow key, one green — color IS function, nothing else.

**Otl Aicher (Ulm school):** the grid is the design. Uniform module widths,
one type family, pictograms over prose. Labels are silkscreen: small, white,
uppercase, tracked wide, printed once and never moved.

**Teenage Engineering (OP-1, TX-6):** the modern heir — instruments whose
screens draw *signal*, not chrome. The display shows the thing itself (a
wave, a curve), controls are physical metaphors that actually operate.

**FabFilter / RME channel EQ:** the filter *is* the curve. Direct
manipulation: grab the cutoff on the response plot itself.

**Berlin techno:** darkness as a working environment, not a style; equipment
legible at 2am from a meter away; green = powered, orange = signal; nothing
glows unless it means something.

### The ten rules of MODUL
1. Anthracite is the only material. `#141517` field, `#1c1e21` panel, one
   machined seam (`1px #000` + `1px rgba(255,255,255,.05)` top light).
2. One signal color: **orange `#ff4d00`** (Braun phono orange). It marks the
   signal path: curve, knob position, active value. Nothing decorative is orange.
3. Green `#3ddc84` exists only as POWER (section on, device alive). Red only
   as failure. No other hues anywhere.
4. Type: silkscreen labels = 10px uppercase tracked `Inter`; data = `IBM Plex
   Mono`. Two families, ever.
5. **Show the signal.** A filter renders as its magnitude response on a log
   axis — grabable. A value is a number in mono, never a percent of nothing.
6. Controls are components: rotary knobs (270° arc, drag/scroll/dblclick),
   thrown switches, keys that depress. If it looks operable it is operable.
7. Motion is mechanics: `--mo-fast: 140ms cubic-bezier(.3,.7,.25,1)` for
   operations, `--mo-settle: 260ms cubic-bezier(.2,.9,.25,1.05)` for things
   coming to rest. Curves morph, LEDs breathe when something runs, keys
   travel 1px. Readouts never animate while being set. Reduced-motion kills all.
8. Elevation has two levels: panel and key. No third.
9. The grid: modules are uniform-width instruments in a rack; macros are a
   keypad. Section titles are engraved plate labels.
10. Anything not carrying information is removed.

## Layout

```
┌ TOP PANEL ──────────────────────────────────────────────────┐
│ TOTALMIX  MODUL          ws/snap plates   MIDI · ● power    │
└─────────────────────────────────────────────────────────────┘
  FILTERS ─ rack of modules
┌ MODULE ─────────────────────┐ ┌ MODULE ─────────────────────┐
│ LO CUT      12dB/OCT   ● ON │ │ HI CUT   LOW PASS ⚲   ○ OFF │
│ ┌─────────────────────────┐ │ │ ┌─────────────────────────┐ │
│ │ 0dB ───────╭────────────│ │ │ │──────────────╮          │ │
│ │        ╱   │ curve      │ │ │ │   curve      │╲         │ │
│ │    ╱  ▓▓▓▓▓▓▓ fill      │ │ │ │  ▓▓▓▓▓▓▓▓▓▓▓│ ╲       │ │
│ │ 100 ── 1k ── 10k        │ │ │ │ 100 ── 1k ── 10k        │ │
│ └───────────●─────────────┘ │ │ └──────────────●──────────┘ │
│   ◉ knob   139 Hz    [ONF] │ │  ◉ knob  17.4k  g─◯  q─◯   │
└─────────────────────────────┘ └─────────────────────────────┘
  MACROS ─ keypad of ET66 keys (FIRE = the key, RAMP = shifted key)
```

- Curve display scale (revised 2026-08-25 after an EQ-design research
  pass): dB axis SYMMETRIC +/-24 centered on an emphasized 0 dB line
  (Pro-Q convention: symmetric ranges; sized to the device: RME gain
  +/-20 dB, Q 9.9 = +19.9 dB peak); frequency grid 1-2-5 with decade
  labels.
- Curve display: 100×36-ish ratio SVG, log-f 20..20k, dB +6..-30, decade
  gridlines, orange response line + translucent orange fill, a handle dot at
  the −3 dB point. **Drag the handle = set frequency** (same coalesced write
  path as the knob). Disabled: line falls flat to 0 dB and greys; power
  transitions animate the morph.
- Rotary knob: value arc in orange over a grey track arc, pointer line on the
  cap. Vertical drag (±, shift = fine), wheel, double-click = snap to device
  value. The knob IS the fader replacement; the old slider is gone in MODUL.
- Companions: slope/type = the plate text next to the title (click cycles);
  gain/Q = 24px mini-knobs. Enable = a real switch with travel + green LED.
- Macro cards: keycap treatment (convex gradient, 1px travel on press, LED
  dot per state); health line stays (rule 5).

## What is sacrificed
Skins stay swappable, but MODUL is the only one with its own layout — the
others remain paint. No per-band multi-EQ editing view (v2). No meters (the
listener drops /level today).

## Build map (as built)
- `web/static/vendor/uplot/` — uPlot 1.6.32 vendored (MIT): the realtime
  graphing engine. Canvas, native log scales, per-frame `setData` — also the
  substrate for future LFO/ramp waveform rendering (`ModulGraph.waveform*`).
- `web/static/modul/graph.js` — `ModulGraph`: filter response plots
  (Butterworth |H| for 6/12/18/24 dB/oct; resonant biquad LP with Q), rAF
  morph in log-frequency space, on-canvas cutoff handle, drag-to-set through
  the `knobInput` coalescer; waveform plots for operation shapes.
- `web/static/modul/knob.js` — `ModulKnob` rotary component (html/set/wire):
  270° arc, vertical drag + shift-fine, wheel, dblclick = device value.
- `ui.js` — `_knobModuleHTML` + `_modulWire` + `_modulSync`, used by
  `_renderKnobSection` when `data-skin="modul"`; `updateKnobCard` calls
  `_modulSync` so every knob_update feeds knob, curve, LED, plates.
- `app.js` — `applySkin` re-renders cards when entering/leaving MODUL.
- `skins/modul.css` — material, silkscreen type, motion tokens, keypad
  macros, module components, mobile (full-width modules; targets ≥44px).

## Architecture: the portable core

The layout is modular on purpose — the server side of this system is
expected to move to C-compiled hardware eventually (see the totalmix-strip
project), so domain logic and presentation are kept strictly apart:

| layer | contents | portability |
|---|---|---|
| **pure core** | `ModulGraph.magDb` (filter math), `_modulModel` (macro state → filter model), `_modulToKnob` (frequency → knob-norm inversion), the taper tables | plain math over plain data; ports 1:1 to C for strip firmware / an embedded server |
| **components** | `ModulKnob`, `ModulGraph` plot wrappers | reusable UI atoms; no knowledge of macros or the bridge |
| **shell** | `_knobModuleHTML`, `_modulWire`, `_modulSync`, `modul.css` | DOM/browser only; replaceable per platform |

Rules that keep it portable: core functions never touch the DOM or fetch;
components never reach into `macros`; the shell is the only layer that knows
both. Device semantics (tapers, enums, enable graphs) stay wire-verified in
`global_units.py` — the browser mirrors are annotated copies, and a C port
would mirror the same single source of truth.

Verified 2026-08-24 on a local no-OSC boot: modules render desktop + 390px
iframe, curve drag clamps to knob bounds, rotary drag/wheel write through
the coalescer, skin round-trip restores the strip layout; hidden-tab wiring
is synchronous (rAF never fires in background tabs).

## RACK (2026-08-25): the chosen layout evolution

A four-agent design council (review + three direction mockups) ran against
the shipped MODUL. The user chose **RACK**: the app is a rack - every
control a full-width 1U unit (identity flank | hero graph | control
flank), unit addresses on a slim left rail, nav as a thin machined bar,
macros as a keypad section. FOCUS (OP-1 page model) was rejected; DESK
(play/fire/config zones) judged confusing. The council's design review
(scratchpad design-review.md, 23 findings) feeds the follow-up backlog:
nav hierarchy, MODUL-native macro cards, gesture-affordance pass,
FontAwesome vendoring, ARIA on the knob component.

