"""KNOB macros: continuous MIDI control (operation type 'knob').

A knob tick is a direct write through the Global transport - no macro
machinery per tick - and 'hold' re-asserts the value after switches so a
snapshot recall can't yank the knob back (snapshot-agnostic).
"""
import json

import pytest

fastapi_testclient = pytest.importorskip("fastapi.testclient")

import bridge as bridge_module  # noqa: E402
import global_units as gu  # noqa: E402
import physical_table as pt  # noqa: E402
from global_listener import GlobalOSCListener  # noqa: E402
from global_transport import GlobalTransport  # noqa: E402
from web.web_client import app, _strip_runtime  # noqa: E402

client = fastapi_testclient.TestClient(app)


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
    pt.merge_observation(t, "outputs", 0, "Main")
    pt.merge_observation(t, "outputs", 4, "Phones")
    return t


KNOB = {"steps": [{
    "target": {"channel": "Main", "row": 3, "param": "lowcut_freq"},
    "value": "{{param}}",
    "operation": {"type": "knob", "hold": True, "range": [0.0, 1.0]},
}], "midi_triggers": [{"type": "control_change", "number": 20,
                       "channel": 1, "use_value_as_param": True}]}


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
        return b, gclient, listener
    return _make


def test_knob_set_writes_through_global(rig):
    b, g, _ = rig({"locut": KNOB})
    r = b.knob_set("locut", 0.5)
    assert r["status"] == "resolved"
    # section state unknown -> auto-enable switches low cut ON first, then
    # the value: lowcut freq is a log taper 20..500 Hz: 0.5 -> 100 Hz
    assert g.sent == [("/output/0/lowcut/enable", 1.0),
                      ("/output/0/lowcut/freq", pytest.approx(100.0))]
    assert b.knob_values["locut"] == 0.5
    assert b.macro_health["locut"]["status"] == "ok"


def test_knob_range_maps_before_units(rig):
    b, g, _ = rig({"locut": {**KNOB, "steps": [{
        **KNOB["steps"][0],
        "operation": {"type": "knob", "hold": True, "range": [0.0, 0.5]}}]}})
    b.knob_set("locut", 1.0)           # full knob travel = top of the window
    assert g.sent[-1] == ("/output/0/lowcut/freq", pytest.approx(100.0))


def test_knob_refuses_under_classic(rig, monkeypatch):
    b, g, _ = rig({"locut": KNOB})
    monkeypatch.setattr(bridge_module, "OSC_TRANSPORT", "classic")
    r = b.knob_set("locut", 0.3)
    assert r["status"] == "knob_needs_global"
    assert g.sent == []
    assert b.macro_health["locut"]["status"] == "skipped"


def test_knob_stream_never_drops_final_value(rig):
    """#knob jump-back: rapid ticks inside the 10 Hz throttle window must
    still end with a broadcast carrying the FINAL value — a trailing-edge
    flush fires when the window closes, so no client is left showing a
    value the server has moved past."""
    import time as _time
    b, g, _ = rig({"locut": KNOB})
    b.knob_set("locut", 0.2)              # broadcasts immediately
    first = [e for e in b.events if (e["event"] or {}).get("type") == "knob_update"]
    assert first and first[-1]["event"]["value"] == 0.2
    b.knob_set("locut", 0.4)              # throttled -> trailing timer armed
    b.knob_set("locut", 0.9)              # still throttled; final value
    mid = [e for e in b.events if (e["event"] or {}).get("type") == "knob_update"]
    assert mid[-1]["event"]["value"] == 0.2       # nothing new broadcast yet
    assert b._knob_trailing_timers.get("locut") is not None
    deadline = _time.time() + 1.0
    while _time.time() < deadline:                # wait for the flush timer
        evs = [e for e in b.events if (e["event"] or {}).get("type") == "knob_update"]
        if evs[-1]["event"]["value"] == 0.9:
            break
        _time.sleep(0.01)
    evs = [e for e in b.events if (e["event"] or {}).get("type") == "knob_update"]
    assert evs[-1]["event"]["value"] == 0.9       # trailing edge carried it
    assert b._knob_trailing_timers.get("locut") is None


def test_knob_trailing_flush_cancelled_by_regular_broadcast(rig, monkeypatch):
    """A write that lands after the throttle window broadcasts normally and
    disarms the pending trailing flush (no duplicate events)."""
    b, g, _ = rig({"locut": KNOB})
    b.knob_set("locut", 0.2)
    b.knob_set("locut", 0.4)              # throttled -> trailing armed
    assert b._knob_trailing_timers.get("locut") is not None
    # next write is outside the window: force the clock forward
    b._knob_last_broadcast["locut"] -= b.KNOB_BROADCAST_INTERVAL_S + 0.01
    b.knob_set("locut", 0.7)              # regular broadcast, cancels trailing
    assert b._knob_trailing_timers.get("locut") is None
    evs = [e for e in b.events if (e["event"] or {}).get("type") == "knob_update"]
    assert evs[-1]["event"]["value"] == 0.7


def test_knob_trailing_flush_survives_unresolved_status(rig, monkeypatch):
    """The trailing flush runs in a timer thread: with no Global transport
    (status knob_needs_global) it must still broadcast the final value —
    device fields gated to None — instead of dying on a device read."""
    import time as _time
    b, g, _ = rig({"locut": KNOB})
    monkeypatch.setattr(bridge_module, "OSC_TRANSPORT", "classic")
    b.knob_set("locut", 0.2)              # broadcasts (status unresolved)
    before = len(b.events)
    b.knob_set("locut", 0.9)              # throttled -> trailing armed
    assert b._knob_trailing_timers.get("locut") is not None
    deadline = _time.time() + 1.0
    while _time.time() < deadline and len(b.events) == before:
        _time.sleep(0.01)                 # wait for the flush to fire
    assert len(b.events) > before         # it fired (did not die in the timer)
    ev = b.events[-1]["event"]
    assert ev["type"] == "knob_update"
    assert ev["status"] == "knob_needs_global"
    # unresolved: value is untracked (existing knob_set behavior) and every
    # device field is gated to its empty form
    assert ev["value"] is None
    assert ev["device_value"] is None and ev["enable_value"] is None
    assert ev["companions"] == {}
    assert b._knob_trailing_timers.get("locut") is None


def test_knob_set_rejects_non_knob(rig):
    b, g, _ = rig({"vol": {"steps": [{"osc": "/1/volume1", "value": 1.0}]}})
    assert b.knob_set("vol", 0.5)["status"] == "not_a_knob"
    assert b.knob_set("nope", 0.5)["status"] == "not_a_knob"


def test_fire_on_knob_macro_is_a_set(rig):
    b, g, _ = rig({"locut": KNOB})
    b.run_macro("locut", 0.25)          # FIRE button / MQTT / PC trigger path
    assert g.writes_to("/output/0/lowcut/freq")  # landed
    assert b.macro_health["locut"]["status"] == "ok"
    assert b.knob_values["locut"] == 0.25


def test_hold_reasserts_after_switch(rig, fake_osc):
    switcher = {"workspace": "Pill_setup", "snapshot": "Reset", "steps": []}
    b, g, _ = rig({"locut": KNOB, "go": switcher})
    b.knob_set("locut", 0.7)
    before = len(g.writes_to("/output/0/lowcut/freq"))
    b.run_macro("go", 0.5)              # snapshot switch (classic recall)
    assert ("/loadQuickWorkspace", 2.0) in fake_osc.sent
    writes = g.writes_to("/output/0/lowcut/freq")
    assert len(writes) == before + 1    # re-asserted once
    assert writes[-1] == pytest.approx(gu.GLOBAL_PARAM_MAP["lowcut_freq"].to_wire(0.7))


def test_no_hold_means_no_reassert(rig):
    knob = {**KNOB, "steps": [{**KNOB["steps"][0],
             "operation": {"type": "knob", "hold": False}}]}
    b, g, _ = rig({"locut": knob, "go": {"workspace": "Pill_setup",
                                         "snapshot": "Reset", "steps": []}})
    b.knob_set("locut", 0.7)
    before = len(g.writes_to("/output/0/lowcut/freq"))
    b.run_macro("go", 0.5)
    assert len(g.writes_to("/output/0/lowcut/freq")) == before


def test_knob_device_value_reads_feedback(rig):
    b, g, listener = rig({"locut": KNOB})
    listener.state.ingest("/output/0/lowcut/freq", (100.0,))
    step = b._knob_step(KNOB)
    assert b.knob_device_value(step) == pytest.approx(0.5, abs=1e-3)
    # volume knob: feedback is dB, normalized through the fader curve
    vol = {"steps": [{"target": {"channel": "Mic 1", "submix": "Phones"},
                      "value": "{{param}}", "operation": {"type": "knob"}}]}
    listener.state.ingest("/mix/in/0/4/fader", (-6.0,))
    assert b.knob_device_value(b._knob_step(vol)) == pytest.approx(gu.fader_lin(-6.0))


def test_runtime_fields_stripped_and_merged():
    m = {"steps": [], "knob_value": 0.4, "device_value": 0.3, "last_fire": {}}
    out = _strip_runtime(m)
    assert "knob_value" not in out and "device_value" not in out


# ── HTTP + WebSocket entry points on the module bridge ─────────────

@pytest.fixture
def module_knob(monkeypatch):
    b = bridge_module.bridge
    prev = (b.global_listener, b.global_transport, b.channel_map,
            dict(b.mappings.get("macros", {})))
    b.channel_map = {"physical_table": _table()}
    g = FakeGlobalClient()
    b.global_listener = GlobalOSCListener(0)
    b.global_transport = GlobalTransport(g, b.global_listener, b._physical_table)
    b.mappings.setdefault("macros", {})["zz_knob"] = json.loads(json.dumps(KNOB))
    monkeypatch.setattr(bridge_module, "OSC_TRANSPORT", "global")
    yield b, g
    b.global_listener, b.global_transport, b.channel_map, macros = prev
    b.mappings["macros"] = macros
    b.knob_values.pop("zz_knob", None)


def test_http_knob_endpoint(module_knob):
    b, g = module_knob
    r = client.post("/api/knob/zz_knob", json={"value": 0.5})
    assert r.status_code == 200 and r.json()["status"] == "resolved"
    assert g.sent[-1][0] == "/output/0/lowcut/freq"
    assert client.post("/api/knob/not_a_macro", json={"value": 0.5}).status_code == 404
    # /api/macros carries the live knob value for the card
    assert client.get("/api/macros").json()["zz_knob"]["knob_value"] == 0.5


def test_websocket_knob_stream(module_knob):
    b, g = module_knob
    with client.websocket_connect("/ws") as ws:
        ws.send_text(json.dumps({"type": "knob", "name": "zz_knob", "value": 0.25}))
        ws.send_text("not json")          # ignored, never kills the socket
        ws.send_text(json.dumps({"type": "knob", "name": "zz_knob", "value": 0.75}))
        # the handler runs in the executor - give it a beat
        import time
        for _ in range(50):
            if len(g.writes_to("/output/0/lowcut/freq")) >= 2:
                break
            time.sleep(0.02)
    writes = g.writes_to("/output/0/lowcut/freq")
    assert len(writes) == 2
    assert b.knob_values["zz_knob"] == 0.75


def test_settle_readback_redumps_channel(rig):
    import time
    b, g, _ = rig({"locut": KNOB})
    b.KNOB_READBACK_DELAY_S = 0.05
    b.knob_set("locut", 0.5)
    b.knob_set("locut", 0.6)            # second tick restarts the timer
    time.sleep(0.5)
    # exactly ONE readback for the burst, after the last tick
    assert g.writes_to("/sendchan/output/0") == [1.0]


def test_auto_enable_switches_section_on_first_move(rig):
    b, g, listener = rig({"locut": KNOB})
    listener.state.ingest("/output/0/lowcut/enable", (0.0,))   # section OFF
    b.knob_set("locut", 0.5)
    assert ("/output/0/lowcut/enable", 1.0) in g.sent          # switched on
    assert g.sent.index(("/output/0/lowcut/enable", 1.0)) < len(g.sent) - 1  # before the value
    b.knob_set("locut", 0.6)                                    # streaming: not re-sent
    assert g.writes_to("/output/0/lowcut/enable") == [1.0]


def test_auto_enable_skips_when_already_on_or_disabled(rig):
    b, g, listener = rig({"locut": KNOB})
    listener.state.ingest("/output/0/lowcut/enable", (1.0,))   # already ON
    b.knob_set("locut", 0.5)
    assert g.writes_to("/output/0/lowcut/enable") == []
    off = {**KNOB, "steps": [{**KNOB["steps"][0], "operation": {
        "type": "knob", "hold": True, "auto_enable": False}}]}
    b2, g2, l2 = rig({"locut": off})
    l2.state.ingest("/output/0/lowcut/enable", (0.0,))
    b2.knob_set("locut", 0.5)
    assert g2.writes_to("/output/0/lowcut/enable") == []


def test_knob_enable_toggle_and_state(rig):
    b, g, listener = rig({"locut": KNOB})
    assert b.knob_enable("locut", False)["status"] == "resolved"
    assert g.sent[-1] == ("/output/0/lowcut/enable", 0.0)
    listener.state.ingest("/output/0/lowcut/enable", (0.0,))
    assert b.knob_enable_state(b._knob_step(KNOB)) is False
    vol = {"steps": [{"target": {"channel": "Mic 1", "submix": "Phones"},
                      "value": "{{param}}", "operation": {"type": "knob"}}]}
    b.mappings["macros"]["vol"] = vol
    assert b.knob_enable("vol", True)["status"] == "no_enable_param"


def test_enable_http_endpoint(module_knob):
    b, g = module_knob
    r = client.post("/api/knob/zz_knob/enable", json={"on": True})
    assert r.status_code == 200 and r.json()["enable"] is True
    assert g.sent[-1] == ("/output/0/lowcut/enable", 1.0)
    assert "enable_value" in client.get("/api/macros").json()["zz_knob"]


def test_companion_state_and_write(rig):
    b, g, listener = rig({"locut": KNOB})
    step = b._knob_step(KNOB)
    assert b.knob_companions(step) == {"lowcut_grade": None}      # unknown yet
    listener.state.ingest("/output/0/lowcut/slope", (1.0,))         # 12 dB/oct
    assert b.knob_companions(step)["lowcut_grade"] == pytest.approx(1 / 3)
    r = b.knob_param_set("locut", "lowcut_grade", 2 / 3)            # -> 18 dB/oct
    assert r["status"] == "resolved"
    assert g.sent[-1] == ("/output/0/lowcut/slope", 2.0)            # enum index on the wire
    assert b.knob_param_set("locut", "nope", 0.5)["status"] == "unsupported_param"


def test_companion_http_endpoint(module_knob):
    b, g = module_knob
    r = client.post("/api/knob/zz_knob/param", json={"param": "lowcut_grade", "value": 1.0})
    assert r.status_code == 200 and g.sent[-1] == ("/output/0/lowcut/slope", 3.0)
    assert "companions" in client.get("/api/macros").json()["zz_knob"]


def test_pinned_companion_asserted_on_move(rig):
    hicut = {"steps": [{"target": {"channel": "Main", "row": 3, "param": "eq_freq_3"},
                        "value": "{{param}}",
                        "operation": {"type": "knob", "hold": True,
                                      "companions": {"eq_type_3": 2 / 3}}}]}
    b, g, listener = rig({"hicut": hicut})
    listener.state.ingest("/output/0/eq/band3type", (0.0,))      # Bell on the device
    listener.state.ingest("/output/0/eq/enable", (1.0,))         # EQ already on
    b.knob_set("hicut", 0.8)
    assert ("/output/0/eq/band3type", 2.0) in g.sent              # pinned to Low Pass (idx 2)
    assert g.sent.index(("/output/0/eq/band3type", 2.0)) < len(g.sent) - 1
    b.knob_set("hicut", 0.81)                                     # throttled: not re-sent
    assert g.writes_to("/output/0/eq/band3type") == [2.0]
    listener.state.ingest("/output/0/eq/band3type", (2.0,))      # device confirms
    b._knob_enable_sent.clear()
    b.knob_set("hicut", 0.82)
    assert g.writes_to("/output/0/eq/band3type") == [2.0]         # matches: nothing to assert


def test_off_at_min_toggles_section(rig):
    knob = {**KNOB, "steps": [{**KNOB["steps"][0], "operation": {
        "type": "knob", "hold": True, "off_at_min": True}}]}
    b, g, listener = rig({"locut": knob})
    listener.state.ingest("/output/0/lowcut/enable", (1.0,))   # section ON
    b.knob_set("locut", 0.0)                                    # bottom of travel
    assert ("/output/0/lowcut/enable", 0.0) in g.sent           # -> OFF
    b.knob_set("locut", 0.0)                                    # still at min: no repeat
    assert g.writes_to("/output/0/lowcut/enable") == [0.0]
    b.knob_set("locut", 0.3)                                    # back up -> ON immediately
    assert g.writes_to("/output/0/lowcut/enable") == [0.0, 1.0]
    b.knob_set("locut", 0.4)                                    # moving: no repeat
    assert g.writes_to("/output/0/lowcut/enable") == [0.0, 1.0]


def test_off_at_max_for_high_cut(rig):
    hicut = {"steps": [{"target": {"channel": "Main", "row": 3, "param": "eq_freq_3"},
                        "value": "{{param}}",
                        "operation": {"type": "knob", "hold": True, "off_at": "max"}}]}
    b, g, listener = rig({"hicut": hicut})
    listener.state.ingest("/output/0/eq/enable", (1.0,))
    b.knob_set("hicut", 1.0)                                     # top of travel = no cut
    assert g.writes_to("/output/0/eq/enable") == [0.0]
    b.knob_set("hicut", 0.7)                                     # back down -> ON
    assert g.writes_to("/output/0/eq/enable") == [0.0, 1.0]


def test_device_side_change_pushes_knob_update(rig):
    """Someone flips EQ off IN TOTALMIX -> the watcher pushes the truth."""
    b, g, listener = rig({"locut": KNOB})
    listener.state.ingest("/output/0/lowcut/enable", (1.0,))
    listener.state.ingest("/output/0/lowcut/freq", (100.0,))
    b._knob_watch_stop.set()                      # drive ticks by hand
    b.__class__._knob_watch_loop  # noqa - exists
    # first tick: primes the cache (initial state counts as a change)
    tick = lambda: [b._knob_last_pushed.update({n: b._knob_snapshot(b._knob_step(m))})
                    for n, m in b.mappings["macros"].items() if b._knob_step(m)]
    # use the real logic via one manual pass of the loop body
    def one_tick():
        for name, macro in list(b.mappings["macros"].items()):
            step = b._knob_step(macro)
            if step is None:
                continue
            snap = b._knob_snapshot(step)
            if snap == b._knob_last_pushed.get(name):
                continue
            b._knob_last_pushed[name] = snap
            b.broadcast_state(macro_event={"type": "knob_update", "name": name,
                                           "status": "resolved", "value": None,
                                           "device_value": snap[0], "enable_value": snap[1],
                                           "companions": dict(b.knob_companions(step)),
                                           "source": "device"})
    one_tick()
    n0 = len([e for e in b.events if e["event"] and e["event"]["type"] == "knob_update"])
    assert n0 == 1                                 # initial state pushed once
    one_tick()
    n1 = len([e for e in b.events if e["event"] and e["event"]["type"] == "knob_update"])
    assert n1 == n0                                # no change -> no push
    listener.state.ingest("/output/0/lowcut/enable", (0.0,))   # TotalMix side: EQ off
    one_tick()
    evs = [e["event"] for e in b.events if e["event"] and e["event"]["type"] == "knob_update"]
    assert len(evs) == n0 + 1
    assert evs[-1]["enable_value"] is False and evs[-1]["source"] == "device"
