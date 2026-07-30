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
