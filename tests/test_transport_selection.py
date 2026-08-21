"""#25: run_macro routing between the classic and Global transports.
The Global transport gets its own fake client so tests can assert which
wire each write went out on."""
import pytest

import bridge as bridge_mod
import physical_table as pt
from global_listener import GlobalOSCListener
from global_transport import GlobalTransport


class FakeGlobalClient:
    def __init__(self):
        self.sent = []

    def send_message(self, address, value):
        self.sent.append((address, value))

    def addresses(self):
        return [a for a, _ in self.sent]


@pytest.fixture
def global_rig(make_bridge, monkeypatch):
    """Bridge with an attached (unstarted) Global transport, selected via
    OSC_TRANSPORT=global."""
    def _make(macros):
        b = make_bridge(macros)
        table = pt.empty_table()
        pt.merge_observation(table, "inputs", 0, "Mic 1")
        pt.merge_observation(table, "outputs", 4, "Phones")
        b.channel_map = {"physical_table": table}
        gclient = FakeGlobalClient()
        listener = GlobalOSCListener(0)  # never started — state fed directly
        b.global_transport = GlobalTransport(
            gclient, listener, b._physical_table)
        monkeypatch.setattr(bridge_mod, "OSC_TRANSPORT", "global")
        return b, gclient, listener
    return _make


TARGET_MACRO = {
    "steps": [{
        "target": {"channel": "Mic 1", "submix": "Phones", "param": "volume"},
        "value": "{{param}}",
    }],
}


def _skips(b):
    return [e["event"]["reason"] for e in b.events
            if e["event"] and e["event"]["type"] == "macro_skipped"]


def test_target_step_routes_to_global_wire(global_rig, fake_osc):
    b, gclient, _ = global_rig({"vol": TARGET_MACRO})
    b.run_macro("vol", 0.7)
    assert ("/mix/in/0/4/faderlin", 0.7) in gclient.sent
    # nothing classic: no aiming, no restores, no writes
    assert fake_osc.sent == []


def test_classic_default_ignores_attached_transport(global_rig, monkeypatch,
                                                    fake_osc):
    b, gclient, _ = global_rig({"vol": TARGET_MACRO})
    monkeypatch.setattr(bridge_mod, "OSC_TRANSPORT", "classic")
    assert b._global_active() is False
    b.run_macro("vol", 0.7)
    # classic resolution has no feedback in tests → step skipped, but the
    # decisive assertion is that the GLOBAL wire stayed silent
    assert gclient.sent == []


def test_global_refusal_emits_skip_event(global_rig, fake_osc):
    b, gclient, _ = global_rig({"bad": {"steps": [{
        "target": {"channel": "Ghost", "submix": "Phones", "param": "volume"},
        "value": "{{param}}"}]}})
    b.run_macro("bad", 0.5)
    assert _skips(b) == ["target_not_in_table"]
    assert gclient.sent == [] and fake_osc.sent == []


def test_uncalibrated_param_refused_end_to_end(global_rig, fake_osc):
    b, gclient, _ = global_rig({"eq": {"steps": [{
        "target": {"channel": "Mic 1", "param": "input_gain"},
        "value": "{{param}}"}]}})
    b.run_macro("eq", 0.5)
    assert _skips(b) == ["target_uncalibrated_param"]
    assert gclient.sent == []


def test_raw_osc_step_stays_on_classic_wire(global_rig, fake_osc):
    b, gclient, _ = global_rig({"raw": {"steps": [
        {"osc": "/1/mastermute", "value": 1.0}]}})
    b.run_macro("raw", 0.5)
    assert ("/1/mastermute", 1.0) in fake_osc.sent
    assert gclient.sent == []


def test_ramp_operation_runs_through_global_writer(global_rig, fake_osc):
    b, gclient, _ = global_rig({"ramp": {"steps": [{
        "target": {"channel": "Mic 1", "submix": "Phones", "param": "volume"},
        "value": "{{param}}",
        "operation": {"type": "ramp", "bars": 1, "bpm": 600,
                      "steps_per_beat": 4, "curve": "linear"}}]}})
    b.run_macro("ramp", 1.0)
    addrs = set(gclient.addresses())
    assert addrs == {"/mix/in/0/4/faderlin"}
    assert len(gclient.sent) > 3          # a stream, not a single set
    assert fake_osc.sent == []


def test_snapshot_switch_uses_global_recall(global_rig, fake_osc):
    b, gclient, listener = global_rig({"go": {
        "workspace": "Pill_setup", "snapshot": "Reset",
        "steps": []}})
    # device will confirm slot 1 active
    listener.state.ingest("/snapshot/load/1", (2.0,))
    b.run_macro("go", 0.5)
    # workspace switching has no Global equivalent — stays classic
    assert ("/loadQuickWorkspace", 2.0) in fake_osc.sent
    # snapshot recall is Global: 1-based, no 9-N inversion
    assert ("/snapshot/load/1", 1.0) in gclient.sent
    assert not [a for a in fake_osc.addresses()
                if a.startswith("/3/snapshots")]
    assert b.state_confirmed is True
    assert b.current_snapshot == "reset"
