"""FastAPI application: create_app() factory, lifespan, middleware, static mount.

Routes live in tmosc/api/routes/ (one APIRouter per area), config persistence
in tmosc/api/persistence.py, the shared-token gate in tmosc/api/auth.py.
`app` (module level) is what `uvicorn tmosc.api.app:app`, `python -m tmosc`,
web/web_client.py and the tests import; it owns the bridge built by
tmosc.bridge.build_bridge() at `app.state.bridge`. `create_app(bridge=...)`
builds an app around any bridge (tests: an isolated one on a fake OSC
client). The names in __all__ beyond `app` are compatibility re-exports
(tests import them from here; patch them on the module that owns them).
"""
import os
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
import threading
import logging
import asyncio

from tmosc.bridge import TotalMixOSCBridge, build_bridge
from tmosc.logsetup import configure_logging
import tmosc.app_paths as app_paths
from tmosc.api.auth import API_TOKEN, _auth_gate
from tmosc.api.persistence import (
    RUNTIME_FIELDS, MACRO_NAME_RE, MAX_UPLOAD_BYTES, _strip_runtime, _sanitize_mappings,
    _persist_mappings, backup_json_files, _atomic_write_json, _read_json_upload)
from tmosc.api.routes import health, macros, midi, knobs, device, config, ws
logger = logging.getLogger(__name__)

__all__ = [
    "app", "create_app", "lifespan", "WEB_PORT",
    # compatibility re-exports
    "API_TOKEN", "RUNTIME_FIELDS", "MACRO_NAME_RE", "MAX_UPLOAD_BYTES", "_strip_runtime",
    "_sanitize_mappings", "_persist_mappings", "backup_json_files", "_atomic_write_json",
    "_read_json_upload",
]

WEB_PORT = int(os.getenv("WEB_PORT", 8088))


def _keepalive():
    import time
    while True:
        time.sleep(60)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Process lifecycle. uvicorn runs this around serving (docker CMD,
    `python -m tmosc`); a bare TestClient(app) never enters it, so the
    tests run without a broker, listeners or network."""
    bridge: TotalMixOSCBridge = app.state.bridge
    threading.Thread(target=_keepalive, daemon=True).start()
    bridge.start_mqtt()
    bridge.start_osc_listener()
    bridge.start_global_osc()   # #25: no-op unless enabled via env
    bridge.main_loop = asyncio.get_running_loop()
    try:
        import tmosc.discovery as discovery
        discovery.start(WEB_PORT, int(os.getenv("DISCOVERY_PORT", WEB_PORT)))
    except Exception as e:
        logger.warning(f"discovery responder not started: {e}")
    print(f"🚀 TotalMix Web Client + Bridge started (port {WEB_PORT}) — MQTT ACTIVE")
    yield
    # Graceful stop (docker stop/restart = SIGTERM fires this) tears down the
    # global transport, which sets _knob_watch_stop and lets the duck
    # supervisor run its restore-on-exit so no send is left ducked
    # (critical-review C1). A hard kill -9 / power loss cannot be caught.
    try:
        bridge.stop_global_osc()
    except Exception:
        logger.exception("shutdown: stop_global_osc failed")


async def static_no_cache(request: Request, call_next):
    """Force revalidation of static assets. Browsers heuristically cache JS
    for hours, so after a deploy the UI kept running stale code until a hard
    refresh (observed live: a pulled midi.js fix was invisible). ETag/304
    keeps revalidation cheap."""
    response = await call_next(request)
    if request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache"
    return response


def create_app(bridge: TotalMixOSCBridge | None = None) -> FastAPI:
    """Build the app around `bridge` (default: build_bridge() from the data
    dir + env). Logging is configured here so the first thing a process
    logs - the loaders inside build_bridge() - already lands in bridge.log."""
    configure_logging()
    if bridge is None:
        bridge = build_bridge()
    app = FastAPI(title="TotalMix OSC Bridge Web Client", lifespan=lifespan)
    app.state.bridge = bridge     # routers reach it via tmosc.api.deps.Bridge

    static_dir = app_paths.static_dir()
    print(f"DEBUG: Mounting static files from: {static_dir}")
    print(f"DEBUG: Files found: {list(Path(static_dir).glob('*'))}")
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    # Register the opt-in token gate FIRST, the no-cache header second: Starlette
    # wraps middleware in reverse order of registration (last added = outermost).
    app.middleware("http")(_auth_gate)
    app.middleware("http")(static_no_cache)

    for r in (health.router, macros.router, midi.router, knobs.router,
              device.router, config.router, ws.router):
        app.include_router(r)
    return app


app = create_app()
