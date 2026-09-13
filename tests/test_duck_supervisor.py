"""DuckSupervisor against a rigged bridge: key lookup, per-macro isolation,
and the knob_set -> seed hand-off (audit run 5)."""
import pytest

fastapi_testclient = pytest.importorskip("fastapi.testclient")

import tmosc.bridge as bridge_module  # noqa: E402
import tmosc.global_units as gu  # noqa: E402
import tmosc.physical_table as pt  # noqa: E402
from tmosc.duck_engine import DuckSupervisor  # noqa: E402
from tmosc.global_listener import GlobalOSCListener  # noqa: E402
from tmosc.global_transport import GlobalTransport  # noqa: E402


class FakeGlobalClient:
    def __init__(self):
        self.sent = []

    def send_message(self, address, value):
        self.sent.append((address, value))

    def writes_to(self, address):
        return [v for a, v in self.sent if a == address]


def _table():
    t = pt.empty_table()
    pt.merge_observation(t, "inputs", 0, "Mic 1")
    pt.merge_observation(t, "playbacks", 0, "PB 1")
    pt.merge_observation(t, "outputs", 0, "Main")
    pt.merge_observation(t, "outputs", 4, "Phones")
    return t


def _duck_knob(key_row, key_channel, **duck_extra):
    return {"steps": [{
        "target": {"channel": "Mic 1", "submix": "Phones"},
        "value": "{{param}}",
        "operation": {"type": "knob", "duck": {
            "enabled": True, "threshold": -30.0, "depth": 12.0,
            "attack": 20.0, "release": 250.0,
            "key": {"row": key_row, "channel": key_channel}, **duck_extra}},
    }]}


@pytest.fixture
def rig(make_bridge, monkeypatch):
    def _make(macros):
        b = make_bridge(macros)
        b.channel_map = {"physical_table": _table()}
        gclient = FakeGlobalClient()
        listener = GlobalOSCListener(0)
        b.global_listener = listener
        b.global_transport = GlobalTransport(gclient, listener, b._physical_table)
        monkeypatch.setattr(bridge_module, "OSC_TRANSPORT", "global")
        b.duck = DuckSupervisor(b)
        return b, gclient, listener
    return _make


def test_key_db_resolves_playback_row(rig):
    """Names/stereo are filed under 'playbacks', meters under 'playback';
    one shared row key made a playback-row duck key never resolve."""
    b, _, listener = rig({"pad": _duck_knob(2, "PB 1")})
    listener.state.ingest("/level/pb/0", (-5.0,))
    listener.state.ingest("/level/in/0", (-7.0,))
    assert b.duck._key_db({"key": {"row": 2, "channel": "PB 1"}}) == pytest.approx(-5.0)
    assert b.duck._key_db({"key": {"row": 1, "channel": "Mic 1"}}) == pytest.approx(-7.0)


def test_bad_duck_config_does_not_starve_the_others(rig):
    b, g, listener = rig({
        "a_bad": _duck_knob(1, "Mic 1", threshold="abc", key=None),
        "b_good": _duck_knob(1, "Mic 1"),
    })
    listener.state.ingest("/mix/in/0/4/fader", (-20.0,))
    listener.state.ingest("/level/in/0", (-5.0,))            # key hot
    for _ in range(10):
        b.duck._tick(0.04)
    assert "b_good" in b.duck.status
    assert b.duck.status["b_good"]["gr"] > 0.0
    assert g.writes_to("/mix/in/0/4/faderlin")            # reduction written


def test_knob_set_seeds_duck_base(rig):
    b, g, listener = rig({"echo": _duck_knob(1, "Mic 1")})
    listener.state.ingest("/mix/in/0/4/fader", (-20.0,))
    listener.state.ingest("/level/in/0", (-5.0,))            # key hot
    for _ in range(80):
        b.duck._tick(0.04)
    assert abs(b.duck.rt["echo"]["written"] - (-32.0)) < 0.3
    # performer turns the send up via MIDI while the key is hot
    b.knob_set("echo", 0.5)
    new_db = gu.fader_db(g.sent[-1][1])
    assert b.duck.rt["echo"]["base"] == pytest.approx(new_db)
    listener.state.ingest("/level/in/0", (-100.0,))          # key silent
    for _ in range(120):
        b.duck._tick(0.04)
    # release lands on the NEW level (listener cache still says -20)
    assert abs(b.duck.rt["echo"]["written"] - new_db) < 0.3


def test_mix_pan_knob_has_no_device_value_from_fader(rig):
    """A mix pan knob was answered with the send's FADER through the fader
    curve (wrong param, wrong units) - unknown beats wrong."""
    pan = {"steps": [{"target": {"channel": "Mic 1", "submix": "Phones", "param": "pan"},
                      "value": "{{param}}", "operation": {"type": "knob"}}]}
    b, _, listener = rig({"pan": pan})
    listener.state.ingest("/mix/in/0/4/fader", (-6.0,))
    assert b.knob_device_value(b._knob_step(pan)) is None
