"""API tests via FastAPI's TestClient.

TestClient is used without a context manager on purpose: startup events
(bridge.start_mqtt) never run, so no broker or network is needed.
Only read-only and validation-failure paths are exercised — success paths of
the config-save endpoints write real files into the repo.
"""
import pytest

fastapi_testclient = pytest.importorskip("fastapi.testclient")

from web.web_client import app  # noqa: E402
import bridge as bridge_module  # noqa: E402

client = fastapi_testclient.TestClient(app)


def test_health_reports_osc_unconfigured():
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["osc_configured"] is False
    assert body["mqtt_connected"] is False


def test_macros_returns_mapping_dict():
    r = client.get("/api/macros")
    assert r.status_code == 200
    assert isinstance(r.json(), dict)


def test_status_shape():
    r = client.get("/api/status")
    assert r.status_code == 200
    body = r.json()
    for key in ("macros", "channel_map_submixes", "snapshot_map_workspaces",
                "workspace", "snapshot", "mappings_is_example"):
        assert key in body


def test_trigger_unknown_macro_404():
    r = client.post("/api/trigger/definitely-not-a-macro")
    assert r.status_code == 404


def test_trigger_known_macro_accepted():
    macros = bridge_module.bridge.mappings.get("macros", {})
    assert macros, "example mappings should provide at least one macro"
    name = next(iter(macros))
    r = client.post(f"/api/trigger/{name}", json={"param": 0.5})
    assert r.status_code == 200
    assert r.json()["status"] == "accepted"


def test_switch_without_osc_returns_503():
    r = client.post("/api/switch", json={"workspace": "anything"})
    assert r.status_code == 503


def test_save_mappings_rejects_missing_macros_key():
    r = client.post("/api/config/mappings", json={"not_macros": {}})
    assert r.status_code == 400


def test_save_channel_map_rejects_missing_submixes_key():
    r = client.post("/api/config/channel_map", json={"nope": {}})
    assert r.status_code == 400


def test_patch_unknown_macro_404():
    r = client.patch("/api/config/macros/definitely-not-a-macro", json={})
    assert r.status_code == 404


def test_device_state_503_without_listener():
    assert bridge_module.bridge.osc_listener is None  # startup never ran
    r = client.get("/api/device/state")
    assert r.status_code == 503


def test_discover_503_without_osc():
    r = client.post("/api/device/discover", json={})
    assert r.status_code == 503


def test_discovery_status_starts_idle():
    r = client.get("/api/device/discovery")
    assert r.status_code == 200
    assert r.json()["status"] == "idle"


def test_apply_discovery_409_before_any_run():
    r = client.post("/api/device/discovery/apply")
    assert r.status_code == 409


def test_root_redirects_to_ui():
    r = client.get("/", follow_redirects=False)
    assert r.status_code in (301, 302, 307)
    assert "/static/index.html" in r.headers["location"]
