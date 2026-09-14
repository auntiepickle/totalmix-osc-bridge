"""#27 phase 4b: the bridge factory, create_app(bridge=...) and idempotent
logging setup. The tests build their OWN app around an isolated bridge on a
fake OSC client - nothing here touches the module-level `app`'s bridge."""
import logging

from fastapi.testclient import TestClient

import tmosc.bridge as bridge_module
from tmosc.api.app import app as shared_app, create_app
from tmosc.logsetup import configure_logging


def test_create_app_serves_the_bridge_it_was_given(make_bridge):
    b = make_bridge({"only_here": {"steps": []}})
    b.mappings_source = "mappings.json"
    b.mappings_is_example = False
    mine = create_app(bridge=b)
    assert mine.state.bridge is b
    assert mine.state.bridge is not shared_app.state.bridge
    client = TestClient(mine)
    assert set(client.get("/api/macros").json()) == {"only_here"}
    assert client.get("/api/status").json()["mappings_source"] == "mappings.json"
    # a write through the routes lands on THIS bridge, not the shared one
    r = client.post("/api/config/macros/only_here", json={"steps": [], "description": "x"})
    assert r.status_code == 200
    assert b.mappings["macros"]["only_here"]["description"] == "x"
    assert "only_here" not in shared_app.state.bridge.mappings["macros"]


def test_build_bridge_loads_example_fallback(monkeypatch, tmp_path):
    """An empty data dir (earlier API tests persist a mappings.json into the
    suite's shared one, hence the override): the bundled example is what the
    factory finds - and it says so."""
    monkeypatch.setenv("TMOSC_DATA_DIR", str(tmp_path))
    b = bridge_module.build_bridge()
    assert b.mappings_is_example is True
    assert b.mappings_source == "mappings.example.json"
    assert b.mappings.get("macros")
    assert b.osc_client is None          # conftest pops OSC_IP
    assert b.mqtt_client is None         # nothing started


def test_load_mappings_prefers_user_file(monkeypatch, tmp_path):
    monkeypatch.setenv("TMOSC_DATA_DIR", str(tmp_path))
    (tmp_path / "mappings.json").write_text('{"macros": {"mine": {"steps": []}}}')
    mappings, source, is_example = bridge_module.load_mappings()
    assert (source, is_example) == ("mappings.json", False)
    assert set(mappings["macros"]) == {"mine"}


def test_load_snapshot_map_missing_is_empty(tmp_path):
    assert bridge_module.load_snapshot_map([str(tmp_path / "nope.json")]) == {}


def test_configure_logging_is_idempotent():
    root = logging.getLogger()
    before = len(root.handlers)
    assert configure_logging() is False      # create_app() already did it at import
    assert len(root.handlers) == before
    kinds = {type(h).__name__ for h in root.handlers}
    assert "RotatingFileHandler" in kinds and "StreamHandler" in kinds
