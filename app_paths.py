"""Where the bridge keeps its per-install state: config JSON, logs, backups.

One rule, two modes - live STATE lives in ``data_dir()``, shipped RESOURCES
(templates, static web files) live in ``bundle_dir()``:

* SOURCE / DOCKER (the live Linux server): both are the repo root - the
  folder holding bridge.py - exactly where every file has always lived.
  Nothing moves and nothing is redirected, so an existing deployment behaves
  byte-for-byte as before.

* FROZEN (the PyInstaller Windows exe, ``sys.frozen`` set): the install
  folder is replaced on every upgrade and may be read-only, so live state
  moves to a per-user writable dir - ``%APPDATA%\tmosc-bridge`` on Windows
  (XDG / Library equivalents elsewhere). The bundled ``*.example.json``
  templates stay read-only inside the bundle and remain the startup
  fallbacks / "init from example" sources, same as today.

``TMOSC_DATA_DIR`` overrides the data dir in either mode (opt-in - e.g. a
systemd service running from a git checkout that wants state under /var/lib).

``prepare()`` runs once at import of config.py and is idempotent: it creates
the data dir, loads ``<data_dir>/config.env`` into the environment (real
environment variables win) and, when frozen, chdir()s into it so any
leftover relative path lands there too.

Files that live in ``data_dir()``: mappings.json, ufx2_channel_map.json,
ufx2_snapshot_map.json, backups/, bridge.log, osc_monitor.log, config.env.
"""
import os
import sys
from pathlib import Path

APP_NAME = "tmosc-bridge"
CONFIG_ENV = "config.env"

# Module-level so a test can steer the per-platform branch without touching
# sys.platform for the whole process.
_PLATFORM = sys.platform


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def bundle_dir() -> Path:
    """Read-only resources shipped with the app: the repo root from source;
    PyInstaller's extraction dir (``_internal`` for a onedir build) when
    frozen."""
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS", os.path.dirname(sys.executable)))
    return Path(__file__).resolve().parent


def _platform_data_home() -> Path:
    if _PLATFORM == "win32":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    elif _PLATFORM == "darwin":
        base = str(Path.home() / "Library" / "Application Support")
    else:
        base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / APP_NAME


def data_dir() -> Path:
    """Writable per-install state. Resolved on every call (cheap) so a test
    or a launcher can steer it through TMOSC_DATA_DIR."""
    override = os.environ.get("TMOSC_DATA_DIR", "").strip()
    if override:
        return Path(override).expanduser()
    if is_frozen():
        return _platform_data_home()
    return bundle_dir()


def data_path(name: str) -> str:
    """Absolute path of a live state file (mappings.json, bridge.log, ...)."""
    return str(data_dir() / name)


def example_path(name: str) -> str:
    """Absolute path of a shipped ``*.example.json`` template (read-only)."""
    return str(bundle_dir() / name)


def static_dir() -> str:
    """The web UI's static files (bundled next to the code)."""
    return str(bundle_dir() / "web" / "static")


def load_config_env(path=None) -> int:
    """Load ``KEY=VALUE`` lines from ``<data_dir>/config.env`` (or ``path``)
    into os.environ. Real environment variables always win. Blank lines and
    ``#`` comments are skipped; surrounding quotes are stripped. Returns the
    number of keys applied; a missing file is 0, never an error."""
    p = Path(path) if path else data_dir() / CONFIG_ENV
    try:
        text = p.read_text(encoding="utf-8-sig")
    except OSError:
        return 0
    applied = 0
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        if key and key not in os.environ:
            os.environ[key] = val
            applied += 1
    return applied


def prepare(chdir: bool = True) -> Path:
    """Idempotent startup hook (see module docstring). Returns the data dir."""
    d = data_dir()
    if d.resolve() != bundle_dir().resolve():
        d.mkdir(parents=True, exist_ok=True)
    load_config_env(d / CONFIG_ENV)
    # chdir only when frozen: there every import resolves through absolute
    # bundle paths, so it is safe, and it catches any leftover relative path.
    # From source (even with TMOSC_DATA_DIR set) the CWD is left alone - a
    # relative sys.path entry would otherwise break later lazy imports.
    if chdir and is_frozen():
        try:
            os.chdir(d)
        except OSError:
            pass
    return d
