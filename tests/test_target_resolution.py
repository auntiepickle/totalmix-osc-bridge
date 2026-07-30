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


class LiveFakeTotalMix:
    """Answers /setSubmix by pushing feedback where AN 1/2 are now LINKED —
    one strip — so AN 3 lives at strip 2, not the captured strip 3."""

    def __init__(self, listener, fake_osc):
        self.listener = listener
        self.fake_osc = fake_osc

    def send_message(self, address, value):
        self.fake_osc.send_message(address, value)
        if address == "/setSubmix" and int(value) == 14:
            self.listener._handle("/1/labelSubmix", "RE-150 In")
            self.listener._handle("/1/trackname1", "AN 1/2")  # linked pair
            self.listener._handle("/1/trackname2", "AN 3")    # shifted!
            self.listener._handle("/1/trackname3", "RE-101")


def make_target_bridge(make_bridge, fake_osc, listener=None):
    b = make_bridge({"m": TARGET_MACRO})
    b.channel_map = CHANNEL_MAP
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


def test_no_listener_falls_back_to_stored_address(make_bridge, fake_osc):
    b = make_target_bridge(make_bridge, fake_osc, listener=None)
    b.run_macro("m", 0.8)
    assert ("/setSubmix", 14.0) in fake_osc.sent
    assert ("/1/volume3", 0.8) in fake_osc.sent  # stale but best available


def test_no_feedback_times_out_to_stored_address(make_bridge, fake_osc):
    listener = OSCListener(0)          # never receives anything
    listener._server = object()
    b = make_bridge({"m": TARGET_MACRO})
    b.channel_map = CHANNEL_MAP
    b.osc_listener = listener
    b.run_macro("m", 0.8)              # osc_client is the plain fake — no feedback

    # Patch the timeout small for test speed? run_macro used default 1.5s —
    # acceptable, but verify the fallback happened:
    assert ("/1/volume3", 0.8) in fake_osc.sent


def test_unknown_submix_skips_step_without_crash(make_bridge, fake_osc):
    b = make_bridge({"m": {"steps": [{
        "target": {"submix": "Nonexistent", "channel": "AN 3"},
        "value": "{{param}}",
    }]}})
    b.channel_map = CHANNEL_MAP
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
    b.channel_map = CHANNEL_MAP
    b.osc_listener = listener
    b.osc_client = LiveFakeTotalMix(listener, fake_osc)
    listener._server = object()
    b.run_macro("m", 0.8)

    ramp_addrs = {a for a, _ in fake_osc.sent if a.startswith("/1/volume")}
    assert ramp_addrs == {"/1/volume2"}  # whole ramp on the live strip


class SlowBurstTotalMix(LiveFakeTotalMix):
    """Delivers the label immediately but the high strip's trackname late —
    models a 48-fader bank dump still in flight when resolution starts."""

    def send_message(self, address, value):
        self.fake_osc.send_message(address, value)
        if address == "/setSubmix" and int(value) == 14:
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
    b.channel_map = CHANNEL_MAP
    b.osc_listener = listener
    b.osc_client = SlowBurstTotalMix(listener, fake_osc)
    listener._server = object()
    b.run_macro("m", 0.8)
    assert ("/1/volume23", 0.8) in fake_osc.sent


class RepairedBankTotalMix(LiveFakeTotalMix):
    """Models a snapshot having re-paired strips: 'AN 2' no longer exists,
    the linked strip 'AN 1/2' covers it (hardware-observed on snapshot
    recall)."""

    def send_message(self, address, value):
        self.fake_osc.send_message(address, value)
        if address == "/setSubmix" and int(value) == 14:
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
    b.channel_map = CHANNEL_MAP
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
    b.channel_map = CHANNEL_MAP
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

    def send_message(self, address, value):
        self.fake_osc.send_message(address, value)
        if address in ("/1/busInput", "/1/busPlayback"):
            self.listener._handle(address, value)
        if address == "/setSubmix" and int(value) == 14:
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
    b.channel_map = CHANNEL_MAP
    b.osc_listener = listener
    b.osc_client = PlaybackRowTotalMix(listener, fake_osc)
    listener._server = object()
    b.run_macro("m", 0.6)

    addrs = fake_osc.addresses()
    # Row selected before the submix, write on page 1, input row restored
    assert ("/1/busPlayback", 1.0) in fake_osc.sent
    assert addrs.index("/1/busPlayback") < addrs.index("/setSubmix")
    assert ("/1/volume3", 0.6) in fake_osc.sent
    assert addrs.index("/1/volume3") < addrs.index("/1/busInput")
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
