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
    """Return all macros from the live bridge mappings (updated by live editor + reload).

    routing_label is derived at read time — persisted copies rot when the device
    renames outputs (an3_to_adat1_send kept saying "ADAT 1" after the rename)."""
    macros = bridge.mappings.get("macros", {})
    logger.info(f"✅ /api/macros → serving {len(macros)} macro cards to web client")
    return {
        name: {**m, "routing_label": bridge.get_routing_label(name)}
        for name, m in macros.items()
    }


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
    listener = bridge.osc_listener
    listening = listener is not None and listener.running
    bank_width = listener.state.bank_width if listening else None
    live_strips = listener.state.real_strip_count if listening else None
    # Highest channel the map expects — if the live bank is narrower, part
    # of the rig is invisible to routing (per-workspace TotalMix setting)
    map_max_channel = max(
        (s.get("channel", 0)
         for sub in channel_map.get("submixes", {}).values()
         for s in sub.get("sends", {}).values()),
        default=0,
    )
    # How many INPUT-row channels the map knows per submix — compared against
    # live_strip_count, which only ever reflects the currently selected row
    # (input in practice). Counting playback sends here masked a real stale
    # map once: 17 live vs '39 total' stayed silent while 16 mapped input
    # channels didn't exist on the device.
    map_strip_count = max(
        (sum(1 for s in sub.get("sends", {}).values() if s.get("row", 1) == 1)
         for sub in channel_map.get("submixes", {}).values()),
        default=0,
    )
    # NOTE (2026-08-20 architecture review): input strip counts change with
    # EVERY snapshot (pairing is per-snapshot) — that is normal operation,
    # not drift. An earlier version auto-walked here and made snapshot
    # switches trigger 90s walks in a loop. Strip counts are reported for
    # telemetry only; nothing acts on them.
    return {
        "osc_bank_width": bank_width,
        "live_strip_count": live_strips,
        "channel_map_max_channel": map_max_channel,
        "channel_map_strip_count": map_strip_count,
        # workspace/snapshot below are the bridge's commanded belief;
        # state_confirmed says whether the device confirmed the last switch
        "state_confirmed": getattr(bridge, "state_confirmed", None),
        # Live-vs-map drift (output side): False drives the UI banner
        "map_matches_device": getattr(bridge, "map_matches_device", None),
        "live_submix_count": getattr(bridge, "live_submix_count", None),
        "discovery_status": bridge.discovery_state.get("status"),
        "input_widths_coverage": getattr(bridge, "input_widths_coverage", None),
        "device_probe": getattr(bridge, "last_probe", None),
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

# run_macro merges these into live state; the browser's macros{} object carries
# them, so editor saves used to round-trip them into mappings.json. Strip on
# every save path — must mirror RUNTIME_FIELDS in web/static/ui.js.
RUNTIME_FIELDS = (
    "name", "value", "progress", "lfo_active",
    "last_trigger", "osc_preview", "midi_trigger", "routing_label",
)


def _strip_runtime(macro: dict) -> dict:
    return {k: v for k, v in macro.items() if k not in RUNTIME_FIELDS}


def _sanitize_mappings(data: dict) -> dict:
    macros = data.get("macros")
    if isinstance(macros, dict):
        data = {**data, "macros": {
            name: _strip_runtime(m) if isinstance(m, dict) else m
            for name, m in macros.items()
        }}
    return data


def _persist_mappings():
    """Write bridge.mappings to mappings.json (backup first).

    Sanitizes ALL macros, not just the one being saved — a dirty file loaded
    at startup would otherwise re-persist its legacy runtime fields on every
    per-macro save forever (server smoke finding, 2026-08-20)."""
    backup_json_files("mappings.json")
    bridge.mappings = _sanitize_mappings(bridge.mappings)
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
        bridge.mappings["macros"][macro_name] = _strip_runtime(data)
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
        data = _sanitize_mappings(data)
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
        # Backup first (every other config write does), and only adopt the
        # new map in memory AFTER the disk write succeeds — assigning first
        # left memory and disk divergent on a failed write (review finding)
        backup_json_files("ufx2_snapshot_map.json")
        local_target = os.path.join(os.path.dirname(__file__), "../ufx2_snapshot_map.json")
        with open(local_target, "w") as f:
            json.dump(data, f, indent=2)
        bridge.snapshot_map = data
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
    include_playback: bool = True


def _launch_walk(submix_count=32, settle_s=1.0, include_playback=True,
                 auto_apply=False):
    """Start the discovery walk thread (shared by the endpoint and the
    auto-walk path). Caller must have verified no walk is running."""
    bridge.discovery_state = {"status": "running", "progress": 0,
                              "total": submix_count}

    def _progress(i, total, label):
        bridge.discovery_state.update({"progress": i, "current_label": label})
        bridge.broadcast_state(macro_event={
            "type": "discovery_progress", "progress": i, "total": total,
            "label": label,
        })

    def _run():
        from discovery import discover_channel_map
        try:
            # Device lock: a walk's /setSubmix stream must not interleave
            # with a concurrent macro's aim/writes (review finding [1] —
            # the walk was the one sender left outside the lock)
            with bridge._device_lock:
                channel_map, walk_log = discover_channel_map(
                    bridge.osc_client, bridge.osc_listener,
                    submix_count=submix_count, settle_s=settle_s,
                    progress_cb=_progress, include_playback=include_playback,
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
            if auto_apply:
                try:
                    res = _apply_discovery_result(force=False)
                    logger.info(f"🤖 auto-walk applied — "
                                f"{res.get('submixes')} submixes; layout "
                                f"learned, no click needed")
                except HTTPException as e:
                    logger.error(f"auto-walk apply refused: {e.detail} — "
                                 f"the drift banner stays up with the "
                                 f"manual button")
        except Exception as e:
            logger.error(f"Discovery failed: {e}", exc_info=True)
            bridge.discovery_state = {"status": "error", "error": str(e)}
            bridge.broadcast_state(macro_event={
                "type": "discovery_error", "error": str(e),
            })

    threading.Thread(target=_run, daemon=True).start()


# Auto-walk unknown layouts (default ON): the drift banner demanded a
# click per never-walked layout; the user wants zero clicks. Guarded by a
# cooldown so a failing layout cannot walk-loop.
AUTO_WALK = os.getenv("AUTO_WALK_NEW_LAYOUTS", "true").strip().lower() == "true"
_auto_walk_state = {"t": 0.0}


def _auto_walk():
    if not AUTO_WALK:
        return
    import time as _time
    now = _time.time()
    if now - _auto_walk_state["t"] < 120:
        return
    if bridge.discovery_state.get("status") == "running":
        return
    if bridge.osc_client is None or bridge.osc_listener is None             or not bridge.osc_listener.running:
        return
    _auto_walk_state["t"] = now
    logger.info("🤖 Unknown layout — auto-walking it now "
                "(AUTO_WALK_NEW_LAYOUTS=true; set false to get the manual "
                "banner button instead)")
    _launch_walk(auto_apply=True)


bridge.auto_walk_cb = _auto_walk


class SweepBody(BaseModel):
    rows: list = ["inputs", "outputs"]
    settle_s: float = 0.3
    reset: bool = False


@app.post("/api/device/sweep")
async def start_sweep(body: SweepBody = SweepBody()):
    """Measure the physical hardware-channel table (#24): /setBankStart
    0..33 + row-mirror nudge + /2/trackname read per offset, both rows.
    Read-only w.r.t. mixer state; never sends /setSubmix. Replaces the
    discovery walk as the learning mechanism."""
    if bridge.osc_client is None:
        raise HTTPException(status_code=503, detail="OSC client not configured")
    if bridge.osc_listener is None or not bridge.osc_listener.running:
        raise HTTPException(status_code=503, detail="OSC listener not running")
    if bridge.sweep_state.get("status") == "running":
        raise HTTPException(status_code=409, detail="Sweep already running")
    if bridge.discovery_state.get("status") == "running":
        raise HTTPException(status_code=409, detail="Discovery walk running")
    threading.Thread(
        target=bridge.run_sweep,
        kwargs={"rows": tuple(body.rows), "settle_s": body.settle_s,
                "reset": body.reset},
        daemon=True,
    ).start()
    return {"status": "started", "rows": body.rows,
            "estimated_s": round(len(body.rows) * 34 * (body.settle_s + 0.2), 1)}


@app.get("/api/device/sweep")
async def get_sweep_status():
    return bridge.sweep_state


@app.get("/api/device/physical_table")
async def get_physical_table():
    table = (bridge.channel_map or {}).get("physical_table")
    if not table:
        raise HTTPException(status_code=404,
                            detail="No physical table — run POST /api/device/sweep")
    return table


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

    _launch_walk(body.submix_count, body.settle_s, body.include_playback)
    # Playback capture adds two extra settles per REAL submix; the real count
    # is unknown up front, so estimate the worst case (every index real)
    per_index = body.settle_s * (3 if body.include_playback else 1)
    return {"status": "started", "submix_count": body.submix_count,
            "estimated_s": body.submix_count * per_index}


class WidthsBody(BaseModel):
    widths: dict
    layout: Optional[list] = None


@app.post("/api/device/widths")
async def set_widths(body: WidthsBody):
    """Store a verified width map (strip name -> 1|2) for one layout.

    Widths are layout-scoped (#16): the same strip name can have different
    widths in different snapshots, so entries are keyed by the layout's
    row-1 name set — by default the LIVE one, or an explicit `layout` list
    of strip names. EQ aiming consults the map matching the live layout at
    fire time, so hand-entered or fingerprint-derived widths stop rotting
    when snapshots rotate."""
    bad = {k: v for k, v in body.widths.items()
           if isinstance(v, bool) or v not in (1, 2)}
    if bad:
        raise HTTPException(status_code=422,
                            detail=f"widths must be 1 or 2: {bad}")
    if body.layout:
        names = [str(n).strip() for n in body.layout if str(n).strip()]
    else:
        live = _live_row1_names()
        if not live:
            raise HTTPException(
                status_code=409,
                detail="listener has no live layout to key the widths to — "
                       "run the connection check (or wiggle a fader) so the "
                       "bank is known, or pass an explicit layout: [names]")
        names = sorted(live)
    key = bridge._layout_key_from_names(names)
    uncovered = sorted(n for n in names if n not in body.widths)
    # Never inherit the EXAMPLE map: persisting onto it would write the
    # example's submixes/indices out as the user's real config, and stale
    # example indices feed the /setSubmix path (review finding)
    cm = ({"submixes": {}} if getattr(bridge, "channel_map_is_example", False)
          else bridge.channel_map) or {"submixes": {}}
    cm.setdefault("width_maps", {})[key] = dict(body.widths)
    _persist_channel_map(cm)
    total = sum(body.widths.get(n, 0) for n in names)
    logger.info(f"✅ width map stored for a {len(names)}-strip layout "
                f"({total} hw channels covered, {len(uncovered)} uncovered)")
    return {"status": "success", "layout_strips": len(names),
            "hw_channels_covered": total, "uncovered": uncovered}


@app.post("/api/device/probe")
async def probe_device():
    """Liveness probe: sends a state-changing command and confirms feedback.
    Briefly flips the selected submix (restored after) — user-triggered only,
    never on a timer. Silence alone is NOT evidence of a freeze."""
    result = bridge.probe_device()
    if result.get("alive") is None:
        raise HTTPException(status_code=503, detail=result.get("reason", "probe unavailable"))
    return result


@app.get("/api/device/discovery")
async def get_discovery():
    """Status and result of the last discovery run."""
    return bridge.discovery_state


def _row1_names(channel_map):
    """The input-strip name set — the layout identity used to decide whether
    channel_widths may carry across a discovery apply."""
    return {str(s.get("name", "")).strip()
            for sub in (channel_map or {}).get("submixes", {}).values()
            for s in sub.get("sends", {}).values() if s.get("row", 1) == 1}


def _persist_channel_map(cm):
    """Write the channel map to disk (backup first) and hot-reload it."""
    backup_json_files("ufx2_channel_map.json")
    target = os.path.join(os.path.dirname(__file__), "../ufx2_channel_map.json")
    with open(target, "w") as f:
        json.dump(cm, f, indent=2)
    bridge._load_channel_map()


def _carry_widths(old_map, new_map):
    """Preserve channel_widths across a discovery apply ONLY when the strip
    layout is unchanged. Widths are LAYOUT-scoped (hardware-proven: RE-101 is
    width 2 in one snapshot and width 1 in another), so carrying them across
    a layout change could mis-aim page-2 writes — refusal is safe, a stale
    width is not. Returns True if carried."""
    # Layout-KEYED width maps are immune to layout change by construction
    # (each entry only ever matches its own layout) — carry them verbatim.
    wm = (old_map or {}).get("width_maps")
    if wm:
        new_map["width_maps"] = wm
    widths = (old_map or {}).get("channel_widths")
    if not widths:
        return False
    if _row1_names(old_map) == _row1_names(new_map):
        new_map["channel_widths"] = widths
        return True
    return False


def _live_row1_names():
    """Strip names on the live device's input row — from the CURRENT bank
    only. A union across every bank ever seen mixed layouts together and
    mis-keyed width maps (review finding: widths stored under a phantom
    union layout never match at fire time, silently disarming EQ). None
    when the listener is absent or blind."""
    lst = bridge.osc_listener
    if lst is None or not lst.running:
        return None
    st = lst.state
    if not st.current_submix:
        return None
    floor = getattr(bridge, "_layout_epoch", 0.0)
    names = set()
    for _ch, d in st.submix_snapshot(st.current_submix).get("1", {}).items():
        if d.get("_seen", 0) < floor:
            continue
        n = str(d.get("name", "")).strip()
        if n and n.lower() not in ("n.a.", "n/a"):
            names.add(n)
    return names or None


class ApplyBody(BaseModel):
    force: bool = False


@app.post("/api/device/discovery/apply")
async def apply_discovery(body: ApplyBody = ApplyBody()):
    """Promote the last discovery result to the live ufx2_channel_map.json."""
    return _apply_discovery_result(body.force)


def _apply_discovery_result(force: bool = False):
    state = bridge.discovery_state
    if state.get("status") != "done" or "channel_map" not in state:
        raise HTTPException(status_code=409, detail="No completed discovery to apply")
    new_map = state["channel_map"]

    # Sanity guard: a mid-walk device freeze produces a walk that "completes"
    # with one submix (the one it was parked on) — applying that clobbers a
    # good map (happened live). Refuse a dramatic collapse unless forced.
    old_subs = len((bridge.channel_map or {}).get("submixes", {}))
    new_subs = len(new_map["submixes"])
    if not force and old_subs >= 4 and new_subs * 2 < old_subs:
        raise HTTPException(
            status_code=409,
            detail=f"Walk found only {new_subs} submixes vs {old_subs} in the "
                   f"live map — the device may have stopped responding "
                   f"mid-walk. Probe it (POST /api/device/probe), re-run "
                   f"discovery, or pass {{\"force\": true}} to apply anyway.")

    # LAYOUT LIBRARY: every applied walk is remembered under its output-
    # layout key so later snapshot swaps hot-swap instead of re-walking.
    # Carried verbatim (layout-keyed = immune to layout change), then the
    # new walk registers itself.
    library = dict((bridge.channel_map or {}).get("layout_library", {}))
    new_key = bridge._layout_key_from_names(new_map.get("submixes", {}).keys())
    library[new_key] = new_map.get("submixes", {})
    new_map["layout_library"] = library
    # snapshot-layout memory is (workspace|snapshot) -> layout key: it
    # survives a re-walk unchanged, and dropping it silently re-imposed
    # the one-time learn on every other snapshot (hardware: 4 entries ->
    # 1 on every apply, including automatic ones). Stale keys are
    # harmless — the instant swap falls through on a missing library
    # entry.
    new_map["snapshot_layouts"] = dict(
        (bridge.channel_map or {}).get("snapshot_layouts", {}))

    # Discovery builds the map from scratch — without this, every apply
    # silently disarmed EQ macros by dropping the verified widths
    carried = _carry_widths(bridge.channel_map, new_map)
    if carried:
        logger.info("channel_widths preserved across discovery apply (layout unchanged)")
    elif (bridge.channel_map or {}).get("channel_widths"):
        logger.warning("⚠️ channel_widths DROPPED: the strip layout changed across "
                       "this discovery — EQ macros will refuse until widths are "
                       "re-derived or re-entered for the new layout")

    # 'carried' only says old-map names == new-map names. Widths are CONSUMED
    # against LIVE strip names, a different namespace — report coverage of
    # the live layout so carried:true can't masquerade as EQ-is-armed
    # (observed live: carried widths keyed on a 17-strip layout with a
    # 23-strip device — 16 live strips uncovered, EQ fully disarmed).
    live = _live_row1_names()
    widths = {}
    if live is not None:
        key = bridge._layout_key_from_names(live)
        widths = (new_map.get("width_maps", {}).get(key)
                  or new_map.get("channel_widths", {}))
    coverage = None
    if live is not None:
        uncovered = sorted(n for n in live if n not in widths)
        coverage = {"live_strips": len(live),
                    "covered": len(live) - len(uncovered),
                    "uncovered": uncovered}
        if uncovered and widths:
            logger.warning(f"⚠️ channel_widths cover {coverage['covered']}/"
                           f"{len(live)} live strips — EQ will refuse on: "
                           f"{uncovered}")

    _persist_channel_map(new_map)
    bridge.channel_map_is_example = False
    bridge.check_map_freshness()
    logger.info(f"✅ Discovered channel map applied ({new_subs} submixes)")
    return {"status": "success", "submixes": new_subs,
            "channel_widths_carried": carried,
            "widths_live_coverage": coverage}


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
        data = _sanitize_mappings(data)
        target = os.path.join(os.path.dirname(__file__), "../mappings.json")
        with open(target, "w") as f:
            json.dump(data, f, indent=2)
        bridge.mappings = data
        bridge.mappings_is_example = False
        bridge.mappings_source = "mappings.json"
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
