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
import physical_table as pt

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
        # #24: no drift concept — per-write confirmations carry correctness.
        # The physical table summary + sweep status are the honest surface.
        "physical_table": pt.summarize(
            (bridge.channel_map or {}).get("physical_table") or {}),
        "sweep_status": bridge.sweep_state.get("status"),
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
    "last_fire", "knob_value", "device_value", "enable_value", "companions",
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
        if "submixes" not in data and "physical_table" not in data:
            raise HTTPException(status_code=400, detail="Invalid channel_map: needs 'physical_table' (or legacy 'submixes')")
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


@app.get("/api/device/global")
def get_global_osc_status(probe: bool = False):
    # sync endpoint on purpose: alive() may block ~2s on a /sendstate
    # probe — FastAPI runs sync handlers in the threadpool.
    # #22: probe defaults OFF so the header can poll this cheaply — the
    # light path reports heartbeat age only; pass ?probe=true for the
    # active /sendstate check (what the deploy verifications used).
    """Global OSC (#25) transport/listener status: which transport writes,
    heartbeat liveness, and what the Global listener has learned."""
    import config as cfg
    out = {
        "transport": cfg.OSC_TRANSPORT,
        "listener_enabled": cfg.ENABLE_GLOBAL_OSC_LISTENER,
        "running": bridge.global_listener is not None,
    }
    if bridge.global_listener:
        st = bridge.global_listener.state
        out.update({
            "listen_port": bridge.global_listener.port,
            "heartbeat_age_s": st.heartbeat_age(),
            "status": dict(st.status),
            "message_count": st.message_count,
            "names": {row: st.channel_names(row)
                      for row in ("inputs", "playbacks", "outputs")},
            "snapshots": {str(k): v for k, v in st.snapshots.items()},
        })
    if bridge.global_transport:
        if probe:
            out["alive"] = bridge.global_transport.alive()
        else:
            age = bridge.global_listener.state.heartbeat_age() \
                if bridge.global_listener else None
            out["alive"] = {
                "alive": age is not None
                    and age < bridge.global_transport.heartbeat_timeout_s,
                "method": "heartbeat_age",
                "age_s": round(age, 3) if age is not None else None,
            }
    return out


@app.post("/api/knob/{name}")
def set_knob(name: str, body: dict):
    """HTTP fallback for the WebSocket knob stream (and for scripts/HA):
    set a KNOB macro to a 0..1 value. Mapped through the knob's range."""
    r = bridge.knob_set(name, body.get("value", 0.0), source="api")
    if r["status"] == "not_a_knob":
        raise HTTPException(status_code=404, detail=f"'{name}' is not a knob macro")
    if r["status"] != "resolved":
        raise HTTPException(status_code=409, detail=r["status"])
    return r


@app.post("/api/knob/{name}/enable")
def set_knob_enable(name: str, body: dict):
    """Flip a KNOB macro's section switch (EQ / low cut / dynamics / FX)."""
    r = bridge.knob_enable(name, bool(body.get("on", True)), source="ui")
    if r["status"] == "not_a_knob":
        raise HTTPException(status_code=404, detail=f"'{name}' is not a knob macro")
    if r["status"] != "resolved":
        raise HTTPException(status_code=409, detail=r["status"])
    return r


@app.post("/api/knob/{name}/param")
def set_knob_param(name: str, body: dict):
    """Write a companion param on a KNOB macro's routing (low-cut slope,
    EQ band type): {"param": "lowcut_grade", "value": 0..1}."""
    r = bridge.knob_param_set(name, str(body.get("param", "")),
                              body.get("value", 0.0), source="ui")
    if r["status"] == "not_a_knob":
        raise HTTPException(status_code=404, detail=f"'{name}' is not a knob macro")
    if r["status"] != "resolved":
        raise HTTPException(status_code=409, detail=r["status"])
    return r


@app.get("/api/device/activity")
def get_device_activity(since: float = 0.0):
    """Channel identify, world→screen half (#8): per-channel VALUE-CHANGE
    activity from Global OSC feedback since a timestamp. Own bridge writes
    never echo and dumps re-reporting unchanged values don't register, so
    entries are (almost always) a human touching the device — the UI's
    wiggle-to-learn polls this while armed."""
    if bridge.global_listener is None:
        raise HTTPException(status_code=503,
                            detail="Global OSC listener not running")
    st = bridge.global_listener.state
    channels = st.recent_changes(since)
    for e in channels:
        names = st.channel_names(e["row_key"])
        name = names.get(e["hw"])
        if name is None and st.stereo.get(e["row_key"], {}).get(e["hw"] - 1):
            # right member of a linked pair — the name lives at the left
            name = names.get(e["hw"] - 1)
        e["name"] = name
    import time as _time
    return {"now": _time.time(), "channels": channels}


@app.post("/api/device/pulse")
def pulse_channel(body: dict):
    """Channel identify, screen→world half (#8): briefly blip the selected
    send so the user can hear/see which physical channel it is. Two short
    bumps (current+6 dB, floor -30 dB when the send is off), restored to
    the exact prior level. Global transport only."""
    import time as _time
    import global_units as gu
    if not bridge._global_active():
        raise HTTPException(status_code=409,
                            detail="pulse needs the Global OSC transport")
    target = {"channel": body.get("channel", ""),
              "submix": body.get("submix", ""),
              "row": body.get("row", 1),
              "param": "volume"}
    writer, label, status = bridge.global_transport.resolve_step(target)
    if status != "resolved":
        raise HTTPException(status_code=422, detail=f"{label}: {status}")
    # Current level in dB from live state; if unknown, provoke a targeted
    # re-dump and wait. NEVER guess: restoring a guessed level could mute a
    # live output — refuse instead.
    st = bridge.global_listener.state
    tx = bridge.global_transport._client

    def _read_cur():
        if writer.address.startswith("/mix/"):
            _, _, src, in_hw, out_hw, _ = writer.address.split("/")
            e = st.get_mix(src, int(in_hw), int(out_hw), "fader")
        else:  # /output/{n}/faderlin — row-3 output fader
            n = writer.address.split("/")[2]
            e = st.get_param("outputs", int(n), "fader")
        return e[0] if e else None

    cur_db = _read_cur()
    if cur_db is None:
        if writer.address.startswith("/mix/"):
            tx.send_message("/sendmix", 1.0)
        else:
            tx.send_message(f"/sendchan/output/{writer.address.split('/')[2]}", 1.0)
        bridge.global_listener.wait_for(lambda s: _read_cur() is not None, 8.0)
        cur_db = _read_cur()
    if cur_db is None:
        raise HTTPException(status_code=422,
                            detail="current level unknown even after a "
                                   "re-dump — refusing to pulse blind")
    cur_lin = gu.fader_lin(cur_db)
    pulse_lin = gu.fader_lin(max(cur_db + 6.0, -30.0))
    for lin, hold in ((pulse_lin, 0.18), (cur_lin, 0.12),
                      (pulse_lin, 0.18), (cur_lin, 0.0)):
        writer.send_message("pulse", lin)
        if hold:
            _time.sleep(hold)
    return {"pulsed": getattr(writer, "address", label),
            "restored_db": round(cur_db, 2)}


@app.post("/api/device/probe")
async def probe_device():
    """Liveness probe (kept through #24 — TASK 6 deviation fix): a state-
    changing row toggle that must produce a dump. The only sound aliveness
    check; silence from an idle mixer is not evidence."""
    result = bridge.probe_device()
    bridge.last_probe = result
    return result


@app.get("/api/device/picker")
async def get_picker():
    """Routing-picker inventory (#6/#24): LIVE names preferred — inputs
    from the listener's cached current bank (zero device traffic),
    outputs from a fresh row-3 enumeration (~0.2s, cached) — each mapped
    to its hw start via the physical table. Falls back to the table's
    alias lists when the listener is blind (source: 'table')."""
    table = (bridge.channel_map or {}).get("physical_table") or {}
    listener = bridge.osc_listener
    result = {"inputs": [], "outputs": [], "source": {}}

    live_outs = None
    if bridge.osc_client is not None and listener is not None and listener.running:
        live_outs = bridge._live_output_names()
    if live_outs:
        def _okey(n):
            hw = pt.resolve_start(table, "outputs", n)
            return (hw if hw is not None else 999, n)
        result["outputs"] = [
            {"hw": pt.resolve_start(table, "outputs", n), "name": n}
            for n in sorted(live_outs, key=_okey)]
        result["source"]["outputs"] = "live"
    else:
        result["outputs"] = [{"hw": e["hw"], "name": e["name"]}
                             for e in pt.display_names(table, "outputs")]
        result["source"]["outputs"] = "table"

    # TASK-8 finding: the listener's cached bank can be the OUTGOING
    # snapshot's input row right after a switch — serving it as 'live'
    # made the picker lie. Provoke a fresh, settled dump instead (~0.4s,
    # 2s-cached), exactly like resolution does.
    live_ins = None
    if bridge.osc_client is not None and listener is not None and listener.running:
        live_ins = bridge._live_input_names()
    if live_ins:
        result["inputs"] = [
            {"hw": pt.resolve_start(table, "inputs", n), "name": n}
            for n in live_ins]
        result["source"]["inputs"] = "live"
    else:
        result["inputs"] = [{"hw": e["hw"], "name": e["name"]}
                            for e in pt.display_names(table, "inputs")]
        result["source"]["inputs"] = "table"
    return result




# ── WebSocket ────────────────────────────────────────────────────────────────

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    ws_clients.append(websocket)
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
        if "submixes" not in data and "physical_table" not in data:
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
    bridge.start_global_osc()   # #25: no-op unless enabled via env
    bridge.main_loop = asyncio.get_running_loop()
    print(f"🚀 TotalMix Web Client + Bridge started (port {WEB_PORT}) — MQTT ACTIVE")
