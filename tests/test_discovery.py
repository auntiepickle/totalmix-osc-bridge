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
        "row": 1, "name": "AN 3", "channel": 2, "osc_address": "/1/volume2",
        "description": "AN 3 send to ADAT 1",
    }
    # Playback sends: suffixed key, page-1 write address, raw name kept
    pb = adat["sends"]["SPDIF PB (playback)"]
    assert pb["row"] == 2
    assert pb["name"] == "SPDIF PB"
    assert pb["osc_address"] == "/1/volume1"
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


def test_walk_aborts_at_first_past_last_submix():
    """One consecutive duplicate = stereo pair half. A SECOND repeat means
    the walk went past the last submix — hardware-proven to crash TotalMix
    — so the walk must ABORT there, not keep sending (backstop for when
    the output row could not be enumerated)."""
    class SaturatingFake(FakeTotalMix):
        SUBMIXES = {
            1: ("Main", {}),
            2: ("ADAT 15/16", {}),  # pair: indices 2+3 share the label
            3: ("ADAT 15/16", {}),
        }

    listener = OSCListener(0)
    device = SaturatingFake(listener)
    _, walk_log = discover_channel_map(device, listener,
                                       submix_count=6, settle_s=0)
    by_index = {e["index"]: e for e in walk_log}
    assert by_index[3]["skipped"] == "duplicate label (stereo pair)"
    assert by_index[4]["skipped"] == "past last submix (label repeating)"
    assert "stop" in by_index[4]
    assert device.set_submix_calls == [1, 2, 3, 4]  # 5 and 6 never sent
    assert 5 not in by_index and 6 not in by_index


def test_walk_stops_at_output_count_before_any_fatal_index():
    """When the output row is enumerable, the walk knows how many submixes
    exist and stops the moment it has found them all — no index is ever
    sent past the last submix (the fatal one included)."""
    class OutputAwareFake(FakeTotalMix):
        def send_message(self, address, value):
            if address == "/1/busOutput":
                self.listener._handle(address, 1.0)
                for n, name in enumerate(["ADAT 1", "AES", "RE-150 In"], 1):
                    self.listener._handle(f"/1/trackname{n}", name)
                return
            if address in ("/1/busInput", "/1/busPlayback"):
                self.listener._handle(address, 1.0)
                return
            super().send_message(address, value)

    listener = OSCListener(0)
    device = OutputAwareFake(listener)
    channel_map, walk_log = discover_channel_map(device, listener,
                                                 submix_count=32, settle_s=0)
    # 3 real outputs: found at 1, 2 (AES pair dups at 3), skipped 4, last at 5
    assert set(channel_map["submixes"]) == {"ADAT 1", "AES", "RE-150 In"}
    assert device.set_submix_calls == [1, 2, 3, 4, 5]  # stopped at 5, not 32
    assert "stop" in walk_log[-1]




class PairAccurateFake(FakeTotalMix):
    """Device-accurate: a /setSubmix that selects the ALREADY-selected
    submix emits NOTHING (hardware-measured — every stereo pair's second
    index is a silent no-op). Bus-row toggles always produce feedback."""

    def __init__(self, listener):
        super().__init__(listener)
        self._current = None

    def send_message(self, address, value):
        if address in ("/1/busPlayback", "/1/busInput"):
            self.listener._handle(address, 1.0)  # guaranteed change/echo
            return
        if address != "/setSubmix":
            return
        self.set_submix_calls.append(int(value))
        index = min(int(value), max(self.SUBMIXES))
        name, rows = self.SUBMIXES[index]
        if name == self._current:
            return  # SILENT no-op — the regression's root cause
        self._current = name
        self.listener._handle("/1/labelSubmix", name)
        for row, channels in rows.items():
            for ch, trackname in channels.items():
                self.listener._handle(f"/{row}/trackname{ch}", trackname)
                self.listener._handle(f"/{row}/volume{ch}", 0.5)


def test_walk_survives_silent_pair_indices():
    """v0.1.0-alpha regression: the pair's silent second index aborted the
    walk at 2 of 16 submixes. Silence + alive row-toggle = no-op, classify
    as the stereo-pair duplicate and continue."""
    class Pairs(PairAccurateFake):
        SUBMIXES = {
            1: ("Main", {}),
            2: ("AN 3/4", {}),   # pair: indices 2+3, 3 is SILENT
            3: ("AN 3/4", {}),
            4: ("AN 5/6", {}),
            5: ("AN 5/6", {}),   # silent again
        }

    listener = OSCListener(0)
    device = Pairs(listener)
    channel_map, walk_log = discover_channel_map(device, listener,
                                                 submix_count=5, settle_s=0)
    assert set(channel_map["submixes"]) == {"Main", "AN 3/4", "AN 5/6"}
    by_index = {e["index"]: e for e in walk_log}
    assert by_index[3]["skipped"] == "duplicate label (stereo pair)"
    assert by_index[5]["skipped"] == "duplicate label (stereo pair)"


def test_walk_aborts_when_silent_and_dead():
    """Silence + a dead row toggle = the #17 injury scenario — abort."""
    class DiesAfterTwo(PairAccurateFake):
        SUBMIXES = {1: ("Main", {}), 2: ("AES", {}), 3: ("AES", {})}
        def send_message(self, address, value):
            if getattr(self, "_dead", False):
                if address == "/setSubmix":
                    self.set_submix_calls.append(int(value))
                return  # crashed: silent to EVERYTHING incl. toggles
            super().send_message(address, value)
            if address == "/setSubmix" and int(value) == 2:
                self._dead = True

    listener = OSCListener(0)
    device = DiesAfterTwo(listener)
    channel_map, walk_log = discover_channel_map(device, listener,
                                                 submix_count=6, settle_s=0)
    assert "stop" in walk_log[-1]
    assert "device dead or feedback lost" in walk_log[-1]["stop"]
    assert device.set_submix_calls == [1, 2, 3]  # aborted at the dead index
