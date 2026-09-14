"""FastAPI dependencies shared by the routers.

`Bridge` resolves to the TotalMixOSCBridge that create_app() stored at
`app.state.bridge` - one object per app, no module-level singleton (#27
phase 4b). Handlers declare `bridge: Bridge`; a WebSocket handler reads
`websocket.app.state.bridge` directly (Request dependencies do not apply).
"""
from typing import Annotated

from fastapi import Depends, Request

from tmosc.bridge import TotalMixOSCBridge


def get_bridge(request: Request) -> TotalMixOSCBridge:
    return request.app.state.bridge


Bridge = Annotated[TotalMixOSCBridge, Depends(get_bridge)]
