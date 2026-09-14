"""Config persistence shared by the routers: atomic JSON writes, auto-backup,
mappings sanitizing/persist, upload reading. Tests patch names HERE
(monkeypatch.setattr(tmosc.api.persistence, ...)) - the routers look them up
through this module at call time."""
import os
import tempfile
import re
import shutil
import datetime
import json
import logging

from fastapi import HTTPException, UploadFile

import tmosc.app_paths as app_paths
logger = logging.getLogger(__name__)


def _atomic_write_json(target, data):
    """Write JSON atomically: temp file + fsync + os.replace, so a crash or
    power loss mid-write can never truncate the live config (critical-review
    H1 - a truncated mappings.json boots empty = every knob and macro gone
    at showtime)."""
    d = os.path.dirname(target) or "."
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, target)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


MACRO_NAME_RE = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")

# run_macro merges these into live state; the browser's macros{} object carries
# them, so editor saves used to round-trip them into mappings.json. Strip on
# every save path — must mirror RUNTIME_FIELDS in web/static/ui/editor.js.
RUNTIME_FIELDS = (
    "name", "value", "progress", "lfo_active",
    "last_trigger", "osc_preview", "midi_trigger", "routing_label",
    "last_fire", "knob_value", "device_value", "enable_value", "companions",
)


def _strip_runtime(macro: dict) -> dict:
    return {k: v for k, v in macro.items() if k not in RUNTIME_FIELDS}


def _sanitize_mappings(data: dict) -> dict:
    macros = data.get("macros")
    if isinstance(macros, dict):
        data = {**data, "macros": {
            name: _strip_runtime(m) if isinstance(m, dict) else m
            for name, m in macros.items()
        }}
    return data


def _persist_mappings(bridge):
    """Write bridge.mappings to mappings.json (backup first).

    Sanitizes ALL macros, not just the one being saved — a dirty file loaded
    at startup would otherwise re-persist its legacy runtime fields on every
    per-macro save forever (server smoke finding, 2026-08-20)."""
    backup_json_files("mappings.json")
    bridge.mappings = _sanitize_mappings(bridge.mappings)
    target = app_paths.data_path("mappings.json")
    _atomic_write_json(target, bridge.mappings)
    bridge.mappings_is_example = False
    bridge.mappings_source = "mappings.json"


# ── File Upload + Auto-Backup ────────────────────────────────────────────────

def backup_json_files(files=("mappings.json", "ufx2_channel_map.json")):
    """Auto-backup the config file(s) about to be overwritten.

    Pass the specific filename being written — backing up both regardless
    fills backups/ with redundant copies and makes a backup's timestamp
    meaningless as an edit marker.
    """
    if isinstance(files, str):
        files = (files,)
    # Millisecond precision — two writes in the same second (easy with the
    # macro editor) were overwriting each other's backup (observed live)
    timestamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S-%f")[:-3]
    backup_dir = app_paths.data_path("backups")
    os.makedirs(backup_dir, exist_ok=True)
    for fn in files:
        src = app_paths.data_path(fn)
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(backup_dir, f"{fn}.{timestamp}"))
            logger.info(f"✅ Auto-backup: {fn}.{timestamp}")


# Config uploads are small JSON files; a runaway body must not be read into
# memory whole, and a rejected upload must not leave a pointless backup behind.
MAX_UPLOAD_BYTES = 2 * 1024 * 1024


async def _read_json_upload(file: UploadFile) -> dict:
    if not (file.filename or "").endswith(".json"):
        raise HTTPException(status_code=400, detail="Only .json files allowed")
    contents = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(contents) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413,
                            detail=f"File larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB")
    try:
        data = json.loads(contents)
    except (ValueError, UnicodeDecodeError) as e:
        raise HTTPException(status_code=400, detail=f"Not valid JSON: {e}")
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="Top level must be a JSON object")
    return data
