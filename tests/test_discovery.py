"""Closed-loop discovery test: a fake TotalMix answers /setSubmix by injecting
the feedback a real device would send, and the walker builds the map from it."""
from discovery import discover_channel_map
from osc_listener import OSCListener


class FakeTotalMix:
    """Stands in for the real device: receiving /setSubmix i makes it 'send'
    the new bank's feedback straight into the listener. Mirrors real UFX II
    behavior: stereo-linked outputs occupy two consecutive indices that
    report the same label."""

    SUBMIXES = {
        1: ("ADAT 1", {"1": {2: "AN 3"}, "2": {1: "SPDIF PB"}}),
        2: ("AES", {"1": {1: "AN 1/2"}, "2": {}}),      # stereo pair 2/3
        3: ("AES", {"1": {1: "AN 1/2"}, "2": {}}),
        4: ("<Empty>", {}),
        5: ("RE-150 In", {"1": {1: "AN 1/2"}, "2": {}}),  # new label after dupes
    }

    def __init__(self, listener):
        self.listener = listener
        self.set_submix_calls = []

    def send_message(self, address, value):
        if address != "/setSubmix":
            return
        self.set_submix_calls.append(int(value))
        index = min(int(value), max(self.SUBMIXES))
        name, rows = self.SUBMIXES[index]
        self.listener._handle("/1/labelSubmix", name)
        for row, channels in rows.items():
            for ch, trackname in channels.items():
                self.listener._handle(f"/{row}/trackname{ch}", trackname)
                self.listener._handle(f"/{row}/volume{ch}", 0.5)


def run_discovery(submix_count=5):
    listener = OSCListener(0)  # never started — handler invoked directly
    device = FakeTotalMix(listener)
    result = discover_channel_map(device, listener,
                                  submix_count=submix_count, settle_s=0)
    return result + (device,)


def test_discovers_named_submixes_and_sends():
    channel_map, _, _ = run_discovery()
    subs = channel_map["submixes"]
    assert set(subs) == {"ADAT 1", "AES", "RE-150 In"}

    adat = subs["ADAT 1"]
    assert adat["index"] == 1
    assert adat["sends"]["AN 3"] == {
        "row": 1, "channel": 2, "osc_address": "/1/volume2",
        "description": "AN 3 send to ADAT 1",
    }
    assert adat["sends"]["SPDIF PB"]["osc_address"] == "/2/volume1"
    assert subs["AES"]["sends"]["AN 1/2"]["osc_address"] == "/1/volume1"


def test_stereo_pair_duplicates_skipped_but_walk_continues():
    channel_map, walk_log, _ = run_discovery()
    by_index = {e["index"]: e for e in walk_log}
    assert by_index[3]["skipped"] == "duplicate label (stereo pair)"
    assert by_index[4]["skipped"] == "empty or no feedback"
    # New label AFTER a duplicate run — proves the walk does not stop early
    assert "skipped" not in by_index[5]
    assert channel_map["submixes"]["RE-150 In"]["index"] == 5


def test_prewalk_submix_restored():
    listener = OSCListener(0)
    device = FakeTotalMix(listener)
    device.send_message("/setSubmix", 2.0)  # user was on AES before the walk
    device.set_submix_calls.clear()
    discover_channel_map(device, listener, submix_count=5, settle_s=0)
    # Walk 1..5, then restore to AES's first index (2)
    assert device.set_submix_calls == [1, 2, 3, 4, 5, 2]


def test_schema_matches_existing_channel_map_consumers():
    """get_routing_label() must work against a discovered map unchanged."""
    channel_map, _, _ = run_discovery()
    from bridge import TotalMixOSCBridge

    b = TotalMixOSCBridge.__new__(TotalMixOSCBridge)
    b.channel_map = channel_map
    b.mappings = {"macros": {"m": {"steps": [{"osc": "/1/volume2"}]}}}
    assert b.get_routing_label("m") == "AN 3 → ADAT 1"


def test_na_strips_beyond_hardware_filtered_out():
    """A 48-wide OSC bank reports 'n.a.' for strips past the device's
    channel count — they must not become sends."""
    listener = OSCListener(0)
    device = FakeTotalMix(listener)
    listener._handle("/1/labelSubmix", "ADAT 1")
    listener._handle("/1/trackname1", "AN 3")
    listener._handle("/1/volume1", 0.5)
    for ch in range(24, 27):
        listener._handle(f"/1/trackname{ch}", "n.a.")
        listener._handle(f"/1/volume{ch}", 0.0)
    channel_map, _ = discover_channel_map(device, listener,
                                          submix_count=1, settle_s=0)
    sends = channel_map["submixes"]["ADAT 1"]["sends"]
    assert "AN 3" in sends
    assert not [k for k in sends if "n.a." in k.lower()]
