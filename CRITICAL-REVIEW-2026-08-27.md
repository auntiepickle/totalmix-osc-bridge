# Critical Review — TotalMix OSC Bridge

**Date:** 2026-08-27
**Method:** Four independent reviewers read the codebase cold, each with a
different axe to grind (DSP, live-reliability, architecture/security, product
strategy). Mandated to verify every claim in code with `file:line` citations
and to report only real flaws — no praise padding. Two of them ran the actual
engine code under wire-accurate conditions.

---

## TL;DR — is this a good idea or a bad idea?

**The idea is good. The execution has real bugs — one of them serious.**

- **Strategically sound.** No existing product automates TotalMix's *hardware*
  sends musically. "Just use a DAW" fails for a DAW-less hardware rig (a DAW
  can only automate audio that passes through it — yours doesn't). "Buy an
  X32" is greenfield-reasonable but wrong for someone who already owns the
  UFX II and still needs the tempo-synced layer. The square peg is real; the
  round hole doesn't exist.
- **But the sidechain duck — the feature I was proudest of — is broken on the
  real hardware**, and my own tests passed because they encoded the wrong
  assumption. Two reviewers found this independently by running the code.
- **Two safety guarantees I gave you are defeatable** (the −20 dB master cap;
  and the whole API is unauthenticated).

Verdict: keep building — the direction is right and the criticisms that
survive are ones the repo's own docs already concede. But fix the duck before
using it live, and close the two safety holes.

---

## 🔴 The showstopper: the sidechain duck is broken on your hardware

**Found independently by the DSP reviewer and the live-reliability reviewer,
both by running the engine code.**

The duck's base-tracking assumes TotalMix **echoes our fader writes back to
us**. It does not — the transport runs with re-send OFF, and mix-scope
readback is deliberately skipped elsewhere in the bridge. Consequences:

- `dev_db` (the "current fader position") is **frozen** during a duck.
- The engine misreads its own writes as "a human grabbed the fader" and
  re-derives its baseline every tick.
- Net effect on the wire: **under 1 dB of real ducking** while the UI reports
  "GR −12 dB" — *the meter lies*.
- Worse: the baseline **ratchets upward** each cycle. A kick-ducked pad gets
  **louder every bar**, and on release/disable the send is driven **up toward
  +6 dB**.

**The embarrassing part:** the 7 unit tests all passed because the test file
literally sets `dev_db = out` to simulate an echo the real device never sends.
Green tests validating a fiction. This is the exact "you don't know what you
don't know" risk — I shipped it with confidence.

*Currently latent* only because no duck is enabled on a live macro yet. First
time you toggle DUCK, it misbehaves.

**Fix:** own-write ledger (value + timestamp) so the engine can tell its own
writes from a real hand-move — the same pattern already used client-side for
the knob jump-back fix. Plus honest tests that model re-send OFF.

---

## 🟠 Safety findings (these undercut guarantees I gave you)

| # | Finding | Why it matters |
|---|---------|----------------|
| S1 | **Send groups bypass the −20 dB master cap.** The cap is an input clamp on the knob only; group *members* write to the +6 dB ceiling, and Main can be a member. | I told you "the cap protects your speakers." Adding Main to a group defeats that — up to 26 dB past your cap. |
| S2 | **The control API has no authentication.** Binds to all interfaces; reachable on the LAN *and* via the HTTPS nip.io proxy. Two unauthenticated POSTs can widen a monitor knob's ceiling and slam it to full, no ramp. | The ear-safety hole. It's been this way the whole project on your home LAN (standing exposure, not a new fire), but the MQTT/phone work makes remote reach more real. Needs a decision: token auth, or lock to localhost + drop the proxy. |
| S3 | **Duck never restores on shutdown.** The restore path is effectively dead code (stop function never called; the thread is a daemon that dies mid-loop). | Bridge crashes while ducked → send stuck down all set, and re-adopts the ducked level as its baseline on restart. |

---

## 🟡 Reliability (bite you mid-set)

- **`mappings.json` is written non-atomically** — crash mid-write and every
  knob/macro is gone at boot. One-line fix (`tmp + os.replace`), no downside.
- **25 Hz infinite write loop** when a duck-enabled send sits at −∞ (floor
  constant mismatch: `fader_db(0) = −300` vs `FLOOR_DB = −65`): 25 UDP
  msgs/sec forever, per parked knob.
- **Stereo-alias double-writes** inside a group (a mono alias resolving to the
  primary's channel), with no member/primary dedup.
- **Group + duck on the same knob** don't compose — the duck writes only the
  primary, so a captured group balance breaks on every duck.
- **Graph FS hardcoded at 48 kHz** — the digital-cliff render we just shipped
  only matches TotalMix at 48k; at 96/192k it over-states rolloff.
  Display-only.

---

## 🟢 What the reviewers expected to be broken but found done right

- **The RME fader-law port is exact** — round-trips to 6 decimal places
  across the −6 dB quadratic break.
- **Group offsets don't ratchet** — `offset_db` is authoritative, never
  re-derived from clamped writes; CAPTURE guards −∞ members. (This is the
  duck's exact bug done *correctly* — telling.)
- **The RBJ biquad coefficients match the cookbook exactly**; the adaptive
  peak-sampling cluster width covers the resonant lobe at every legal Q.
- **The wire-format coupling is quarantined** to ~5% of the code
  (`global_listener` + `global_transport`); a firmware change is a bad week,
  not a rewrite. There's even a legacy fallback transport.
- **Threading doesn't crash under web edits** — the loops iterate `list()`
  snapshots.
- **Meter-stream death fails safe** — after the freshness window the key reads
  −100 and the duck *releases* (fail-open, correct direction).

---

## The "square peg / round hole" verdict, point by point

| Argument | Verdict for *your* rig (own the UFX II, DAW-less) |
|----------|---------------------------------------------------|
| "Just use a DAW" | **False.** A DAW can only automate audio passing through it; your sends are TotalMix hardware routing. A DAW replaces the rig, it doesn't automate it. |
| "Buy an X32/SQ-5" | **Partially true.** Greenfield-reasonable, but $2k+ to replace working converters, and you'd *still* need the software layer for tempo-synced ramps/LFOs. |
| "It's a fake compressor" | **Partially true, doesn't matter.** It's an auto-ducker (~50–200 ms response), and the repo labels it DUCK not compression. For echo/pad *gestures* that latency is musically fine. Just never market it as "compression." |
| "One firmware update kills it" | **Partially true.** Real risk, currently at peak volatility (2.1 beta protocol) — but only ~5% of the code is wire-coupled. Bad week, not a funeral. |
| "A stranger rage-quits onboarding" | **True today.** ufx2-hardcoded channel table, two-remote + workspace-resave ritual, HTTPS/Web-MIDI hurdle. The Babyface test targets exactly the weakest joint — predict it breaks first at the channel table/sweep, not at MQTT. |

**The three criticisms that survive** — and your own docs already concede all
three: (a) never call the duck "compression"; (b) the Global-OSC bet is timed
at maximum protocol volatility; (c) it's one person's rig, not yet software
for strangers.

---

## Recommended fix order

1. **Duck echo defect** (own-write ledger + honest tests). *Don't enable a
   duck live until this lands.*
2. **Group master-cap guard** (S1) + **duck restore/persist on shutdown**
   (S3).
3. **Atomic persist** + **floor write-loop** — cheap, no-tradeoff; just do
   them.
4. **Auth** (S2) — your architecture call: shared-secret token on mutating
   routes, or localhost-only + drop the nip.io exposure.
