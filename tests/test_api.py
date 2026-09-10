"""API tests via FastAPI's TestClient.

TestClient is used without a context manager on purpose: startup events
(bridge.start_mqtt) never run, so no broker or network is needed.
Only read-only and validation-failure paths are exercised — success paths of
the config-save endpoints write real files into the repo.
"""
import pytest

fastapi_testclient = pytest.importorskip("fastapi.testclient")

from tmosc.api.app import app  # noqa: E402
import tmosc.bridge as bridge_module  # noqa: E402

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
    import tmosc.api.app as wc
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
    from tmosc.api.app import RUNTIME_FIELDS
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
    import tmosc.api.app as wc
    monkeypatch.setattr(wc, "backup_json_files", lambda *a, **k: None)
    out = tmp_path / "mappings.json"
    monkeypatch.setattr(wc, "_atomic_write_json",
                        lambda path, data: out.write_text(_json.dumps(data, indent=2)))
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
    from tmosc.api.app import _sanitize_mappings
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




def test_reorder_macros_in_place(monkeypatch):
    """Drag-to-reorder: new order persists, dict identity survives (held
    references stay valid), and a partial/wrong list is rejected."""
    import tmosc.api.app as wc
    monkeypatch.setattr(wc, "_persist_mappings", lambda: None)
    b = bridge_module.bridge
    macros = b.mappings.setdefault("macros", {})
    saved = dict(macros)
    macros.clear()
    macros.update({"aa": {"steps": []}, "bb": {"steps": []}, "cc": {"steps": []}})
    ident = id(macros)
    try:
        r = client.post("/api/config/macros-order", json={"order": ["cc", "aa", "bb"]})
        assert r.status_code == 200
        assert list(macros) == ["cc", "aa", "bb"]
        assert id(b.mappings["macros"]) == ident          # reordered IN PLACE
        r = client.post("/api/config/macros-order", json={"order": ["cc", "aa"]})
        assert r.status_code == 400                       # must list all
        assert list(macros) == ["cc", "aa", "bb"]         # unchanged on error
    finally:
        macros.clear()
        macros.update(saved)


def test_rename_macro_moves_key_and_state(monkeypatch):
    """Rename keeps dict order, carries runtime state, rejects collisions."""
    import tmosc.api.app as wc
    monkeypatch.setattr(wc, "_persist_mappings", lambda: None)   # no disk writes
    b = bridge_module.bridge
    macros = b.mappings.setdefault("macros", {})
    macros["zz_old"] = {"steps": []}
    b.macro_health["zz_old"] = {"status": "ok"}
    try:
        r = client.post("/api/config/macros/zz_old/rename", json={"new_name": "zz_new"})
        assert r.status_code == 200 and r.json()["macro"] == "zz_new"
        assert "zz_new" in macros and "zz_old" not in macros
        assert b.macro_health["zz_new"]["status"] == "ok"
        assert client.post("/api/config/macros/zz_new/rename", json={"new_name": "bad name!"}).status_code == 400
        existing = next(n for n in macros if n != "zz_new")
        assert client.post("/api/config/macros/zz_new/rename", json={"new_name": existing}).status_code == 409
        assert client.post("/api/config/macros/nope/rename", json={"new_name": "x"}).status_code == 404
    finally:
        macros.pop("zz_new", None); macros.pop("zz_old", None)
        b.macro_health.pop("zz_new", None); b.macro_health.pop("zz_old", None)


def test_auth_off_by_default_allows_writes():
    # API_TOKEN unset (default) -> the gate is a pass-through
    import tmosc.api.app as wc
    assert wc.API_TOKEN == ""
    r = client.post("/api/trigger/does_not_exist")
    assert r.status_code in (404, 409, 503)   # reached the handler, not 401


def test_auth_blocks_unauthenticated_write_when_set(monkeypatch):
    import tmosc.api.app as wc
    monkeypatch.setattr(wc, "API_TOKEN", "s3cret")
    # a mutating request WITHOUT the token is refused before the handler
    r = client.post("/api/trigger/whatever")
    assert r.status_code == 401
    # GETs stay open
    assert client.get("/api/macros").status_code == 200
    # with the token it passes the gate (handler then decides)
    r2 = client.post("/api/trigger/whatever", headers={"X-Api-Token": "s3cret"})
    assert r2.status_code != 401
    # ?token= also works
    r3 = client.post("/api/trigger/whatever?token=s3cret")
    assert r3.status_code != 401


def test_midi_bindings_tsv(monkeypatch):
    """/api/midi/bindings emits the agent's trigger table as TSV."""
    import tmosc.bridge as bridge_module
    saved = bridge_module.bridge.mappings
    try:
        bridge_module.bridge.mappings = {"macros": {
            "fader": {"steps": [{"operation": {"type": "knob"},
                                 "target": {"channel": "Mic 1"}}],
                      "midi_triggers": [{"type": "control_change", "number": 82,
                                         "channel": 1, "use_value_as_param": True}]},
            "scene": {"steps": [{"osc": "/setSubmix", "value": "1"}],
                      "midi_triggers": [{"type": "program_change", "number": 5,
                                         "channel": 1}]},
        }}
        r = client.get("/api/midi/bindings")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/plain")
        lines = r.text.strip().split("\n")
        assert "fader\t1\tcontrol_change\t82\t-1\t1\t1" in lines
        assert "scene\t0\tprogram_change\t5\t-1\t1\t0" in lines
    finally:
        bridge_module.bridge.mappings = saved


# ── MIDI ownership (coexistence) ──────────────────────────────────────────────

def test_midi_owner_claim_and_release():
    b = bridge_module.bridge
    b._midi_owner = None
    assert client.get("/api/health").json()["midi_owner"] is None
    # an agent claims the port
    o = client.post("/api/midi/owner/heartbeat",
                    json={"id": "agent-1", "host": "BOX"}).json()["owner"]
    assert o["id"] == "agent-1" and o["host"] == "BOX"
    assert client.get("/api/health").json()["midi_owner"]["id"] == "agent-1"
    # a release from a DIFFERENT id must not steal/clear ownership
    assert client.post("/api/midi/owner/release",
                       json={"id": "someone-else"}).json()["released"] is False
    assert client.get("/api/health").json()["midi_owner"]["id"] == "agent-1"
    # the owner releases cleanly -> gone immediately
    assert client.post("/api/midi/owner/release",
                       json={"id": "agent-1"}).json()["released"] is True
    assert client.get("/api/health").json()["midi_owner"] is None


def test_midi_owner_ttl_expiry(monkeypatch):
    b = bridge_module.bridge
    b._midi_owner = None
    # a stale heartbeat (older than the TTL) reads as no owner — the browser
    # reclaims MIDI even if the agent crashed without releasing.
    monkeypatch.setattr(b, "MIDI_OWNER_TTL_S", -1.0)
    client.post("/api/midi/owner/heartbeat", json={"id": "crash-agent"})
    assert b.midi_owner_state() is None
    assert client.get("/api/health").json()["midi_owner"] is None
    b._midi_owner = None


def test_midi_activity_relay_contract():
    # valid relay (P2): the agent forwards a raw MIDI message for the browser
    assert client.post("/api/midi/activity", json={"m": [176, 82, 64]}).json() == {"ok": True}
    # pure best-effort fan-out: malformed payloads are ignored, never error
    for bad in [{}, {"m": []}, {"m": "x"}, {"m": [1, 2, 3, 4]}, {"m": [1, "x"]}]:
        assert client.post("/api/midi/activity", json=bad).status_code == 200
