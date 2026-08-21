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


def test_uncalibrated_params_are_marked_and_refusable():
    m = gu.GLOBAL_PARAM_MAP
    for p in ("eq_gain_1", "eq_freq_2", "lowcut_freq", "input_gain",
              "reverb_time", "echo_feedback"):
        assert not m[p].calibrated
    assert m["volume"].calibrated and m["mute"].calibrated


def test_lr_params_flagged():
    m = gu.GLOBAL_PARAM_MAP
    assert m["input_gain_r"].lr and m["phase_r"].lr
    assert not m["input_gain"].lr and not m["phase"].lr


def test_calibrate_installs_transform():
    gu.calibrate("lowcut_freq", *gu._linear(20.0, 500.0))
    try:
        assert gu.GLOBAL_PARAM_MAP["lowcut_freq"].calibrated
        assert gu.GLOBAL_PARAM_MAP["lowcut_freq"].to_wire(0.0) == 20.0
    finally:
        gu.GLOBAL_PARAM_MAP["lowcut_freq"].to_wire = None
        gu.GLOBAL_PARAM_MAP["lowcut_freq"].from_wire = None
