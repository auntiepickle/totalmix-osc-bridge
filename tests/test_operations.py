import threading

from operations import OperationRegistry


def run_op(name, fake_osc, config, cancel=None):
    OperationRegistry.execute(name, fake_osc, "/1/volume1", 0.5, config,
                              cancel_event=cancel)


def test_ramp_triangle_shape_and_final_zero(fake_osc):
    run_op("ramp", fake_osc, {"duration": 0.3, "steps_per_sec": 30})
    values = [v for _, v in fake_osc.sent]
    assert len(values) >= 5
    assert max(values) > 0.8          # triangle peaks near 1.0 mid-ramp
    assert values[-1] == 0.0          # always parks the fader at zero
    peak_idx = values.index(max(values))
    assert 0 < peak_idx < len(values) - 1  # peak is in the middle, not an end


def test_ramp_linear_curve_is_monotonic(fake_osc):
    run_op("ramp", fake_osc, {"duration": 0.2, "steps_per_sec": 30,
                              "curve": "linear"})
    values = [v for _, v in fake_osc.sent][:-1]  # drop the final 0.0 reset
    assert all(b >= a for a, b in zip(values, values[1:]))


def test_ramp_duration_from_bars_and_bpm(fake_osc):
    # 1 bar @ 240 BPM = 1s; at 10 steps/sec expect ~11 sends + final zero
    run_op("ramp", fake_osc, {"bars": 1, "bpm": 240, "steps_per_sec": 10})
    assert 8 <= len(fake_osc.sent) <= 14


def test_ramp_cancel_stops_immediately_and_zeroes(fake_osc):
    cancel = threading.Event()
    cancel.set()
    run_op("ramp", fake_osc, {"duration": 5.0, "steps_per_sec": 20},
           cancel=cancel)
    assert fake_osc.sent == [("/1/volume1", 0.0)]


def test_lfo_bounded_by_depth_and_final_zero(fake_osc):
    run_op("lfo", fake_osc, {"bars": 1, "bpm": 480, "depth": 0.5,
                             "steps_per_sec": 40})
    values = [v for _, v in fake_osc.sent]
    assert values[-1] == 0.0
    assert all(0.0 <= v <= 0.5 for v in values)


def test_lfo_cancel_stops_immediately(fake_osc):
    cancel = threading.Event()
    cancel.set()
    run_op("lfo", fake_osc, {"bars": 8, "bpm": 60}, cancel=cancel)
    assert fake_osc.sent == [("/1/volume1", 0.0)]


def test_unknown_operation_is_noop(fake_osc):
    run_op("warp-drive", fake_osc, {})
    assert fake_osc.sent == []


def test_ramp_range_maps_sweep_window(fake_osc):
    """range [lo, hi] confines the sweep (#13) — e.g. an auto-pan L60→R30."""
    run_op("ramp", fake_osc, {"duration": 0.2, "steps_per_sec": 20,
                              "curve": "linear", "range": [0.2, 0.65]})
    values = [v for _, v in fake_osc.sent]
    assert min(values) >= 0.2 - 1e-9
    assert max(values) <= 0.65 + 1e-9
    # linear = transition: parks at the window's DESTINATION (#19) —
    # and shaped, not raw 1.0
    assert values[-1] == 0.65


def test_lfo_threshold_gates_binary_params(fake_osc):
    """threshold binarizes the LFO — a mute gate with a controllable duty
    point (#13). String config values (from UI sliders) must coerce."""
    run_op("lfo", fake_osc, {"bars": 1, "bpm": 480, "steps_per_sec": 40,
                             "threshold": "0.5"})
    values = {v for _, v in fake_osc.sent}
    assert values <= {0.0, 1.0}
    assert 1.0 in values and 0.0 in values  # it actually gates both ways


def test_range_then_threshold_compose(fake_osc):
    from operations import shape_value
    # range maps 0..1 → 0.4..0.6; threshold 0.5 splits the middle
    assert shape_value(0.0, {"range": ["0.4", "0.6"], "threshold": 0.5}) == 0.0
    assert shape_value(1.0, {"range": ["0.4", "0.6"], "threshold": 0.5}) == 1.0
    assert shape_value(0.5, {}) == 0.5  # no shaping config = passthrough


def test_ramp_linear_parks_at_destination(fake_osc):
    """A linear ramp is a transition — it must STAY at the sweep ceiling,
    not snap back to the floor on the final send (#19, user-reported)."""
    OperationRegistry.execute("ramp", fake_osc, "/1/volume1", 0.5, {
        "duration": 0.2, "steps_per_sec": 20, "curve": "linear",
        "range": [0.2, 0.8],
    })
    values = [v for _, v in fake_osc.sent]
    assert values[-1] == 0.8          # parked at the destination
    assert values[0] <= 0.25          # started at the floor


def test_lfo_integer_cycles_return_to_start(fake_osc):
    """The wobble must end where it began: (1-cos)/2 starts at the floor
    and an integer cycle count lands it back there — the final park is
    seamless instead of a jump (#19, user-reported)."""
    OperationRegistry.execute("lfo", fake_osc, "/1/pan1", 0.5, {
        "bars": 1, "bpm": 960, "steps_per_sec": 200, "range": [0.3, 0.7],
    })
    values = [v for _, v in fake_osc.sent]
    assert abs(values[0] - 0.3) < 0.01   # starts at the floor (first
    # sample lands microseconds into the wave, so allow launch jitter)
    assert values[-1] == 0.3          # parks at the floor — same value
    assert max(values) > 0.65         # actually wobbled up


def test_lfo_rate_sets_cycle_count(fake_osc):
    """bars = how long, rate = how fast: 1 bar at rate 0.5 = 2 cycles,
    rate 2 = 8 cycles (was bpm/15 cycles regardless of bars)."""
    def count_cycles(rate):
        fake_osc.clear()
        OperationRegistry.execute("lfo", fake_osc, "/1/pan1", 0.5, {
            "bars": 1, "bpm": 960, "steps_per_sec": 400, "rate": rate,
        })
        values = [v for _, v in fake_osc.sent]
        mid = 0.5
        return sum(1 for a, b in zip(values, values[1:])
                   if a < mid <= b)
    assert count_cycles(0.5) == 2
    assert count_cycles(2) == 8
