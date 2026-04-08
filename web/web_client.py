from fastapi import FastAPI, WebSocket, WebSocketDisconnect, BackgroundTasks
from fastapi.staticfiles import StaticFiles
import json
import threading
import uvicorn
from bridge import bridge, ws_clients, MAPPINGS

app = FastAPI(title="TotalMix OSC Bridge Web Client")

app.mount("/static", StaticFiles(directory="web/static"), name="static")

@app.get("/")
async def root():
    return {"message": "TotalMix Web Client running — open /static/index.html"}

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
    print("🚀 TotalMix Web Client + Bridge started (port 8090)")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8090)