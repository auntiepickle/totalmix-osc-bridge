"""Classic OSC aim-and-write: parameter tables, target resolution, page-2
reads, button state, liveness probe. Requires (facade __init__): osc_client,
osc_listener, _device_lock, _layout_epoch, _inputs_cache, _outputs_cache,
last_probe. Requires (other mixins): _physical_table, _record_table_observation
(channel_map), broadcast_state (broadcast)."""
import logging
import re
import time

import tmosc.physical_table as pt

logger = logging.getLogger(__name__)

class ClassicTransportMixin:
    # Global FX-section parameters (#5 phase 1). These addresses are FIXED —
    # captured from live feedback, page 3, no channel/submix/row scope — so
    # resolution is deterministic and needs no feedback at all. Channel EQ is
    # NOT here: its scope needs a hardware measurement first (design law).
    GLOBAL_FX_PARAMS = {
        "reverb_enable":   "/3/reverbEnable",
        "reverb_time":     "/3/reverbTime",
        "reverb_volume":   "/3/reverbVolume",
        "reverb_width":    "/3/reverbWidth",
        "reverb_predelay": "/3/reverbPredelay",
        "echo_enable":     "/3/echoEnable",
        "echo_time":       "/3/echoDelaytime",
        "echo_feedback":   "/3/echoFeedback",
        "echo_volume":     "/3/echoVolume",
        "echo_width":      "/3/echoWidth",
    }

    # Channel-detail (page 2) parameters (#5 phase 2). Page 2 mirrors the
    # channel at the BANK-START position (hardware-verified with fixture
    # EQs), so resolution aims the page: pin bank 0 → resolve the strip by
    # name → compute the strip's HARDWARE-CHANNEL offset (stereo pairs
    # occupy two positions) → /setBankStart offset → write /2/... → restore
    # bank 0 after the step (bankStart is shared global state every other
    # resolve assumes is 0).
    # Momentary TOGGLE buttons (hardware-verified: /3/ FX enables
    # 2026-08-01, ALL /2/ enables 2026-08-02 — 6/6 unanimous, both rows):
    # writing 1.0 FLIPS the state and 0.0 does NOTHING. A plain value
    # write therefore "sets" nothing — set = read fresh state, press only
    # on difference; modulate = press on 0/1 edges. /2/recordEnable is
    # deliberately unexposed and untested (DURec record-arm — real-world
    # consequence); if ever exposed, assume momentary and verify first.
    BUTTON_PARAMS = {"reverb_enable", "echo_enable",
                     "eq_enable", "dyn_enable", "lowcut_enable",
                     "alev_enable", "phase", "phase_r"}

    CHANNEL_DETAIL_PARAMS = {
        "eq_enable":   "/2/eqEnable",
        "eq_gain_1":   "/2/eqGain1",
        "eq_freq_1":   "/2/eqFreq1",
        "eq_q_1":      "/2/eqQ1",
        "eq_gain_2":   "/2/eqGain2",
        "eq_freq_2":   "/2/eqFreq2",
        "eq_q_2":      "/2/eqQ2",
        "eq_gain_3":   "/2/eqGain3",
        "eq_freq_3":   "/2/eqFreq3",
        "eq_q_3":      "/2/eqQ3",
        "lowcut_freq": "/2/lowcutFreq",
        # Dynamics / Auto-Level / input stage (#20) — same page-2 aiming.
        # First tranche: addresses confirmed in the original 90-address
        # probe. The full Dynamics inventory (comp/exp threshold, ratio,
        # attack, release, enable) lands after the device inventory round.
        "dyn_gain":       "/2/compexpGain",
        "dyn_enable":     "/2/compexpEnable",
        "comp_thresh":    "/2/compTrsh",
        "comp_ratio":     "/2/compRatio",
        "exp_thresh":     "/2/expTrsh",
        "exp_ratio":      "/2/expRatio",
        "dyn_attack":     "/2/compexpAttack",
        "dyn_release":    "/2/compexpRelease",
        "alev_risetime":  "/2/alevRisetime",
        "lowcut_enable":  "/2/lowcutEnable",
        "lowcut_grade":   "/2/lowcutGrade",
        "eq_type_1":      "/2/eqType1",
        "eq_type_3":      "/2/eqType3",
        "alev_enable":    "/2/alevEnable",
        "alev_headroom":  "/2/alevHeadroom",
        "alev_maxgain":   "/2/alevMaxgain",
        "input_gain":     "/2/gain",
        "input_gain_r":   "/2/gainRight",
        "phase":          "/2/phase",
        "phase_r":        "/2/phaseRight",
    }

    @staticmethod
    def _param_address(param: str, strip) -> str:
        """OSC address for a parameter on a live-resolved strip. All are
        page-1, row-relative (the bus selection picks the row):
        volume /1/volume{n} · mute /1/mute/1/{n} · pan /1/pan{n}."""
        if param == "mute":
            return f"/1/mute/1/{strip}"
        if param == "pan":
            return f"/1/pan{strip}"
        return f"/1/volume{strip}"

    @staticmethod
    def _names_cover(strip_name: str, wanted: str) -> bool:
        """True when a strip label covers the wanted channel name across
        stereo-link changes. Snapshots re-pair strips: 'AN 2' disappears when
        AN 1+2 link into one 'AN 1/2' strip (whose fader controls both), and
        vice versa. Pair labels look like '<prefix> <a>/<b>'."""
        s = strip_name.strip().lower()
        w = wanted.strip().lower()
        if s == w:
            return True
        pair = re.match(r"^(.*?)\s*(\d+)/(\d+)$", s)
        if pair and w in (f"{pair.group(1).strip()} {pair.group(2)}",
                          f"{pair.group(1).strip()} {pair.group(3)}"):
            return True  # strip is the linked pair containing wanted
        pair = re.match(r"^(.*?)\s*(\d+)/(\d+)$", w)
        if pair and s in (f"{pair.group(1).strip()} {pair.group(2)}",
                          f"{pair.group(1).strip()} {pair.group(3)}"):
            return True  # wanted was a pair, strip is one (unlinked) half
        return False

    def _resolve_target(self, target: dict, timeout: float = 1.5):
        """Resolve {"submix": name, "channel": name} to
        (setsubmix_index, live_osc_address, status).

        status:
          "resolved"    — live match (exact or stereo-pair covering)
          "no_feedback" — no listener / no confirmation; caller MAY fall
                          back to the stored address (we know nothing)
          "not_in_bank" — the live bank was seen and the channel is NOT in
                          it; caller MUST NOT write to the stored address
                          (snapshots re-pair strips, so it now points at a
                          different channel — hardware-observed)
        """
        submix_name = str(target.get("submix", "")).strip()
        channel_name = str(target.get("channel", "")).strip()
        row = str(target.get("row", 1))
        param = str(target.get("param", "volume")).strip().lower()
        if param in self.GLOBAL_FX_PARAMS:
            # Fixed global address — no submix, no channel, no feedback needed
            addr = self.GLOBAL_FX_PARAMS[param]
            logger.info(f"   → target: global FX '{param}' → {addr}")
            return None, addr, "resolved"
        channel_scoped = (param == "mute")  # channel-detail handled below
        if param in self.CHANNEL_DETAIL_PARAMS and row == "2":
            # EQ/channel-detail exists on hardware inputs and outputs but
            # NOT on software playback (user-reported, device has no such
            # page) — an aimed write would land on a real channel instead
            logger.error(f"   → '{param}' does not exist on the playback "
                         f"row — refusing (EQ lives on hardware inputs "
                         f"and outputs only)")
            return None, None, "not_in_bank"
        if param in self.CHANNEL_DETAIL_PARAMS:
            # Page-2 aiming (#24): /setBankStart takes FIXED hardware-mono
            # offsets (RME-documented, trackname-sweep-proven invariant
            # across snapshots) — the physical table resolves the name to
            # its start directly. No widths, no strip resolution, no
            # live-layout gates: the per-write /2/trackname confirmation is
            # the correctness claim, made at the only moment it matters.
            table = self._physical_table()
            row_key = "outputs" if row == "3" else "inputs"
            offset = (pt.resolve_start(table, row_key, channel_name)
                      if table else None)
            if offset is None:
                logger.error(f"   → '{channel_name}' is not in the physical "
                             f"{row_key} table — cannot aim page 2; run "
                             f"POST /api/device/sweep to (re)measure it")
                return None, None, "not_in_bank"
            listener = self.osc_listener
            if listener is None or not listener.running:
                logger.error(f"   → no OSC listener — cannot confirm the "
                             f"page-2 aim for '{param}', refusing")
                return None, None, "not_in_bank"
            bus_addr = "/1/busOutput" if row == "3" else "/1/busInput"
            self.osc_client.send_message(bus_addr, 1.0)
            self.osc_client.send_message("/setBankStart", float(offset))
            # compute, CONFIRM, then act — every wrong-channel write this
            # project has produced would have been caught by this check
            if not self._confirm_page2_aim(channel_name, row, offset=offset):
                # restore what the failed aim changed: the scrolled bank
                # and the row selection both persist on the device
                self.osc_client.send_message("/setBankStart", 0.0)
                self.osc_client.send_message("/1/busInput", 1.0)
                return None, None, "not_in_bank"
            addr = self.CHANNEL_DETAIL_PARAMS[param]
            logger.info(f"   → resolved '{channel_name}' {param} → hw offset "
                        f"{offset} (physical table, {row_key}) → aimed "
                        f"page 2 ({addr})")
            return offset, addr, "resolved"
        if channel_scoped:
            # Mute is GLOBAL-per-channel (hardware-verified, #4/#10) and
            # channel-detail params (EQ etc.) address the CHANNEL, not a
            # submix — no /setSubmix is sent for either. Row still matters.
            index = None
        else:
            # /setSubmix takes the output's FIXED hardware start channel,
            # 0-based mono (RME-documented, sweep-proven). Membership in the
            # MEASURED table is the crash guard: every key is < the measured
            # hardware end, so a resolved start can never be the fatal
            # out-of-range send. The per-switch labelSubmix confirmation
            # below is the staleness defense — no live-layout equality gate.
            table = self._physical_table()
            index = (pt.resolve_start(table, "outputs", submix_name)
                     if table else None)
            if index is None:
                logger.warning(f"   → target submix '{submix_name}' not in "
                               f"the physical outputs table — run "
                               f"POST /api/device/sweep")
                return None, None, "not_in_bank"

        # Normalize the bank so strip indices are absolute — a bank left
        # scrolled (e.g. by TouchOSC) would shift every /1/volume{N}.
        # /setBankStart is 0-BASED (hardware-verified on a UFX II: 1.0
        # starts the bank at the SECOND channel and the shift persists as
        # global device state, silently mis-targeting hardcoded macros).
        self.osc_client.send_message("/setBankStart", 0.0)
        # Page-1 feedback AND writes follow the selected ROW — select it
        # explicitly so a stray bus selection can't mis-route the write
        bus_addr = {"1": "/1/busInput", "2": "/1/busPlayback",
                    "3": "/1/busOutput"}.get(row, "/1/busInput")
        self.osc_client.send_message(bus_addr, 1.0)
        _t_switch = time.time()
        if index is not None:
            self.osc_client.send_message("/setSubmix", float(index))
            logger.info(f"   → target: /setSubmix {index} ('{submix_name}') row {row}")
        else:
            logger.info(f"   → target: {param} '{channel_name}' row {row} (no submix switch)")

        listener = self.osc_listener
        if listener is None or not listener.running:
            logger.warning("   → no OSC listener — cannot live-resolve strip, using stored address")
            return index, None, "no_feedback"

        # Event-driven waits: the listener wakes us the instant the matching
        # OSC message arrives — no sleeps, no polling. The deadline is only
        # an error bound (UDP is lossy; the channel may not exist), never a
        # pacing mechanism.
        deadline = time.time() + timeout
        wanted = submix_name.lower()
        wanted_ch = channel_name.lower()

        def _submix_label_ok(label: str) -> bool:
            """Accept the confirmed submix label when it IS the wanted name,
            default-pair-covers it, or is a known alias of the same hw
            output (#24: 'ADAT 2' targeted while the label shows 'RE-150 In'
            covering 14-15)."""
            label = str(label or "").strip()
            if not label:
                return False
            if label.lower() == wanted or self._names_cover(label, submix_name):
                return True
            tbl = self._physical_table()
            return bool(tbl is not None and index is not None
                        and pt.covers(tbl, "outputs", index, submix_name, label))

        # Freshness watermark: DeviceState accumulates banks across layout
        # changes, and a STALE bank winning name resolution writes another
        # channel's strip index (review finding — mute had no page-2
        # confirmation to catch it). #24 TASK-6 hardware finding: the epoch
        # alone is NOT enough — a pre-switch dump can land AFTER the switch
        # command with fresh stamps (the wrong-fader race, step 5). Strips
        # must postdate THIS resolution's own sends: anything older may
        # describe the previous snapshot's numbering.
        _fresh_floor = max(self._layout_epoch, _t_switch)

        def _match_in(strips):
            # Exact name first, stereo-pair cover second — a pair strip's
            # fader controls both halves, so it is a correct target
            fresh = {s: d for s, d in strips.items()
                     if d.get("_seen", 0) >= _fresh_floor}
            for strip, data in sorted(fresh.items()):
                if str(data.get("name", "")).strip().lower() == wanted_ch:
                    return strip
            for strip, data in sorted(fresh.items()):
                if self._names_cover(str(data.get("name", "")), channel_name):
                    return strip
            # Learned-alias third priority (#24): the wanted name and the
            # shown name are aliases of the SAME hw channel — handles
            # custom-renamed pairs the a/b grammar cannot parse ('Mic 10'
            # while the strip shows 'Pill Out'). Co-occurrence required,
            # so unrelated strips can never cross-match.
            tbl = self._physical_table()
            in_key = {"1": "inputs", "3": "outputs"}.get(row)
            if tbl is not None and in_key:
                start = pt.resolve_start(tbl, in_key, channel_name)
                if start is not None:
                    for strip, data in sorted(fresh.items()):
                        shown = str(data.get("name", "")).strip()
                        if shown and pt.covers(tbl, in_key, start,
                                               channel_name, shown):
                            return strip
            return None

        def find_strip(state):
            # No logging in here — this runs as a wait predicate on every
            # incoming message, so it would log once per evaluation
            if channel_scoped:
                # Channel-scoped param: any visible bank yields the same strip
                names = ([state.current_submix] if state.current_submix else [])
                names += [s for s in list(state.submixes.keys()) if s not in names]
                for sub in names:
                    strip = _match_in(state.submix_snapshot(sub).get(row, {}))
                    if strip is not None:
                        return strip
                return None
            if not _submix_label_ok(state.current_submix):
                return None
            return _match_in(state.submix_snapshot(state.current_submix).get(row, {}))

        if not channel_scoped and not listener.wait_for(
                lambda st: _submix_label_ok(st.current_submix), timeout):
            # Distinguish SILENCE (no label followed the switch — feedback
            # loss, stored-address fallback stays legitimate) from a
            # CONFIRMED DIFFERENT label (the device answered and it is not
            # our submix under any known alias — writing anywhere now would
            # land on the wrong bus, refuse)
            lbl = listener.state.raw_entry("/1/labelSubmix") or {}
            if lbl.get("last_seen", 0) >= _t_switch and not _submix_label_ok(
                    (lbl.get("args") or [""])[0]):
                logger.error(f"   → device confirmed submix "
                             f"'{(lbl.get('args') or [''])[0]}', wanted "
                             f"'{submix_name}' — REFUSING (wrong bus)")
                return None, None, "not_in_bank"
            logger.warning(f"   → no labelSubmix confirmation for '{submix_name}' "
                           f"within {timeout}s — using stored address")
            return index, None, "no_feedback"

        # If nothing already matches post-floor, provoke a dump with the
        # probe's row-toggle trick (guaranteed change). This runs for BOTH
        # paths now: a /setSubmix to the already-selected submix is a total
        # no-op (zero messages, hardware fact), so a fresh-strips floor
        # would otherwise starve — and after a snapshot switch the only
        # candidates may be stale-content dumps (the TASK-6 race).
        if find_strip(listener.state) is None:
            other = "/1/busPlayback" if bus_addr == "/1/busInput" else "/1/busInput"
            logger.info(f"   → no fresh post-switch match — provoking a dump "
                        f"({other} → {bus_addr})")
            self.osc_client.send_message(other, 1.0)
            self.osc_client.send_message(bus_addr, 1.0)

        # TASK-7 hardware finding: a candidate match in a bank that is STILL
        # STREAMING can be the outgoing snapshot's content — the first fire
        # after a switch exact-matched 'Mic 10' at its OLD strip while the
        # true dump was in flight. Never match mid-burst: once a candidate
        # appears, wait for message-flow quiescence (no new messages for
        # SETTLE_S), then re-match on the SETTLED bank. Fire-2 on hardware
        # proved settled banks match correctly; this makes every fire a
        # fire-2. Costs ~SETTLE_S per page-1 resolution.
        SETTLE_S = 0.15
        strip = None
        while time.time() < deadline:
            if not listener.wait_for(lambda st: find_strip(st) is not None,
                                     max(0.0, deadline - time.time())):
                break
            while time.time() < deadline:
                _before = listener.state.message_count
                if not listener.wait_for(
                        lambda st: st.message_count > _before, SETTLE_S):
                    break  # quiescent — the burst has ended
            strip = find_strip(listener.state)
            if strip is not None:
                break  # settled AND matching — trustworthy
        if strip is not None:
            strip_name = str(listener.state.submix_snapshot(
                listener.state.current_submix).get(row, {})
                .get(strip, {}).get("name", ""))
            if strip_name.strip().lower() != wanted_ch:
                if self._names_cover(strip_name, channel_name):
                    logger.info(f"   → pair-matched '{channel_name}' to strip "
                                f"'{strip_name}' (stereo link changed)")
                else:
                    # Distinct tag (TASK-6 reporting gap): the learned-
                    # alias branch must be tellable from the log alone
                    logger.info(f"   → ALIAS-resolved '{channel_name}' via "
                                f"covering channel '{strip_name}' (same hw "
                                f"channel, physical table)")
            # Write address is always page 1 — the bus selection above
            # decides which row the write lands on
            addr = self._param_address(param, strip)
            logger.info(f"   → live-resolved '{channel_name}' {param} → strip {strip} "
                        f"({addr}, row {row})")
            return index, addr, "resolved"

        strips = listener.state.submix_snapshot(listener.state.current_submix).get(row, {})
        # Filter the placeholder strips past the hardware channel count —
        # a 48-wide bank would otherwise bury the real names in 30x 'n.a.'
        # — and judge 'bank seen' by FRESH entries only (stale banks must
        # not turn a refusal into a stored-address fallback)
        real = [d.get('name') for d in strips.values()
                if str(d.get('name', '')).strip().lower() not in ('n.a.', 'n/a')
                and d.get('_seen', 0) >= _fresh_floor]
        logger.error(f"   → channel '{channel_name}' is NOT in the live bank for "
                     f"'{submix_name}' (live strips: {real}) — refusing the "
                     f"stored address, it may point at a different channel now")
        return index, None, "not_in_bank"

    def _live_input_names(self, timeout: float = 1.5):
        """Fresh input-row names via a provoked dump (row toggle), settled
        to quiescence. TASK-8 hardware findings baked in: dumps stream
        VALUES FIRST and TRACKNAMES LAST (~200ms apart), so freshness is
        judged by post-provoke arrival AND a settle window, never by raw
        strip timestamps alone; and the picker must never serve the
        outgoing snapshot's row as 'live'. Ordered list of real names.
        Cached ~2s. None = cannot tell."""
        cached = getattr(self, "_inputs_cache", None)
        if cached and time.time() - cached[0] < 2.0:
            return cached[1]
        listener = self.osc_listener
        if listener is None or not listener.running or self.osc_client is None:
            return None
        t0 = time.time()
        before = listener.state.message_count
        with self._device_lock:
            # exactly one of the two is a guaranteed state change
            self.osc_client.send_message("/1/busPlayback", 1.0)
            self.osc_client.send_message("/1/busInput", 1.0)
        if not listener.wait_for(lambda s: s.message_count > before, timeout):
            return None
        deadline = t0 + timeout
        while time.time() < deadline:      # settle: outlast the whole dump
            b = listener.state.message_count
            if not listener.wait_for(lambda s: s.message_count > b, 0.15):
                break
        st = listener.state
        strips = (st.submix_snapshot(st.current_submix).get("1", {})
                  if st.current_submix else {})
        names = []
        for _, d in sorted(strips.items()):
            if d.get("_seen", 0) < t0:
                continue                    # pre-provoke content: not live
            n = str(d.get("name", "")).strip()
            if n and n.lower() not in ("n.a.", "n/a") and n not in names:
                names.append(n)
        if names:
            self._inputs_cache = (time.time(), names)
            return names
        return None

    def _live_output_names(self, timeout: float = 1.5):
        """Names of the live output strips (row 3), from a fresh busOutput
        dump. Each output strip IS one submix, so this enumerates the
        submixes that exist RIGHT NOW without sending /setSubmix — which
        matters because an out-of-range /setSubmix CRASHES TotalMix
        (hardware root cause, controlled test 2026-07-31). Cached ~2s so
        one macro run pays for the row toggle once. None = cannot tell."""
        cached = getattr(self, "_outputs_cache", None)
        if cached and time.time() - cached[0] < 2.0:
            return cached[1]
        listener = self.osc_listener
        if listener is None or not listener.running or self.osc_client is None:
            return None

        def _names(st, floor):
            # Only entries refreshed by THIS dump count: _outputs keeps
            # ghost strips from wider/older layouts forever, and a ghost
            # that matches the map would defeat the /setSubmix crash
            # guard after a layout shrink (review finding)
            strips = st.submix_snapshot("_outputs").get("3", {})
            names = {str(d.get("name", "")).strip() for d in strips.values()
                     if d.get("_seen", 0) >= floor}
            return {n for n in names
                    if n and n.lower() not in ("n.a.", "n/a")}

        with self._device_lock:
            t0 = time.time()
            before = listener.state.message_count
            self.osc_client.send_message("/1/busOutput", 1.0)
            if listener.wait_for(lambda st: st.message_count > before, timeout):
                # A row dump has no end-of-burst marker — short bounded
                # settle so stale names get overwritten first
                time.sleep(0.15)
            self.osc_client.send_message("/1/busInput", 1.0)
        names = _names(listener.state, t0) or None
        if names:
            self._outputs_cache = (time.time(), names)
        return names

    def _read_button_state(self, addr: str, row=None, timeout: float = 1.0):
        """Fresh state of a momentary button. The refresh is picked BY
        PAGE: /3/ addresses use the page-3 no-op; /2/ addresses use the
        page-2 row mirror MATCHING THE COMMANDED ROW — which must be
        threaded in by the caller, never derived from listener belief
        (the mirror is a WRITE: only the matching one is a no-op, a stale
        row belief would silently switch rows — same hazard as
        _confirm_page2_aim). For /2/ addresses the bank must already be
        aimed at the target channel (the step path aims before writing;
        the mirror does not move the bank). None = unknowable — pressing
        blind toggles RANDOMLY, so callers must refuse on None."""
        listener = self.osc_listener
        if listener is None or not listener.running or self.osc_client is None:
            return None
        if addr.startswith("/2/"):
            if row is None:
                logger.error(f"   → cannot read {addr}: page-2 button reads "
                             f"need the commanded row threaded in — refusing")
                return None
            nudge = ({"1": "/2/busInput", "2": "/2/busPlayback",
                      "3": "/2/busOutput"}.get(str(row), "/2/busInput"), 1.0)
        else:
            nudge = ("/3/faderGroups/1/1", 0.0)
        with self._device_lock:
            before = (listener.state.raw_entry(addr) or {}).get("count", 0)
            self.osc_client.send_message(nudge[0], nudge[1])
            fresh = listener.wait_for(
                lambda st: (st.raw_entry(addr) or {}).get("count", 0) > before,
                timeout)
        if not fresh:
            return None
        args = (listener.state.raw_entry(addr) or {}).get("args") or []
        try:
            return float(args[0]) >= 0.5
        except (TypeError, ValueError, IndexError):
            return None

    def _set_button_state(self, addr: str, desired: bool, row=None) -> bool:
        """Set a momentary toggle button to a target state: read fresh,
        press (1.0) only if it differs. Returns False when the state is
        unknowable — a blind press is a coin flip, refuse instead."""
        current = self._read_button_state(addr, row)
        if current is None:
            logger.error(f"   → cannot read {addr} state (no page-3 dump) — "
                         f"REFUSING the button press (a blind press toggles "
                         f"randomly)")
            return False
        if current != desired:
            with self._device_lock:
                self.osc_client.send_message(addr, 1.0)
            logger.info(f"   → {addr}: pressed (now {'On' if desired else 'Off'})")
        else:
            logger.info(f"   → {addr}: already {'On' if desired else 'Off'} — no press")
        return True

    def _confirm_page2_aim(self, channel_name: str, row: str,
                           timeout: float = 0.8, offset=None) -> bool:
        """Confirm the page-2 window shows the INTENDED channel before a
        write (#20: /2/trackname names the aimed channel, and the row-
        mirror no-op reliably triggers a fresh 90-message page-2 dump —
        both hardware-verified, idempotent across repeats).

        `row` MUST be the row the caller just commanded — the mirror is a
        WRITE (/2/busX sets the row exactly like /1/busX; only the
        matching one is a no-op), so deriving it from listener belief
        would silently SWITCH rows whenever that belief was stale
        (hardware-observed hazard, same class as the stale-map aim).

        Returns True only on a confirmed match. A mismatch refuses, and
        SILENCE refuses too: the dump primitive is verified reliable, so
        no confirmation means something is genuinely wrong."""
        shown = self._read_page2_trackname(row, timeout)
        if shown is None:
            logger.error(f"   → no page-2 dump followed the row-mirror nudge "
                         f"within {timeout}s — the confirmation primitive is "
                         f"verified reliable, so REFUSING the write")
            return False
        row_key = {"1": "inputs", "2": "playbacks", "3": "outputs"}.get(str(row))
        if shown == channel_name.strip() or self._names_cover(shown, channel_name):
            logger.info(f"   → page-2 aim CONFIRMED by /2/trackname ('{shown}')")
            self._record_table_observation(row_key, offset, shown)
            return True
        # Alias-default rule (#24): the targeted name and the shown name are
        # known aliases of the SAME hardware channel ("Mic 10" targeted while
        # the device shows "Pill Out" covering 8-9) — measured co-occurrence,
        # so this cannot cross-match unrelated strips
        table = self._physical_table()
        if (table is not None and offset is not None and row_key
                and pt.covers(table, row_key, offset, channel_name, shown)):
            logger.info(f"   → page-2 aim CONFIRMED via alias: '{channel_name}' "
                        f"is covered by '{shown}' at hw {offset}")
            return True
        logger.error(f"   → page-2 window shows '{shown}', intended "
                     f"'{channel_name}' — aim landed WRONG, refusing the write")
        return False

    def _read_page2_trackname(self, row: str, timeout: float = 0.8):
        """Read which channel the page-2 window currently shows, by nudging
        the COMMANDED row's /2/ mirror (a verified-idempotent no-op that
        triggers a fresh page-2 dump) and reading /2/trackname from it.
        None = no dump followed (refuse-worthy: the primitive is reliable)."""
        listener = self.osc_listener
        if listener is None or not listener.running or self.osc_client is None:
            return None
        st = listener.state
        entry = st.raw_entry("/2/trackname")
        before = entry["count"] if entry else 0
        row_addr = {"1": "/2/busInput", "2": "/2/busPlayback",
                    "3": "/2/busOutput"}.get(str(row), "/2/busInput")
        self.osc_client.send_message(row_addr, 1.0)
        fresh = listener.wait_for(
            lambda s: (s.raw_entry("/2/trackname") or {}).get("count", 0) > before,
            timeout)
        if not fresh:
            return None
        entry = st.raw_entry("/2/trackname") or {}
        args = entry.get("args") or []
        return str(args[0]).strip() if args else ""

    def probe_device(self, timeout: float = 2.5):
        """Liveness probe: send a state-CHANGING command and confirm a
        feedback dump follows.

        Silence is NOT evidence of a freeze — TotalMix emits feedback only on
        change, so an idle mixer is indistinguishable from a dead one by
        listening alone. This is the only sound aliveness check.

        The probe toggles the fader ROW (/1/busPlayback then /1/busInput):
        whatever row is currently selected, exactly one of the two is a
        guaranteed state change, so a dump must follow. The submix is never
        touched (a cold listener has no prior submix to restore — the old
        /setSubmix probe left the device moved after a restart), and the
        probe ends on the input row, the bridge's canonical state. Bonus: a
        cold-booted listener comes out primed with the current bank.
        """
        listener = self.osc_listener
        if listener is None or not listener.running or self.osc_client is None:
            return {"alive": None, "reason": "no OSC listener or client"}

        state = listener.state
        before = state.message_count
        t0 = time.time()
        with self._device_lock:
            self.osc_client.send_message("/1/busPlayback", 1.0)
            self.osc_client.send_message("/1/busInput", 1.0)
        alive = listener.wait_for(lambda st: st.message_count > before, timeout)
        elapsed = time.time() - t0

        result = {"alive": bool(alive), "elapsed_s": round(elapsed, 3),
                  "method": "bus row toggle (submix untouched, ends on input row)",
                  "at": time.time()}
        if alive:
            logger.info(f"Device probe OK — feedback in {elapsed:.2f}s")
        else:
            # Auto-capture evidence while the failure is fresh
            result["evidence"] = {
                "last_message_at": state.last_message_at,
                "message_count": state.message_count,
                "current_submix": state.current_submix,
                "bank_width": state.bank_width,
                "real_strip_count": state.real_strip_count,
            }
            logger.error(f"DEVICE PROBE FAILED — TotalMix is not responding to OSC. "
                         f"Check Options → Settings → OSC → 'In Use' on the device "
                         f"(ticked = OSC thread wedged; unticked = remote dropped). "
                         f"Evidence: {result['evidence']}")
        self.last_probe = result
        self.broadcast_state(macro_event={"type": "device_probe",
                                          "alive": result["alive"]})
        return result
