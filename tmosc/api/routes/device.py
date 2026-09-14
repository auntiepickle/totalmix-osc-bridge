"""Device capture + discovery: classic listener state, sweep, physical
table, Global OSC status, channel identify (activity/pulse), probe, picker."""
import threading

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from tmosc.api.deps import Bridge
import tmosc.physical_table as pt

router = APIRouter(tags=["device"])


# ── Device Capture + Discovery ───────────────────────────────────────────────

@router.get("/api/device/state")
async def get_device_state(bridge: Bridge):
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


@router.post("/api/device/sweep")
async def start_sweep(bridge: Bridge, body: SweepBody = SweepBody()):
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
    # Claim the state HERE (event-loop thread), not in the worker: two quick
    # POSTs both passed the check above and started two sweeps (review finding).
    bridge.sweep_state = {"status": "running", "progress": 0, "total": 0,
                          "rows": list(body.rows)}
    threading.Thread(
        target=bridge.run_sweep,
        kwargs={"rows": tuple(body.rows), "settle_s": body.settle_s,
                "reset": body.reset},
        daemon=True,
    ).start()
    return {"status": "started", "rows": body.rows,
            "estimated_s": round(len(body.rows) * 34 * (body.settle_s + 0.2), 1)}


@router.get("/api/device/sweep")
async def get_sweep_status(bridge: Bridge):
    return bridge.sweep_state


@router.get("/api/device/physical_table")
async def get_physical_table(bridge: Bridge):
    table = (bridge.channel_map or {}).get("physical_table")
    if not table:
        raise HTTPException(status_code=404,
                            detail="No physical table — run POST /api/device/sweep")
    return table


@router.get("/api/device/global")
def get_global_osc_status(bridge: Bridge, probe: bool = False):
    # sync endpoint on purpose: alive() may block ~2s on a /sendstate
    # probe — FastAPI runs sync handlers in the threadpool.
    # #22: probe defaults OFF so the header can poll this cheaply — the
    # light path reports heartbeat age only; pass ?probe=true for the
    # active /sendstate check (what the deploy verifications used).
    """Global OSC (#25) transport/listener status: which transport writes,
    heartbeat liveness, and what the Global listener has learned."""
    import tmosc.config as cfg
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
            "snapshots": {str(k): v for k, v in dict(st.snapshots).items()},
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


@router.get("/api/device/activity")
def get_device_activity(bridge: Bridge, since: float = 0.0):
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
    return {"now": _time.time(), "channels": channels,
            "name_ver": getattr(st, "name_change_count", 0)}


@router.post("/api/device/pulse")
def pulse_channel(body: dict, bridge: Bridge):
    """Channel identify, screen→world half (#8): briefly blip the selected
    send so the user can hear/see which physical channel it is. Two short
    bumps (current+6 dB, floor -30 dB when the send is off), restored to
    the exact prior level. Global transport only."""
    import time as _time
    import tmosc.global_units as gu
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


@router.post("/api/device/probe")
def probe_device(bridge: Bridge):
    """Liveness probe (kept through #24 — TASK 6 deviation fix): a state-
    changing row toggle that must produce a dump. The only sound aliveness
    check; silence from an idle mixer is not evidence."""
    result = bridge.probe_device()
    bridge.last_probe = result
    return result


@router.get("/api/device/picker")
def get_picker(bridge: Bridge):
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
