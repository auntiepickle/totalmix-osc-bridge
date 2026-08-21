"""#8 channel identify: activity log (wiggle) + pulse endpoint.

Global-first by design (user directive 2026-08-21): classic mode is legacy —
pulse refuses under classic instead of emulating it there.
"""
import time

import pytest

fastapi_testclient = pytest.importorskip("fastapi.testclient")

import bridge as bridge_module  # noqa: E402
import global_units as gu  # noqa: E402
from global_listener import GlobalOSCListener  # noqa: E402
from global_transport import GlobalTransport  # noqa: E402
from web.web_client import app  # noqa: E402

client = fastapi_testclient.TestClient(app)


# ── listener change log ────────────────────────────────────────────

def make_listener():
    return GlobalOSCListener(0)   # never started — state fed directly


def test_first_sight_is_not_activity():
    rx = make_listener()
    rx.state.ingest("/input/3/mute", (0.0,))          # bootstrap dump
    assert rx.state.recent_changes(0) == []


def test_unchanged_redump_is_not_activity():
    rx = make_listener()
    rx.state.ingest("/input/3/mute", (0.0,))
    rx.state.ingest("/input/3/mute", (0.0,))          # dump re-report
    assert rx.state.recent_changes(0) == []


def test_value_change_is_activity_and_aggregates():
    rx = make_listener()
    rx.state.ingest("/mix/in/8/0/fader", (-20.0,))    # first sight
    for db in (-19.0, -18.0, -16.5):                  # the wiggle
        rx.state.ingest("/mix/in/8/0/fader", (db,))
    rx.state.ingest("/output/4/fader", (-10.0,))      # first sight elsewhere
    changes = rx.state.recent_changes(0)
    assert len(changes) == 1
    top = changes[0]
    assert (top["row_key"], top["hw"], top["count"]) == ("inputs", 8, 3)
    assert top["last_value"] == -16.5
    assert top["last_param"] == "fader"


def test_playback_mix_activity_attributed_to_playbacks():
    rx = make_listener()
    rx.state.ingest("/mix/pb/26/0/balpan", (0.0,))
    rx.state.ingest("/mix/pb/26/0/balpan", (0.4,))
    changes = rx.state.recent_changes(0)
    assert changes[0]["row_key"] == "playbacks" and changes[0]["hw"] == 26


def test_since_filter():
    rx = make_listener()
    rx.state.ingest("/input/2/gain", (0.0,))
    rx.state.ingest("/input/2/gain", (6.0,))
    cutoff = time.time()
    assert rx.state.recent_changes(cutoff) == []
    rx.state.ingest("/input/2/gain", (9.0,))
    assert rx.state.recent_changes(cutoff)[0]["count"] == 1


# ── /api/device/activity ───────────────────────────────────────────

@pytest.fixture
def wired_listener():
    b = bridge_module.bridge
    prev = b.global_listener
    b.global_listener = make_listener()
    yield b.global_listener
    b.global_listener = prev


def test_activity_endpoint_503_without_listener():
    b = bridge_module.bridge
    prev = b.global_listener
    b.global_listener = None
    try:
        assert client.get("/api/device/activity").status_code == 503
    finally:
        b.global_listener = prev


def test_activity_endpoint_names_channels(wired_listener):
    st = wired_listener.state
    st.ingest("/input/8/name", ("Pill Out",))
    st.ingest("/mix/in/8/0/fader", (-20.0,))
    st.ingest("/mix/in/8/0/fader", (-18.0,))
    r = client.get("/api/device/activity?since=0")
    assert r.status_code == 200
    body = r.json()
    assert body["channels"][0]["name"] == "Pill Out"
    assert body["channels"][0]["hw"] == 8
    assert "now" in body


def test_activity_endpoint_pair_right_member_uses_left_name(wired_listener):
    st = wired_listener.state
    st.ingest("/input/28/name", ("ADAT 15/16",))
    st.ingest("/input/28/stereo", (1.0,))
    st.ingest("/input/29/phase", (0.0,))
    st.ingest("/input/29/phase", (1.0,))
    r = client.get("/api/device/activity?since=0")
    assert r.json()["channels"][0]["name"] == "ADAT 15/16"


# ── /api/device/pulse ──────────────────────────────────────────────

class FakeGlobalClient:
    def __init__(self):
        self.sent = []

    def send_message(self, address, value):
        self.sent.append((address, value))


@pytest.fixture
def global_bridge(monkeypatch):
    import physical_table as pt
    b = bridge_module.bridge
    table = pt.empty_table()
    pt.merge_observation(table, "inputs", 0, "Mic 1")
    pt.merge_observation(table, "outputs", 4, "Phones")
    prev = (b.global_listener, b.global_transport, b.channel_map)
    b.global_listener = make_listener()
    b.channel_map = {"physical_table": table}
    gclient = FakeGlobalClient()
    b.global_transport = GlobalTransport(gclient, b.global_listener,
                                         b._physical_table)
    monkeypatch.setattr(bridge_module, "OSC_TRANSPORT", "global")
    yield b, gclient
    b.global_listener, b.global_transport, b.channel_map = prev


def test_pulse_refused_under_classic(global_bridge, monkeypatch):
    monkeypatch.setattr(bridge_module, "OSC_TRANSPORT", "classic")
    r = client.post("/api/device/pulse",
                    json={"channel": "Mic 1", "submix": "Phones"})
    assert r.status_code == 409


def test_pulse_blips_and_restores_exactly(global_bridge):
    b, gclient = global_bridge
    b.global_listener.state.ingest("/mix/in/0/4/fader", (-20.0,))
    r = client.post("/api/device/pulse",
                    json={"channel": "Mic 1", "submix": "Phones"})
    assert r.status_code == 200
    assert r.json()["restored_db"] == -20.0
    writes = [v for a, v in gclient.sent if a == "/mix/in/0/4/faderlin"]
    cur = gu.fader_lin(-20.0)
    up = gu.fader_lin(-14.0)   # current + 6 dB
    assert writes == [pytest.approx(x) for x in (up, cur, up, cur)]
    assert writes[-1] == pytest.approx(cur)   # ends at the exact prior level


def test_pulse_from_off_send_uses_floor(global_bridge):
    b, gclient = global_bridge
    b.global_listener.state.ingest("/mix/in/0/4/fader", (-300.0,))  # off
    r = client.post("/api/device/pulse",
                    json={"channel": "Mic 1", "submix": "Phones"})
    assert r.status_code == 200
    writes = [v for a, v in gclient.sent if a == "/mix/in/0/4/faderlin"]
    assert writes[0] == pytest.approx(gu.fader_lin(-30.0))  # audible floor
    assert writes[-1] == pytest.approx(0.0)                 # back to off


def test_pulse_refuses_when_level_unknowable(global_bridge, monkeypatch):
    b, gclient = global_bridge
    # no state, and the provoked re-dump yields nothing
    monkeypatch.setattr(b.global_listener, "wait_for",
                        lambda pred, timeout: False)
    r = client.post("/api/device/pulse",
                    json={"channel": "Mic 1", "submix": "Phones"})
    assert r.status_code == 422
    # only the read-provoke went out — no blind level writes
    assert [a for a, _ in gclient.sent] == ["/sendmix"]


def test_pulse_unresolved_target_422(global_bridge):
    r = client.post("/api/device/pulse",
                    json={"channel": "Ghost", "submix": "Phones"})
    assert r.status_code == 422
