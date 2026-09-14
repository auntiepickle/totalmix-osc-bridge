"""Macro cards: list, trigger, switch, and the per-macro editor writes."""
import logging
import threading
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from tmosc.api.deps import Bridge
from tmosc.api import persistence
logger = logging.getLogger(__name__)

router = APIRouter(tags=["macros"])


# ── Macro Cards API ──────────────────────────────────────────────────────────

@router.get("/api/macros")
async def get_macros(bridge: Bridge):
    """Return all macros from the live bridge mappings (updated by live editor + reload).

    routing_label is derived at read time — persisted copies rot when the device
    renames outputs (an3_to_adat1_send kept saying "ADAT 1" after the rename)."""
    macros = bridge.mappings.get("macros", {})
    logger.info(f"✅ /api/macros → serving {len(macros)} macro cards to web client")
    out = {}
    for name, m in macros.items():
        entry = {**m, "routing_label": bridge.get_routing_label(name),
                 "last_fire": bridge.macro_health.get(name)}
        step = bridge._knob_step(m)
        if step is not None:
            entry["knob_value"] = bridge.knob_values.get(name)
            entry["device_value"] = bridge.knob_device_value(step)
            entry["enable_value"] = bridge.knob_enable_state(step)
            entry["companions"] = bridge.knob_companions(step)
        out[name] = entry
    return out


class TriggerBody(BaseModel):
    param: float = 0.5
    clock_bpm: Optional[float] = None


class SwitchBody(BaseModel):
    workspace: str
    snapshot: Optional[str] = None


@router.post("/api/trigger/{macro_name}")
async def trigger_macro(macro_name: str, bridge: Bridge, body: TriggerBody = TriggerBody()):
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


@router.post("/api/switch")
async def switch_workspace(body: SwitchBody, bridge: Bridge):
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


# ── Live Config Editor ────────────────────────────────────────────────────────

@router.post("/api/config/macros/{macro_name}")
@router.patch("/api/config/macros/{macro_name}")
async def upsert_macro(macro_name: str, request: Request, bridge: Bridge):
    """Create or update a single macro — used by the card editor and the
    New Macro flow. POST and PATCH behave identically (upsert); api.js has
    always POSTed here, so update-only PATCH semantics would 405 the editor."""
    try:
        data = await request.json()
        if not isinstance(data, dict):
            raise HTTPException(status_code=400, detail="Macro body must be a JSON object")
        if not persistence.MACRO_NAME_RE.match(macro_name):
            raise HTTPException(
                status_code=400,
                detail="Macro name must be 1-64 chars: letters, digits, _ or -",
            )
        created = macro_name not in bridge.mappings.setdefault("macros", {})
        bridge.mappings["macros"][macro_name] = persistence._strip_runtime(data)
        persistence._persist_mappings(bridge)
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


@router.delete("/api/config/macros/{macro_name}")
async def delete_macro(macro_name: str, bridge: Bridge):
    """Delete a macro (auto-backup first, hot-reloads into the bridge)."""
    if macro_name not in bridge.mappings.get("macros", {}):
        raise HTTPException(status_code=404, detail=f"Macro '{macro_name}' not found")
    del bridge.mappings["macros"][macro_name]
    bridge.macro_live_state.pop(macro_name, None)
    persistence._persist_mappings(bridge)
    logger.info(f"🗑 Macro '{macro_name}' deleted via editor")
    bridge.broadcast_state(macro_event={"type": "macro_deleted", "name": macro_name})
    return {"status": "success", "macro": macro_name}


@router.post("/api/config/macros-order")
async def reorder_macros(request: Request, bridge: Bridge):
    """Persist a new macro ordering (drag-to-reorder in the rack UI).
    Body: {"order": [every macro name exactly once]}. Reorders the dict
    IN PLACE (clear + reinsert) so held references stay valid."""
    body = await request.json()
    order = body.get("order") or []
    macros = bridge.mappings.get("macros", {})
    if sorted(order) != sorted(macros.keys()):
        raise HTTPException(status_code=400,
                            detail="order must list every macro exactly once")
    snapshot = {k: macros[k] for k in order}
    macros.clear()
    macros.update(snapshot)
    persistence._persist_mappings(bridge)
    return {"ok": True, "order": list(macros)}
