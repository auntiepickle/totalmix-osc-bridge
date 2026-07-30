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
