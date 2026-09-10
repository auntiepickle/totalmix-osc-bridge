"""app_paths - where state lives. From source / Docker everything must stay
in the repo root (byte-for-byte unchanged deployment); frozen (PyInstaller)
redirects live state to a per-user data dir while templates stay bundled."""
import json
import os
import sys
from pathlib import Path

import tmosc.app_paths as app_paths
REPO = Path(__file__).resolve().parent.parent


def test_source_mode_keeps_everything_in_repo_root(monkeypatch):
    monkeypatch.delenv("TMOSC_DATA_DIR", raising=False)
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    assert app_paths.bundle_dir() == REPO
    assert app_paths.data_dir() == REPO
    assert app_paths.data_path("mappings.json") == str(REPO / "mappings.json")
    assert app_paths.example_path("mappings.example.json") == str(REPO / "examples" / "mappings.example.json")
    assert app_paths.static_dir() == str(REPO / "web" / "static")
    cwd = os.getcwd()
    assert app_paths.prepare() == REPO
    assert os.getcwd() == cwd, "prepare() must never chdir when nothing is redirected"


def test_frozen_windows_redirects_state_to_appdata(monkeypatch, tmp_path):
    bundle = tmp_path / "app" / "_internal"
    (bundle / "web" / "static").mkdir(parents=True)
    (bundle / "examples").mkdir()
    (bundle / "examples" / "mappings.example.json").write_text("{}")
    appdata = tmp_path / "Roaming"
    appdata.mkdir()
    monkeypatch.delenv("TMOSC_DATA_DIR", raising=False)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle), raising=False)
    monkeypatch.setattr(app_paths, "_PLATFORM", "win32")
    monkeypatch.setenv("APPDATA", str(appdata))

    expected = appdata / "tmosc-bridge"
    assert app_paths.bundle_dir() == bundle
    assert app_paths.data_dir() == expected
    # state -> appdata, templates + static -> bundle
    assert app_paths.data_path("mappings.json") == str(expected / "mappings.json")
    assert app_paths.data_path("bridge.log") == str(expected / "bridge.log")
    assert app_paths.example_path("mappings.example.json") == str(bundle / "examples" / "mappings.example.json")
    assert app_paths.static_dir() == str(bundle / "web" / "static")
    # prepare() creates the data dir on first run
    assert not expected.exists()
    assert app_paths.prepare(chdir=False) == expected
    assert expected.is_dir()


def test_frozen_linux_uses_xdg_data_home(monkeypatch, tmp_path):
    monkeypatch.delenv("TMOSC_DATA_DIR", raising=False)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    monkeypatch.setattr(app_paths, "_PLATFORM", "linux")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    assert app_paths.data_dir() == tmp_path / "xdg" / "tmosc-bridge"


def test_override_wins_in_any_mode(monkeypatch, tmp_path):
    monkeypatch.setenv("TMOSC_DATA_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    assert app_paths.data_dir() == tmp_path / "state"
    cwd = os.getcwd()
    d = app_paths.prepare()
    assert d.is_dir()
    assert os.getcwd() == cwd, "from source the CWD is never touched, override or not"
    # templates are still read from the bundle (repo root), not the override
    assert app_paths.example_path("mappings.example.json") == str(REPO / "examples" / "mappings.example.json")


def test_config_env_loader(tmp_path):
    keys = ("TMOSC_T_IP", "TMOSC_T_PORT", "TMOSC_T_BROKER", "TMOSC_T_EXISTING")
    cfg = tmp_path / "config.env"
    cfg.write_text(
        "# TotalMix bridge config\n"
        "\n"
        "TMOSC_T_IP=192.168.1.50\n"
        'TMOSC_T_PORT = "8090"\n'
        "TMOSC_T_BROKER='broker'\n"
        "not a key value line\n"
        "TMOSC_T_EXISTING=from_file\n",
        encoding="utf-8",
    )
    os.environ["TMOSC_T_EXISTING"] = "from_env"
    try:
        assert app_paths.load_config_env(cfg) == 3
        assert os.environ["TMOSC_T_IP"] == "192.168.1.50"
        assert os.environ["TMOSC_T_PORT"] == "8090"      # quotes stripped
        assert os.environ["TMOSC_T_BROKER"] == "broker"
        assert os.environ["TMOSC_T_EXISTING"] == "from_env"  # real env wins
        assert app_paths.load_config_env(tmp_path / "missing.env") == 0
    finally:
        for k in keys:
            os.environ.pop(k, None)


def test_web_layer_persists_into_data_dir(monkeypatch, tmp_path):
    """The web save path (mappings + auto-backup) follows app_paths at call
    time - this is the redirect the frozen build relies on, proven from
    source via the TMOSC_DATA_DIR override."""
    import tmosc.api.app as wc
    import tmosc.bridge as bridge_module
    monkeypatch.setenv("TMOSC_DATA_DIR", str(tmp_path))
    b = bridge_module.bridge
    saved = (b.mappings, b.mappings_is_example, b.mappings_source)
    try:
        b.mappings = {"macros": {"m1": {"steps": []}}}
        wc._persist_mappings()
        on_disk = json.loads((tmp_path / "mappings.json").read_text())
        assert on_disk["macros"] == {"m1": {"steps": []}}
        # a second save backs the first copy up into <data_dir>/backups
        b.mappings = {"macros": {"m1": {"steps": []}, "m2": {"steps": []}}}
        wc._persist_mappings()
        assert len(list((tmp_path / "backups").glob("mappings.json.*"))) == 1
        assert set(json.loads((tmp_path / "mappings.json").read_text())["macros"]) == {"m1", "m2"}
    finally:
        b.mappings, b.mappings_is_example, b.mappings_source = saved
