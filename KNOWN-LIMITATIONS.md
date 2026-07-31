# Known limitations — v0.1.0-alpha

Honest edges of the pre-alpha, kept current so nobody re-discovers them.

## Hardware-untested branches
- **Page-2 silence refusal** ("no page-2 dump followed the row-mirror
  nudge"): needs a dead device to exercise; unit-tested only. If it fires
  in the wild, apply the freeze protocol — first question is whether the
  rack is powered.
- **Ramp/LFO mid-run trajectories**: park values are hardware-verified;
  the in-flight shape can't be sampled without diverting row-scoped writes
  (forced-dump toggle limitation). Verified by unit tests + the math.
- **Review batch fixes** (device lock, ordered ingestion, freshness
  floors): unit-tested; a hardware regression round is queued with the
  server agent.

## By design / device constraints
- The **discovery walk cannot be replaced** — submix name→index is not
  queryable from feedback (order is derivable, spacing is not, and a
  mispredicted `/setSubmix` is the crash operation). Run a walk after
  layout changes.
- **TotalMix does not echo OSC-originated changes** — live-value UI would
  need forced dumps (constraint recorded on #6).
- **Widths and layouts are snapshot-dependent** — new layouts need their
  input widths posted (`POST /api/device/widths`) or a fingerprint
  derivation (#16 phase 2, not built) before input EQ/dynamics aim there.
  Output aiming needs no widths.
- Concurrent macros serialize at step granularity behind the device-aim
  lock — a long ramp makes a simultaneously fired macro wait. Correctness
  over parallelism; finer-grained locking is future work.
- **Ramp "parked at start" means the ramp trajectory's start** (the sweep
  floor), not the channel's pre-ramp value. Natural for a volume fade;
  on an EQ-gain ramp it reads as "slammed to the floor and left there".
  Restore-to-prior-value and editor wording are #19 design-half work.
- A `/setSubmix` to the already-selected submix (every stereo pair's
  second index) is a **total no-op — zero feedback**. The walk
  disambiguates silence from a crash with a row-toggle probe.

## Not exposed
- `/2/reverbSend` — constant sentinel (−3.615/−oo) on every channel;
  not a real control on this device.
- `/2/select` — persistent Select-button state, not a parameter.
- EQ band 2 type — the device has none (band 2 is always Bell).
- Page-2 input-stage extras (phantom, pad, instrument, refLevel, width,
  msProc, loopback, recordEnable) — inventoried, shippable on request.

## Open feature board
- #6 live-fed routing picker (channel state only; the map stays for
  indices), #8 channel identify, #9 simple patch mode, #16 phase 2 width
  auto-derivation, #19 design half (rate/curve controls in the editor,
  mode descriptions, SET/SWEEP/WOBBLE naming).

## Device quirks (documented, not ours to fix)
- A strip reports `RE-!50 Out` (device-side typo).
- Page-2 low-cut frequencies read back quantised (250 → 260 Hz).
- Number of Faders per Bank and other OSC settings are per-workspace and
  revert on workspace load unless the workspace is re-saved.
