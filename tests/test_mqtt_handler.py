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
        self.run_macro_calls = []
        self.workspace_updates = []
        self.snapshot_updates = []

    def run_macro(self, name, param):
        self.run_macro_calls.append((name, param))

    def update_workspace(self, name=None, slot=None):
        self.workspace_updates.append(name)

    def update_snapshot(self, name=None, index=None, workspace=None):
        self.snapshot_updates.append(name)


def msg(topic, payload):
    return SimpleNamespace(topic=topic, payload=payload.encode())


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
