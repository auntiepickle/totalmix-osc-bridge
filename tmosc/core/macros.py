"""Macro execution: run_macro (classic + global steps), health records,
routing labels, durations. Requires (facade __init__): mappings, macro_health,
macro_live_state, _running_macros, _cancel_events, _queued_params, _macro_lock,
_suppress_count, _device_lock, _layout_epoch, _last_macro_end_time,
state_confirmed, mqtt_client, channel_map, global_transport. Requires (other
mixins / facade): _global_active (facade), _wait_device (switching),
reapply_held_knobs + knob_set (knobs), _resolve_target / _read_button_state /
_set_button_state / *_PARAMS (transport), _physical_table / _load_channel_map
(channel_map), broadcast_state (broadcast)."""
import logging
import re
import threading
import time

from tmosc.config import snapshot_num_to_osc_index
from tmosc.operations import OperationRegistry

logger = logging.getLogger(__name__)

class _EdgeToggleClient:
    """OSC-client shim for momentary-button addresses: turns the absolute
    0/1 stream an operation emits into edge-triggered 1.0 presses (the
    device toggles on 1.0 and ignores 0.0). Non-button addresses pass
    through untouched."""

    def __init__(self, real, addr, initial_on):
        self._real = real
        self._addr = addr
        self._on = bool(initial_on)

    def send_message(self, addr, value):
        if addr != self._addr:
            self._real.send_message(addr, value)
            return
        want = float(value) >= 0.5
        if want != self._on:
            self._real.send_message(addr, 1.0)
            self._on = want


class MacrosMixin:
    @property
    def _suppress_handler(self):
        """True while ANY macro is executing. A plain boolean was cleared
        by whichever concurrent macro finished FIRST, dropping feedback
        suppression mid-ramp for the still-running one (review finding) —
        so this is a counter now."""
        return self._suppress_count > 0

    def _get_macro_duration_ms(self, macro: dict, clock_bpm: float = None) -> int:
        """Return ramp/LFO duration in ms, or 400 for instant macros (used for WS progress events).

        If the operation config has ``"bpm": "clock"`` and clock_bpm is provided,
        the detected MIDI clock BPM is used for the duration calculation.
        """
        for step in macro.get("steps", []):
            if "operation" in step:
                op = step["operation"]
                if "duration" in op:   # explicit seconds override bars/bpm
                    try:
                        return int(float(op["duration"]) * 1000)
                    except (TypeError, ValueError):
                        pass
                bars = op.get("bars", 2)
                bpm  = op.get("bpm", 140)
                if bpm == "clock":
                    bpm = clock_bpm if clock_bpm else 140
                return int(bars * (240000 / bpm))
        return 400  # instant macro — brief visual feedback

    def get_routing_label(self, macro_name: str) -> str:
        """Return human-readable routing line for UI cards"""
        steps = self.mappings.get("macros", {}).get(macro_name, {}).get("steps", [])
        # Name-based targets are authoritative — they never go stale
        for step in steps:
            t = step.get("target")
            if (t and t.get("channel")
                    and str(t.get("param", "")).lower() in self.CHANNEL_DETAIL_PARAMS):
                pretty = str(t["param"]).replace("_", " ").title()
                return f"{t['channel']} ({pretty})"
            if t and str(t.get("param", "")).lower() in self.GLOBAL_FX_PARAMS:
                pretty = str(t["param"]).replace("_", " ").title()
                return f"FX: {pretty}"
            if t and t.get("channel"):
                param = str(t.get("param", "volume")).lower()
                if param == "mute":
                    # Mute is global-per-channel — naming a submix would
                    # imply a scope that does not exist (#10)
                    return f"{t['channel']} (mute)"
                if t.get("submix"):
                    suffix = f" ({param})" if param != "volume" else ""
                    return f"{t['channel']} → {t['submix']}{suffix}"
        if not self.channel_map:
            self._load_channel_map()
        # Legacy: match raw addresses against the channel map
        for submix_name, submix_data in self.channel_map.get("submixes", {}).items():
            for send_name, send_data in submix_data.get("sends", {}).items():
                if any(step.get("osc") == send_data.get("osc_address")
                       for step in steps):
                    return f"{send_name} → {submix_name}"
        return "—"

    def _record_fire(self, name, status, reason=None, skipped=None):
        """#22 card health: remember how the last fire of a macro went."""
        self.macro_health[name] = {
            "status": status,               # "ok" | "partial" | "skipped"
            "reason": reason,               # whole-macro skip reason
            "skipped_steps": skipped or [], # per-step skip reasons (partial)
            "at": time.time(),
        }

    def run_macro(self, macro_name: str, param: float = 0.5, clock_bpm: float = None):
        if macro_name not in self.mappings.get("macros", {}):
            logger.error(f"Macro '{macro_name}' not found")
            return

        if self.osc_client is None:
            logger.warning(f"Macro '{macro_name}' skipped — OSC not configured (set OSC_IP)")
            self._record_fire(macro_name, "skipped", "osc_not_configured")
            self.broadcast_state(macro_event={
                "type": "macro_skipped",
                "name": macro_name,
                "reason": "osc_not_configured",
            })
            return

        macro = self.mappings["macros"][macro_name]
        value = max(macro.get("param_range", [0.0, 1.0])[0],
                    min(macro.get("param_range", [0.0, 1.0])[1], float(param)))

        # === ROBUST NAME EXTRACTION ===
        ws_name = macro.get("workspace")
        snap_name = macro.get("snapshot")
        if isinstance(snap_name, dict):
            snap_name = snap_name.get("name") or list(snap_name.values())[0] if snap_name else None
        if isinstance(ws_name, dict):
            ws_name = ws_name.get("name") or list(ws_name.values())[0] if ws_name else None

        if snap_name:
            # Normalize to lowercase-stripped so comparisons are case-insensitive.
            # Previously used .title() which caused mismatches when mqtt_handler
            # stored snapshot names in lowercase from the snapshot map.
            snap_name = re.sub(r'^\d+\s*-\s*', '', str(snap_name)).strip().lower()

        force_switch = macro.get("force_switch", False)

        logger.info(f"Running macro '{macro_name}' → {ws_name}/{snap_name} param={value:.4f} (force_switch={force_switch})")

        # === FIRE MODE GUARD ===
        # fire_mode in mappings.json controls behaviour when macro is already running:
        #   "ignore"  (default) — drop the new trigger
        #   "queue"             — run once more after current completes
        #   "restart"           — cancel current immediately, re-run with new param
        fire_mode = macro.get("fire_mode", "ignore")
        # Guard + registration are atomic: triggers arrive concurrently from the
        # web thread, the MQTT thread, and queued re-fire threads.
        with self._macro_lock:
            if macro_name in self._running_macros:
                if fire_mode == "ignore":
                    logger.info(f"   → '{macro_name}' running, mode=ignore — dropped")
                    return
                elif fire_mode == "queue":
                    self._queued_params[macro_name] = (param, clock_bpm)
                    logger.info(f"   → '{macro_name}' running, mode=queue — queued (param={param:.3f})")
                    return
                elif fire_mode == "restart":
                    self._queued_params[macro_name] = (param, clock_bpm)
                    ev = self._cancel_events.get(macro_name)
                    if ev:
                        ev.set()
                    logger.info(f"   → '{macro_name}' running, mode=restart — cancelling and re-queuing")
                    return

            # Optional post-completion cooldown (ms). Set "debounce_ms" in macro config.
            debounce_ms = macro.get("debounce_ms", 0)
            if debounce_ms > 0:
                elapsed = (time.time() - self.macro_live_state.get(macro_name, {}).get("last_trigger", 0)) * 1000
                if elapsed < debounce_ms:
                    logger.info(f"   → '{macro_name}' in debounce window ({elapsed:.0f}/{debounce_ms}ms) — ignored")
                    return

            self._suppress_count += 1
            cancel_event = threading.Event()
            self._cancel_events[macro_name] = cancel_event
            self._running_macros.add(macro_name)

        try:
          # All device-global aim state (/setSubmix, /setBankStart, /1/busX)
          # and every write relative to it happens under the device lock —
          # one macro's step sequence at a time. Without it a concurrent
          # macro's re-aim silently retargets this macro's in-flight writes.
          with self._device_lock:
            # === ALWAYS RESOLVE SLOTS/INDICES ===
            ws_slot = None
            snap_num = None

            if ws_name and ws_name in self.snapshot_map:
                ws_slot = self.snapshot_map[ws_name].get("slot")

            if snap_name and ws_name in self.snapshot_map:
                snapshots = self.snapshot_map[ws_name].get("snapshots", {})
                for snap_key, snap_data in snapshots.items():
                    if isinstance(snap_data, dict):
                        candidate_name = snap_data.get("name") or snap_key
                        candidate_index = snap_data.get("index")
                    else:
                        candidate_name = snap_data
                        candidate_index = snap_key
                    if str(candidate_name).strip().lower() == snap_name:
                        snap_num = candidate_index or snap_key
                        break

            # === STATE-AWARE SWITCH ===
            # state_confirmed gate: an absorbed retained belief can be stale
            # (device moved while the bridge was down). Now that startup
            # resolves it to a REAL name it can match the target — skipping
            # the switch would leave the device on the wrong snapshot. One
            # redundant recall after a restart is the price of correctness.
            already_on_target = (
                bool(self.state_confirmed) and
                self.current_workspace == ws_name and
                self.current_snapshot == snap_name and
                ws_name is not None and snap_name is not None
            )

            # Block WS/SS switch if another macro is mid-execution and force_switch is off.
            # Avoids tearing the mixer state while a ramp is running.
            with self._macro_lock:
                other_running = len(self._running_macros) > 1  # this macro already in set
            if not already_on_target and not force_switch and other_running:
                logger.info(
                    f"   → '{macro_name}' skipped: WS/SS switch needed but "
                    f"{len(self._running_macros)-1} other macro(s) running (force_switch=False)"
                )
                self._record_fire(macro_name, "skipped", "ws_ss_blocked")
                self.broadcast_state(macro_event={
                    "type": "macro_skipped",
                    "name": macro_name,
                    "reason": "ws_ss_blocked",
                })
                return

            if force_switch or not already_on_target:
                logger.info(f"   → Need to switch (force={force_switch} or state mismatch)")

                if ws_name and ws_slot is not None:
                    t0 = time.time()
                    self.osc_client.send_message("/loadQuickWorkspace", float(ws_slot))
                    # The layout just changed: cached output rows and any
                    # state captured before this instant are void (a stale
                    # cache here once could approve a crashing /setSubmix)
                    self._outputs_cache = None
                    self._inputs_cache = None
                    self._layout_epoch = time.time()
                    logger.info(f"   → Switched workspace to '{ws_name}' (slot {ws_slot})")
                    self.current_workspace = ws_name
                    if self.mqtt_client:
                        self.mqtt_client.publish("totalmix/workspace", str(ws_slot), retain=True)
                        logger.info(f"   → Published to HA → totalmix/workspace = {ws_slot}")
                    # A workspace load triggers a full state dump (always
                    # including /1/labelSubmix) — its arrival confirms the
                    # switch, typically well under the old fixed 1.0s sleep
                    self.state_confirmed = self._wait_device(
                        lambda st: st.raw.get("/1/labelSubmix", {}).get("last_seen", 0) >= t0,
                        timeout=2.0, fallback_sleep=1.0,
                        what=f"workspace '{ws_name}' switch")
                    # TASK-6 race hardening: dumps that raced in BETWEEN the
                    # command and its confirmation can carry pre-switch
                    # content with fresh stamps — re-stamp the epoch so only
                    # post-confirmation banks are trusted for matching
                    if self.state_confirmed:
                        self._layout_epoch = time.time()

                # #25 NOTE: snapshot recall deliberately stays CLASSIC even
                # under the global transport. TASK 11 (2026-08-21) measured
                # Global /snapshot/load feedback as unreliable — load/4
                # never confirmed (2s penalty each) and the snapshots dict
                # went stale vs reality — while classic button-echo confirm
                # is 0.02–0.08s and consistent. The classic remote stays
                # configured regardless (workspace switching has no Global
                # equivalent), so this costs nothing.
                if snap_name and snap_num is not None:
                    osc_addr = f"/3/snapshots/{snapshot_num_to_osc_index(snap_num)}/1"
                    t0 = time.time()
                    self.osc_client.send_message(osc_addr, 1.0)
                    # Snapshots re-pair strips and can change layouts too
                    self._outputs_cache = None
                    self._inputs_cache = None
                    self._layout_epoch = time.time()
                    logger.info(f"   → Switched snapshot to '{snap_name}' (OSC {osc_addr} = 1.0)")
                    self.current_snapshot = snap_name
                    if self.mqtt_client:
                        self.mqtt_client.publish("totalmix/snapshot", str(snap_num), retain=True)
                        logger.info(f"   → Published to HA → totalmix/snapshot = {snap_num}")
                    # TotalMix echoes the active snapshot's button state back
                    self.state_confirmed = self._wait_device(
                        lambda st, addr=osc_addr: (
                            st.raw.get(addr, {}).get("args") == [1.0]
                            and st.raw.get(addr, {}).get("last_seen", 0) >= t0),
                        timeout=1.0, fallback_sleep=0.3,
                        what=f"snapshot '{snap_name}' recall")
                    if self.state_confirmed:
                        self._layout_epoch = time.time()  # TASK-6 race hardening
            else:
                logger.info(f"   → Already on target {ws_name}/{snap_name} — skipping ws/ss switch (force_switch=False)")

            # (#24: snapshot switches are non-events for resolution — the
            # physical table is layout-invariant and per-write confirmations
            # carry the correctness claim. No map work here, by design.)
            if (force_switch or not already_on_target) and (ws_slot is not None or snap_num is not None):
                self.reapply_held_knobs()   # snapshot-agnostic knobs

            # === EMIT macro_start SO BROWSER CAN SYNC PROGRESS BAR ===
            duration_ms = self._get_macro_duration_ms(macro, clock_bpm=clock_bpm)
            self.broadcast_state(macro_event={
                "type": "macro_start",
                "name": macro_name,
                "duration_ms": duration_ms,
            })

            # === MACRO STEPS WITH OPERATION LIBRARY ===
            # Restores are GUARANTEED via finally: an exception (or refusal
            # mid-sequence) must never leave the bank scrolled or a non-
            # input row selected — both persist on the device and silently
            # mis-target every later macro (review finding).
            _bank_dirty = False
            skip_reasons = []   # #22 health: per-step skips → "partial"
            # target steps never touch classic row state under the global
            # transport, so they cannot dirty it
            _row_dirty = (not self._global_active() and
                          any(str(s.get("target", {}).get("row", 1)) in ("2", "3")
                              for s in macro.get("steps", []) if "target" in s))
            try:
             for step in macro.get("steps", []):
                osc_addr = step.get("osc")

                # #25: name-targeted steps route through the Global
                # transport when selected — absolute addressing, no aiming,
                # no bank/row state to dirty or restore. Raw-OSC steps
                # (classic namespace) fall through to the classic path.
                if (step.get("operation") or {}).get("type") == "knob":
                    # a KNOB step fired as a macro (FIRE button, MQTT, PC
                    # trigger) is just a set to the param value
                    r = self.knob_set(macro_name, value, source="fire")
                    if r["status"] != "resolved":
                        skip_reasons.append(r["status"])
                        self.broadcast_state(macro_event={
                            "type": "macro_skipped", "name": macro_name,
                            "reason": r["status"]})
                    continue
                if "target" in step and self._global_active():
                    _fail = self._run_step_global(step, macro_name, value,
                                                  cancel_event, clock_bpm)
                    if _fail:
                        skip_reasons.append(_fail)
                    continue

                # CRASH GUARD for RAW steps: /setSubmix past the last real
                # output crashes TotalMix (hardware root cause). A raw index
                # cannot be name-verified, so it is allowed ONLY when it is
                # exactly a known submix index from the map AND the live
                # output row still matches that map. No arithmetic bounds:
                # the first version used 2×strips, which assumes every
                # output is stereo — one mono output (live layout: Main)
                # made the bound land exactly ON the fatal index. And no
                # index+1 pair-half allowance either: that re-assumes width
                # at the tail, where a mono LAST submix would make +1 the
                # fatal index. Widths have now bitten three times — exact
                # match or refuse. The whole macro stops on refusal — later
                # raw steps assume the switch happened.
                if "target" not in step and osc_addr == "/setSubmix":
                    # CRASH GUARD: /setSubmix past the hardware end crashes
                    # TotalMix (root cause). #24: membership in the MEASURED
                    # physical outputs table is the guard — every measured
                    # key is a real hardware start, bounded by the sweep's
                    # verified boundary. No arithmetic, no live enumeration.
                    table = self._physical_table()
                    known = {int(k) for k in
                             ((table or {}).get("rows", {})
                              .get("outputs", {}) or {})}
                    try:
                        raw_idx = float(value if step.get("value") == "{{param}}"
                                        else step.get("value"))
                    except (TypeError, ValueError):
                        raw_idx = None
                    if (raw_idx is None or raw_idx != int(raw_idx)
                            or int(raw_idx) not in known):
                        why = (f"index {raw_idx} is not a measured output "
                               f"channel (table knows {sorted(known)})"
                               if known else
                               "no measured outputs table — run "
                               "POST /api/device/sweep")
                        logger.error(f"   → raw /setSubmix REFUSED ({why}) — "
                                     f"an out-of-range /setSubmix crashes "
                                     f"TotalMix; use a name-based target. "
                                     f"Macro aborted.")
                        skip_reasons.append("setsubmix_unverifiable")
                        self.broadcast_state(macro_event={
                            "type": "macro_skipped",
                            "name": macro_name,
                            "reason": "setsubmix_unverifiable",
                        })
                        break

                # Name-based target: live-resolve strip index via OSC feedback.
                # Stored-address fallback ONLY when feedback is unavailable —
                # if the live bank is visible and the channel is absent, the
                # stored address may point at a different channel (snapshots
                # re-pair strips), so the step is skipped instead.
                if "target" in step:
                    _, live_addr, status = self._resolve_target(step["target"])
                    if status == "resolved":
                        osc_addr = live_addr
                    elif status == "no_feedback" and osc_addr:
                        logger.warning(f"   → using stored address {osc_addr} (no feedback)")
                    else:
                        logger.error(f"   → step skipped: target "
                                     f"{step['target'].get('channel')}@"
                                     f"{step['target'].get('submix')} unresolved ({status})")
                        skip_reasons.append(f"target_{status}")
                        self.broadcast_state(macro_event={
                            "type": "macro_skipped",
                            "name": macro_name,
                            "reason": f"target_{status}",
                        })
                        continue

                if not osc_addr:
                    logger.error(f"   → step skipped: no osc address")
                    continue

                # Channel-detail steps shift the bank window to aim page 2 —
                # restore it right after the step so every bank-0 assumption
                # elsewhere (volume/pan/mute resolution) stays true
                _restore_bank = ("target" in step and
                                 str(step["target"].get("param", "")).lower()
                                 in self.CHANNEL_DETAIL_PARAMS)

                if _restore_bank:
                    _bank_dirty = True

                _is_button = ("target" in step and
                              str(step["target"].get("param", "")).lower()
                              in self.BUTTON_PARAMS)
                _button_row = (str(step["target"].get("row", 1))
                               if _is_button else None)

                if "operation" in step and step.get("value") == "{{param}}":
                    op_config = step["operation"]
                    # Substitute live MIDI clock BPM when the mapping uses "bpm": "clock"
                    if op_config.get("bpm") == "clock":
                        resolved_bpm = clock_bpm if clock_bpm else 140
                        op_config = {**op_config, "bpm": resolved_bpm}
                        logger.info(f"   → BPM clock sync: using {resolved_bpm} BPM")
                    op_client = self.osc_client
                    if _is_button:
                        # Momentary button under modulation: sync the real
                        # state once, then press only on 0/1 edges
                        initial = self._read_button_state(osc_addr, _button_row)
                        if initial is None:
                            logger.error(f"   → step skipped: {osc_addr} state "
                                         f"unknowable, cannot modulate a "
                                         f"momentary button blind")
                            skip_reasons.append("button_state_unknown")
                            continue
                        op_client = _EdgeToggleClient(self.osc_client,
                                                      osc_addr, initial)
                    try:
                        OperationRegistry.execute(
                            op_config["type"],
                            op_client,
                            osc_addr,
                            value,
                            op_config,
                            cancel_event=cancel_event,
                        )
                    except Exception:
                        # an operation bug must not kill the trigger thread
                        # before the fire is recorded (#22 health)
                        logger.exception(f"   → operation {op_config.get('type')!r} "
                                         f"failed on {osc_addr}")
                        skip_reasons.append("operation_error")
                    if _restore_bank:
                        self.osc_client.send_message("/setBankStart", 0.0)
                        _bank_dirty = False
                        logger.info("   → bank window restored to 0 after channel-detail step")
                    continue

                # === NORMAL STATIC STEP ===
                step_val = value if step.get("value") == "{{param}}" else step.get("value")
                try:
                    if _is_button:
                        # A value write cannot set a momentary button —
                        # read fresh state, press only if it differs
                        self._set_button_state(osc_addr,
                                               float(step_val) >= 0.5,
                                               _button_row)
                    else:
                        self.osc_client.send_message(osc_addr, float(step_val))
                        logger.info(f"   → {osc_addr} = {step_val}")
                except Exception as e:
                    logger.error(f"OSC send failed: {e}")
                if _restore_bank:
                    self.osc_client.send_message("/setBankStart", 0.0)
                    _bank_dirty = False
                    logger.info("   → bank window restored to 0 after channel-detail step")
            finally:
                if _bank_dirty:
                    self.osc_client.send_message("/setBankStart", 0.0)
                    logger.info("   → bank window restored to 0 (finally)")
                # Restore the input row if any step drove the playback or
                # output row — page-1 addresses are row-relative, so leaving
                # another row selected would mis-route the next macro
                if _row_dirty:
                    self.osc_client.send_message("/1/busInput", 1.0)
                    logger.info("   → input row restored after playback/output-row step")

            # === GUARANTEED HA SYNC ===
            if self.mqtt_client:
                if ws_slot is not None:
                    self.mqtt_client.publish("totalmix/workspace", str(ws_slot), retain=True)
                if snap_num is not None:
                    self.mqtt_client.publish("totalmix/snapshot", str(snap_num), retain=True)
                    self.mqtt_client.publish("totalmix/snapshot/status", f"loaded_{snap_num}", retain=True)

            logger.info(f"Macro '{macro_name}' complete")

            # === RICH MACRO UPDATE + macro_complete EVENT ===
            self._record_fire(macro_name,
                              "partial" if skip_reasons else "ok",
                              skipped=skip_reasons)
            macro_data = self.mappings["macros"][macro_name]
            live_data = {
                "name": macro_name,
                "description": macro_data.get("description", ""),
                "value": float(value),
                "progress": 100,
                "lfo_active": False,
                "last_trigger": time.time(),
                "osc_preview": f"{(macro_data.get('steps') or [{}])[0].get('osc', '')} = {value:.3f}",
                "routing_label": self.get_routing_label(macro_name),
                "midi_trigger": macro_data.get("midi_triggers", [{}])[0] if macro_data.get("midi_triggers") else None,
                "last_fire": self.macro_health.get(macro_name),
            }
            self.macro_live_state[macro_name] = live_data
            self.broadcast_state(
                macro_update=live_data,
                macro_event={"type": "macro_complete", "name": macro_name},
            )

        finally:
            self._last_macro_end_time = time.time()
            with self._macro_lock:
                self._suppress_count = max(0, self._suppress_count - 1)
                self._cancel_events.pop(macro_name, None)
                self._running_macros.discard(macro_name)
                # Fire any queued trigger (queue mode or restart mode)
                queued = self._queued_params.pop(macro_name, None)
            if queued is not None:
                q_param, q_bpm = queued
                logger.info(f"   → '{macro_name}' firing queued trigger (param={q_param:.3f})")
                threading.Thread(target=self.run_macro, args=(macro_name, q_param),
                                 kwargs={"clock_bpm": q_bpm}, daemon=True).start()

    def _run_step_global(self, step, macro_name, value, cancel_event,
                         clock_bpm):
        """One name-targeted step over the Global transport. Handles its
        own refusal events; raw-OSC steps never reach here (they stay on
        the classic client — their addresses are classic-namespace)."""
        writer, label, status = self.global_transport.resolve_step(
            step["target"])
        if status != "resolved":
            logger.error(f"   → step skipped (global): {label!r} "
                         f"unresolved ({status})")
            self.broadcast_state(macro_event={
                "type": "macro_skipped",
                "name": macro_name,
                "reason": f"target_{status}",
            })
            return f"target_{status}"   # #22 health: caller records it
        if "operation" in step and step.get("value") == "{{param}}":
            op_config = step["operation"]
            if op_config.get("bpm") == "clock":
                resolved_bpm = clock_bpm if clock_bpm else 140
                op_config = {**op_config, "bpm": resolved_bpm}
                logger.info(f"   → BPM clock sync: using {resolved_bpm} BPM")
            # Global switches are absolute sets (no edge-toggle shim
            # needed): the writer's to_wire threshold turns the 0..1
            # stream into clean 0/1 writes.
            try:
                OperationRegistry.execute(
                    op_config["type"], writer, writer.address, value,
                    op_config, cancel_event=cancel_event)
            except Exception:
                logger.exception(f"   → operation {op_config.get('type')!r} "
                                 f"failed on {writer.address}")
                return "operation_error"   # fire recorded as partial, not lost
        else:
            step_val = value if step.get("value") == "{{param}}" else step.get("value")
            try:
                writer.send_message(writer.address, float(step_val))
                logger.info(f"   → {writer.address} = {step_val} (global)")
            except Exception as e:
                logger.error(f"Global OSC send failed: {e}")
