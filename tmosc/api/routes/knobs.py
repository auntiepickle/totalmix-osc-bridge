"""Knob macros over HTTP: meters, send-group capture, duck status, and the
set/enable/param fallbacks for the WebSocket knob stream."""
from fastapi import APIRouter, HTTPException

from tmosc.api.deps import Bridge
from tmosc.api import persistence

router = APIRouter(tags=["knobs"])


@router.get("/api/meters")
def get_meters(bridge: Bridge):
    """Live peak levels (dB) for every knob macro's meter source (#meters):
    sends meter their SOURCE channel, row-3 knobs their output. Stereo
    pairs report the max of both members. Values fresher than 2s only."""
    import time as _time
    lst = getattr(bridge, "global_listener", None)
    gt = getattr(bridge, "global_transport", None)
    if lst is None or gt is None:
        return {"available": False, "meters": {}}
    st = lst.state
    now = _time.time()
    with st._lock:
        # rows with ANY frame since boot: every channel of a streaming
        # row has a meter (TotalMix sends nothing for digital silence,
        # so per-channel history is boot-order-dependent - task61
        # finding: a restarted bridge showed no master until Main's
        # first frame)
        rows_seen = {k[0] for k in st.levels}
    out = {}
    for name, m in list(bridge.mappings.get("macros", {}).items()):   # snapshot: other threads resize it
        step = bridge._knob_step(m)
        if not step:
            continue
        t = step.get("target") or {}
        ch = t.get("channel")
        if not ch:
            continue
        row = str(t.get("row", 1))
        rk = {"1": "inputs", "2": "playback", "3": "outputs"}.get(row, "inputs")
        # fallback chain: TotalMix may stream only some rows' meters (live-
        # observed: inputs only unless more level options are enabled) -
        # a send knob's playback twin carries the same musical signal
        rks = [rk] + (["playback"] if rk == "inputs" else
                      ["inputs"] if rk == "playback" else [])
        val = None
        for r in rks:
            try:
                hw = gt._hw_for_name(r, ch)
            except Exception:
                hw = None
            if hw is None:
                continue
            with st._lock:
                hws = (hw, hw + 1) if st.stereo.get(r, {}).get(hw) else (hw,)
                # 8s window (wire-observed: unchanged/floor values resend
                # only every 2-4s - a 2s window made quiet meters blink;
                # changing values stream continuously so no decay lag)
                ent = [st.levels.get((r, h)) for h in hws]
                vals = [v[0] for v in ent if v and now - v[1] < 8.0]
                # digitally-silent channels stop sending entirely (only
                # analog noise keeps a stream alive) - a channel we HAVE
                # seen since boot that went quiet is at the floor, not
                # meterless (#user report: master vanished when playback
                # stopped)
                if not vals and (any(ent) or r in rows_seen):
                    vals = [-100.0]
            if vals:
                val = max(vals)
                break
        if val is not None:
            out[name] = round(val, 1)
    return {"available": True, "meters": out}


@router.post("/api/knobs/{name}/group_capture")
async def knob_group_capture(name: str, bridge: Bridge):
    # async on purpose: this mutates bridge.mappings and persists it, which
    # every other writer does on the event-loop thread - a threadpool copy
    # raced persistence._persist_mappings' rebind (review finding). Nothing here blocks.
    """Re-capture a send group's balance (#groups): for every member,
    offset_db = member's CURRENT level minus the primary's - the mixer
    as it sounds right now becomes the stored balance. Members whose
    level is unknown (no feedback yet) or at hard bottom keep their
    stored offset."""
    import tmosc.global_units as gu
    macro = bridge.mappings.get("macros", {}).get(name)
    step = bridge._knob_step(macro) if macro else None
    grp = ((step or {}).get("operation") or {}).get("group")
    if step is None or not isinstance(grp, list):
        return {"status": "no_group"}
    primary = bridge.knob_device_value(step)
    if primary is None or primary <= 0.0005:
        return {"status": "no_primary_state"}
    pdb = gu.fader_db(primary)
    captured = 0
    for mem in grp:
        if not isinstance(mem, dict) or not mem.get("channel"):
            continue
        tgt = {k: v for k, v in mem.items() if k in ("submix", "channel", "row")}
        cur = bridge.knob_device_value({"target": tgt})
        if cur is not None and cur > 0.0005:
            mem["offset_db"] = round(gu.fader_db(cur) - pdb, 1)
            captured += 1
    persistence._persist_mappings(bridge)
    return {"status": "ok", "captured": captured, "group": grp}


@router.get("/api/duck")
def get_duck(bridge: Bridge):
    """Live sidechain state per duck-enabled knob: gain reduction (dB)
    and the key channel's level - painted onto the modules by the UI."""
    d = getattr(bridge, "duck", None)
    return {"available": d is not None,
            "duck": {k: dict(v) for k, v in dict(d.status if d else {}).items()}}


@router.post("/api/knob/{name}")
def set_knob(name: str, body: dict, bridge: Bridge):
    """HTTP fallback for the WebSocket knob stream (and for scripts/HA):
    set a KNOB macro to a 0..1 value. Mapped through the knob's range."""
    r = bridge.knob_set(name, body.get("value", 0.0), source="api")
    if r["status"] == "not_a_knob":
        raise HTTPException(status_code=404, detail=f"'{name}' is not a knob macro")
    if r["status"] != "resolved":
        raise HTTPException(status_code=409, detail=r["status"])
    return r


@router.post("/api/knob/{name}/enable")
def set_knob_enable(name: str, body: dict, bridge: Bridge):
    """Flip a KNOB macro's section switch (EQ / low cut / dynamics / FX)."""
    r = bridge.knob_enable(name, bool(body.get("on", True)), source="ui")
    if r["status"] == "not_a_knob":
        raise HTTPException(status_code=404, detail=f"'{name}' is not a knob macro")
    if r["status"] != "resolved":
        raise HTTPException(status_code=409, detail=r["status"])
    return r


@router.post("/api/knob/{name}/param")
def set_knob_param(name: str, body: dict, bridge: Bridge):
    """Write a companion param on a KNOB macro's routing (low-cut slope,
    EQ band type): {"param": "lowcut_grade", "value": 0..1}."""
    r = bridge.knob_param_set(name, str(body.get("param", "")),
                              body.get("value", 0.0), source="ui")
    if r["status"] == "not_a_knob":
        raise HTTPException(status_code=404, detail=f"'{name}' is not a knob macro")
    if r["status"] != "resolved":
        raise HTTPException(status_code=409, detail=r["status"])
    return r
