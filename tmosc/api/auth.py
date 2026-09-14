"""Opt-in shared-token gate for state-changing HTTP requests and /ws."""
import os

from fastapi import Request
from fastapi.responses import JSONResponse

# Opt-in shared-token auth (critical-review S2). OFF by default: when the
# API_TOKEN env var is unset, this is a pure pass-through and nothing changes.
# When set, every state-changing request (POST/PUT/PATCH/DELETE) and the /ws
# socket must carry it (X-Api-Token header or ?token=). GETs - the UI and
# reads - stay open; the ear-safety risk is the writes. See docs/security.md.
API_TOKEN = os.environ.get("API_TOKEN", "").strip()
_AUTH_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


async def _auth_gate(request: Request, call_next):
    if API_TOKEN and request.method in _AUTH_METHODS:
        supplied = (request.headers.get("x-api-token")
                    or request.query_params.get("token"))
        if supplied != API_TOKEN:
            return JSONResponse({"detail": "unauthorized"}, status_code=401)
    return await call_next(request)


def ws_authorized(websocket) -> bool:
    """The /ws socket carries the token as ?token= (same gate as the writes)."""
    return not API_TOKEN or websocket.query_params.get("token") == API_TOKEN
