"""Sidechain ducking built from what the RME actually gives us.

#user idea: "using the dynamics module build side chain compression" -
TotalMix has no sidechain input, but since the bandwidth-cap lift the
bridge sees EVERY channel's live level. So: a KEY channel's meter drives
gain reduction on a knob's own target send - kick ducks the pad, vocal
ducks the echo return. The goblin now turns the knob to the beat.

Config lives on the knob step, next to the knob operation:

    operation.duck = {
        "enabled":   bool,
        "key":       {"row": 1|2|3, "channel": "<name>"},   # meter source
        "threshold": dB   (default -30),
        "depth":     dB of reduction when keyed (default 12, max 40),
        "attack":    ms   (default 20),
        "release":   ms   (default 250),
    }

The engine only ever writes the knob's own volume target (base - gr).
Base tracking: the device's fader IS the base until we write; any
external move (knob drag, TotalMix, another UI) shows up as a device
value that differs from our last write and re-derives the base under
the current reduction - so riding the send while it ducks Just Works.
"""

import logging
import math
import time

logger = logging.getLogger(__name__)

FLOOR_DB = -65.0          # never duck below this - silence, not -inf snap
EXTERNAL_EPS_DB = 0.75    # device vs last-write mismatch = external move
WRITE_EPS_DB = 0.05       # don't spam sub-0.05dB writes
RESTORE_MIN_DB = 0.1      # restore base on disable only if still reduced

_ROW_KEYS = {1: "inputs", 2: "playback", 3: "outputs"}


def duck_tick(cfg, rt, key_db, dev_db, dt):
    """One control-rate step - PURE except for mutating rt (runtime dict).

    cfg: the operation.duck dict. rt: per-macro runtime {gr, base,
    written, applied}. key_db: key channel level in dB (None = no meter
    data -> treat as silent). dev_db: target's current device level in
    dB (None = unknown -> envelope still runs, nothing written).
    Returns the dB value to write, or None.
    """
    depth = max(0.0, min(40.0, float(cfg.get("depth", 12.0))))
    thr = float(cfg.get("threshold", -30.0))
    atk_s = max(1.0, float(cfg.get("attack", 20.0))) / 1000.0
    rel_s = max(10.0, float(cfg.get("release", 250.0))) / 1000.0

    over = key_db is not None and key_db > thr
    target = depth if over else 0.0
    gr = rt.get("gr", 0.0)
    tau = atk_s if target > gr else rel_s
    gr += (target - gr) * (1.0 - math.exp(-dt / tau))
    if target == 0.0 and gr < 0.005:
        gr = 0.0
    rt["gr"] = gr

    # The un-ducked base IS the target's externally-set level. Under re-send
    # OFF (the bridge default) our OWN writes never echo back, so dev_db holds
    # the human's level and moves ONLY when they move it - which re-bases the
    # duck for free. Deriving base from our own writes (the old code) misread
    # every write as an external move, ratcheted the fader UP a little each
    # cycle and collapsed the reduction to <1 dB (critical-review HIGH-1). We
    # no longer do that: base = the freshest external device level, full stop.
    if dev_db is not None:
        rt["base"] = dev_db
    base = rt.get("base")
    if base is None:
        return None                        # level unknown yet - nothing to write
    if base <= FLOOR_DB:                    # send already at/below silence
        rt["written"] = None               # (fader_db(0) == -300) -> no -inf
        rt["applied"] = 0.0                #  write loop, nothing to duck
        return None
    if rt.get("written") is None:
        rt["written"] = base               # device already sits at base - no
        rt["applied"] = 0.0                #  redundant write just to establish
    out = max(FLOOR_DB, base - gr)
    if abs(out - rt["written"]) <= WRITE_EPS_DB:
        return None
    rt["written"] = out
    rt["applied"] = gr
    return out


class DuckSupervisor:
    """One 25Hz thread over every duck-enabled knob macro. Stateless per
    tick against bridge.mappings (edits are live next tick); per-macro
    runtime in self.rt; live status (gr, key_db) in self.status for the
    web client."""

    RATE_HZ = 25.0

    def __init__(self, bridge):
        self.bridge = bridge
        self.rt = {}
        self.status = {}

    def run(self, stop_event):
        dt = 1.0 / self.RATE_HZ
        last = time.time()
        logger.info("Duck supervisor started (%.0f Hz)", self.RATE_HZ)
        while not stop_event.wait(dt):
            now = time.time()
            try:
                self._tick(min(0.5, now - last))
            except Exception:
                logger.exception("duck tick failed")
            last = now
        # engine going away: leave no send stuck ducked
        for name in list(self.rt):
            self._restore(name)

    # ── internals ───────────────────────────────────────────────────
    def _step_for(self, name):
        macro = self.bridge.mappings.get("macros", {}).get(name)
        return self.bridge._knob_step(macro) if macro else None

    def _restore(self, name, step=None):
        rt = self.rt.pop(name, None)
        self.status.pop(name, None)
        if not rt or rt.get("applied", 0.0) <= RESTORE_MIN_DB:
            return
        step = step or self._step_for(name)
        if step is None or not self.bridge._global_active():
            return
        import global_units as gu
        try:
            writer, _, status = \
                self.bridge.global_transport.resolve_step(step["target"])
            if status == "resolved":
                writer.send_message("duck-restore", gu.fader_lin(rt["base"]))
                logger.info("duck %s: restored base %.1f dB", name, rt["base"])
        except Exception:
            logger.exception("duck restore failed for %s", name)

    def _key_db(self, duck):
        key = duck.get("key") or {}
        ch = key.get("channel")
        if not ch:
            return None
        try:
            row = int(key.get("row", 1) or 1)
        except (TypeError, ValueError):
            row = 1
        rk = _ROW_KEYS.get(row, "inputs")
        gt = self.bridge.global_transport
        try:
            hw = gt._hw_for_name(rk, ch)
        except Exception:
            hw = None
        if hw is None:
            return None
        st = self.bridge.global_listener.state
        now = time.time()
        with st._lock:
            hws = (hw, hw + 1) if st.stereo.get(rk, {}).get(hw) else (hw,)
            vals = [v[0] for h in hws
                    for v in [st.levels.get((rk, h))]
                    if v and now - v[1] < 8.0]
            row_alive = any(k[0] == rk for k in st.levels)
        if vals:
            return max(vals)
        return -100.0 if row_alive else None   # silent vs no-meter-data

    def _tick(self, dt):
        b = self.bridge
        if not b._global_active():
            return
        import global_units as gu
        active = set()
        for name, macro in list(b.mappings.get("macros", {}).items()):
            step = b._knob_step(macro)
            duck = ((step or {}).get("operation") or {}).get("duck")
            if step is None or not isinstance(duck, dict):
                continue
            # duck rides the fader law - volume targets only
            if str(step.get("target", {}).get("param", "volume")) != "volume":
                continue
            if not duck.get("enabled"):
                if name in self.rt:
                    self._restore(name, step)
                continue
            active.add(name)
            dev = b.knob_device_value(step)
            dev_db = gu.fader_db(dev) if dev is not None else None
            rt = self.rt.setdefault(name, {})
            kdb = self._key_db(duck)
            out = duck_tick(duck, rt, kdb, dev_db, dt)
            self.status[name] = {
                "gr": round(rt.get("gr", 0.0), 1),
                "key_db": None if kdb is None else round(kdb, 1),
            }
            if out is not None:
                try:
                    writer, _, status = \
                        b.global_transport.resolve_step(step["target"])
                    if status == "resolved":
                        writer.send_message("duck", gu.fader_lin(out))
                except Exception:
                    logger.exception("duck write failed for %s", name)
        # macros deleted while ducked: restore what we can, drop the rest
        for name in list(self.rt):
            if name not in active:
                self._restore(name)
