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


def test_patch_unknown_macro_creates_it(macro_crud):
    # PATCH is an upsert alias (api.js has always POSTed to this route)
    r = client.patch("/api/config/macros/previously-unknown", json={"steps": []})
    assert r.status_code == 200
    assert r.json()["created"] is True


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


@pytest.fixture
def macro_crud(monkeypatch):
    """Isolate macro CRUD tests: no disk writes, mappings restored after."""
    import web.web_client as wc
    persisted = []
    monkeypatch.setattr(wc, "_persist_mappings", lambda: persisted.append(True))
    saved = {k: dict(v) for k, v in bridge_module.bridge.mappings.get("macros", {}).items()}
    yield persisted
    bridge_module.bridge.mappings["macros"] = saved


def test_macro_create_update_delete_cycle(macro_crud):
    body = {"description": "test", "steps": [{"osc": "/1/volume1", "value": "{{param}}"}]}

    r = client.post("/api/config/macros/crud_test_macro", json=body)
    assert r.status_code == 200 and r.json()["created"] is True
    assert "crud_test_macro" in bridge_module.bridge.mappings["macros"]
    assert len(macro_crud) == 1  # persisted once

    r = client.post("/api/config/macros/crud_test_macro",
                    json={**body, "description": "changed"})
    assert r.status_code == 200 and r.json()["created"] is False
    assert bridge_module.bridge.mappings["macros"]["crud_test_macro"]["description"] == "changed"

    r = client.delete("/api/config/macros/crud_test_macro")
    assert r.status_code == 200
    assert "crud_test_macro" not in bridge_module.bridge.mappings["macros"]

    assert client.delete("/api/config/macros/crud_test_macro").status_code == 404


def test_macro_create_patch_alias(macro_crud):
    r = client.patch("/api/config/macros/patch_alias_macro", json={"steps": []})
    assert r.status_code == 200 and r.json()["created"] is True


def test_macro_create_rejects_bad_names(macro_crud):
    for bad in ("has space", "a" * 65, "ünïcode"):
        r = client.post(f"/api/config/macros/{bad}", json={"steps": []})
        assert r.status_code == 400, bad
    # A slash never reaches the handler — the router 404s it
    assert client.post("/api/config/macros/slash/name", json={}).status_code == 404
    assert len(macro_crud) == 0


def test_macro_create_rejects_non_object_body(macro_crud):
    r = client.post("/api/config/macros/list_body", json=["not", "a", "dict"])
    assert r.status_code == 400
    assert len(macro_crud) == 0


def test_root_redirects_to_ui():
    r = client.get("/", follow_redirects=False)
    assert r.status_code in (301, 302, 307)
    assert "/static/index.html" in r.headers["location"]


def test_status_reports_bank_width_fields():
    r = client.get("/api/status")
    body = r.json()
    assert "osc_bank_width" in body          # None without a listener
    assert body["osc_bank_width"] is None
    assert body["channel_map_max_channel"] >= 0


def test_map_strip_count_counts_input_row_only(monkeypatch):
    """Playback sends must not inflate the stale-map comparison — counting
    them masked a real stale map (17 live vs '39 total' stayed silent)."""
    monkeypatch.setattr(bridge_module.bridge, "channel_map", {
        "submixes": {"Main": {"index": 1, "name": "Main", "sends": {
            "AN 1": {"row": 1, "channel": 1, "osc_address": "/1/volume1"},
            "AN 2": {"row": 1, "channel": 2, "osc_address": "/1/volume2"},
            "AN 1/2 (playback)": {"row": 2, "channel": 1,
                                  "osc_address": "/1/volume1"},
        }}},
    })
    body = client.get("/api/status").json()
    assert body["channel_map_strip_count"] == 2  # input rows only


def test_widths_carry_only_when_layout_unchanged():
    """channel_widths are LAYOUT-scoped (RE-101 was width 2 in one snapshot,
    width 1 in another — hardware-proven). Carrying them across a layout
    change could mis-aim page-2 writes; dropping them merely refuses."""
    from web.web_client import _carry_widths

    def cmap(names, widths=None):
        m = {"submixes": {"Main": {"sends": {
            n: {"name": n, "row": 1, "channel": i + 1} for i, n in enumerate(names)
        }}}}
        if widths:
            m["channel_widths"] = widths
        return m

    old = cmap(["AN 1/2", "RE-101"], widths={"AN 1/2": 2, "RE-101": 2})
    same_layout = cmap(["AN 1/2", "RE-101"])
    assert _carry_widths(old, same_layout) is True
    assert same_layout["channel_widths"] == {"AN 1/2": 2, "RE-101": 2}

    changed_layout = cmap(["AN 1", "AN 2", "RE-101"])
    assert _carry_widths(old, changed_layout) is False
    assert "channel_widths" not in changed_layout

    assert _carry_widths(cmap(["AN 1/2"]), cmap(["AN 1/2"])) is False  # nothing to carry
