# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the frozen Windows bridge - ONEDIR on purpose: a
onefile exe self-extracts on every start (slow cold-start for a resident
server, and a magnet for AV heuristics); the installer hides the folder.

    pip install -r requirements-frozen.txt
    pyinstaller --noconfirm tmosc-bridge.spec   -> dist/tmosc-bridge/tmosc-bridge.exe

Templates + web/static are bundled read-only (app_paths.bundle_dir() =
<app>/_internal); live state goes to %APPDATA%/tmosc-bridge (app_paths.data_dir()).
"""
import os
from PyInstaller.utils.hooks import collect_all, collect_submodules

ROOT = SPECPATH  # the repo root (directory of this spec)

datas = [
    (os.path.join(ROOT, "web", "static"), os.path.join("web", "static")),
    (os.path.join(ROOT, "mappings.example.json"), "."),
    (os.path.join(ROOT, "ufx2_channel_map.example.json"), "."),
    (os.path.join(ROOT, "ufx2_snapshot_map.example.json"), "."),
]
if os.path.exists(os.path.join(ROOT, "VERSION")):   # written by CI from the release tag
    datas.append((os.path.join(ROOT, "VERSION"), "."))

binaries, hiddenimports = [], []
for pkg in ("uvicorn", "fastapi", "starlette", "anyio", "pydantic", "h11", "websockets"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h
hiddenimports += collect_submodules("pythonosc") + collect_submodules("paho")
hiddenimports += [
    "python_multipart", "multipart",             # UploadFile endpoints
    # our own modules, including the ones imported lazily inside functions
    "app_paths", "config", "bridge", "discovery", "osc", "osc_listener", "osc_monitor",
    "mqtt_handler", "operations", "physical_table", "global_units", "global_transport",
    "global_listener", "duck_engine", "web", "web.web_client",
]
excludes = ["uvloop", "httptools", "watchfiles", "mido", "rtmidi",
            "tkinter", "numpy", "pytest", "IPython", "matplotlib", "PIL"]

a = Analysis(
    [os.path.join(ROOT, "bridge_main.py")],
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=excludes,
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="tmosc-bridge",
    icon=os.path.join(ROOT, "agent", "windows", "tray.ico"),
    console=True,
    upx=False, strip=False,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="tmosc-bridge")
