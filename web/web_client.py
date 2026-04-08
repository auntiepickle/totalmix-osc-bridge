import os
from pathlib import Path
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse
import json
import threading
import uvicorn
from bridge import bridge, ws_clients, MAPPINGS

app = FastAPI(title="TotalMix OSC Bridge Web Client")

# === CONFIGURABLE WEB PORT (single source: .env) ===
WEB_PORT = int(os.getenv("WEB_PORT", 8088))

# ROBUST STATIC MOUNT - absolute path guarantees it works from /app/web/
static_dir = str(Path(__file__).parent / "static")
app.mount("/static", StaticFiles(directory=static_dir), name="static")

@app.get("/")
async def root():
    # Auto-redirect root → beautiful UI
    return RedirectResponse(url="/static/index.html")

@app.get("/api/macros")
async def get_macros():
    return MAPPINGS.get("macros", {})

@app.post("/api/trigger/{macro_name}")
async def trigger_macro(macro_name: str, param: float = 1.0):
    bridge.run_macro(macro_name, param)
    return {"status": "fired", "macro": macro_name, "param": param}

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
    print(f"🚀 TotalMix Web Client + Bridge started (port {WEB_PORT})")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=WEB_PORT)