"""Duck engine math: the pure control-rate step (duck_tick).

Modeled on the REAL wire: under re-send OFF the bridge's own writes never
echo, so dev_db stays at the human's set level (the base) and changes ONLY
when the human moves the fader externally. The old tests wrongly simulated
an echo (dev_db = out) and so passed over a defect that collapsed the duck
and ratcheted the fader up (critical-review HIGH-1); these encode the truth.
"""
from tmosc.duck_engine import duck_tick, FLOOR_DB


CFG = {"enabled": True, "threshold": -30.0, "depth": 12.0,
       "attack": 20.0, "release": 250.0}


def _hold(cfg, rt, key_db, base_db, ticks, dt=0.04):
    """Drive N ticks with dev_db FROZEN at base_db (re-send OFF: our writes
    don't echo, so the device value the engine sees never moves on its own)."""
    last = None
    for _ in range(ticks):
        out = duck_tick(cfg, rt, key_db, base_db, dt)
        if out is not None:
            last = out
    return last


def test_reduces_by_depth_and_does_not_ratchet():
    rt = {}
    last = _hold(CFG, rt, key_db=-10.0, base_db=-20.0, ticks=80)
    assert abs(rt["gr"] - 12.0) < 0.1
    # settles at base - depth = -32, NOT ratcheted upward
    assert abs(last - (-32.0)) < 0.2


def test_release_returns_to_exact_base():
    rt = {}
    _hold(CFG, rt, key_db=-10.0, base_db=-20.0, ticks=80)
    last = _hold(CFG, rt, key_db=-100.0, base_db=-20.0, ticks=120)
    assert rt["gr"] < 0.1
    assert abs(last - (-20.0)) < 0.2         # no upward drift per cycle


def test_external_move_rebases_under_reduction():
    rt = {}
    _hold(CFG, rt, key_db=-10.0, base_db=-20.0, ticks=80)   # ducked at -32
    # the human raises the fader to -14 mid-duck -> dev_db reports -14
    out = duck_tick(CFG, rt, key_db=-10.0, dev_db=-14.0, dt=0.04)
    assert abs(rt["base"] - (-14.0)) < 0.01
    for _ in range(5):
        out = duck_tick(CFG, rt, key_db=-10.0, dev_db=-14.0, dt=0.04)
    # full depth under the NEW base: -14 - 12 = -26
    assert abs(rt["written"] - (-26.0)) < 0.3


def test_silent_send_never_writes_no_inf_loop():
    # a send parked at -inf reads fader_db(0) == -300; nothing to duck, and
    # crucially NO 25Hz write loop (the -300 vs -65 floor mismatch bug).
    rt = {}
    for _ in range(50):
        assert duck_tick(CFG, rt, key_db=-10.0, dev_db=-300.0, dt=0.04) is None


def test_below_threshold_no_write():
    rt = {}
    assert duck_tick(CFG, rt, key_db=-50.0, dev_db=-20.0, dt=0.04) is None
    assert rt["gr"] == 0.0


def test_unknown_device_holds_envelope_but_writes_nothing():
    rt = {}
    out = duck_tick(CFG, rt, key_db=-10.0, dev_db=None, dt=0.04)
    assert out is None
    assert rt["gr"] > 0.0                     # envelope still advances


def test_settled_state_does_not_spam():
    rt = {}
    _hold(CFG, rt, key_db=-10.0, base_db=-20.0, ticks=120)
    # fully settled and unchanged -> no further write
    assert duck_tick(CFG, rt, key_db=-10.0, dev_db=-20.0, dt=0.04) is None


def test_floor_clamp():
    cfg = dict(CFG, depth=40.0)
    rt = {}
    last = _hold(cfg, rt, key_db=0.0, base_db=-30.0, ticks=120)
    assert last is not None and last >= FLOOR_DB - 1e-9


# ── audit run 5: echoes of our own write are not external moves ──────────

def test_echo_of_own_write_does_not_rebase():
    """Row-3 settle readback / sendall / sendstate re-report the value WE
    wrote. Treating that as the human's level ratcheted the base down by a
    full depth per report; the release then settled 12 dB low."""
    rt = {}
    _hold(CFG, rt, key_db=-10.0, base_db=-20.0, ticks=80)   # ducked at -32
    duck_tick(CFG, rt, key_db=-10.0, dev_db=-32.0, dt=0.04)  # readback echo
    assert abs(rt["base"] - (-20.0)) < 0.01
    last = _hold(CFG, rt, key_db=-100.0, base_db=-32.0, ticks=120)
    assert abs(last - (-20.0)) < 0.2                          # release -> base


def test_resend_on_holds_base_and_depth():
    """TotalMix 'Re-send' ON echoes every write: dev_db == out each tick.
    Base must hold and the send settle at base - depth, not FLOOR_DB."""
    rt = {}
    dev = -20.0
    last = None
    for _ in range(120):
        out = duck_tick(CFG, rt, key_db=-10.0, dev_db=dev, dt=0.04)
        if out is not None:
            last, dev = out, out
    assert abs(rt["base"] - (-20.0)) < 0.01
    assert abs(last - (-32.0)) < 0.2


def test_bridge_side_knob_move_seeds_new_base():
    """knob_set on a ducked send: under re-send OFF the write never echoes,
    so the engine must be told (DuckSupervisor.seed) or it releases to the
    OLD level and reverts the performer's move every cycle."""
    from tmosc.duck_engine import DuckSupervisor
    rt = {}
    _hold(CFG, rt, key_db=-10.0, base_db=-20.0, ticks=80)    # ducked at -32
    sup = DuckSupervisor(bridge=None)
    sup.rt["x"] = rt
    sup.seed("x", -13.2)                                      # knob -> -13.2 dB
    for _ in range(5):
        duck_tick(CFG, rt, key_db=-10.0, dev_db=-20.0, dt=0.04)   # stale cache
    assert abs(rt["written"] - (-25.2)) < 0.3                 # -13.2 - 12
    last = _hold(CFG, rt, key_db=-100.0, base_db=-20.0, ticks=120)
    assert abs(last - (-13.2)) < 0.2                          # NOT -20


def test_malformed_numeric_config_falls_back_to_defaults():
    cfg = dict(CFG, threshold="", depth="abc", attack=None)
    rt = {}
    last = _hold(cfg, rt, key_db=-10.0, base_db=-20.0, ticks=80)
    assert abs(last - (-32.0)) < 0.2                          # default depth 12
