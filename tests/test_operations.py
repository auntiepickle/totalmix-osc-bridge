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
    assert values[-1] == 0.2  # parks at the window's low end, not raw 0


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
