# Known limitations

What the bridge cannot do, why, and what to do instead. Each entry names the
constraint, its consequence, and the workaround where one exists. Entries that
only bite on the legacy classic transport say so. The August 2026 notes this
page replaces are archived in
[history/known-limitations-2026-08.md](history/known-limitations-2026-08.md).

## TotalMix OSC protocol

- **The active workspace is not reported over OSC.** The bridge only knows
  the workspace it last switched to itself (or absorbed from a retained MQTT
  message). A workspace changed inside TotalMix stays invisible until the
  next bridge-side switch. The header renders an unconfirmed workspace dashed
  and dimmed; picking one there switches TotalMix and re-syncs. (#30)
- **Snapshot feedback is a slot number, not a name.** The Global feed reports
  which Quick Select slot is active and whether it was edited since; the name
  comes from `ufx2_snapshot_map.json` for the believed workspace. A stale map
  names the wrong snapshot. Keep the map current
  (`tools/scrape_totalmix_snapshots.*` or the config editor).
- **TotalMix never echoes the bridge's own writes.** The remote's "re-send"
  option stays off on purpose: with it on, every write would come back as a
  change. After a knob move the device value is therefore only known once the
  bridge re-reads the channel (about 0.4 s later). Moves made in TotalMix are
  reported normally, which is what device sync relies on.
- **Submix name to index is not queryable on the classic remote.** Classic
  aiming (`/setSubmix`) needs the index, and a wrong index moves the wrong
  send. The sweep (`POST /api/device/sweep`) measures the physical table once
  per layout and Global name feedback keeps it current. Re-run the sweep after
  changing the channel layout.
- **Every OSC remote setting is stored per workspace.** Ports, IPs and
  "Number of Faders per Bank" revert when a workspace loads unless that
  workspace was saved with them. Symptom: the bridge sees 8 strips instead of
  48 after a switch. Re-save every workspace after changing OSC settings
  ([setup.md](setup.md#totalmix-osc-configuration-the-canonical-client-setup)).
- **The classic remote only reports the current bank.** With the default bank
  of 8, channels above 8 never reach the classic listener (sweep, probe). Set
  the bank wide enough for the whole mixer (48 on a UFX II).

## Bridge design decisions

- **Workspace and snapshot switching stay on the classic remote**, even with
  Global as the transport: Global snapshot-load feedback proved unreliable as
  a switch confirmation and the classic dump did not. Both remotes must be
  configured.
- **Macros serialize at step granularity behind the device lock.** A long
  ramp makes a macro fired at the same time wait for that step to finish.
  Correctness over parallelism.
- **Cancelling a ramp parks it at the ramp's own floor** (the low end of its
  `range`), not at the value the channel had before. Natural for a fade; on
  an EQ-gain ramp it reads as "slammed to the floor". Use a `hold` knob where
  the previous value matters.
- **Knob positions clamp to their range.** A knob is 0..1 across its
  configured `range`; a fader moved in TotalMix beyond that range shows as
  0 or 1 (full travel) on the card and on the Home Assistant slider.
- **Momentary buttons are pressed on difference only.** `/3/reverbEnable`
  and `/3/echoEnable` are toggles (1.0 flips, 0.0 is ignored), so the bridge
  reads fresh state and presses only when it differs. The page-2 enables (eq,
  dyn, lowcut, phase) are treated as value-settable; that has not been
  discriminated on hardware.
- **Web MIDI needs a secure context.** On a LAN address the browser only
  offers MIDI over HTTPS (Caddy with `tls internal`, see
  [setup.md](setup.md#https)) or from localhost. The tray agent takes MIDI
  out of the browser entirely.

## Not measured on hardware

- **Hidden channels (Channel Layout presets) on the classic paths.** The
  physical table was measured with every channel visible; whether hiding
  channels shifts the `/setBankStart` and `/setSubmix` offsets is unmeasured.
  Global handles hidden channels through TotalMix's "Receive on hidden
  channels" option. This rig hides nothing (decision 2026-08-21).
- **Page-2 silence refusal** (no dump after the row-mirror nudge) exists only
  in unit tests; it needs a dead device to reproduce. If it ever fires, check
  that the rack is powered before anything else.
- **Ramp and LFO shapes in flight** are verified by the unit tests and the
  math; the park values are hardware-verified, the trajectory itself was
  never sampled on the device.

## Device quirks (RME, not ours)

- Output names cap at about 11 characters and may gain a trailing space, so a
  long rename can yield a name identical to the old one once stripped. Use
  short, clearly different names when testing layouts.
- One strip on this rig reports `RE-!50 Out` (a typo stored on the device).
- Page-2 low-cut frequencies read back quantised (250 reads as 260 Hz).
- `/2/reverbSend` reports a constant sentinel on every channel and
  `/2/select` is the Select-button state, not a parameter; neither is exposed.
- EQ band 2 has no type on this device (always Bell).
