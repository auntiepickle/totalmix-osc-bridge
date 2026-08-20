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


def test_status_never_drives_the_device_on_strip_count_drift(monkeypatch):
    """Architecture review 2026-08-20: input strip counts change with EVERY
    snapshot (pairing is per-snapshot state) — that is normal operation.
    An earlier version auto-walked on this comparison and made snapshot
    switches trigger 90s walks in a loop. GET /api/status must be pure."""
    from types import SimpleNamespace
    import web.web_client as wc
    calls = []
    monkeypatch.setattr(wc, "_auto_walk", lambda: calls.append(1))
    fake_listener = SimpleNamespace(
        running=True, state=SimpleNamespace(bank_width=48, real_strip_count=23))
    monkeypatch.setattr(bridge_module.bridge, "osc_listener", fake_listener)
    monkeypatch.setattr(bridge_module.bridge, "channel_map", {
        "submixes": {"Main": {"sends": {
            f"C{i}": {"row": 1, "channel": i} for i in range(1, 23)}}},  # 22
    })
    assert client.get("/api/status").status_code == 200
    assert not calls, "strip-count drift is snapshot-normal — never walk on it"


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


def test_apply_refuses_collapsed_walk(monkeypatch):
    """A mid-walk device freeze produces a 1-submix 'successful' walk —
    applying it clobbered a good 16-submix map on hardware. Refuse a
    dramatic collapse unless forced."""
    import web.web_client as wc
    monkeypatch.setattr(wc, "backup_json_files", lambda *a, **k: None)
    monkeypatch.setattr(bridge_module.bridge, "channel_map", {
        "submixes": {f"Sub {i}": {"sends": {}} for i in range(16)},
    })
    monkeypatch.setattr(bridge_module.bridge, "discovery_state", {
        "status": "done",
        "channel_map": {"submixes": {"Only One": {"sends": {}}}},
    })
    r = client.post("/api/device/discovery/apply", json={})
    assert r.status_code == 409
    assert "mid-walk" in r.json()["detail"]


def test_widths_endpoint_stores_layout_keyed(monkeypatch):
    """POST /api/device/widths keys the widths to the given layout so two
    layouts can hold conflicting widths for the same strip name (#16)."""
    import web.web_client as wc
    persisted = {}
    monkeypatch.setattr(wc, "_persist_channel_map",
                        lambda cm: persisted.update(cm))
    monkeypatch.setattr(bridge_module.bridge, "channel_map", {"submixes": {}})
    r = client.post("/api/device/widths", json={
        "widths": {"AN 1/2": 2, "RE-101": 2},
        "layout": ["AN 1/2", "RE-101"]})
    assert r.status_code == 200
    body = r.json()
    assert body["hw_channels_covered"] == 4 and body["uncovered"] == []
    key = bridge_module.bridge._layout_key_from_names(["AN 1/2", "RE-101"])
    assert persisted["width_maps"][key] == {"AN 1/2": 2, "RE-101": 2}

    r = client.post("/api/device/widths", json={
        "widths": {"RE-101": 3}, "layout": ["RE-101"]})
    assert r.status_code == 422


def test_widths_endpoint_refuses_blind_listener(monkeypatch):
    """Without a live layout or an explicit one, keying is impossible —
    refuse rather than guess (mis-keyed widths would silently disarm EQ)."""
    monkeypatch.setattr(bridge_module.bridge, "osc_listener", None)
    r = client.post("/api/device/widths", json={"widths": {"AN 1/2": 2}})
    assert r.status_code == 409


def test_apply_registers_walk_in_layout_library(monkeypatch):
    """Every applied walk is remembered under its output-layout key so a
    later snapshot swap hot-swaps the stored map instead of demanding a
    re-walk (user pain: a walk per swap)."""
    import web.web_client as wc
    persisted = {}
    monkeypatch.setattr(wc, "backup_json_files", lambda *a, **k: None)
    monkeypatch.setattr(wc, "_persist_channel_map",
                        lambda cm: persisted.update(cm))
    monkeypatch.setattr(wc, "_live_row1_names", lambda: None)
    monkeypatch.setattr(bridge_module.bridge, "check_map_freshness",
                        lambda: None)
    old_lib_key = bridge_module.bridge._layout_key_from_names(["Old A", "Old B"])
    monkeypatch.setattr(bridge_module.bridge, "channel_map", {
        "submixes": {"Old A": {"index": 1}, "Old B": {"index": 2}},
        "layout_library": {old_lib_key: {"Old A": {"index": 1},
                                         "Old B": {"index": 2}}},
    })
    monkeypatch.setattr(bridge_module.bridge, "discovery_state", {
        "status": "done",
        "channel_map": {"submixes": {"New A": {"index": 1},
                                     "New B": {"index": 2}}},
    })
    r = client.post("/api/device/discovery/apply", json={})
    assert r.status_code == 200
    lib = persisted["layout_library"]
    new_key = bridge_module.bridge._layout_key_from_names(["New A", "New B"])
    assert set(lib) == {old_lib_key, new_key}          # old walk kept
    assert lib[new_key] == {"New A": {"index": 1}, "New B": {"index": 2}}


def test_apply_carries_snapshot_layouts(monkeypatch):
    """Apply wiped snapshot_layouts (4 entries -> 1 on hardware), silently
    re-imposing the one-time learn on every other snapshot."""
    import web.web_client as wc
    persisted = {}
    monkeypatch.setattr(wc, "backup_json_files", lambda *a, **k: None)
    monkeypatch.setattr(wc, "_persist_channel_map",
                        lambda cm: persisted.update(cm))
    monkeypatch.setattr(wc, "_live_row1_names", lambda: None)
    monkeypatch.setattr(bridge_module.bridge, "check_map_freshness",
                        lambda: None)
    monkeypatch.setattr(bridge_module.bridge, "channel_map", {
        "submixes": {"Old": {"index": 1}},
        "snapshot_layouts": {"WS|a": "k1", "WS|b": "k2"},
    })
    monkeypatch.setattr(bridge_module.bridge, "discovery_state", {
        "status": "done",
        "channel_map": {"submixes": {"New": {"index": 1}}},
    })
    r = client.post("/api/device/discovery/apply", json={})
    assert r.status_code == 200
    assert persisted["snapshot_layouts"] == {"WS|a": "k1", "WS|b": "k2"}
