"""global_units — the RME fader curve (spec-fixed points) + transforms."""
import pytest

import global_units as gu


def test_fader_curve_spec_fixed_points():
    assert gu.fader_db(1.0) == pytest.approx(6.0, abs=0.01)          # top = +6 dB
    assert gu.fader_db(649.0 / 1023.0) == pytest.approx(-6.0, abs=0.01)  # breakpoint
    assert gu.fader_db(0.5) == pytest.approx(-12.13, abs=0.05)       # spec curve
    assert gu.fader_db(0.0) == gu.GAIN_SUBMIX_OFF                    # floor = off


def test_fader_curve_round_trips():
    for x in (0.2, 0.4, 0.634, 0.75, 0.9, 1.0):
        assert gu.fader_lin(gu.fader_db(x)) == pytest.approx(x, abs=0.002)
    assert gu.fader_lin(-6.0) == pytest.approx(649.0 / 1023.0, abs=0.001)
    assert gu.fader_lin(gu.GAIN_SUBMIX_OFF) == 0.0
    assert gu.fader_lin(99.0) == 1.0                                 # clamped


def test_measured_linear_transforms():
    m = gu.GLOBAL_PARAM_MAP
    assert m["dyn_gain"].to_wire(0.5) == pytest.approx(0.0)          # -30..+30 dB
    assert m["comp_thresh"].to_wire(0.0) == pytest.approx(-60.0)
    assert m["comp_ratio"].to_wire(1.0) == pytest.approx(10.0)
    assert m["alev_headroom"].to_wire(1.0) == pytest.approx(12.0)    # 3..12 dB
    # round trips
    for p in ("dyn_gain", "exp_thresh", "dyn_attack", "alev_risetime"):
        assert m[p].from_wire(m[p].to_wire(0.3)) == pytest.approx(0.3)


def test_switches_pan_and_enums():
    m = gu.GLOBAL_PARAM_MAP
    assert m["mute"].to_wire(0.9) == 1.0 and m["mute"].to_wire(0.1) == 0.0
    assert m["pan"].to_wire(0.0) == -1.0 and m["pan"].to_wire(1.0) == 1.0
    assert m["pan"].to_wire(0.5) == pytest.approx(0.0)
    assert m["eq_type_1"].to_wire(0.6667) == 2.0                     # High Pass
    assert m["eq_type_1"].from_wire(3.0) == pytest.approx(1.0)


def test_every_param_is_calibrated():
    # as of 2026-08-21 the whole map is wire-measured (input_gain was the
    # last, swept on AN 1/2) — an uncalibrated entry would be a regression
    bad = [k for k, p in gu.GLOBAL_PARAM_MAP.items() if not p.calibrated]
    assert bad == []


def test_hw5_measured_calibrations():
    """Wire-measured 2026-08-21 (classic 3-point sweeps on the UFX II)."""
    m = gu.GLOBAL_PARAM_MAP
    assert m["eq_gain_1"].to_wire(0.0) == -20.0
    assert m["eq_gain_1"].to_wire(1.0) == 20.0
    assert m["eq_gain_2"].to_wire(0.5) == pytest.approx(0.0)
    # freq knobs are LOG taper: classic 0.5 measured at 632 Hz = sqrt(20*20k)
    assert m["eq_freq_1"].to_wire(0.5) == pytest.approx(632.455, abs=0.01)
    assert m["eq_freq_1"].to_wire(0.0) == pytest.approx(20.0)
    assert m["eq_freq_1"].to_wire(1.0) == pytest.approx(20000.0)
    assert m["eq_freq_1"].from_wire(632.455) == pytest.approx(0.5, abs=1e-4)
    assert m["eq_q_1"].to_wire(0.0) == pytest.approx(0.4)
    assert m["eq_q_1"].to_wire(1.0) == pytest.approx(9.9)
    assert m["lowcut_freq"].to_wire(0.5) == pytest.approx(100.0)  # sqrt(20*500)
    assert m["lowcut_grade"].to_wire(1.0) == 3.0                  # enum 0..3
    assert m["reverb_time"].to_wire(0.5) == pytest.approx(2.55)
    assert m["reverb_volume"].to_wire(0.5) == pytest.approx(-29.5)
    assert m["reverb_predelay"].to_wire(1.0) == pytest.approx(999.0)
    assert m["echo_time"].to_wire(1.0) == pytest.approx(2.0)
    assert m["echo_feedback"].to_wire(0.5) == pytest.approx(50.0)
    assert m["echo_width"].to_wire(0.5) == pytest.approx(0.5)
    # input gain: measured on AN 1/2 (LINE input) — 0..+12 dB linear;
    # mic channels are wider (unmeasured) and this map under-ranges there
    assert m["input_gain"].to_wire(0.25) == pytest.approx(3.0)
    assert m["input_gain"].to_wire(1.0) == pytest.approx(12.0)
    assert m["input_gain_r"].to_wire(0.5) == pytest.approx(6.0)


def test_lr_params_flagged():
    m = gu.GLOBAL_PARAM_MAP
    assert m["input_gain_r"].lr and m["phase_r"].lr
    assert not m["input_gain"].lr and not m["phase"].lr


def test_calibrate_installs_transform():
    p = gu.GLOBAL_PARAM_MAP["input_gain"]
    orig = (p.to_wire, p.from_wire)
    gu.calibrate("input_gain", *gu._linear(0.0, 75.0))
    try:
        assert p.calibrated
        assert p.to_wire(1.0) == 75.0
    finally:
        p.to_wire, p.from_wire = orig
