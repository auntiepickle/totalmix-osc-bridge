import os
from pathlib import Path
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse
import json
import threading
import uvicorn
import logging
import asyncio  # ← ADD THIS LINE (fixes the NameError)

from bridge import bridge, ws_clients, MAPPINGS

# Setup logger so the M2 functions can log cleanly
logger = logging.getLogger(__name__)

app = FastAPI(title="TotalMix OSC Bridge Web Client")

# === CONFIGURABLE WEB PORT ===
WEB_PORT = int(os.getenv("WEB_PORT", 8088))

# ROBUST STATIC MOUNT (absolute path)
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

# ─────────────────────────────────────────────────────────────
# M2: Macro Cards API (Web Client v1) – CLEAN VERSION
# ─────────────────────────────────────────────────────────────
@app.get("/api/macros")
async def get_macros():
    """Return ALL macros from mappings.json so the web UI can render the card grid."""
    macros = MAPPINGS.get("macros", {})
    logger.info(f"✅ /api/macros → serving {len(macros)} macro cards to web client")
    return macros


@app.post("/api/trigger/{macro_name}")
async def trigger_macro(macro_name: str, param: float = 0.5):
    """Fire a macro when user clicks a card in the web UI."""
    if macro_name in MAPPINGS.get("macros", {}):
        logger.info(f"Web UI clicked macro → {macro_name} (param={param:.3f})")
        bridge.run_macro(macro_name, param)
        return {"status": "success", "macro": macro_name, "param": param}
    else:
        logger.warning(f"Unknown macro from UI: {macro_name}")
        raise HTTPException(status_code=404, detail=f"Macro '{macro_name}' not found")


@app.get("/api/test")
async def test_api():
    return {
        "status": "ok",
        "macros_count": len(MAPPINGS.get("macros", {})),
        "static_dir": static_dir,
        "web_port": WEB_PORT
    }


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


def start_bridge():
    import time
    while True:
        time.sleep(60)


@app.on_event("startup")
async def startup_event():
    threading.Thread(target=start_bridge, daemon=True).start()
    # NEW: start MQTT in web mode too
    bridge.start_mqtt()
    
    # ←←← THIS LINE NOW WORKS (asyncio is imported)
    bridge.main_loop = asyncio.get_running_loop()
    
    print(f"🚀 TotalMix Web Client + Bridge started (port {WEB_PORT}) - MQTT ACTIVE")