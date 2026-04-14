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
            osc_client.send_message(osc_addr, 0.0)
            logger.info(f"   → {osc_addr} ramp cancelled (restart/mode)")
            return
        t = min((time.time() - start_t) / duration, 1.0)
        if curve == "triangle":
            val = 2.0 * t if t < 0.5 else 2.0 - (2.0 * t)
        else:  # linear
            val = t
        osc_client.send_message(osc_addr, float(val))
        time.sleep(1.0 / steps_per_sec)

    osc_client.send_message(osc_addr, 0.0)
    logger.info(f"   → {osc_addr} ramp finished at 0.0")


@OperationRegistry.register("lfo")
def lfo_op(osc_client, osc_addr: str, param: float, config: dict,
           cancel_event: threading.Event = None):
    """Simple sine LFO synced to BPM (depth 0.0–1.0). Cancellable via cancel_event."""
    bpm = config.get("bpm", 140)
    bars = config.get("bars", 2)
    depth = config.get("depth", 1.0)
    duration = (bars * 4 * 60.0) / bpm
    steps_per_sec = config.get("steps_per_sec", 30)

    logger.info(f"   → Starting sine LFO on {osc_addr} ({depth:.1f} depth) for {bars} bars @ {bpm} BPM")

    start_t = time.time()
    total_steps = int(duration * steps_per_sec) + 1

    for _ in range(total_steps):
        if cancel_event and cancel_event.is_set():
            osc_client.send_message(osc_addr, 0.0)
            logger.info(f"   → {osc_addr} LFO cancelled (restart/mode)")
            return
        t = (time.time() - start_t) / duration
        phase = t * 2 * math.pi * (bpm / 60) * 4
        val = (math.sin(phase) * 0.5 + 0.5) * depth
        osc_client.send_message(osc_addr, float(val))
        time.sleep(1.0 / steps_per_sec)

    osc_client.send_message(osc_addr, 0.0)
    logger.info(f"   → {osc_addr} LFO finished at 0.0")
