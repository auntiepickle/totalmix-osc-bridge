"""bridge.run_sweep — the physical-table learning mechanism (#24).

The sweep replaces the discovery walk: /setBankStart 0..33 + row-mirror
nudge + /2/trackname read per offset. Read-only, never sends /setSubmix.
Fake device serves per-(row, offset) tracknames incl. the measured
saturation behavior past the hardware end."""
import pytest

from osc_listener import OSCListener


class SweepFakeTotalMix:
    """Row-aware page-2 trackname server. Keys: ('1'|'3', offset)."""

    def __init__(self, listener, fake_osc, inputs, outputs, channels=30):
        self.listener = listener
        self.fake_osc = fake_osc
        self._bank = 0
        self._row = "1"
        self.inputs, self.outputs, self.channels = inputs, outputs, channels

    def send_message(self, address, value):
        self.fake_osc.send_message(address, value)
        if address == "/setBankStart":
            self._bank = int(value)
        elif address == "/1/busInput":
            self._row = "1"
            self.listener._handle(address, 1.0)
        elif address == "/1/busOutput":
            self._row = "3"
            self.listener._handle(address, 1.0)
        elif address in ("/2/busInput", "/2/busOutput"):
            table = self.inputs if address == "/2/busInput" else self.outputs
            idx = min(self._bank, self.channels - 1)
            self.listener._handle("/2/trackname", table[idx])


IN_NAMES = (["AN 1/2", "AN 1/2", "RE-101", "RE-!50 Out"]
            + ["Mavis", "Mavis"] + [f"In {i}" for i in range(6, 30)])
OUT_NAMES = (["Main", "Main"] + [f"Out {i}" for i in range(2, 30)])


@pytest.fixture
def sweep_bridge(make_bridge, fake_osc):
    listener = OSCListener(0)
    b = make_bridge({})
    b.channel_map = {}
    b.osc_listener = listener
    listener._server = object()
    b.osc_client = SweepFakeTotalMix(listener, fake_osc, IN_NAMES, OUT_NAMES)
    return b, fake_osc


def test_sweep_builds_table_and_restores(sweep_bridge, monkeypatch):
    b, fake_osc = sweep_bridge
    persisted = []
    monkeypatch.setattr(b, "_persist_channel_map_file",
                        lambda cm: persisted.append(True))
    state = b.run_sweep(settle_s=0)
    assert state["status"] == "done"
    rows = b.channel_map["physical_table"]["rows"]
    assert rows["inputs"]["0"] == ["AN 1/2"]
    assert rows["inputs"]["2"] == ["RE-101"]
    assert rows["inputs"]["4"] == ["Mavis"] and rows["inputs"]["5"] == ["Mavis"]
    assert rows["outputs"]["0"] == ["Main"] and rows["outputs"]["1"] == ["Main"]
    # saturation offsets (30..33) are boundary checks only — never stored
    assert "30" not in rows["inputs"] and "33" not in rows["outputs"]
    # NEVER sends the one fatal op
    assert "/setSubmix" not in fake_osc.addresses()
    # bank and row restored
    assert fake_osc.sent[-2:] == [("/setBankStart", 0.0), ("/1/busInput", 1.0)]
    assert persisted


def test_sweep_aborts_on_new_name_past_hardware_end(sweep_bridge, monkeypatch):
    """A NEW name at offset 30 means channels_per_row is wrong for this
    device — abort without persisting anything."""
    b, fake_osc = sweep_bridge
    b.osc_client.channels = 31            # device "has" a 31st channel
    b.osc_client.inputs = IN_NAMES + ["SURPRISE 31"]
    b.osc_client.outputs = OUT_NAMES + ["SURPRISE 31"]
    persisted = []
    monkeypatch.setattr(b, "_persist_channel_map_file",
                        lambda cm: persisted.append(True))
    state = b.run_sweep(rows=("inputs",), settle_s=0)
    assert state["status"] == "error"
    assert "channels_per_row" in state["error"]
    assert not persisted
    # bank/row restored even on abort
    assert fake_osc.sent[-2:] == [("/setBankStart", 0.0), ("/1/busInput", 1.0)]


def test_sweep_prunes_legacy_only_when_both_rows_measured(sweep_bridge, monkeypatch):
    b, _ = sweep_bridge
    b.channel_map = {"width_maps": {"k": {}}, "layout_library": {"k": {}},
                     "snapshot_layouts": {"a|b": "k"}, "channel_widths": {}}
    monkeypatch.setattr(b, "_persist_channel_map_file", lambda cm: None)
    b.run_sweep(rows=("inputs",), settle_s=0)
    assert "width_maps" in b.channel_map          # outputs not measured yet
    state = b.run_sweep(rows=("outputs",), settle_s=0)
    assert state["status"] == "done"
    for legacy in ("width_maps", "channel_widths", "layout_library",
                   "snapshot_layouts"):
        assert legacy not in b.channel_map
    assert set(state["pruned_legacy"]) >= {"width_maps", "layout_library"}


def test_sweep_reset_clears_the_row_first(sweep_bridge, monkeypatch):
    b, _ = sweep_bridge
    monkeypatch.setattr(b, "_persist_channel_map_file", lambda cm: None)
    b.run_sweep(rows=("inputs",), settle_s=0)
    import physical_table as pt
    pt.merge_observation(b.channel_map["physical_table"], "inputs", 2, "OLD NAME")
    b.run_sweep(rows=("inputs",), settle_s=0, reset=True)
    assert b.channel_map["physical_table"]["rows"]["inputs"]["2"] == ["RE-101"]


def test_sweep_refuses_without_listener(make_bridge):
    b = make_bridge({})
    b.osc_listener = None
    state = b.run_sweep(settle_s=0)
    assert state["status"] == "error"


def test_migration_seeds_outputs_in_memory_only(make_bridge, monkeypatch):
    """Legacy walked indices ARE hw starts (first output index 1 -> hw 0).
    Migration is in-memory: nothing persists until the first sweep."""
    b = make_bridge({})
    persisted = []
    monkeypatch.setattr(b, "_persist_channel_map_file",
                        lambda cm: persisted.append(True))
    b.channel_map = {"submixes": {"Main": {"index": 1}, "AES": {"index": 4},
                                  "RE-150 In": {"index": 14}}}
    b._migrate_physical_table()
    t = b.channel_map["physical_table"]
    assert t["rows"]["outputs"]["0"] == ["Main"]
    assert t["rows"]["outputs"]["4"] == ["AES"]
    assert t["rows"]["outputs"]["14"] == ["RE-150 In"]
    assert t["source"]["outputs"] == "legacy_migration"
    assert "inputs" not in t["rows"] or not t["rows"]["inputs"]
    assert not persisted
