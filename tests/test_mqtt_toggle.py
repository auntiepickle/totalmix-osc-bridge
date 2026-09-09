"""ENABLE_MQTT=False must leave the bridge without a client (installer "MQTT off")."""
import bridge as bridge_module


def test_enable_mqtt_false_creates_no_client(monkeypatch):
    b = bridge_module.bridge
    saved = b.mqtt_client
    monkeypatch.setattr(bridge_module, "ENABLE_MQTT", False)
    monkeypatch.setattr(bridge_module, "ENABLE_OSC_MONITOR", False)
    try:
        b.mqtt_client = None
        b.start_mqtt()
        assert b.mqtt_client is None
        assert b.mqtt_connected is False
    finally:
        b.mqtt_client = saved
