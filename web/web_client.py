import os
import re
import shutil
import datetime
from pathlib import Path
from typing import Optional
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, UploadFile, File, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
import json
import threading
import uvicorn
import logging
import asyncio

from bridge import bridge, ws_clients, MAPPINGS, SNAPSHOT_MAP

logger = logging.getLogger(__name__)

app = FastAPI(title="TotalMix OSC Bridge Web Client")

WEB_PORT = int(os.getenv("WEB_PORT", 8088))

static_dir = str(Path(__file__).parent / "static")
print(f"DEBUG: Mounting static files from: {static_dir}")
print(f"DEBUG: Files found: {list(Path(static_dir).glob('*'))}")

app.mount("/static", StaticFiles(directory=static_dir), name="static")


@app.middleware("http")
async def static_no_cache(request: Request, call_next):
    """Force revalidation of static assets. Browsers heuristically cache JS
    for hours, so after a deploy the UI kept running stale code until a hard
    refresh (observed live: a pulled midi.js fix was invisible). ETag/304
    keeps revalidation cheap."""
    response = await call_next(request)
    if request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache"
    return response


@app.get("/")
async def root():
    return RedirectResponse(url="/static/index.html")


@app.get("/index.html")
async def index_fallback():
    return RedirectResponse(url="/static/index.html")


# ── Macro Cards API ──────────────────────────────────────────────────────────

@app.get("/api/macros")
async def get_macros():
    """Return all macros from the live bridge mappings (updated by live editor + reload)."""
    macros = bridge.mappings.get("macros", {})
    logger.info(f"✅ /api/macros → serving {len(macros)} macro cards to web client")
    return macros


class TriggerBody(BaseModel):
    param: float = 0.5
    clock_bpm: Optional[float] = None


class SwitchBody(BaseModel):
    workspace: str
    snapshot: Optional[str] = None


@app.post("/api/trigger/{macro_name}")
async def trigger_macro(macro_name: str, body: TriggerBody = TriggerBody()):
    """Fire a macro — runs in a background thread so the response returns immediately.

    Accepts a JSON body with ``param`` (0.0–1.0) and an optional ``clock_bpm``
    (detected from the MIDI clock). When ``clock_bpm`` is provided the bridge
    substitutes it for any step that specifies ``"bpm": "clock"``.

    The browser gets progress bar timing from the ``macro_start`` WebSocket event.
    """
    if macro_name not in bridge.mappings.get("macros", {}):
        raise HTTPException(status_code=404, detail=f"Macro '{macro_name}' not found")
    logger.info(
        f"Web UI triggered macro → {macro_name} "
        f"(param={body.param:.3f}, clock_bpm={body.clock_bpm})"
    )
    threading.Thread(
        target=bridge.run_macro,
        args=(macro_name, body.param),
        kwargs={"clock_bpm": body.clock_bpm},
        daemon=True,
    ).start()
    return {"status": "accepted", "macro": macro_name, "param": body.param}


@app.post("/api/switch")
async def switch_workspace(body: SwitchBody):
    """Switch to a workspace and optionally a snapshot without firing a macro.

    Used by the click-to-switch buttons in the UI group headers. Runs in a
    daemon thread — the OSC + sleep sequence takes up to 1.3s.
    """
    if not bridge.osc_client:
        raise HTTPException(status_code=503, detail="OSC client not configured")
    threading.Thread(
        target=bridge.switch_to,
        args=(body.workspace,),
        kwargs={"snapshot": body.snapshot},
        daemon=True,
    ).start()
    return {"status": "accepted", "workspace": body.workspace, "snapshot": body.snapshot}


@app.get("/api/health")
async def get_health():
    """Return connection health for MQTT and OSC."""
    return {
        "mqtt_connected": getattr(bridge, "mqtt_connected", False),
        "osc_configured": bridge.osc_client is not None,
    }


@app.get("/api/test")
async def test_api():
    return {
        "status": "ok",
        "macros_count": len(MAPPINGS.get("macros", {})),
        "static_dir": static_dir,
        "web_port": WEB_PORT,
    }


@app.get("/api/status")
async def get_status():
    """Return currently-loaded config summary for the gear menu."""
    channel_map = bridge.channel_map or {}
    snap_map = bridge.snapshot_map or {}
    return {
        "macros": len(bridge.mappings.get("macros", {})),
        "channel_map_submixes": len(channel_map.get("submixes", {})),
        "snapshot_map_workspaces": len(snap_map),
        "workspace": bridge.current_workspace,
        "snapshot": bridge.current_snapshot,
        "mappings_is_example": bridge.mappings_is_example,
        "mappings_source": bridge.mappings_source,
        "channel_map_is_example": getattr(bridge, "channel_map_is_example", False),
    }


@app.get("/api/snapshot_map")
async def get_snapshot_map():
    """Return the loaded snapshot map (for client-side WS/SS validation)."""
    return bridge.snapshot_map or {}


# ── Live Config Editor ────────────────────────────────────────────────────────

MACRO_NAME_RE = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")


def _persist_mappings():
    """Write bridge.mappings to mappings.json (backup first)."""
    backup_json_files("mappings.json")
    target = os.path.join(os.path.dirname(__file__), "../mappings.json")
    with open(target, "w") as f:
        json.dump(bridge.mappings, f, indent=2)
    bridge.mappings_is_example = False
    bridge.mappings_source = "mappings.json"


@app.post("/api/config/macros/{macro_name}")
@app.patch("/api/config/macros/{macro_name}")
async def upsert_macro(macro_name: str, request: Request):
    """Create or update a single macro — used by the card editor and the
    New Macro flow. POST and PATCH behave identically (upsert); api.js has
    always POSTed here, so update-only PATCH semantics would 405 the editor."""
    try:
        data = await request.json()
        if not isinstance(data, dict):
            raise HTTPException(status_code=400, detail="Macro body must be a JSON object")
        if not MACRO_NAME_RE.match(macro_name):
            raise HTTPException(
                status_code=400,
                detail="Macro name must be 1-64 chars: letters, digits, _ or -",
            )
        created = macro_name not in bridge.mappings.setdefault("macros", {})
        bridge.mappings["macros"][macro_name] = data
        _persist_mappings()
        logger.info(f"✅ Macro '{macro_name}' {'created' if created else 'updated'} via editor")
        bridge.broadcast_state(macro_event={
            "type": "macro_created" if created else "macro_updated",
            "name": macro_name,
        })
        return {"status": "success", "macro": macro_name, "created": created}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Macro save failed: {e}")
        raise HTTPException(status_code=400, detail=str(e))


@app.delete("/api/config/macros/{macro_name}")
async def delete_macro(macro_name: str):
    """Delete a macro (auto-backup first, hot-reloads into the bridge)."""
    if macro_name not in bridge.mappings.get("macros", {}):
        raise HTTPException(status_code=404, detail=f"Macro '{macro_name}' not found")
    del bridge.mappings["macros"][macro_name]
    bridge.macro_live_state.pop(macro_name, None)
    _persist_mappings()
    logger.info(f"🗑 Macro '{macro_name}' deleted via editor")
    bridge.broadcast_state(macro_event={"type": "macro_deleted", "name": macro_name})
    return {"status": "success", "macro": macro_name}


@app.get("/api/config/mappings")
async def get_config_mappings():
    """Return full mappings.json content for the live editor."""
    return bridge.mappings


@app.post("/api/config/mappings")
async def save_config_mappings(request: Request):
    """Save JSON body directly to mappings.json and hot-reload into bridge."""
    try:
        data = await request.json()
        if "macros" not in data:
            raise HTTPException(status_code=400, detail="Invalid mappings.json: missing 'macros' key")
        backup_json_files("mappings.json")
        target = os.path.join(os.path.dirname(__file__), "../mappings.json")
        with open(target, "w") as f:
            json.dump(data, f, indent=2)
        bridge.mappings = data
        bridge.mappings_is_example = False
        bridge.mappings_source = "mappings.json"
        logger.info(f"✅ mappings.json saved via live editor ({len(data.get('macros', {}))} macros)")
        return {"status": "success", "macros": len(data.get("macros", {}))}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Config save failed: {e}")
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/config/channel_map")
async def get_config_channel_map():
    """Return full channel_map content for the live editor."""
    return bridge.channel_map or {}


@app.post("/api/config/channel_map")
async def save_config_channel_map(request: Request):
    """Save JSON body directly to ufx2_channel_map.json and hot-reload into bridge."""
    try:
        data = await request.json()
        if "submixes" not in data:
            raise HTTPException(status_code=400, detail="Invalid channel_map: missing 'submixes' key")
        backup_json_files("ufx2_channel_map.json")
        target = os.path.join(os.path.dirname(__file__), "../ufx2_channel_map.json")
        with open(target, "w") as f:
            json.dump(data, f, indent=2)
        bridge._load_channel_map()
        bridge.channel_map_is_example = False
        logger.info("✅ channel_map.json saved via live editor")
        return {"status": "success", "submixes": len(data.get("submixes", {}))}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Config save failed: {e}")
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/config/snapshot_map")
async def get_config_snapshot_map():
    """Return full snapshot_map content for the live editor."""
    return bridge.snapshot_map or {}


@app.post("/api/config/snapshot_map")
async def save_config_snapshot_map(request: Request):
    """Save snapshot_map to both local file and /app/config (SMB mount if present).
    Updates bridge.snapshot_map immediately so run_macro resolves slots correctly."""
    try:
        data = await request.json()
        if not isinstance(data, dict):
            raise HTTPException(status_code=400, detail="snapshot_map must be a JSON object")
        bridge.snapshot_map = data
        # Write to local app directory
        local_target = os.path.join(os.path.dirname(__file__), "../ufx2_snapshot_map.json")
        with open(local_target, "w") as f:
            json.dump(data, f, indent=2)
        # Also write to SMB mount if accessible
        smb_target = "/app/config/ufx2_snapshot_map.json"
        smb_written = False
        try:
            with open(smb_target, "w") as f:
                json.dump(data, f, indent=2)
            smb_written = True
        except Exception:
            pass  # SMB mount not available in dev
        workspaces = sum(1 for k, v in data.items() if not k.startswith("_") and isinstance(v, dict))
        logger.info(f"✅ snapshot_map saved ({workspaces} workspaces, SMB={smb_written})")
        return {"status": "success", "workspaces": workspaces, "smb_written": smb_written}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"snapshot_map save failed: {e}")
        raise HTTPException(status_code=400, detail=str(e))


# ── Device Capture + Discovery ───────────────────────────────────────────────

@app.get("/api/device/state")
async def get_device_state():
    """Live TotalMix state captured from OSC feedback (submixes, channels,
    raw address dump). Requires the OSC listener and TotalMix's OSC
    'Port outgoing' pointed at this server."""
    if bridge.osc_listener is None or not bridge.osc_listener.running:
        raise HTTPException(status_code=503, detail="OSC listener not running")
    return bridge.osc_listener.state.to_dict()


class DiscoverBody(BaseModel):
    # Stereo-paired outputs take two indices each, so real submixes can sit
    # well past 16 (seen live: ADAT outputs above index 16 on a UFX II)
    submix_count: int = 32
    settle_s: float = 1.0


@app.post("/api/device/discover")
async def start_discovery(body: DiscoverBody = DiscoverBody()):
    """Walk all output submixes (/setSubmix 1..N) and build a channel-map
    draft from the feedback. Runs in a background thread — poll
    GET /api/device/discovery or watch discovery_* WebSocket events."""
    if bridge.osc_client is None:
        raise HTTPException(status_code=503, detail="OSC client not configured")
    if bridge.osc_listener is None or not bridge.osc_listener.running:
        raise HTTPException(status_code=503, detail="OSC listener not running")
    if bridge.discovery_state.get("status") == "running":
        raise HTTPException(status_code=409, detail="Discovery already running")

    bridge.discovery_state = {"status": "running", "progress": 0,
                              "total": body.submix_count}

    def _progress(i, total, label):
        bridge.discovery_state.update({"progress": i, "current_label": label})
        bridge.broadcast_state(macro_event={
            "type": "discovery_progress", "progress": i, "total": total,
            "label": label,
        })

    def _run():
        from discovery import discover_channel_map
        try:
            channel_map, walk_log = discover_channel_map(
                bridge.osc_client, bridge.osc_listener,
                submix_count=body.submix_count, settle_s=body.settle_s,
                progress_cb=_progress,
            )
            draft_path = os.path.join(os.path.dirname(__file__),
                                      "../discovered_channel_map.json")
            with open(draft_path, "w") as f:
                json.dump(channel_map, f, indent=2)
            bridge.discovery_state = {
                "status": "done",
                "channel_map": channel_map,
                "walk_log": walk_log,
                "submixes": len(channel_map["submixes"]),
            }
            logger.info(f"✅ Discovery draft saved → discovered_channel_map.json "
                        f"({len(channel_map['submixes'])} submixes)")
            bridge.broadcast_state(macro_event={
                "type": "discovery_complete",
                "submixes": len(channel_map["submixes"]),
            })
        except Exception as e:
            logger.error(f"Discovery failed: {e}", exc_info=True)
            bridge.discovery_state = {"status": "error", "error": str(e)}
            bridge.broadcast_state(macro_event={
                "type": "discovery_error", "error": str(e),
            })

    threading.Thread(target=_run, daemon=True).start()
    return {"status": "started", "submix_count": body.submix_count,
            "estimated_s": body.submix_count * body.settle_s}


@app.get("/api/device/discovery")
async def get_discovery():
    """Status and result of the last discovery run."""
    return bridge.discovery_state


@app.post("/api/device/discovery/apply")
async def apply_discovery():
    """Promote the last discovery result to the live ufx2_channel_map.json."""
    state = bridge.discovery_state
    if state.get("status") != "done" or "channel_map" not in state:
        raise HTTPException(status_code=409, detail="No completed discovery to apply")
    backup_json_files("ufx2_channel_map.json")
    target = os.path.join(os.path.dirname(__file__), "../ufx2_channel_map.json")
    with open(target, "w") as f:
        json.dump(state["channel_map"], f, indent=2)
    bridge._load_channel_map()
    bridge.channel_map_is_example = False
    submixes = len(state["channel_map"]["submixes"])
    logger.info(f"✅ Discovered channel map applied ({submixes} submixes)")
    return {"status": "success", "submixes": submixes}


# ── WebSocket ────────────────────────────────────────────────────────────────

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    ws_clients.append(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        if websocket in ws_clients:
            ws_clients.remove(websocket)


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
    backup_dir = os.path.join(os.path.dirname(__file__), "../backups")
    os.makedirs(backup_dir, exist_ok=True)
    for fn in files:
        src = os.path.join(os.path.dirname(__file__), "../" + fn)
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(backup_dir, f"{fn}.{timestamp}"))
            logger.info(f"✅ Auto-backup: {fn}.{timestamp}")


@app.post("/api/upload/mappings")
async def upload_mappings(file: UploadFile = File(...)):
    if not file.filename.endswith(".json"):
        raise HTTPException(status_code=400, detail="Only .json files allowed")
    try:
        backup_json_files("mappings.json")
        contents = await file.read()
        data = json.loads(contents)
        if "macros" not in data:
            raise HTTPException(status_code=400, detail="Invalid mappings.json format")
        target = os.path.join(os.path.dirname(__file__), "../mappings.json")
        with open(target, "w") as f:
            json.dump(data, f, indent=2)
        bridge.mappings = data
        logger.info(f"✅ mappings.json uploaded + reloaded ({len(data.get('macros', {}))} macros)")
        return {"status": "success", "message": "mappings.json updated and reloaded"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Upload failed: {e}")
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/upload/channel_map")
async def upload_channel_map(file: UploadFile = File(...)):
    if not file.filename.endswith(".json"):
        raise HTTPException(status_code=400, detail="Only .json files allowed")
    try:
        backup_json_files("ufx2_channel_map.json")
        contents = await file.read()
        data = json.loads(contents)
        if "submixes" not in data:
            raise HTTPException(status_code=400, detail="Invalid ufx2_channel_map.json format")
        target = os.path.join(os.path.dirname(__file__), "../ufx2_channel_map.json")
        with open(target, "w") as f:
            json.dump(data, f, indent=2)
        bridge._load_channel_map()
        logger.info("✅ ufx2_channel_map.json uploaded + reloaded")
        return {"status": "success", "message": "channel map updated and reloaded"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Upload failed: {e}")
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/config/channel_map/init-from-example")
async def init_channel_map_from_example():
    """Copy ufx2_channel_map.example.json → ufx2_channel_map.json and reload."""
    base = os.path.dirname(__file__)
    example = os.path.join(base, "../ufx2_channel_map.example.json")
    target  = os.path.join(base, "../ufx2_channel_map.json")
    try:
        if not os.path.exists(example):
            raise HTTPException(status_code=404, detail="ufx2_channel_map.example.json not found")
        shutil.copy2(example, target)
        bridge._load_channel_map()
        bridge.channel_map_is_example = False
        submixes = len((bridge.channel_map or {}).get("submixes", {}))
        logger.info(f"✅ ufx2_channel_map.json initialized from example ({submixes} submixes)")
        return {"status": "success", "submixes": submixes}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Init channel_map from example failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/config/mappings/init-from-example")
async def init_mappings_from_example():
    """Copy mappings.example.json → mappings.json and reload into the bridge.
    Called from the UI when no mappings.json exists on the server."""
    base = os.path.dirname(__file__)
    example = os.path.join(base, "../mappings.example.json")
    target  = os.path.join(base, "../mappings.json")
    try:
        if not os.path.exists(example):
            raise HTTPException(status_code=404, detail="mappings.example.json not found")
        shutil.copy2(example, target)
        with open(target, "r") as f:
            data = json.load(f)
        bridge.mappings = data
        bridge.mappings_is_example = False
        bridge.mappings_source = "mappings.json"
        logger.info(f"✅ mappings.json initialized from example ({len(data.get('macros', {}))} macros)")
        return {"status": "success", "macros": len(data.get("macros", {}))}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Init from example failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/reload")
async def reload_bridge():
    """Reload mappings.json from disk into the running bridge."""
    try:
        target = os.path.join(os.path.dirname(__file__), "../mappings.json")
        with open(target, "r") as f:
            data = json.load(f)
        bridge.mappings = data
        bridge.mappings_is_example = False
        bridge.mappings_source = "mappings.json"
        logger.info(f"✅ Bridge reloaded — {len(data.get('macros', {}))} macros")
        return {"status": "success", "macros": len(data.get("macros", {}))}
    except Exception as e:
        logger.error(f"Reload failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ── Startup ──────────────────────────────────────────────────────────────────

def _keepalive():
    import time
    while True:
        time.sleep(60)


@app.on_event("startup")
async def startup_event():
    threading.Thread(target=_keepalive, daemon=True).start()
    bridge.start_mqtt()
    bridge.start_osc_listener()
    bridge.main_loop = asyncio.get_running_loop()
    print(f"🚀 TotalMix Web Client + Bridge started (port {WEB_PORT}) — MQTT ACTIVE")
