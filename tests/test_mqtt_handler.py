"""mqtt_handler routing tests — a fake paho client captures the callbacks
setup_mqtt registers, then tests invoke on_message directly. No broker."""
import time
from types import SimpleNamespace

import pytest

import mqtt_handler
from mqtt_handler import setup_mqtt


class FakeMQTTClient:
    def __init__(self):
        self.on_connect = None
        self.on_message = None
        self.on_disconnect = None
        self.published = []  # (topic, payload) tuples

    def username_pw_set(self, user, password):
        pass

    def connect_async(self, broker, port, keepalive):
        pass

    def subscribe(self, topic):
        pass

    def publish(self, topic, payload=None, retain=False, qos=0):
        self.published.append((topic, payload))


class FakeBridge:
    def __init__(self):
        self.mappings = {"macros": {"known_macro": {"steps": []}}}
        self.snapshot_map = {}
        self.current_workspace = None
        self.current_snapshot = None
        self.mqtt_connected = False
        self._suppress_handler = False
        self._last_macro_end_time = 0.0
        self.state_confirmed = None
        self.run_macro_calls = []
        self.workspace_updates = []
        self.snapshot_updates = []
        self.wait_device_calls = []

    def _wait_device(self, predicate, timeout, fallback_sleep, what=""):
        self.wait_device_calls.append(what)
        return True

    def _global_active(self):
        return False  # classic transport (#25 seam)

    def run_macro(self, name, param):
        self.run_macro_calls.append((name, param))

    def update_workspace(self, name=None, slot=None):
        self.workspace_updates.append(name)

    def update_snapshot(self, name=None, index=None, workspace=None):
        self.snapshot_updates.append(name)


def msg(topic, payload, retain=False):
    return SimpleNamespace(topic=topic, payload=payload.encode(), retain=retain)


@pytest.fixture
def handler(monkeypatch):
    """Returns (client, bridge, sent_osc) with on_message wired up."""
    sent_osc = []
    monkeypatch.setattr(
        mqtt_handler, "send_osc",
        lambda addr, val, ip=None, port=7001: sent_osc.append((addr, val)),
    )
    monkeypatch.setattr(
        mqtt_handler, "SNAPSHOT_MAP",
        {"Pill_setup": {"slot": 2, "snapshots": {"1": "Reset"}}},
    )
    client = FakeMQTTClient()
    fake_bridge = FakeBridge()
    setup_mqtt(client, "broker", 1883, "user", "pass", "1.2.3.4", 7001,
               fake_bridge)
    assert client.on_message is not None
    return client, fake_bridge, sent_osc


def test_macro_topic_triggers_run_macro(handler):
    client, bridge, _ = handler
    client.on_message(client, None, msg("totalmix/macro/known_macro", "0.75"))
    assert bridge.run_macro_calls == [("known_macro", 0.75)]


def test_unknown_macro_not_triggered(handler):
    client, bridge, _ = handler
    client.on_message(client, None, msg("totalmix/macro/ghost", "0.5"))
    assert bridge.run_macro_calls == []


def test_invalid_macro_param_ignored(handler):
    client, bridge, _ = handler
    client.on_message(client, None, msg("totalmix/macro/known_macro", "loud"))
    assert bridge.run_macro_calls == []


def test_workspace_message_sends_osc_and_updates(handler):
    client, bridge, sent_osc = handler
    client.on_message(client, None, msg("totalmix/workspace", "2"))
    assert ("/loadQuickWorkspace", 2) in sent_osc
    assert bridge.workspace_updates == ["Pill_setup"]
    # MQTT-commanded switches confirm via feedback like macro switches
    assert any("workspace" in w for w in bridge.wait_device_calls)
    assert bridge.state_confirmed is True


def test_snapshot_message_confirms_via_feedback(handler):
    client, bridge, _ = handler
    client.on_message(client, None, msg("totalmix/snapshot", "3"))
    assert any("snapshot" in w for w in bridge.wait_device_calls)
    assert bridge.state_confirmed is True


def test_workspace_suppressed_during_macro(handler):
    client, bridge, sent_osc = handler
    bridge._suppress_handler = True
    client.on_message(client, None, msg("totalmix/workspace", "2"))
    assert sent_osc == []
    assert bridge.workspace_updates == []


def test_workspace_suppressed_in_cooldown_window(handler):
    client, bridge, sent_osc = handler
    bridge._last_macro_end_time = time.time()
    client.on_message(client, None, msg("totalmix/workspace", "2"))
    assert sent_osc == []


def test_snapshot_message_uses_inverted_index(handler):
    client, bridge, sent_osc = handler
    client.on_message(client, None, msg("totalmix/snapshot", "3"))
    # slot 3 → OSC button index 6
    assert ("/3/snapshots/6/1", 1.0) in sent_osc
    assert ("totalmix/snapshot/status", "loaded_3") in client.published


def test_snapshot_out_of_range_ignored(handler):
    client, bridge, sent_osc = handler
    client.on_message(client, None, msg("totalmix/snapshot", "9"))
    assert sent_osc == []


def test_non_integer_payloads_ignored(handler):
    client, bridge, sent_osc = handler
    client.on_message(client, None, msg("totalmix/workspace", "garbage"))
    client.on_message(client, None, msg("totalmix/snapshot", "garbage"))
    assert sent_osc == []


def test_retained_workspace_absorbed_as_belief_not_sent(handler):
    """Retained deliveries replay at every reconnect — they must inform the
    bridge's belief but NEVER drive the device (restarts were recalling
    snapshots and loading workspaces over the user's live mixer)."""
    client, bridge, sent_osc = handler
    client.on_message(client, None, msg("totalmix/workspace", "2", retain=True))
    assert sent_osc == []                        # device untouched
    assert bridge.workspace_updates == ["Pill_setup"]  # belief updated
    assert bridge.state_confirmed is False


def test_retained_snapshot_absorbed_as_belief_not_sent(handler):
    client, bridge, sent_osc = handler
    bridge.current_workspace = "Pill_setup"
    client.on_message(client, None, msg("totalmix/snapshot", "1", retain=True))
    assert sent_osc == []
    assert bridge.snapshot_updates == ["Reset"]
    assert bridge.state_confirmed is False


def test_retained_macro_trigger_ignored(handler):
    client, bridge, _ = handler
    client.on_message(client, None,
                      msg("totalmix/macro/known_macro", "0.5", retain=True))
    assert bridge.run_macro_calls == []          # would fire on every restart


def test_confirmed_mqtt_workspace_switch_republishes_retained(handler):
    """MQTT-driven switches left the retained topics at the last MACRO
    switch, so every restart restored a belief that old (server finding,
    2026-08-20). A CONFIRMED switch now refreshes the retained value."""
    client, bridge, _ = handler
    client.on_message(client, None, msg("totalmix/workspace", "2"))
    assert bridge.state_confirmed is True
    assert ("totalmix/workspace", "2") in client.published


def test_confirmed_mqtt_snapshot_switch_republishes_retained(handler):
    client, bridge, _ = handler
    client.on_message(client, None, msg("totalmix/snapshot", "3"))
    assert bridge.state_confirmed is True
    assert ("totalmix/snapshot", "3") in client.published


def test_unconfirmed_switch_does_not_republish(handler, monkeypatch):
    """No device confirmation → the retained belief must NOT be refreshed
    (we would be persisting a commanded belief the device may not hold)."""
    client, bridge, sent_osc = handler
    monkeypatch.setattr(
        bridge, "_wait_device",
        lambda predicate, timeout, fallback_sleep, what="": False)
    client.on_message(client, None, msg("totalmix/workspace", "2"))
    client.on_message(client, None, msg("totalmix/snapshot", "3"))
    assert sent_osc  # switches were still commanded
    assert ("totalmix/workspace", "2") not in client.published
    assert ("totalmix/snapshot", "3") not in client.published


def test_own_republish_echo_suppressed_exactly_once(handler):
    """The broker echoes our own retained republish back as a live message —
    exactly that one delivery is dropped (no OSC re-send), but an identical
    genuine command afterwards processes normally."""
    client, bridge, sent_osc = handler
    client.on_message(client, None, msg("totalmix/workspace", "2"))
    osc_after_first = list(sent_osc)
    # the echo of our own republish arrives (live delivery, same payload)
    client.on_message(client, None, msg("totalmix/workspace", "2"))
    assert sent_osc == osc_after_first           # echo drove nothing
    # a genuine identical command later is NOT swallowed (marker consumed);
    # it takes the already-on-target skip path, which updates state again
    updates_before = len(bridge.workspace_updates)
    client.on_message(client, None, msg("totalmix/workspace", "2"))
    assert len(bridge.workspace_updates) > updates_before


def test_ws_and_snap_echo_markers_are_independent(handler):
    """A snapshot republish must not unmask a pending workspace echo."""
    client, bridge, sent_osc = handler
    client.on_message(client, None, msg("totalmix/workspace", "2"))
    client.on_message(client, None, msg("totalmix/snapshot", "3"))
    osc_after_commands = list(sent_osc)
    # echoes arrive late, after both commands — both must be dropped
    client.on_message(client, None, msg("totalmix/workspace", "2"))
    client.on_message(client, None, msg("totalmix/snapshot", "3"))
    assert sent_osc == osc_after_commands
