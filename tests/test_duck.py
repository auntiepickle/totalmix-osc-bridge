"""Duck engine math: the pure control-rate step (duck_tick).

The envelope, base tracking, and write hygiene are all decided here -
the supervisor thread is a thin wrapper around this function plus the
same meter/resolve plumbing /api/meters already exercises.
"""
import math

from duck_engine import duck_tick, FLOOR_DB


CFG = {"enabled": True, "threshold": -30.0, "depth": 12.0,
       "attack": 20.0, "release": 250.0}


def _run(cfg, rt, key_db, dev_db, ticks, dt=0.04):
    """Drive N ticks; the device follows our writes (echo model)."""
    last = None
    for _ in range(ticks):
        out = duck_tick(cfg, rt, key_db, dev_db, dt)
        if out is not None:
            last = out
            dev_db = out          # TotalMix echoes the write back
    return last, dev_db


def test_attack_reaches_depth():
    rt = {}
    last, dev = _run(CFG, rt, key_db=-10.0, dev_db=-20.0, ticks=50)
    # 2 s at 20 ms attack: fully ducked - 12 dB under the -20 base
    assert abs(rt["gr"] - 12.0) < 0.1
    assert abs(last - (-32.0)) < 0.2


def test_release_returns_to_base():
    rt = {}
    _run(CFG, rt, key_db=-10.0, dev_db=-20.0, ticks=50)
    last, _ = _run(CFG, rt, key_db=-100.0, dev_db=rt["written"], ticks=100)
    assert rt["gr"] < 0.1
    assert abs(last - (-20.0)) < 0.2      # back at base


def test_below_threshold_never_writes():
    rt = {}
    out = duck_tick(CFG, rt, key_db=-50.0, dev_db=-20.0, dt=0.04)
    assert out is None
    assert rt["gr"] == 0.0


def test_unknown_device_value_never_writes():
    rt = {}
    out = duck_tick(CFG, rt, key_db=-10.0, dev_db=None, dt=0.04)
    assert out is None                    # envelope ran, nothing written
    assert rt["gr"] > 0.0


def test_external_move_rebases_under_reduction():
    rt = {}
    _run(CFG, rt, key_db=-10.0, dev_db=-20.0, ticks=50)   # settled at -32
    # someone drags the fader to -26 while ducked: new base = -26 + 12
    out = duck_tick(CFG, rt, key_db=-10.0, dev_db=-26.0, dt=0.04)
    assert abs(rt["base"] - (-14.0)) < 0.1
    # and the next write keeps the full reduction under the NEW base
    assert out is None or abs(out - (rt["base"] - rt["gr"])) < 0.2


def test_floor_clamp():
    cfg = dict(CFG, depth=40.0)
    rt = {}
    last, _ = _run(cfg, rt, key_db=0.0, dev_db=-60.0, ticks=80)
    assert last is not None and last >= FLOOR_DB - 1e-9


def test_small_changes_are_not_written():
    rt = {}
    _run(CFG, rt, key_db=-10.0, dev_db=-20.0, ticks=200)
    # fully settled: another tick must not emit a sub-epsilon write
    out = duck_tick(CFG, rt, key_db=-10.0, dev_db=rt["written"], dt=0.04)
    assert out is None
