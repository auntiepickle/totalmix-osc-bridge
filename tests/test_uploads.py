"""Config uploads: size cap, validation before backup, round trip."""
import json

import pytest

fastapi_testclient = pytest.importorskip("fastapi.testclient")

import tmosc.bridge as bridge_module  # noqa: E402
from tmosc.api.app import app, MAX_UPLOAD_BYTES  # noqa: E402

client = fastapi_testclient.TestClient(app)


@pytest.fixture
def isolated_state(monkeypatch, tmp_path):
    """Writes land in a scratch state dir; the singleton's mappings are restored."""
    monkeypatch.setenv("TMOSC_DATA_DIR", str(tmp_path))
    b = bridge_module.bridge
    saved = (b.mappings, b.mappings_is_example, b.mappings_source)
    yield tmp_path
    b.mappings, b.mappings_is_example, b.mappings_source = saved


def _upload(path, name, payload):
    return client.post(path, files={"file": (name, payload, "application/json")})


def test_oversize_upload_is_rejected_before_anything_is_written(isolated_state):
    big = b'{"macros": {}, "pad": "' + b"x" * (MAX_UPLOAD_BYTES + 10) + b'"}'
    r = _upload("/api/upload/mappings", "mappings.json", big)
    assert r.status_code == 413
    assert not (isolated_state / "mappings.json").exists()
    assert not (isolated_state / "backups").exists()      # no backup for a rejected upload


def test_invalid_json_and_wrong_shape_are_400_without_a_backup(isolated_state):
    assert _upload("/api/upload/mappings", "mappings.json", b"{not json").status_code == 400
    assert _upload("/api/upload/mappings", "mappings.json", b"[1, 2]").status_code == 400
    assert _upload("/api/upload/mappings", "mappings.json", b'{"nope": 1}').status_code == 400
    assert _upload("/api/upload/mappings", "notes.txt", b"{}").status_code == 400
    assert not (isolated_state / "backups").exists()


def test_valid_upload_round_trips_and_hot_reloads(isolated_state):
    body = json.dumps({"macros": {"up_test": {"steps": [], "progress": 3}}}).encode()
    r = _upload("/api/upload/mappings", "mappings.json", body)
    assert r.status_code == 200
    on_disk = json.loads((isolated_state / "mappings.json").read_text())
    assert on_disk["macros"]["up_test"] == {"steps": []}    # runtime fields stripped
    assert "up_test" in bridge_module.bridge.mappings["macros"]
