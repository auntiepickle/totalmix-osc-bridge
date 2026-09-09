"""Entry point for the frozen (PyInstaller) Windows build of the bridge.

    tmosc-bridge.exe             run the server (the same app as `uvicorn web.web_client:app`)
    tmosc-bridge.exe --version   print the build version
    tmosc-bridge.exe --data-dir  print where live state lives

Live state (mappings.json, ufx2_*.json, backups/, logs, config.env) lives in
app_paths.data_dir() - %APPDATA%/tmosc-bridge on Windows. Configuration is
read from <data_dir>/config.env (written by the installer wizard); real
environment variables win. Runs from source too: `python bridge_main.py`.
"""
import multiprocessing
import os
import sys


def version() -> str:
    """Build version - a VERSION file bundled by CI from the release tag."""
    import app_paths
    try:
        return (app_paths.bundle_dir() / "VERSION").read_text(encoding="utf-8").strip() or "dev"
    except OSError:
        return "dev"


def _utf8_console():
    # The bridge logs emoji; a cp1252 Windows console would raise on them.
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    multiprocessing.freeze_support()
    _utf8_console()
    import app_paths
    data = app_paths.prepare()          # data dir + config.env + chdir (frozen)
    if "--version" in argv:
        print(version())
        return 0
    if "--data-dir" in argv:
        print(data)
        return 0

    import uvicorn
    from web.web_client import app, WEB_PORT
    host = os.getenv("WEB_HOST", "0.0.0.0")
    print(f"tmosc-bridge {version()} | data: {data} | http://{host}:{WEB_PORT}", flush=True)
    # In-process app object (no import-string / reload / worker machinery -
    # none of that works frozen). Pure-Python HTTP + websockets stacks only.
    uvicorn.run(app, host=host, port=WEB_PORT,
                log_level=os.getenv("LOG_LEVEL", "info").lower(),
                reload=False, workers=1, loop="asyncio", http="h11", ws="websockets")
    return 0


if __name__ == "__main__":
    sys.exit(main())
