"""Name-based target resolution — the fix for strip-index drift.

/1/volume{N} indexes visible fader strips; stereo-link state (snapshot-
dependent) shifts every index. Steps with {"target": {...}} must resolve the
strip LIVE from OSC feedback at fire time, not trust a captured map.
"""
import threading
import time

from osc_listener import OSCListener


CHANNEL_MAP = {
    "submixes": {
        "RE-150 In": {
            "index": 14,
            "name": "RE-150 In",
            # Captured when AN 1/AN 2 were unlinked: AN 3 sat at strip 3
            "sends": {"AN 3": {"row": 1, "channel": 3,
                               "osc_address": "/1/volume3"}},
        },
    },
}

TARGET_MACRO = {
    "steps": [{
        "osc": "/1/volume3",              # stale fallback from capture time
        "target": {"submix": "RE-150 In", "channel": "AN 3"},
        "value": "{{param}}",
    }],
}

# #24: the fixed per-device table — measured hw starts. RE-150 In is the
# renamed ADAT 1 at output hw 14 (covers 15 when paired), sweep-proven.
PHYSICAL_TABLE = {
    "schema_version": 1, "channels_per_row": 30,
    "rows": {
        "outputs": {"14": ["RE-150 In"], "15": ["ADAT 2", "RE-150 In"]},
        "inputs": {},
    },
    "last_sweep": {}, "source": {"outputs": "sweep"},
}


def cmap(extra_inputs=None, extra_outputs=None):
    """Deep-copied channel map with the physical table — tests must never
    share the mutable alias lists (aim confirmations append to them)."""
    import copy
    m = copy.deepcopy(CHANNEL_MAP)
    m["physical_table"] = copy.deepcopy(PHYSICAL_TABLE)
    if extra_inputs:
        m["physical_table"]["rows"]["inputs"].update(extra_inputs)
    if extra_outputs:
        m["physical_table"]["rows"]["outputs"].update(extra_outputs)
    return m


class LiveFakeTotalMix:
    """Answers /setSubmix by pushing feedback where AN 1/2 are now LINKED —
    one strip — so AN 3 lives at strip 2, not the captured strip 3.
    Also echoes bus selections and serves the OUTPUT row (one strip per
    submix): the /setSubmix crash guard enumerates it before any switch."""

    OUTPUT_NAMES = ["RE-150 In"]
    # offset -> channel name shown by page 2 (the /2/trackname
    # self-identification, #20). None = device never answers (used to
    # prove silence now REFUSES).
    PAGE2_BY_OFFSET = None

    def __init__(self, listener, fake_osc):
        self.listener = listener
        self.fake_osc = fake_osc
        self._bank = 0

    def send_message(self, address, value):
        self.fake_osc.send_message(address, value)
        if address == "/setBankStart":
            self._bank = int(value)
            return
        if address in ("/2/busInput", "/2/busPlayback", "/2/busOutput"):
            # row-mirror no-op -> page-2 dump, incl. the window's trackname
            if self.PAGE2_BY_OFFSET is not None:
                self.listener._handle(
                    "/2/trackname",
                    self.PAGE2_BY_OFFSET.get(self._bank, "n.a."))
            return
        if address in ("/1/busInput", "/1/busPlayback", "/1/busOutput"):
            self.listener._handle(address, 1.0)
            if address == "/1/busOutput":
                for n, name in enumerate(self.OUTPUT_NAMES, 1):
                    self.listener._handle(f"/1/trackname{n}", name)
            self.on_bus(address)
            return
        if address == "/setSubmix":
            self.dump_bank(int(value))

    def on_bus(self, address):
        pass

    def dump_bank(self, index):
        if index == 14:
            self.listener._handle("/1/labelSubmix", "RE-150 In")
            self.listener._handle("/1/trackname1", "AN 1/2")  # linked pair
            self.listener._handle("/1/trackname2", "AN 3")    # shifted!
            self.listener._handle("/1/trackname3", "RE-101")


def make_target_bridge(make_bridge, fake_osc, listener=None):
    b = make_bridge({"m": TARGET_MACRO})
    b.channel_map = cmap()
    b.osc_listener = listener
    if listener is not None:
        b.osc_client = LiveFakeTotalMix(listener, fake_osc)
        # _resolve_target checks listener.running — fake a live server
        listener._server = object()
    return b


def test_live_resolution_overrides_stale_address(make_bridge, fake_osc):
    listener = OSCListener(0)
    b = make_target_bridge(make_bridge, fake_osc, listener)
    b.run_macro("m", 0.8)

    assert ("/setSubmix", 14.0) in fake_osc.sent
    # /setBankStart is 0-BASED — 1.0 shifts the bank one strip left and the
    # shift persists on the device (hardware-verified regression)
    assert ("/setBankStart", 0.0) in fake_osc.sent
    assert ("/setBankStart", 1.0) not in fake_osc.sent
    # Live bank says AN 3 is strip 2 now — captured map said 3
    assert ("/1/volume2", 0.8) in fake_osc.sent
    assert "/1/volume3" not in fake_osc.addresses()


def test_no_listener_sends_measured_index_and_falls_back(make_bridge, fake_osc):
    """#24 behavior change: the index comes from the MEASURED table, so it
    is in-range by construction — a blind bridge may still switch (safe)
    and use the stored address as the documented no-feedback fallback."""
    b = make_target_bridge(make_bridge, fake_osc, listener=None)
    b.run_macro("m", 0.8)
    assert ("/setSubmix", 14.0) in fake_osc.sent
    assert ("/1/volume3", 0.8) in fake_osc.sent


def test_silent_device_falls_back_to_stored_address(make_bridge, fake_osc):
    """Listener up but the device answers nothing: the measured index is
    safe to send (#24); with no label confirmation the step takes the
    stored-address fallback (mis-aim risk only, never crash risk)."""
    listener = OSCListener(0)          # never receives anything
    listener._server = object()
    b = make_bridge({"m": TARGET_MACRO})
    b.channel_map = cmap()
    b.osc_listener = listener
    b.run_macro("m", 0.8)              # osc_client is the plain fake — no feedback
    assert ("/setSubmix", 14.0) in fake_osc.sent
    assert ("/1/volume3", 0.8) in fake_osc.sent


def test_unmeasured_submix_never_switches(make_bridge, fake_osc):
    """No physical table entry = no send: membership in the measured table
    IS the crash guard now."""
    b = make_target_bridge(make_bridge, fake_osc, listener=None)
    del b.channel_map["physical_table"]
    b.run_macro("m", 0.8)
    assert "/setSubmix" not in fake_osc.addresses()
    assert "/1/volume3" not in fake_osc.addresses()


class OutputsOnlyTotalMix(LiveFakeTotalMix):
    """Serves the output row (crash guard passes) but never answers the
    /setSubmix — models feedback loss after a healthy, valid switch."""

    def dump_bank(self, index):
        pass


def test_no_submix_feedback_falls_back_to_stored_address(make_bridge, fake_osc):
    """Guard passed (live outputs match the map) and the switch was sent —
    only the bank dump is missing. THIS is where the stored address stays
    a legitimate last resort (mis-aim risk only, no crash risk)."""
    listener = OSCListener(0)
    b = make_target_bridge(make_bridge, fake_osc, listener)
    b.osc_client = OutputsOnlyTotalMix(listener, fake_osc)
    b.run_macro("m", 0.8)
    assert ("/setSubmix", 14.0) in fake_osc.sent
    assert ("/1/volume3", 0.8) in fake_osc.sent  # stale but best available


class WrongLabelTotalMix(LiveFakeTotalMix):
    """The device answers the switch with a DIFFERENT submix label — the
    channel at hw 14 was renamed to something no alias knows."""

    def dump_bank(self, index):
        self.listener._handle("/1/labelSubmix", "Something Else Out")


class RacyDumpTotalMix(LiveFakeTotalMix):
    """Models the TASK-6 step-5 hardware failure: a PRE-switch bank dump
    (old snapshot's strip numbering) lands with fresh timestamps just
    before resolution; the TRUE bank arrives only when a row-toggle
    provokes a dump. /setSubmix to the already-selected submix is a total
    no-op (hardware fact) — it dumps nothing."""

    STALE_BANK = {1: "AN 1/2", 6: "Pill Out"}           # old numbering
    TRUE_BANK = {1: "AN 1/2", 6: "AN 7/8", 7: "Pill Out"}  # current

    def __init__(self, listener, fake_osc):
        super().__init__(listener, fake_osc)
        self._row_selected = None

    def dump_bank(self, index):
        pass  # already-selected submix: total no-op

    def on_bus(self, address):
        # a REPEATED select of the same row is a no-op; a row CHANGE dumps
        if address == self._row_selected:
            return
        self._row_selected = address
        if address == "/1/busInput":
            self.listener._handle("/1/labelSubmix", "Main")
            for n, name in self.TRUE_BANK.items():
                self.listener._handle(f"/1/trackname{n}", name)

    def inject_stale_dump(self):
        self.listener._handle("/1/labelSubmix", "Main")
        for n, name in self.STALE_BANK.items():
            self.listener._handle(f"/1/trackname{n}", name)


def test_stale_fresh_dump_never_wins_the_match(make_bridge, fake_osc):
    """TASK-6 hardware failure (wrong-fader write): a pre-switch dump with
    fresh stamps must NOT be matched — strips must postdate THIS
    resolution's own sends, and when the no-op switch yields no dump the
    resolver provokes one and matches the TRUE bank."""
    listener = OSCListener(0)
    b = make_bridge({"m": {"steps": [{
        "target": {"submix": "Main", "channel": "Mic 10"},
        "value": "0.5",
    }]}})
    b.channel_map = cmap(
        extra_inputs={"8": ["Mic 10", "Pill Out"], "9": ["Mic 10", "Pill Out"]},
        extra_outputs={"0": ["Main"]})
    b.osc_listener = listener
    b.osc_client = RacyDumpTotalMix(listener, fake_osc)
    listener._server = object()
    # the race: an old-content dump lands fresh, right before the fire
    b.osc_client.inject_stale_dump()
    b.run_macro("m", 0.5)
    # OLD numbering had Pill Out at strip 6 — writing 6 moved AN 7/8 on
    # hardware. The write must land on the TRUE bank's strip 7.
    assert ("/1/volume7", 0.5) in fake_osc.sent
    assert ("/1/volume6", 0.5) not in fake_osc.sent


def test_confirmed_different_label_refuses_write(make_bridge, fake_osc):
    """#24: the measured index is safe to SEND, but when the device
    CONFIRMS a label no alias covers, writing anywhere would land on the
    wrong bus — refuse (distinct from silence, which falls back)."""
    listener = OSCListener(0)
    b = make_target_bridge(make_bridge, fake_osc, listener)
    b.osc_client = WrongLabelTotalMix(listener, fake_osc)
    b.run_macro("m", 0.8)
    assert ("/setSubmix", 14.0) in fake_osc.sent   # in-range, safe
    assert "/1/volume3" not in fake_osc.addresses()  # write refused
    assert "/1/volume2" not in fake_osc.addresses()
    skipped = [e["event"] for e in b.events
               if e["event"] and e["event"]["type"] == "macro_skipped"]
    assert skipped and skipped[0]["reason"] == "target_not_in_bank"


def test_unknown_submix_skips_step_without_crash(make_bridge, fake_osc):
    b = make_bridge({"m": {"steps": [{
        "target": {"submix": "Nonexistent", "channel": "AN 3"},
        "value": "{{param}}",
    }]}})
    b.channel_map = cmap()
    b.run_macro("m", 0.8)
    assert fake_osc.sent == []  # nothing sent, no exception


def test_routing_label_prefers_target_names(make_bridge, fake_osc):
    b = make_target_bridge(make_bridge, fake_osc)
    assert b.get_routing_label("m") == "AN 3 → RE-150 In"


def test_target_with_operation_ramps_resolved_address(make_bridge, fake_osc):
    listener = OSCListener(0)
    macro = {
        "steps": [{
            "osc": "/1/volume3",
            "target": {"submix": "RE-150 In", "channel": "AN 3"},
            "value": "{{param}}",
            "operation": {"type": "ramp", "duration": 0.2, "steps_per_sec": 20},
        }],
    }
    b = make_bridge({"m": macro})
    b.channel_map = cmap()
    b.osc_listener = listener
    b.osc_client = LiveFakeTotalMix(listener, fake_osc)
    listener._server = object()
    b.run_macro("m", 0.8)

    ramp_addrs = {a for a, _ in fake_osc.sent if a.startswith("/1/volume")}
    assert ramp_addrs == {"/1/volume2"}  # whole ramp on the live strip


class SlowBurstTotalMix(LiveFakeTotalMix):
    """Delivers the label immediately but the high strip's trackname late —
    models a 48-fader bank dump still in flight when resolution starts."""

    def dump_bank(self, index):
        if index == 14:
            self.listener._handle("/1/labelSubmix", "RE-150 In")
            # High strip arrives ~0.3s later, past the old 0.15s fixed grace
            def late():
                time.sleep(0.3)
                self.listener._handle("/1/trackname23", "ADAT 15/16")
            threading.Thread(target=late, daemon=True).start()


def test_late_burst_high_strip_still_resolves(make_bridge, fake_osc):
    macro = {"steps": [{
        "target": {"submix": "RE-150 In", "channel": "ADAT 15/16"},
        "value": "{{param}}",
    }]}
    listener = OSCListener(0)
    b = make_bridge({"m": macro})
    b.channel_map = cmap()
    b.osc_listener = listener
    b.osc_client = SlowBurstTotalMix(listener, fake_osc)
    listener._server = object()
    b.run_macro("m", 0.8)
    assert ("/1/volume23", 0.8) in fake_osc.sent


class RepairedBankTotalMix(LiveFakeTotalMix):
    """Models a snapshot having re-paired strips: 'AN 2' no longer exists,
    the linked strip 'AN 1/2' covers it (hardware-observed on snapshot
    recall)."""

    def dump_bank(self, index):
        if index == 14:
            self.listener._handle("/1/labelSubmix", "RE-150 In")
            self.listener._handle("/1/trackname1", "AN 1/2")
            self.listener._handle("/1/trackname2", "RE-101")


def test_pair_match_when_snapshot_relinked_strips(make_bridge, fake_osc):
    """Target 'AN 2' must match the covering pair strip 'AN 1/2' — its
    fader controls both halves."""
    macro = {"steps": [{
        "osc": "/1/volume9",  # stale — must not be used
        "target": {"submix": "RE-150 In", "channel": "AN 2"},
        "value": "{{param}}",
    }]}
    listener = OSCListener(0)
    b = make_bridge({"m": macro})
    b.channel_map = cmap()
    b.osc_listener = listener
    b.osc_client = RepairedBankTotalMix(listener, fake_osc)
    listener._server = object()
    b.run_macro("m", 0.8)
    assert ("/1/volume1", 0.8) in fake_osc.sent   # the pair strip
    assert "/1/volume9" not in fake_osc.addresses()


def test_refuses_stored_address_when_bank_seen_but_channel_absent(make_bridge, fake_osc):
    """The live bank is visible and the channel is NOT in it: writing to the
    stored address would hit a different channel — the step must be skipped."""
    macro = {"steps": [{
        "osc": "/1/volume2",  # now points at RE-101 in this bank — wrong
        "target": {"submix": "RE-150 In", "channel": "Ghost Channel"},
        "value": "{{param}}",
    }]}
    listener = OSCListener(0)
    b = make_bridge({"m": macro})
    b.channel_map = cmap()
    b.osc_listener = listener
    b.osc_client = RepairedBankTotalMix(listener, fake_osc)
    listener._server = object()
    b.run_macro("m", 0.8)
    assert "/1/volume2" not in fake_osc.addresses()  # refusal, not fallback
    skipped = [e["event"] for e in b.events
               if e["event"] and e["event"]["type"] == "macro_skipped"]
    assert skipped and skipped[0]["reason"] == "target_not_in_bank"


class PlaybackRowTotalMix(LiveFakeTotalMix):
    """Echoes bus selections into feedback (like the real device) and dumps
    playback-row tracknames when /1/busPlayback is selected."""

    def dump_bank(self, index):
        if index == 14:
            self.listener._handle("/1/labelSubmix", "RE-150 In")
            # Current row's bank dumps after the submix confirm; the fake
            # dumps playback names (row was selected before setSubmix)
            self.listener._handle("/1/trackname3", "ADAT 5/6")


def test_playback_row_target_selects_bus_and_restores_input(make_bridge, fake_osc):
    macro = {"steps": [{
        "target": {"submix": "RE-150 In", "channel": "ADAT 5/6", "row": 2},
        "value": "{{param}}",
    }]}
    listener = OSCListener(0)
    b = make_bridge({"m": macro})
    b.channel_map = cmap()
    b.osc_listener = listener
    b.osc_client = PlaybackRowTotalMix(listener, fake_osc)
    listener._server = object()
    b.run_macro("m", 0.6)

    addrs = fake_osc.addresses()
    # Row selected before the submix, write on page 1, input row restored
    assert ("/1/busPlayback", 1.0) in fake_osc.sent
    assert addrs.index("/1/busPlayback") < addrs.index("/setSubmix")
    assert ("/1/volume3", 0.6) in fake_osc.sent
    last_bus_input = len(addrs) - 1 - addrs[::-1].index("/1/busInput")
    assert addrs.index("/1/volume3") < last_bus_input
    assert fake_osc.sent[-1] != ("/1/busPlayback", 1.0)  # not left on playback


class ProbeEchoTotalMix:
    """Alive device for probe tests: a bus-row change produces a dump.
    Tracks the selected row so an idempotent re-select stays silent —
    exactly one of the probe's two toggles must produce feedback."""

    def __init__(self, listener, fake_osc):
        self.listener = listener
        self.fake_osc = fake_osc
        self.row = "input"

    def send_message(self, address, value):
        self.fake_osc.send_message(address, value)
        new_row = {"/1/busPlayback": "playback", "/1/busInput": "input"}.get(address)
        if new_row and new_row != self.row:
            self.row = new_row
            self.listener._handle(address, 1.0)
            self.listener._handle("/1/labelSubmix", "Phones 1")
            self.listener._handle("/1/trackname1", "AN 1")


def _probe_bridge(make_bridge, fake_osc, device_cls):
    listener = OSCListener(0)
    listener._server = object()
    b = make_bridge({})
    b.channel_map = {"submixes": {
        "Main": {"index": 1}, "AES": {"index": 12},
    }}
    b.osc_listener = listener
    b.osc_client = device_cls(listener, fake_osc) if device_cls else b.osc_client
    return b, listener


def test_probe_alive_device_never_touches_submix(make_bridge, fake_osc):
    b, listener = _probe_bridge(make_bridge, fake_osc, ProbeEchoTotalMix)
    result = b.probe_device(timeout=1.0)
    assert result["alive"] is True
    # The whole point of the bus-toggle probe: /setSubmix is never sent,
    # so there is nothing to restore — even on a cold listener
    assert "/setSubmix" not in fake_osc.addresses()
    assert fake_osc.addresses()[:2] == ["/1/busPlayback", "/1/busInput"]
    assert b.last_probe["alive"] is True


def test_probe_works_cold_after_restart(make_bridge, fake_osc):
    """The old /setSubmix probe left the device moved when the listener had
    no prior submix (guaranteed after a restart). Bus toggling has no such
    failure mode: the listener starts empty and the probe still passes."""
    b, listener = _probe_bridge(make_bridge, fake_osc, ProbeEchoTotalMix)
    assert listener.state.current_submix is None  # cold boot
    result = b.probe_device(timeout=1.0)
    assert result["alive"] is True
    # And the probe primed the previously-blind listener for free
    assert listener.state.current_submix == "Phones 1"


def test_probe_dead_device_captures_evidence(make_bridge, fake_osc):
    b, listener = _probe_bridge(make_bridge, fake_osc, None)  # plain fake: no echo
    result = b.probe_device(timeout=0.3)
    assert result["alive"] is False
    assert "evidence" in result
    assert b.last_probe["alive"] is False


def test_probe_without_listener_is_unavailable(make_bridge, fake_osc):
    b = make_bridge({})
    assert b.probe_device()["alive"] is None


def test_mute_target_resolves_without_touching_submix(make_bridge, fake_osc):
    """Mute is global-per-channel (#10): resolution NEVER sends /setSubmix —
    the user's selected submix stays put. TASK-6 hardening: a pre-fire
    'warm' bank is no longer trusted (stale numbering hazard) — the
    row-toggle provoke fetches a fresh dump instead."""
    macro = {"steps": [{
        "target": {"channel": "AN 3", "param": "mute"},  # no submix at all
        "value": "1.0",
    }]}

    class DumpOnToggle(LiveFakeTotalMix):
        def on_bus(self, address):
            if address == "/1/busInput":
                self.listener._handle("/1/labelSubmix", "Phones 1")
                self.listener._handle("/1/trackname2", "AN 3")

    listener = OSCListener(0)
    b = make_bridge({"m": macro})
    b.channel_map = cmap()
    b.osc_listener = listener
    b.osc_client = DumpOnToggle(listener, fake_osc)
    listener._server = object()
    b.run_macro("m", 0.0)
    assert ("/1/mute/1/2", 1.0) in fake_osc.sent
    assert "/setSubmix" not in fake_osc.addresses()
    assert not [a for a in fake_osc.addresses() if a.startswith("/1/volume")]


class ToggleDumpTotalMix:
    """Dumps the bank only on a genuine bus-row change — models a cold
    listener being primed by the mute path's row-toggle trick."""

    def __init__(self, listener, fake_osc):
        self.listener = listener
        self.fake_osc = fake_osc
        self.row = "input"

    def send_message(self, address, value):
        self.fake_osc.send_message(address, value)
        new_row = {"/1/busPlayback": "playback", "/1/busInput": "input"}.get(address)
        if new_row and new_row != self.row:
            self.row = new_row
            self.listener._handle(address, 1.0)
            if new_row == "input":
                self.listener._handle("/1/labelSubmix", "Phones 1")
                self.listener._handle("/1/trackname2", "AN 3")


def test_mute_target_cold_listener_self_primes(make_bridge, fake_osc):
    macro = {"steps": [{
        "target": {"channel": "AN 3", "param": "mute"},
        "value": "1.0",
    }]}
    listener = OSCListener(0)  # completely cold
    b = make_bridge({"m": macro})
    b.channel_map = cmap()
    b.osc_listener = listener
    b.osc_client = ToggleDumpTotalMix(listener, fake_osc)
    listener._server = object()
    b.run_macro("m", 0.0)
    assert ("/1/mute/1/2", 1.0) in fake_osc.sent
    assert "/setSubmix" not in fake_osc.addresses()


def test_pan_target_resolves_to_pan_address(make_bridge, fake_osc):
    macro = {"steps": [{
        "target": {"submix": "RE-150 In", "channel": "AN 3", "param": "pan"},
        "value": "{{param}}",
    }]}
    listener = OSCListener(0)
    b = make_bridge({"m": macro})
    b.channel_map = cmap()
    b.osc_listener = listener
    b.osc_client = LiveFakeTotalMix(listener, fake_osc)
    listener._server = object()
    b.run_macro("m", 0.25)
    assert ("/1/pan2", 0.25) in fake_osc.sent


def test_routing_label_mute_has_no_submix_scope(make_bridge, fake_osc):
    """'AN 3 → RE-150 In (mute)' implied a per-submix scope that does not
    exist (#10) — mute labels name only the channel."""
    b = make_bridge({"m": {"steps": [{
        "target": {"channel": "AN 3", "param": "mute"},
        "value": "1.0",
    }]}})
    assert b.get_routing_label("m") == "AN 3 (mute)"


def test_global_fx_param_resolves_to_fixed_address(make_bridge, fake_osc):
    """FX-section params (#5 phase 1) have fixed global addresses — no
    submix, no channel, no feedback required, works on a cold bridge."""
    macro = {"steps": [{
        "target": {"param": "reverb_time"},
        "value": "{{param}}",
        "operation": {"type": "ramp", "duration": 0.2, "steps_per_sec": 20,
                      "range": [0.3, 0.7]},
    }]}
    b = make_bridge({"m": macro})
    b.run_macro("m", 1.0)  # no listener, no channel map needed
    addrs = {a for a, _ in fake_osc.sent}
    assert addrs == {"/3/reverbTime"}
    values = [v for _, v in fake_osc.sent]
    assert min(values) >= 0.3 - 1e-9 and max(values) <= 0.7 + 1e-9


def test_global_fx_routing_label(make_bridge, fake_osc):
    b = make_bridge({"m": {"steps": [{
        "target": {"param": "echo_feedback"}, "value": "0.4",
    }]}})
    assert b.get_routing_label("m") == "FX: Echo Feedback"


class MixedBankTotalMix(LiveFakeTotalMix):
    """Bank with mixed mono/stereo strips — the hw-channel offset must be
    computed from widths, not strip counts (#5 phase 2)."""

    PAGE2_BY_OFFSET = {0: "AN 1/2", 1: "AN 1/2", 2: "Mavis", 3: "Mavis",
                       4: "ADAT 5/6", 5: "ADAT 5/6"}

    def on_bus(self, address):
        if address == "/1/busInput":
            self.listener._handle("/1/labelSubmix", "Main")
            self.listener._handle("/1/trackname1", "AN 1/2")    # pair -> width 2
            self.listener._handle("/1/trackname2", "Mavis")     # mono -> width 1
            self.listener._handle("/1/trackname3", "ADAT 5/6")  # pair


# #24: the measured input table for the MixedBank fake — mirrors its
# PAGE2_BY_OFFSET exactly (the table IS what a sweep of that device yields)
MIXED_BANK_INPUTS = {
    "0": ["AN 1/2"], "1": ["AN 1/2"],
    "2": ["Mavis"], "3": ["Mavis"],
    "4": ["ADAT 5/6"], "5": ["ADAT 5/6"],
}


def _eq_bridge(make_bridge, fake_osc, macro, inputs=MIXED_BANK_INPUTS):
    from osc_listener import OSCListener
    listener = OSCListener(0)
    b = make_bridge({"m": macro})
    b.channel_map = cmap(extra_inputs=inputs)
    b.osc_listener = listener
    b.osc_client = MixedBankTotalMix(listener, fake_osc)
    listener._server = object()
    # Warm the bank (device dumps on bus select during resolution otherwise)
    b.osc_client.send_message("/1/busInput", 1.0)
    fake_osc.clear()
    return b


def test_eq_target_aims_page2_from_table_and_restores(make_bridge, fake_osc):
    """'Mavis' starts at hw 2 (measured table — labels never enter it).
    Aim, write /2/..., restore bank 0."""
    b = _eq_bridge(make_bridge, fake_osc, {"steps": [{
        "target": {"channel": "Mavis", "param": "eq_gain_1"},
        "value": "0.8",
    }]})
    b.run_macro("m", 0.0)
    sent = fake_osc.sent
    assert ("/2/eqGain1", 0.8) in sent
    aim_i = sent.index(("/setBankStart", 2.0))
    write_i = sent.index(("/2/eqGain1", 0.8))
    restore_i = len(sent) - 1 - sent[::-1].index(("/setBankStart", 0.0))
    assert aim_i < write_i < restore_i
    assert "/setSubmix" not in fake_osc.addresses()


def test_eq_offset_from_table_not_labels(make_bridge, fake_osc):
    """'ADAT 5/6' starts at hw 4 (measured). The old label heuristic said
    3 and wrote the wrong channel on hardware — labels never enter the
    table, so the class of bug is structurally gone."""
    b = _eq_bridge(make_bridge, fake_osc, {"steps": [{
        "target": {"channel": "ADAT 5/6", "param": "eq_freq_2"},
        "value": "0.4",
    }]})
    b.run_macro("m", 0.0)
    assert ("/setBankStart", 4.0) in fake_osc.sent
    assert ("/2/eqFreq2", 0.4) in fake_osc.sent


def test_eq_unknown_name_refuses_only_itself(make_bridge, fake_osc):
    """#24 kills the cumulative-poisoning property: an unmeasured name
    refuses ITSELF, while every measured strip keeps aiming."""
    b = _eq_bridge(make_bridge, fake_osc, {"steps": [
        {"target": {"channel": "Ghost", "param": "eq_gain_1"}, "value": "0.8"},
        {"target": {"channel": "Mavis", "param": "eq_gain_1"}, "value": "0.7"},
    ]})
    b.run_macro("m", 0.0)
    assert ("/setBankStart", 2.0) in fake_osc.sent   # Mavis still aims
    assert ("/2/eqGain1", 0.7) in fake_osc.sent
    assert ("/2/eqGain1", 0.8) not in fake_osc.sent  # Ghost refused


def test_eq_refuses_without_listener(make_bridge, fake_osc):
    """An unaimed page-2 write hits whatever channel the bank shows —
    refuse outright, never fall back to a stored address."""
    b = make_bridge({"m": {"steps": [{
        "osc": "/2/eqGain1",
        "target": {"channel": "Mavis", "param": "eq_gain_1"},
        "value": "0.8",
    }]}})
    b.run_macro("m", 0.0)
    assert "/2/eqGain1" not in fake_osc.addresses()


def test_eq_routing_label(make_bridge, fake_osc):
    b = make_bridge({"m": {"steps": [{
        "target": {"channel": "Mavis", "param": "eq_gain_1"}, "value": "0.8",
    }]}})
    assert b.get_routing_label("m") == "Mavis (Eq Gain 1)"


def test_raw_setsubmix_known_map_index_still_sends(make_bridge, fake_osc):
    """A raw /setSubmix that is exactly a MEASURED output channel passes —
    the user's legacy macros keep working (#24: table membership is the
    guard)."""
    listener = OSCListener(0)
    b = make_bridge({"m": {"steps": [
        {"osc": "/setSubmix", "value": "14"},
        {"osc": "/1/volume3", "value": "{{param}}"},
    ]}})
    b.channel_map = cmap()
    b.osc_listener = listener
    b.osc_client = LiveFakeTotalMix(listener, fake_osc)
    listener._server = object()
    b.run_macro("m", 0.8)
    assert ("/setSubmix", 14.0) in fake_osc.sent
    assert ("/1/volume3", 0.8) in fake_osc.sent


def test_raw_setsubmix_measured_pair_half_now_allowed(make_bridge, fake_osc):
    """#24 behavior change: hw 15 is a MEASURED channel (the pair's second
    half) — measured-safe, so the send is allowed. The old exact-index
    refusal existed because widths were assumptions; they are measurements
    now."""
    listener = OSCListener(0)
    b = make_bridge({"m": {"steps": [
        {"osc": "/setSubmix", "value": "15"},
        {"osc": "/1/volume3", "value": "{{param}}"},
    ]}})
    b.channel_map = cmap()
    b.osc_listener = listener
    b.osc_client = LiveFakeTotalMix(listener, fake_osc)
    listener._server = object()
    b.run_macro("m", 0.8)
    assert ("/setSubmix", 15.0) in fake_osc.sent


def test_raw_setsubmix_out_of_range_aborts_macro(make_bridge, fake_osc):
    """Index 99 is not a known submix index — hardware-proven to CRASH
    TotalMix when out of range. Refuse it AND stop the macro: later raw
    steps assume the switch happened."""
    listener = OSCListener(0)
    b = make_bridge({"m": {"steps": [
        {"osc": "/setSubmix", "value": "99"},
        {"osc": "/1/volume3", "value": "{{param}}"},
    ]}})
    b.channel_map = cmap()
    b.osc_listener = listener
    b.osc_client = LiveFakeTotalMix(listener, fake_osc)
    listener._server = object()
    b.run_macro("m", 0.8)
    assert "/setSubmix" not in {a for a, _ in fake_osc.sent if a == "/setSubmix"} or \
           ("/setSubmix", 99.0) not in fake_osc.sent
    assert "/1/volume3" not in fake_osc.addresses()  # macro aborted, not skipped-past
    skipped = [e["event"] for e in b.events
               if e["event"] and e["event"]["type"] == "macro_skipped"]
    assert skipped and skipped[0]["reason"] == "setsubmix_unverifiable"


def test_raw_setsubmix_no_table_aborts_macro(make_bridge, fake_osc):
    """No measured outputs table = no bound on the raw index — refuse and
    abort (the pre-#24 guard needed live enumeration; the new one needs
    only the measurement, so even a blind listener sends safely when the
    table exists — covered by the known-index test above)."""
    b = make_bridge({"m": {"steps": [
        {"osc": "/setSubmix", "value": "14"},
        {"osc": "/1/volume3", "value": "{{param}}"},
    ]}})
    b.channel_map = {}  # no physical table
    b.run_macro("m", 0.8)
    assert "/setSubmix" not in fake_osc.addresses()
    assert "/1/volume3" not in fake_osc.addresses()
    skipped = [e["event"] for e in b.events
               if e["event"] and e["event"]["type"] == "macro_skipped"]
    assert skipped and skipped[0]["reason"] == "setsubmix_unverifiable"


def test_eq_refused_on_playback_row(make_bridge, fake_osc):
    """EQ/channel-detail exists on hardware inputs and outputs, NOT on the
    software playback row (user-reported) — an aimed page-2 write from a
    playback target would land on a real channel's EQ. Refuse."""
    listener = OSCListener(0)
    b = make_bridge({"m": {"steps": [{
        "osc": "/2/eqGain1", "value": "0.6",
        "target": {"channel": "AN 1/2", "param": "eq_gain_1", "row": 2},
    }]}})
    b.channel_map = cmap()
    b.osc_listener = listener
    b.osc_client = LiveFakeTotalMix(listener, fake_osc)
    listener._server = object()
    b.run_macro("m", 0.5)
    assert "/2/eqGain1" not in fake_osc.addresses()
    assert "/setBankStart" not in fake_osc.addresses()


# Measured output table for the StereoOutputs fake — mirrors its page-2
# self-identification exactly (what a sweep of that device would yield)
STEREO_OUT_TABLE = {
    "0": ["Main"], "1": ["Main"], "2": ["AN 3/4"], "3": ["AN 3/4"],
    "4": ["AES"], "5": ["AES"], "6": ["RE-150 In"], "7": ["RE-150 In"],
}


class StereoOutputsTotalMix(LiveFakeTotalMix):
    OUTPUT_NAMES = ["Main", "AN 3/4", "AES", "RE-150 In"]
    PAGE2_BY_OFFSET = {0: "Main", 1: "Main", 2: "AN 3/4", 3: "AN 3/4",
                       4: "AES", 5: "AES", 6: "RE-150 In", 7: "RE-150 In"}


def _out_eq_bridge(make_bridge, fake_osc, channel, outputs=None):
    listener = OSCListener(0)
    b = make_bridge({"m": {"steps": [{
        "osc": "/2/eqGain1", "value": "0.6",
        "target": {"channel": channel, "param": "eq_gain_1", "row": 3},
    }]}})
    b.channel_map = {"physical_table": {
        "schema_version": 1, "channels_per_row": 30,
        "rows": {"outputs": dict(outputs or STEREO_OUT_TABLE), "inputs": {}},
        "last_sweep": {}, "source": {"outputs": "sweep"},
    }}
    b.osc_listener = listener
    b.osc_client = StereoOutputsTotalMix(listener, fake_osc)
    listener._server = object()
    return b


def test_eq_output_aims_at_table_start(make_bridge, fake_osc):
    """Output page-2 aiming reads the measured start directly: RE-150 In
    at hw 6 in this fixture (14 on the real device)."""
    b = _out_eq_bridge(make_bridge, fake_osc, "RE-150 In")
    b.run_macro("m", 0.5)
    assert ("/1/busOutput", 1.0) in fake_osc.sent
    assert ("/setBankStart", 6.0) in fake_osc.sent
    assert ("/2/eqGain1", 0.6) in fake_osc.sent
    assert ("/1/busInput", 1.0) in fake_osc.sent[-3:]  # row restored


def test_eq_output_first_output_starts_at_zero(make_bridge, fake_osc):
    """Main is measured at hw 0-1 — resolve_start returns the lower
    member. (The legacy walk's index-1 clamp hack is dead: the table
    stores reality.)"""
    b = _out_eq_bridge(make_bridge, fake_osc, "Main")
    b.run_macro("m", 0.5)
    assert ("/setBankStart", 0.0) in fake_osc.sent
    assert ("/2/eqGain1", 0.6) in fake_osc.sent


def test_eq_output_mono_channel_aims_at_own_start(make_bridge, fake_osc):
    """Mono outputs occupy one measured offset — Solo B at hw 5 aims at
    5, with stereo neighbors uninvolved (was: walked-index arithmetic)."""
    mono_table = {"0": ["Main"], "1": ["Main"], "2": ["AN 3/4"],
                  "3": ["AN 3/4"], "4": ["Solo A"], "5": ["Solo B"]}

    class MonoOutputsTotalMix(LiveFakeTotalMix):
        OUTPUT_NAMES = ["Main", "AN 3/4", "Solo A", "Solo B"]
        PAGE2_BY_OFFSET = {0: "Main", 1: "Main", 2: "AN 3/4", 3: "AN 3/4",
                           4: "Solo A", 5: "Solo B"}

    listener = OSCListener(0)
    b = make_bridge({"m": {"steps": [{
        "osc": "/2/eqGain1", "value": "0.6",
        "target": {"channel": "Solo B", "param": "eq_gain_1", "row": 3},
    }]}})
    b.channel_map = {"physical_table": {
        "schema_version": 1, "channels_per_row": 30,
        "rows": {"outputs": mono_table, "inputs": {}},
        "last_sweep": {}, "source": {"outputs": "sweep"},
    }}
    b.osc_listener = listener
    b.osc_client = MonoOutputsTotalMix(listener, fake_osc)
    listener._server = object()
    b.run_macro("m", 0.5)
    assert ("/1/busOutput", 1.0) in fake_osc.sent
    assert ("/setBankStart", 5.0) in fake_osc.sent
    assert ("/2/eqGain1", 0.6) in fake_osc.sent


def test_dynamics_params_share_eq_aiming(make_bridge, fake_osc):
    """#20 first tranche: dynamics/auto-level/gain/phase are page-2
    channel-detail params — same table aiming, refusal and bank
    restore as EQ, just different addresses."""
    b = _eq_bridge(make_bridge, fake_osc, {"steps": [{
        "osc": "/2/compexpGain", "value": "0.7",
        "target": {"channel": "ADAT 5/6", "param": "dyn_gain"},
    }]})
    b.run_macro("m", 0.5)
    assert ("/setBankStart", 4.0) in fake_osc.sent
    assert ("/2/compexpGain", 0.7) in fake_osc.sent
    # and the whole class is registered
    from bridge import TotalMixOSCBridge
    for p in ("dyn_gain", "alev_enable", "alev_headroom", "alev_maxgain",
              "input_gain", "input_gain_r", "phase", "phase_r"):
        assert p in TotalMixOSCBridge.CHANNEL_DETAIL_PARAMS


class ConfirmingTotalMix(LiveFakeTotalMix):
    """Answers the page-2 row no-op with a /2/trackname dump — the #20
    aim-confirmation mechanism. PAGE2_NAME configures what page 2 shows."""

    PAGE2_NAME = None  # override

    def send_message(self, address, value):
        if address in ("/2/busInput", "/2/busPlayback", "/2/busOutput"):
            self.fake_osc.send_message(address, value)
            self.listener._handle("/2/trackname", self.PAGE2_NAME)
            return
        super().send_message(address, value)


def test_page2_aim_confirmed_write_proceeds(make_bridge, fake_osc):
    """/2/trackname matches the intended channel — the write fires."""
    class RightAim(ConfirmingTotalMix):
        PAGE2_NAME = "ADAT 5/6"
    b = _eq_bridge(make_bridge, fake_osc, {"steps": [{
        "osc": "/2/eqGain1", "value": "0.6",
        "target": {"channel": "ADAT 5/6", "param": "eq_gain_1"},
    }]})
    b.osc_client = RightAim(b.osc_listener, fake_osc)
    b.osc_client.send_message("/1/busInput", 1.0)
    fake_osc.clear()
    b.run_macro("m", 0.5)
    assert ("/2/eqGain1", 0.6) in fake_osc.sent


def test_page2_aim_mismatch_refuses_write(make_bridge, fake_osc):
    """Page 2 reports a DIFFERENT channel than intended — the aim landed
    wrong (every wrong-channel write this project produced would have
    been caught here). The write must not fire."""
    class WrongAim(ConfirmingTotalMix):
        PAGE2_NAME = "Mavis"
    b = _eq_bridge(make_bridge, fake_osc, {"steps": [{
        "osc": "/2/eqGain1", "value": "0.6",
        "target": {"channel": "ADAT 5/6", "param": "eq_gain_1"},
    }]})
    b.osc_client = WrongAim(b.osc_listener, fake_osc)
    b.osc_client.send_message("/1/busInput", 1.0)
    fake_osc.clear()
    b.run_macro("m", 0.5)
    assert "/2/eqGain1" not in fake_osc.addresses()


def test_page2_aim_silence_refuses_write(make_bridge, fake_osc):
    """The row-mirror dump is hardware-verified reliable and idempotent —
    so NO confirmation now means something is genuinely wrong, and the
    write refuses (was: proceed, while the primitive was unverified)."""
    class SilentPage2(MixedBankTotalMix):
        PAGE2_BY_OFFSET = None  # device never answers the mirror

    b = _eq_bridge(make_bridge, fake_osc, {"steps": [{
        "osc": "/2/eqGain1", "value": "0.6",
        "target": {"channel": "ADAT 5/6", "param": "eq_gain_1"},
    }]})
    b.osc_client = SilentPage2(b.osc_listener, fake_osc)
    b.osc_client.send_message("/1/busInput", 1.0)
    fake_osc.clear()
    b.run_macro("m", 0.5)
    assert "/2/eqGain1" not in fake_osc.addresses()


def test_tranche2_dynamics_params_registered():
    from bridge import TotalMixOSCBridge
    for p in ("dyn_enable", "comp_thresh", "comp_ratio", "exp_thresh",
              "exp_ratio", "dyn_attack", "dyn_release", "alev_risetime",
              "lowcut_enable", "lowcut_grade"):
        assert p in TotalMixOSCBridge.CHANNEL_DETAIL_PARAMS


class MomentaryFxTotalMix:
    """Hardware-accurate FX buttons: /3/reverbEnable and /3/echoEnable
    TOGGLE on 1.0 and IGNORE 0.0 (measured on the UFX II). The page-3
    no-op (/3/faderGroups/1/1) dumps page 3."""

    def __init__(self, listener, fake_osc, initial=0.0):
        self.listener = listener
        self.fake_osc = fake_osc
        self.state = {"/3/reverbEnable": initial, "/3/echoEnable": initial}

    def send_message(self, address, value):
        self.fake_osc.send_message(address, value)
        if address == "/3/faderGroups/1/1":
            for a, v in self.state.items():
                self.listener._handle(a, v)
            return
        if address in self.state and float(value) == 1.0:
            self.state[address] = 1.0 - self.state[address]


def _button_bridge(make_bridge, fake_osc, macro, initial=0.0):
    listener = OSCListener(0)
    b = make_bridge({"m": macro})
    b.osc_listener = listener
    b.osc_client = MomentaryFxTotalMix(listener, fake_osc, initial)
    listener._server = object()
    return b


def test_fx_enable_set_presses_only_when_state_differs(make_bridge, fake_osc):
    """'On' on an Off device = exactly one press; 'On' again = NO press.
    The old value write toggled on every fire — two 'On' macros turned
    the effect off (user-reported: 'effects don't work anymore')."""
    b = _button_bridge(make_bridge, fake_osc, {"steps": [{
        "osc": "/3/reverbEnable", "target": {"param": "reverb_enable"},
        "value": "1.0"}]}, initial=0.0)
    b.run_macro("m", 0.5)
    assert b.osc_client.state["/3/reverbEnable"] == 1.0
    presses = [v for a, v in fake_osc.sent if a == "/3/reverbEnable"]
    assert presses == [1.0]
    b.run_macro("m", 0.5)  # already On — must NOT toggle it back off
    assert b.osc_client.state["/3/reverbEnable"] == 1.0
    presses = [v for a, v in fake_osc.sent if a == "/3/reverbEnable"]
    assert presses == [1.0]  # still exactly one press total


def test_fx_enable_off_presses_when_on(make_bridge, fake_osc):
    b = _button_bridge(make_bridge, fake_osc, {"steps": [{
        "osc": "/3/echoEnable", "target": {"param": "echo_enable"},
        "value": "0.0"}]}, initial=1.0)
    b.run_macro("m", 0.5)
    assert b.osc_client.state["/3/echoEnable"] == 0.0
    assert [v for a, v in fake_osc.sent if a == "/3/echoEnable"] == [1.0]


def test_fx_enable_lfo_presses_on_edges_and_returns(make_bridge, fake_osc):
    """A threshold LFO on a momentary button must press only on 0/1 EDGES
    (the raw shaped stream would toggle chaotically) and end where the
    wave ends — the floor, i.e. back at Off."""
    b = _button_bridge(make_bridge, fake_osc, {"steps": [{
        "osc": "/3/reverbEnable", "target": {"param": "reverb_enable"},
        "value": "{{param}}",
        "operation": {"type": "lfo", "bars": 1, "bpm": 960,
                      "steps_per_sec": 200, "threshold": 0.5}}]},
        initial=0.0)
    b.run_macro("m", 0.5)
    presses = [v for a, v in fake_osc.sent if a == "/3/reverbEnable"]
    assert presses and all(v == 1.0 for v in presses)
    assert len(presses) % 2 == 0            # each cycle: on-press + off-press
    assert b.osc_client.state["/3/reverbEnable"] == 0.0  # back where it began


def test_fx_enable_refuses_without_state(make_bridge, fake_osc):
    """No listener = the button state is unknowable, and a blind press is
    a coin flip — refuse rather than toggle randomly."""
    b = make_bridge({"m": {"steps": [{
        "osc": "/3/reverbEnable", "target": {"param": "reverb_enable"},
        "value": "1.0"}]}})
    b.run_macro("m", 0.5)
    assert "/3/reverbEnable" not in [a for a, _ in fake_osc.sent]


def test_snapshot_switching_macro_is_a_nonevent(make_bridge, fake_osc, monkeypatch):
    """#24: a macro that switches snapshot needs NO map work — the physical
    table is layout-invariant, so its steps aim immediately with zero
    enumeration, zero library traffic, zero walking. (This scenario looped
    90-second walks in the width-map era.)"""
    listener = OSCListener(0)
    b = make_bridge({"m": {
        "workspace": "Pill_setup", "snapshot": "Live",
        "steps": [{"osc": "/2/eqGain1", "value": "0.6",
                   "target": {"channel": "RE-150 In", "param": "eq_gain_1",
                              "row": 3}}],
    }})
    b.channel_map = {"physical_table": {
        "schema_version": 1, "channels_per_row": 30,
        "rows": {"outputs": dict(STEREO_OUT_TABLE), "inputs": {}},
        "last_sweep": {}, "source": {"outputs": "sweep"},
    }}
    b.osc_listener = listener
    b.osc_client = StereoOutputsTotalMix(listener, fake_osc)
    listener._server = object()
    monkeypatch.setattr(b, "_persist_channel_map_file", lambda cm: None)
    monkeypatch.setattr(b, "_wait_device", lambda *a, **k: True)
    b.run_macro("m", 0.5)
    # the step aimed and fired straight off the table
    assert ("/setBankStart", 6.0) in fake_osc.sent
    assert ("/2/eqGain1", 0.6) in fake_osc.sent
    # and the switch provoked no discovery traffic of any kind
    assert "/setSubmix" not in fake_osc.addresses()


class MomentaryEqMixedBank(MixedBankTotalMix):
    """Hardware-accurate /2/ enable: /2/eqEnable toggles on 1.0, ignores
    0.0 (measured 6/6 on the UFX II); the page-2 row mirror dumps the
    window's trackname AND the enable state."""

    def __init__(self, listener, fake_osc, initial=1.0):
        super().__init__(listener, fake_osc)
        self.eq_state = initial

    def send_message(self, address, value):
        if address in ("/2/busInput", "/2/busPlayback", "/2/busOutput"):
            super().send_message(address, value)   # emits /2/trackname
            self.listener._handle("/2/eqEnable", self.eq_state)
            return
        if address == "/2/eqEnable":
            self.fake_osc.send_message(address, value)
            if float(value) == 1.0:
                self.eq_state = 1.0 - self.eq_state
            return
        super().send_message(address, value)


def _eq_enable_bridge(make_bridge, fake_osc, value, initial):
    b = _eq_bridge(make_bridge, fake_osc, {"steps": [{
        "osc": "/2/eqEnable", "value": value,
        "target": {"channel": "ADAT 5/6", "param": "eq_enable"},
    }]})
    b.osc_client = MomentaryEqMixedBank(b.osc_listener, fake_osc, initial)
    b.osc_client.send_message("/1/busInput", 1.0)
    fake_osc.clear()
    return b


def test_page2_enable_off_presses_when_on(make_bridge, fake_osc):
    """#21 closed: /2/ enables are momentary. 'Off' on an On channel =
    exactly one 1.0 press, read via the ROW MIRROR (the page-3 no-op
    never refreshes a /2/ address — the trap the server flagged)."""
    b = _eq_enable_bridge(make_bridge, fake_osc, "0.0", initial=1.0)
    b.run_macro("m", 0.5)
    assert b.osc_client.eq_state == 0.0
    assert [v for a, v in fake_osc.sent if a == "/2/eqEnable"] == [1.0]
    # the read used the page-2 mirror, not the page-3 no-op
    assert "/3/faderGroups/1/1" not in fake_osc.addresses()
    assert "/2/busInput" in fake_osc.addresses()


def test_page2_enable_idempotent_when_already_there(make_bridge, fake_osc):
    """'On' when already On = NO press (the old value write would have
    toggled it off — the user-visible 'effects don't work' class)."""
    b = _eq_enable_bridge(make_bridge, fake_osc, "1.0", initial=1.0)
    b.run_macro("m", 0.5)
    assert b.osc_client.eq_state == 1.0
    assert [v for a, v in fake_osc.sent if a == "/2/eqEnable"] == []


