"""/ws: state broadcasts out, the knob stream in."""
import asyncio
import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from tmosc.bridge import ws_attach, ws_detach
from tmosc.api.auth import ws_authorized

router = APIRouter(tags=["ws"])


# ── WebSocket ────────────────────────────────────────────────────────────────

@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    if not ws_authorized(websocket):
        await websocket.close(code=1008)
        return
    await websocket.accept()
    bridge = websocket.app.state.bridge
    sender = ws_attach(websocket)    # per-client send queue + sender task (#32)
    try:
        while True:
            raw = await websocket.receive_text()
            # KNOB stream (continuous MIDI control): {"type":"knob","name","value"}
            # rides the existing socket - no HTTP round-trip per tick. Off the
            # event loop: knob_set does a UDP write + a feedback read.
            try:
                msg = json.loads(raw)
            except (ValueError, TypeError):
                continue
            if isinstance(msg, dict) and msg.get("type") == "knob":
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(
                    None, bridge.knob_set, str(msg.get("name", "")),
                    msg.get("value", 0.0), "midi")
    except WebSocketDisconnect:
        pass
    finally:
        sender.cancel()
        ws_detach(websocket)
