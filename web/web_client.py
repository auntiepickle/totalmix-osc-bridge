import os
import shutil
import datetime
from pathlib import Path
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, UploadFile, File, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse
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


@app.post("/api/trigger/{macro_name}")
async def trigger_macro(macro_name: str, param: float = 0.5):
    """Fire a macro — runs in a background thread so the response returns immediately.
    The browser gets progress bar timing from the macro_start WebSocket event."""
    if macro_name not in bridge.mappings.get("macros", {}):
        raise HTTPException(status_code=404, detail=f"Macro '{macro_name}' not found")
    logger.info(f"Web UI triggered macro → {macro_name} (param={param:.3f})")
    threading.Thread(target=bridge.run_macro, args=(macro_name, param), daemon=True).start()
    return {"status": "accepted", "macro": macro_name, "param": param}


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

@app.patch("/api/config/macros/{macro_name}")
async def patch_macro(macro_name: str, request: Request):
    """Update a single macro in-place — used by the card inline editor."""
    try:
        data = await request.json()
        if macro_name not in bridge.mappings.get("macros", {}):
            raise HTTPException(status_code=404, detail=f"Macro '{macro_name}' not found")
        backup_json_files()
        bridge.mappings["macros"][macro_name] = data
        target = os.path.join(os.path.dirname(__file__), "../mappings.json")
        with open(target, "w") as f:
            json.dump(bridge.mappings, f, indent=2)
        logger.info(f"✅ Macro '{macro_name}' updated via inline editor")
        return {"status": "success", "macro": macro_name}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Inline macro save failed: {e}")
        raise HTTPException(status_code=400, detail=str(e))


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
        backup_json_files()
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
        backup_json_files()
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

def backup_json_files():
    """Auto-backup mappings + channel map before every upload."""
    timestamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_dir = os.path.join(os.path.dirname(__file__), "../backups")
    os.makedirs(backup_dir, exist_ok=True)
    for fn in ["mappings.json", "ufx2_channel_map.json"]:
        src = os.path.join(os.path.dirname(__file__), "../" + fn)
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(backup_dir, f"{fn}.{timestamp}"))
            logger.info(f"✅ Auto-backup: {fn}.{timestamp}")


@app.post("/api/upload/mappings")
async def upload_mappings(file: UploadFile = File(...)):
    if not file.filename.endswith(".json"):
        raise HTTPException(status_code=400, detail="Only .json files allowed")
    try:
        backup_json_files()
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
        backup_json_files()
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
    bridge.main_loop = asyncio.get_running_loop()
    print(f"🚀 TotalMix Web Client + Bridge started (port {WEB_PORT}) — MQTT ACTIVE")
