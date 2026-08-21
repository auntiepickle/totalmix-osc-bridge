"""Global transport (#25) — resolution, writers, sync, liveness. No UDP:
feedback is fed straight into the listener state via ingest()."""
import importlib
import os

import pytest

import global_transport as gt
import physical_table as pt
from global_listener import GlobalOSCListener


class FakeClient:
    def __init__(self):
        self.sent = []

    def send_message(self, address, value):
        self.sent.append((address, value))


def make_table():
    t = pt.empty_table()
    for hw, name in ((0, "Mic 1"), (2, "AN 1/2"), (3, "AN 1/2"), (6, "SPDIF"),
                     (9, "Mic 10"), (14, "ADAT 1")):
        pt.merge_observation(t, "inputs", hw, name)
    pt.merge_observation(t, "inputs", 2, "AN 1")
    pt.merge_observation(t, "inputs", 3, "AN 2")
    for hw, name in ((0, "Main"), (0, "AN 1/2"), (4, "Phones")):
        pt.merge_observation(t, "outputs", hw, name)
    pt.merge_observation(t, "playbacks", 0, "Out 1")
    return t


@pytest.fixture
def rig():
    client = FakeClient()
    listener = GlobalOSCListener(0)  # never started — state fed directly
    table = make_table()
    persists = []
    tr = gt.GlobalTransport(client, listener, lambda: table,
                            persist_cb=lambda: persists.append(1))
    return tr, client, listener, table, persists


# ── resolution + writers ───────────────────────────────────────────

def test_volume_send_resolves_to_mix_address(rig):
    tr, client, *_ = rig
    w, label, status = tr.resolve_step(
        {"channel": "Mic 1", "submix": "Phones", "param": "volume"})
    assert status == "resolved"
    assert w.address == "/mix/in/0/4/faderlin"
    w.send_message("x", 0.5)
    assert client.sent == [("/mix/in/0/4/faderlin", 0.5)]  # identity (HW-2)


def test_playback_row_uses_pb_source(rig):
    tr, *_ = rig
    w, _, status = tr.resolve_step(
        {"channel": "Out 1", "submix": "Main", "row": 2, "param": "volume"})
    assert status == "resolved"
    assert w.address == "/mix/pb/0/0/faderlin"


def test_row3_volume_is_output_fader(rig):
    tr, *_ = rig
    w, _, status = tr.resolve_step(
        {"channel": "Phones", "row": 3, "param": "volume"})
    assert status == "resolved"
    assert w.address == "/output/4/faderlin"


def test_pan_converts_to_balpan(rig):
    tr, client, *_ = rig
    w, _, status = tr.resolve_step(
        {"channel": "Mic 1", "submix": "Main", "param": "pan"})
    assert status == "resolved"
    assert w.address == "/mix/in/0/0/balpan"
    w.send_message("x", 0.0)
    w.send_message("x", 1.0)
    assert client.sent == [("/mix/in/0/0/balpan", -1.0),
                           ("/mix/in/0/0/balpan", 1.0)]


def test_mute_is_channel_scoped_switch(rig):
    tr, client, *_ = rig
    w, _, status = tr.resolve_step({"channel": "SPDIF", "param": "mute"})
    assert status == "resolved"
    assert w.address == "/input/6/mute"
    w.send_message("x", 0.8)
    w.send_message("x", 0.2)
    assert client.sent == [("/input/6/mute", 1.0), ("/input/6/mute", 0.0)]


def test_lr_param_addresses_right_member(rig):
    tr, *_ = rig
    w, _, status = tr.resolve_step({"channel": "AN 1/2", "param": "phase_r"})
    assert status == "resolved"
    assert w.address == "/input/3/phase"  # pair start 2 → right member 3


def test_input_gain_range_by_hardware_class(rig):
    tr, client, *_ = rig
    # line input (hw 0): 0..12 dB
    w, _, status = tr.resolve_step({"channel": "Mic 1", "param": "input_gain"})
    assert status == "resolved"
    w.send_message("x", 0.5)
    # mic input (hw 9): 0..75 dB
    w, _, status = tr.resolve_step({"channel": "Mic 10", "param": "input_gain"})
    assert status == "resolved"
    w.send_message("x", 0.5)
    assert client.sent == [("/input/0/gain", 6.0), ("/input/9/gain", 37.5)]


def test_input_gain_refused_on_digital_channel(rig):
    tr, client, *_ = rig
    w, _, status = tr.resolve_step({"channel": "ADAT 1", "param": "input_gain"})
    assert (w, status) == (None, "unsupported_param")  # no gain stage
    assert client.sent == []


def test_fx_param_needs_no_channel(rig):
    tr, client, *_ = rig
    w, _, status = tr.resolve_step({"param": "reverb_enable"})
    assert status == "resolved"
    assert w.address == "/reverb/enable"
    w.send_message("x", 1.0)
    assert client.sent == [("/reverb/enable", 1.0)]


def test_mono_name_hits_own_channel_when_unlinked(rig):
    tr, *_ = rig
    w, _, status = tr.resolve_step(
        {"channel": "AN 2", "submix": "Main", "param": "volume"})
    assert status == "resolved"
    assert w.address == "/mix/in/3/0/faderlin"  # its own hardware channel


def test_mono_name_defaults_to_pair_when_linked(rig):
    tr, _, listener, *_ = rig
    listener.state.ingest("/input/2/stereo", (1.0,))  # pair (2,3) linked now
    w, _, status = tr.resolve_step(
        {"channel": "AN 2", "submix": "Main", "param": "volume"})
    assert status == "resolved"
    # right member isn't an addressable strip while linked → pair start
    assert w.address == "/mix/in/2/0/faderlin"


def test_pair_name_resolves_to_start(rig):
    tr, *_ = rig
    w, _, status = tr.resolve_step(
        {"channel": "AN 1/2", "submix": "Main", "param": "volume"})
    assert status == "resolved"
    assert w.address == "/mix/in/2/0/faderlin"


def test_live_names_take_precedence_over_table(rig):
    tr, _, listener, *_ = rig
    # snapshot renamed hw 6 to "Pill" — table has no such alias yet
    listener.state.ingest("/input/6/name", ("Pill",))
    w, _, status = tr.resolve_step(
        {"channel": "Pill", "submix": "Main", "param": "volume"})
    assert status == "resolved"
    assert w.address == "/mix/in/6/0/faderlin"


# ── refusals ───────────────────────────────────────────────────────

def test_unknown_channel_refused(rig):
    tr, client, *_ = rig
    w, label, status = tr.resolve_step(
        {"channel": "Nope", "submix": "Main", "param": "volume"})
    assert (w, status) == (None, "not_in_table")
    assert label == "Nope"
    assert client.sent == []


def test_unknown_submix_refused(rig):
    tr, *_ = rig
    w, label, status = tr.resolve_step(
        {"channel": "Mic 1", "submix": "Nope", "param": "volume"})
    assert (w, status) == (None, "not_in_table")
    assert label == "Nope"


def test_volume_without_submix_refused(rig):
    tr, *_ = rig
    w, _, status = tr.resolve_step({"channel": "Mic 1", "param": "volume"})
    assert (w, status) == (None, "not_in_table")


def test_unknown_param_refused(rig):
    tr, *_ = rig
    w, _, status = tr.resolve_step({"channel": "Mic 1", "param": "warp"})
    assert (w, status) == (None, "unsupported_param")


def test_uncalibrated_param_refused(rig, monkeypatch):
    tr, client, *_ = rig
    # the map is fully measured now — synthesize an uncalibrated param to
    # prove the refusal path stays wired
    import global_units as gu
    monkeypatch.setattr(gu.GLOBAL_PARAM_MAP["input_gain"], "to_wire", None)
    w, _, status = tr.resolve_step({"channel": "Mic 1", "param": "input_gain"})
    assert (w, status) == (None, "uncalibrated_param")
    assert client.sent == []  # refusal means nothing hits the wire


def test_playback_row_has_no_channel_detail(rig):
    tr, *_ = rig
    w, _, status = tr.resolve_step(
        {"channel": "Out 1", "row": 2, "param": "eq_enable"})
    assert (w, status) == (None, "playback_no_detail")


def test_playback_mute_is_allowed(rig):
    tr, *_ = rig
    w, _, status = tr.resolve_step(
        {"channel": "Out 1", "row": 2, "param": "mute"})
    assert status == "resolved"
    assert w.address == "/playback/0/mute"


# ── snapshot + liveness ────────────────────────────────────────────

def test_load_snapshot_sends_and_confirms(rig):
    tr, client, listener, *_ = rig
    listener.state.ingest("/snapshot/load/3", (2.0,))  # device: slot 3 active
    assert tr.load_snapshot(3) is True
    assert client.sent == [("/snapshot/load/3", 1.0)]  # 1-BASED, no 9-N


def test_load_snapshot_times_out_without_feedback(rig):
    tr, *_ = rig
    assert tr.load_snapshot(5, timeout=0.05) is False


def test_alive_via_fresh_heartbeat(rig):
    tr, client, listener, *_ = rig
    listener.state.ingest("/status/device", (1.0,))
    r = tr.alive()
    assert r["alive"] is True and r["method"] == "heartbeat"
    assert client.sent == []  # no probe traffic needed


def test_alive_probes_with_sendstate_when_stale(rig):
    tr, client, listener, *_ = rig
    listener.wait_for = lambda pred, timeout: False  # nothing ever arrives
    r = tr.alive()
    assert r["alive"] is False
    assert ("/sendstate", 1.0) in client.sent


# ── name sync → physical table ─────────────────────────────────────

def test_name_sync_merges_and_mirrors_stereo_pair(rig):
    tr, _, listener, table, persists = rig
    listener.state.ingest("/input/2/name", ("Pill Out",))
    listener.state.ingest("/input/2/stereo", (1.0,))
    assert tr.sync_names_once() == 2  # left member + mirrored right member
    assert pt.resolve_start(table, "inputs", "Pill Out") == 2
    assert pt.covers(table, "inputs", 3, "AN 2", "Pill Out")
    assert persists == [1]
    assert tr.sync_names_once() == 0  # drained — no re-merge, no re-persist
    assert persists == [1]


def test_name_sync_mono_channel_merges_once(rig):
    tr, _, listener, table, persists = rig
    listener.state.ingest("/input/6/name", ("Sync L",))
    assert tr.sync_names_once() == 1
    assert pt.resolve_start(table, "inputs", "Sync L") == 6
    assert not pt.covers(table, "inputs", 7, "Sync L", "Sync L")


def test_name_sync_known_alias_is_noop(rig):
    tr, _, listener, table, persists = rig
    listener.state.ingest("/input/0/name", ("Mic 1",))
    assert tr.sync_names_once() == 0
    assert persists == []


# ── lifecycle ──────────────────────────────────────────────────────

def test_start_bootstraps_with_sendall_and_stop_joins(rig):
    tr, client, *_ = rig
    tr.start()
    try:
        assert ("/sendall", 1.0) in client.sent
        assert tr._sync_thread.is_alive()
    finally:
        tr.stop()
    assert tr._sync_thread is None


# ── config selection (WI-6) ────────────────────────────────────────

def test_config_transport_selection(monkeypatch):
    import config
    monkeypatch.delenv("OSC_TRANSPORT", raising=False)
    monkeypatch.delenv("ENABLE_GLOBAL_OSC_LISTENER", raising=False)
    importlib.reload(config)
    assert config.OSC_TRANSPORT == "classic"
    assert config.ENABLE_GLOBAL_OSC_LISTENER is False
    assert config.GLOBAL_OSC_PORT == 7002
    assert config.GLOBAL_OSC_LISTEN_PORT == 9002

    monkeypatch.setenv("OSC_TRANSPORT", "global")
    importlib.reload(config)
    assert config.OSC_TRANSPORT == "global"
    # selecting the global transport implies its listener
    assert config.ENABLE_GLOBAL_OSC_LISTENER is True

    monkeypatch.setenv("OSC_TRANSPORT", "classic")
    monkeypatch.setenv("ENABLE_GLOBAL_OSC_LISTENER", "true")
    importlib.reload(config)
    # shadow mode: classic writes, global listener observing
    assert config.OSC_TRANSPORT == "classic"
    assert config.ENABLE_GLOBAL_OSC_LISTENER is True

    monkeypatch.delenv("OSC_TRANSPORT", raising=False)
    monkeypatch.delenv("ENABLE_GLOBAL_OSC_LISTENER", raising=False)
    importlib.reload(config)
