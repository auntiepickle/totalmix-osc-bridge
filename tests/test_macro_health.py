"""#22 card health: persistent per-macro last-fire outcome."""
import pytest

fastapi_testclient = pytest.importorskip("fastapi.testclient")

import bridge as bridge_module  # noqa: E402
from web.web_client import app, _strip_runtime  # noqa: E402

client = fastapi_testclient.TestClient(app)


def _health(b, name):
    return b.macro_health.get(name)


def test_ok_fire_records_ok(make_bridge, fake_osc):
    b = make_bridge({"vol": {"steps": [{"osc": "/1/volume1",
                                        "value": "{{param}}"}]}})
    b.run_macro("vol", 0.7)
    h = _health(b, "vol")
    assert h["status"] == "ok" and h["skipped_steps"] == [] and h["at"] > 0
    # the completion macro_update carries it to the UI
    updates = [e["update"] for e in b.events if e["update"]]
    assert updates and updates[-1]["last_fire"]["status"] == "ok"


def test_osc_unconfigured_records_skipped(make_bridge):
    b = make_bridge({"vol": {"steps": [{"osc": "/1/volume1", "value": 1.0}]}})
    b.osc_client = None
    b.run_macro("vol", 0.5)
    h = _health(b, "vol")
    assert h["status"] == "skipped" and h["reason"] == "osc_not_configured"


def test_step_skip_records_partial(make_bridge, fake_osc):
    # a name target with no feedback and no stored fallback skips the step;
    # the raw step still lands → the run completes PARTIAL, not ok
    b = make_bridge({"mix": {"steps": [
        {"target": {"channel": "Ghost", "submix": "Main"}, "value": "{{param}}"},
        {"osc": "/1/mastermute", "value": 1.0},
    ]}})
    b.run_macro("mix", 0.5)
    h = _health(b, "mix")
    assert h["status"] == "partial"
    assert len(h["skipped_steps"]) == 1
    assert h["skipped_steps"][0].startswith("target_")
    assert ("/1/mastermute", 1.0) in fake_osc.sent


def test_strip_runtime_drops_last_fire():
    m = {"steps": [], "last_fire": {"status": "ok"}, "description": "x"}
    assert "last_fire" not in _strip_runtime(m)
    assert "description" in _strip_runtime(m)


def test_get_macros_merges_last_fire():
    b = bridge_module.bridge
    macros = b.mappings.get("macros", {})
    name = next(iter(macros))
    b.macro_health[name] = {"status": "ok", "reason": None,
                            "skipped_steps": [], "at": 123.0}
    try:
        r = client.get("/api/macros")
        assert r.json()[name]["last_fire"]["status"] == "ok"
    finally:
        b.macro_health.pop(name, None)


def test_global_endpoint_light_by_default():
    """The header polls /api/device/global — the default read must send NO
    probe traffic and report heartbeat age only."""
    from global_listener import GlobalOSCListener
    from global_transport import GlobalTransport

    class FakeClient:
        def __init__(self):
            self.sent = []

        def send_message(self, a, v):
            self.sent.append((a, v))

    b = bridge_module.bridge
    prev = (b.global_listener, b.global_transport)
    b.global_listener = GlobalOSCListener(0)
    fc = FakeClient()
    b.global_transport = GlobalTransport(fc, b.global_listener,
                                         lambda: None)
    try:
        b.global_listener.state.ingest("/status/device", (1.0,))
        r = client.get("/api/device/global")
        body = r.json()
        assert body["alive"]["method"] == "heartbeat_age"
        assert body["alive"]["alive"] is True
        assert fc.sent == []          # LIGHT: nothing hit the wire
        # explicit probe still exists for deploy verification
        r = client.get("/api/device/global?probe=true")
        assert r.json()["alive"]["method"].startswith("heartbeat")
    finally:
        b.global_listener, b.global_transport = prev
