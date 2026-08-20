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


def test_upsert_strips_runtime_fields(macro_crud):
    """run_macro merges runtime fields into the browser's macros{} object, so
    editor saves round-tripped them into mappings.json — strip on save."""
    from web.web_client import RUNTIME_FIELDS
    body = {
        "description": "keep me",
        "steps": [{"osc": "/1/volume1", "value": "0.5"}],
        # runtime pollution the browser sends back after a fire
        "name": "runtime_strip_macro", "value": 0.7, "progress": 42,
        "lfo_active": True, "last_trigger": "CC44", "osc_preview": "/1/volume1",
        "midi_trigger": "CC44 · ch1", "routing_label": "stale → label",
    }
    r = client.post("/api/config/macros/runtime_strip_macro", json=body)
    assert r.status_code == 200
    stored = bridge_module.bridge.mappings["macros"]["runtime_strip_macro"]
    assert not any(f in stored for f in RUNTIME_FIELDS)
    assert stored["description"] == "keep me" and stored["steps"]


def test_get_macros_injects_derived_routing_label(macro_crud):
    """routing_label is derived at read time, never persisted — stored copies
    rot when the device renames outputs."""
    body = {"steps": [{"target": {"submix": "Sub X", "channel": "AN 3"},
                       "value": "{{param}}"}]}
    r = client.post("/api/config/macros/derived_label_macro", json=body)
    assert r.status_code == 200
    served = client.get("/api/macros").json()["derived_label_macro"]
    assert served["routing_label"] == "AN 3 → Sub X"
    assert "routing_label" not in bridge_module.bridge.mappings["macros"]["derived_label_macro"]


def test_persist_sanitizes_preexisting_dirty_macros(monkeypatch, tmp_path):
    """Server smoke finding (2026-08-20): a dirty mappings.json loaded at
    startup kept its legacy runtime fields through every per-macro save —
    _strip_runtime only hit the incoming macro. _persist_mappings must
    sanitize the WHOLE in-memory mappings on every write."""
    import json as _json
    import web.web_client as wc
    monkeypatch.setattr(wc, "backup_json_files", lambda *a, **k: None)
    out = tmp_path / "mappings.json"
    real_open = open
    monkeypatch.setattr(wc, "open",
                        lambda path, mode="r": real_open(out, mode),
                        raising=False)
    saved = bridge_module.bridge.mappings
    try:
        bridge_module.bridge.mappings = {"macros": {
            "legacy_dirty": {"steps": [], "progress": 42, "value": 0.7,
                             "routing_label": "stale → label"},
            "clean_one": {"steps": []},
        }}
        wc._persist_mappings()
        # in-memory cleaned...
        assert bridge_module.bridge.mappings["macros"]["legacy_dirty"] == {"steps": []}
        # ...and the file on disk too
        on_disk = _json.loads(out.read_text())
        assert on_disk["macros"]["legacy_dirty"] == {"steps": []}
        assert on_disk["macros"]["clean_one"] == {"steps": []}
    finally:
        bridge_module.bridge.mappings = saved
        bridge_module.bridge.mappings_is_example = True


def test_sanitize_mappings_pure():
    """_sanitize_mappings strips runtime fields per macro without mutating
    its input (the whole-file save path passes the request body through it)."""
    from web.web_client import _sanitize_mappings
    original = {"macros": {"m1": {"steps": [], "progress": 1, "value": 0.2},
                           "weird": "not-a-dict"},
                "other_key": True}
    out = _sanitize_mappings(original)
    assert out["macros"]["m1"] == {"steps": []}
    assert out["macros"]["weird"] == "not-a-dict"   # passthrough, no crash
    assert out["other_key"] is True
    assert original["macros"]["m1"]["progress"] == 1  # input untouched


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


