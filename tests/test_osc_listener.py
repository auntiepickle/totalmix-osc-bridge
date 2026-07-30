import time

import pytest
from pythonosc.udp_client import SimpleUDPClient

from osc_listener import DeviceState, OSCListener, UNKNOWN_SUBMIX


def feed(state, *messages):
    for address, *args in messages:
        state.ingest(address, args)


def test_label_submix_sets_current():
    s = DeviceState()
    feed(s, ("/1/labelSubmix", "AES"))
    assert s.current_submix == "AES"
    assert "AES" in s.submixes


def test_channel_feedback_scoped_to_current_submix():
    s = DeviceState()
    feed(
        s,
        ("/1/labelSubmix", "AES"),
        ("/1/trackname1", "AN 1/2"),
        ("/1/volume1", 0.72),
        ("/1/volume1Val", "-6.0 dB"),
        ("/1/pan1", 0.5),
        ("/2/trackname3", "ADAT 13/14"),
        ("/2/volume3", 0.5),
    )
    d = s.to_dict()
    ch = d["submixes"]["AES"]["1"][1]
    assert ch == {"name": "AN 1/2", "volume": 0.72,
                  "volume_db": "-6.0 dB", "pan": 0.5}
    assert d["submixes"]["AES"]["2"][3]["name"] == "ADAT 13/14"


def test_submix_switch_starts_new_scope():
    s = DeviceState()
    feed(s, ("/1/labelSubmix", "AES"), ("/1/volume1", 0.7),
         ("/1/labelSubmix", "ADAT 1"), ("/1/volume1", 0.2))
    assert s.submixes["AES"]["1"][1]["volume"] == 0.7
    assert s.submixes["ADAT 1"]["1"][1]["volume"] == 0.2


def test_row3_outputs_not_submix_scoped():
    s = DeviceState()
    feed(s, ("/1/labelSubmix", "AES"), ("/3/volume2", 0.9))
    assert s.submixes["_outputs"]["3"][2]["volume"] == 0.9
    assert "3" not in s.submixes["AES"]


def test_feedback_before_any_submix_goes_to_unselected():
    s = DeviceState()
    feed(s, ("/1/volume4", 0.3))
    assert s.submixes[UNKNOWN_SUBMIX]["1"][4]["volume"] == 0.3


def test_bus_toggle_rescopes_page1_channel_data():
    """Page-1 messages describe whichever ROW the bus* toggles selected —
    seen on the UFX II: /1/busInput 1.0 precedes the input-row bank dump."""
    s = DeviceState()
    feed(
        s,
        ("/1/labelSubmix", "AES"),
        ("/1/busInput", 1.0),
        ("/1/volume1", 0.7),        # input row -> row 1
        ("/1/busPlayback", 1.0),
        ("/1/volume1", 0.3),        # same address, now playback -> row 2
        ("/1/busOutput", 1.0),
        ("/1/volume2", 0.9),        # output row -> _outputs
    )
    assert s.submixes["AES"]["1"][1]["volume"] == 0.7
    assert s.submixes["AES"]["2"][1]["volume"] == 0.3
    assert s.submixes["_outputs"]["3"][2]["volume"] == 0.9


def test_bus_toggle_off_is_ignored():
    s = DeviceState()
    feed(s, ("/1/busPlayback", 1.0), ("/1/busInput", 0.0))  # 0.0 = deselect echo
    assert s.current_row == "2"


def test_unknown_addresses_kept_in_raw_store():
    s = DeviceState()
    feed(s, ("/1/mastermute", 1.0), ("/1/mastermute", 0.0))
    assert s.raw["/1/mastermute"]["count"] == 2
    assert s.raw["/1/mastermute"]["args"] == [0.0]


def test_udp_round_trip():
    """Real python-osc server + client over localhost UDP."""
    listener = OSCListener(0)  # ephemeral port
    assert listener.start() is True
    try:
        client = SimpleUDPClient("127.0.0.1", listener.port)
        client.send_message("/1/labelSubmix", "Live Mix")
        client.send_message("/1/volume1", 0.42)
        deadline = time.time() + 3
        while listener.state.current_submix != "Live Mix" and time.time() < deadline:
            time.sleep(0.02)
        deadline = time.time() + 3
        while (listener.state.submixes.get("Live Mix", {}).get("1", {}).get(1) is None
               and time.time() < deadline):
            time.sleep(0.02)
        assert listener.state.current_submix == "Live Mix"
        assert listener.state.submixes["Live Mix"]["1"][1]["volume"] == pytest.approx(0.42)
    finally:
        listener.stop()


def test_broadcast_throttling():
    fired = []
    listener = OSCListener(0, broadcast_cb=lambda: fired.append(time.time()))
    # Call the handler directly — no UDP needed for throttle logic
    listener._handle("/1/volume1", 0.1)   # first: fires (throttle window empty)
    listener._handle("/1/volume1", 0.2)   # within window: suppressed
    listener._handle("/1/volume1", 0.3)   # within window: suppressed
    assert len(fired) == 1
    listener._handle("/1/labelSubmix", "AES")  # structural: always fires
    assert len(fired) == 2


def test_heartbeat_ignored():
    listener = OSCListener(0)
    listener._handle("/", 0.0)
    assert listener.state.raw == {}


def test_wait_for_wakes_on_matching_message():
    """Event-driven wait: the waiter must wake when the message lands, not
    on a polling interval."""
    listener = OSCListener(0)
    result = {}

    def waiter():
        t0 = time.time()
        ok = listener.wait_for(
            lambda st: st.submixes.get("AES", {}).get("1", {}).get(5, {}).get("name") == "Mavis",
            timeout=5.0)
        result["ok"] = ok
        result["elapsed"] = time.time() - t0

    import threading as _t
    th = _t.Thread(target=waiter)
    th.start()
    time.sleep(0.1)  # let the waiter block
    listener._handle("/1/labelSubmix", "AES")
    listener._handle("/1/trackname5", "Mavis")
    th.join(timeout=2)
    assert result["ok"] is True
    # Woke promptly on arrival — nowhere near the 5s timeout
    assert result["elapsed"] < 1.0


def test_wait_for_immediate_when_already_true():
    listener = OSCListener(0)
    listener._handle("/1/labelSubmix", "AES")
    assert listener.wait_for(lambda st: st.current_submix == "AES", 0.0) is True


def test_wait_for_times_out_cleanly():
    listener = OSCListener(0)
    t0 = time.time()
    assert listener.wait_for(lambda st: False, 0.2) is False
    assert 0.15 < time.time() - t0 < 1.0
    assert listener._waiters == []  # waiter deregistered


def test_bank_width_grows_and_shrinks_at_burst_boundaries():
    """Width grows immediately, shrinks only when a full narrower burst
    completes — detects a workspace load reverting faders-per-bank to 8."""
    s = DeviceState()
    s.ingest("/1/labelSubmix", ("Main",))
    for ch in range(1, 24):
        s.ingest(f"/1/trackname{ch}", (f"CH {ch}",))
    assert s.bank_width == 23
    # Narrow burst: boundary, 8 strips, next boundary seals it
    s.ingest("/1/labelSubmix", ("AES",))
    assert s.bank_width == 23  # previous burst was 23 wide
    for ch in range(1, 9):
        s.ingest(f"/1/trackname{ch}", (f"CH {ch}",))
    s.ingest("/1/labelSubmix", ("Main",))
    assert s.bank_width == 8


def test_real_strip_count_excludes_na_placeholders():
    """real_strip_count feeds the stale-map warning: 48-wide bank with 23
    real channels must report 23, not 48."""
    s = DeviceState()
    s.ingest("/1/labelSubmix", ("Main",))
    for ch in range(1, 24):
        s.ingest(f"/1/trackname{ch}", (f"CH {ch}",))
    for ch in range(24, 49):
        s.ingest(f"/1/trackname{ch}", ("n.a.",))
    assert s.real_strip_count == 23
    s.ingest("/1/labelSubmix", ("AES",))  # seal the burst
    assert s.real_strip_count == 23
    assert s.bank_width == 48


def _burst(state, label, count):
    state.ingest("/1/labelSubmix", (label,))
    for ch in range(1, count + 1):
        state.ingest(f"/1/trackname{ch}", (f"CH {ch}",))


def test_real_strip_count_survives_a_dropped_packet():
    """One lossy burst (dropped trackname UDP packet) must not undercount —
    an undercount suppresses the stale-map banner (observed live: 22 vs 23)."""
    s = DeviceState()
    _burst(s, "Main", 23)
    _burst(s, "AES", 22)      # one trackname lost in transit
    _burst(s, "Phones 1", 23)
    s.ingest("/1/labelSubmix", ("Main",))  # seal the last burst
    assert s.real_strip_count == 23


def test_real_strip_count_accepts_genuine_shrink_after_consistent_bursts():
    s = DeviceState()
    _burst(s, "Main", 23)
    for label in ("AES", "Phones 1", "Phones 2"):
        _burst(s, label, 17)   # snapshot changed the layout for real
    s.ingest("/1/labelSubmix", ("Main",))
    assert s.real_strip_count == 17
