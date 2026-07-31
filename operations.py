#!/usr/bin/env python3
import time
import math
from typing import Dict, Any
import threading
import logging

logger = logging.getLogger(__name__)

class OperationRegistry:
    _ops: Dict[str, callable] = {}

    @classmethod
    def register(cls, name: str):
        def decorator(func):
            cls._ops[name] = func
            logger.info(f"Operation registered: {name}")
            return func
        return decorator

    @classmethod
    def execute(cls, name: str, osc_client, osc_addr: str, param: float, config: dict,
                cancel_event: threading.Event = None):
        if name not in cls._ops:
            logger.error(f"Unknown operation type '{name}' on {osc_addr}")
            return
        cls._ops[name](osc_client, osc_addr, param, config, cancel_event)

def shape_value(val: float, config: dict) -> float:
    """Map a raw 0..1 operation value through per-parameter shaping (#13).

    range: [lo, hi] — the sweep window (an EQ ramp between two points, a
           pan LFO around center). Defaults to the full 0..1.
    threshold: t   — binarize AFTER the range map (the gate point for mute
                     and other binary params).
    """
    rng = config.get("range")
    if rng:
        lo, hi = float(rng[0]), float(rng[1])
        val = lo + val * (hi - lo)
    t = config.get("threshold")
    if t is not None:
        val = 1.0 if val >= float(t) else 0.0
    return val


# ====================== BUILT-IN OPERATIONS ======================

@OperationRegistry.register("ramp")
def ramp_op(osc_client, osc_addr: str, param: float, config: dict,
            cancel_event: threading.Event = None):
    """Smooth ramp (triangle or linear) over musical time. Cancellable via cancel_event."""
    if "duration" in config:
        duration = float(config["duration"])
    else:
        bars = config.get("bars", 2)
        bpm = config.get("bpm", 140)
        duration = (bars * 4 * 60.0) / bpm

    curve = config.get("curve", "triangle")
    steps_per_sec = config.get("steps_per_sec", 20)

    logger.info(f"   → Starting {curve} ramp on {osc_addr} over {duration:.3f}s")

    start_t = time.time()
    total_steps = int(duration * steps_per_sec) + 1

    for _ in range(total_steps):
        if cancel_event and cancel_event.is_set():
            osc_client.send_message(osc_addr, shape_value(0.0, config))
            logger.info(f"   → {osc_addr} ramp cancelled (restart/mode)")
            return
        t = min((time.time() - start_t) / duration, 1.0)
        if curve == "triangle":
            val = 2.0 * t if t < 0.5 else 2.0 - (2.0 * t)
        else:  # linear
            val = t
        osc_client.send_message(osc_addr, float(shape_value(val, config)))
        time.sleep(1.0 / steps_per_sec)

    # A linear ramp is a TRANSITION — it parks at the destination (the
    # sweep ceiling). It used to snap back to the floor on the final
    # send, undoing the whole sweep (user-reported, #19). A triangle
    # ramp is up-and-back by shape, so it parks where it started.
    final = 1.0 if curve == "linear" else 0.0
    osc_client.send_message(osc_addr, shape_value(final, config))
    logger.info(f"   → {osc_addr} ramp finished (parked at "
                f"{'destination' if curve == 'linear' else 'start'})")


@OperationRegistry.register("lfo")
def lfo_op(osc_client, osc_addr: str, param: float, config: dict,
           cancel_event: threading.Event = None):
    """Beat-synced LFO (depth 0.0–1.0). Cancellable via cancel_event.

    `bars` sets how LONG it runs; `rate` (cycles per beat, default 1.0)
    sets how FAST it wobbles — the old code derived cycle count from BPM
    alone (bpm/15 cycles regardless of bars), so changing the length
    changed the speed and nothing landed on a musical rate (#19).

    The wave is (1-cos)/2: it starts at the sweep floor, rises, and —
    because the cycle count is forced to an integer — ends exactly where
    it started. The old sine started mid-range and the final park at the
    floor was a jump; user-reported as "cycles don't return to their
    initial value". Threshold-gated params (mute) keep starting AND
    resting un-tripped for free, matching the #13 hardware round."""
    bpm = config.get("bpm", 140)
    bars = config.get("bars", 2)
    depth = config.get("depth", 1.0)
    rate = float(config.get("rate", 1.0))
    duration = (bars * 4 * 60.0) / bpm
    cycles = max(1, round(bars * 4 * rate))
    steps_per_sec = config.get("steps_per_sec", 30)

    logger.info(f"   → Starting LFO on {osc_addr} ({depth:.1f} depth, "
                f"{cycles} cycles = {rate:g}/beat) for {bars} bars @ {bpm} BPM")

    start_t = time.time()
    total_steps = int(duration * steps_per_sec) + 1

    for _ in range(total_steps):
        if cancel_event and cancel_event.is_set():
            osc_client.send_message(osc_addr, shape_value(0.0, config))
            logger.info(f"   → {osc_addr} LFO cancelled (restart/mode)")
            return
        t = min((time.time() - start_t) / duration, 1.0)
        phase = t * 2 * math.pi * cycles
        val = (1.0 - math.cos(phase)) * 0.5 * depth
        osc_client.send_message(osc_addr, float(shape_value(val, config)))
        time.sleep(1.0 / steps_per_sec)

    # Integer cycles end at the floor, so this park is seamless — the
    # parameter returns to the value the wobble started from
    osc_client.send_message(osc_addr, shape_value(0.0, config))
    logger.info(f"   → {osc_addr} LFO finished (returned to start)")
