"""Closed-loop discovery test: a fake TotalMix answers /setSubmix by injecting
the feedback a real device would send, and the walker builds the map from it."""
from discovery import discover_channel_map
from osc_listener import OSCListener


class FakeTotalMix:
    """Stands in for the real device: receiving /setSubmix i makes it 'send'
    the new bank's feedback straight into the listener."""

    SUBMIXES = {
        1: ("ADAT 1", {"1": {2: "AN 3"}, "2": {1: "SPDIF PB"}}),
        2: ("AES", {"1": {1: "AN 1/2"}, "2": {}}),
        3: ("<Empty>", {}),
        # Indices past the last submix: TotalMix clamps and re-reports the
        # last valid one
    }
    LAST_VALID = 2

    def __init__(self, listener):
        self.listener = listener

    def send_message(self, address, value):
        if address != "/setSubmix":
            return
        index = min(int(value), max(self.SUBMIXES))
        if index > self.LAST_VALID and index not in self.SUBMIXES:
            index = self.LAST_VALID
        name, rows = self.SUBMIXES.get(index, self.SUBMIXES[self.LAST_VALID])
        self.listener._handle("/1/labelSubmix", name)
        for row, channels in rows.items():
            for ch, trackname in channels.items():
                self.listener._handle(f"/{row}/trackname{ch}", trackname)
                self.listener._handle(f"/{row}/volume{ch}", 0.5)


def run_discovery(submix_count=5):
    listener = OSCListener(0)  # never started — handler invoked directly
    device = FakeTotalMix(listener)
    return discover_channel_map(device, listener,
                                submix_count=submix_count, settle_s=0)


def test_discovers_named_submixes_and_sends():
    channel_map, _ = run_discovery()
    subs = channel_map["submixes"]
    assert set(subs) == {"ADAT 1", "AES"}

    adat = subs["ADAT 1"]
    assert adat["index"] == 1
    assert adat["sends"]["AN 3"] == {
        "row": 1, "channel": 2, "osc_address": "/1/volume2",
        "description": "AN 3 send to ADAT 1",
    }
    assert adat["sends"]["SPDIF PB"]["osc_address"] == "/2/volume1"
    assert subs["AES"]["sends"]["AN 1/2"]["osc_address"] == "/1/volume1"


def test_empty_and_duplicate_submixes_skipped():
    channel_map, walk_log = run_discovery()
    assert len(walk_log) == 5
    by_index = {e["index"]: e for e in walk_log}
    assert "skipped" in by_index[3]          # <Empty>
    assert "skipped" in by_index[4]          # clamped duplicate of AES
    assert "skipped" in by_index[5]
    assert "skipped" not in by_index[1]
    assert "skipped" not in by_index[2]


def test_schema_matches_existing_channel_map_consumers():
    """get_routing_label() must work against a discovered map unchanged."""
    channel_map, _ = run_discovery()
    from bridge import TotalMixOSCBridge

    b = TotalMixOSCBridge.__new__(TotalMixOSCBridge)
    b.channel_map = channel_map
    b.mappings = {"macros": {"m": {"steps": [{"osc": "/1/volume2"}]}}}
    assert b.get_routing_label("m") == "AN 3 → ADAT 1"
