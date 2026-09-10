"""Global OSC unit conversions + the declarative param map (#25).

Macros and the UI operate in the classic 0..1 normalized domain per param
(PARAM_DEFS). Global OSC speaks REAL units (dB, Hz, ms, Q) on most
addresses, plus `faderlin` — a 0..1 fader position whose dB curve RME
publishes in the protocol spec (Fader curve sheet, ported verbatim below).

Every transform is declarative in GLOBAL_PARAM_MAP so protocol drift during
the 2.1 beta stays localized here. Params whose real-unit ranges have NOT
been wire-calibrated yet carry to_wire=None — the transport REFUSES them
rather than guessing (the width-assumption lesson, generalized to units).
Spec version pinned: globalosc_protocol_b2.zip, table dated 21.07.26.
"""

# Wire value observed for -infinity dB (e.g. fxsend of an off send)
GAIN_SUBMIX_OFF = -300.0

_FADER_BREAK = 649.0
_FADER_SLOPE = 0.0320855615
_FADER_OFFSET = -26.8235294118


def fader_db(faderlin: float) -> float:
    """RME CalcFaderDB: faderlin position 0..1 -> dB (verbatim port)."""
    pos = max(0.0, min(1.0, float(faderlin))) * 1023.0
    if pos >= _FADER_BREAK:
        db = pos * _FADER_SLOPE + _FADER_OFFSET
    else:
        db = (pos * pos) * (-1.0 / 11033.0) + pos * 0.1497326203 - 65.0
    if db < -64.9:
        return GAIN_SUBMIX_OFF
    return db


def fader_lin(db: float) -> float:
    """RME CalcFaderLin: dB -> faderlin position 0..1 (verbatim port)."""
    db = float(db)
    if db <= -64.9:
        return 0.0
    if db >= -6.0:
        pos = (db - _FADER_OFFSET) * (1.0 / _FADER_SLOPE)
    else:
        pos = 826.0 - ((-34869.0 - 11033.0 * db) ** 0.5)
    return max(0.0, min(1.0, pos * (1.0 / 1023.0)))


def _clamp01(v):
    return max(0.0, min(1.0, float(v)))


def _identity(v):
    return _clamp01(v)


def _to_balpan(v):
    """classic pan 0..1 (L..R) -> balpan -1..+1"""
    return _clamp01(v) * 2.0 - 1.0


def _from_balpan(w):
    return _clamp01((float(w) + 1.0) / 2.0)


def _to_switch(v):
    """threshold at 0.5 -> absolute 0|1 (Global enables are absolute sets,
    unlike classic's momentary buttons — gate HW-3)"""
    return 1.0 if float(v) >= 0.5 else 0.0


def _from_switch(w):
    return 1.0 if float(w) >= 0.5 else 0.0


def _linear(lo, hi):
    span = hi - lo

    def to_wire(v):
        return lo + _clamp01(v) * span

    def from_wire(w):
        return _clamp01((float(w) - lo) / span)

    return to_wire, from_wire


def _log_map(lo, hi):
    """Log-taper knob (freq params): classic 0.5 lands on sqrt(lo*hi) —
    wire-measured 2026-08-21 (20..20000 Hz mid = 632, exactly sqrt)."""
    import math
    ratio = hi / lo

    def to_wire(v):
        return lo * (ratio ** _clamp01(v))

    def from_wire(w):
        return _clamp01(math.log(max(float(w), lo) / lo) / math.log(ratio))

    return to_wire, from_wire


def _enum4(v):
    """classic eq-type enum {0.0, 0.3333, 0.6667, 1.0} -> index 0..3"""
    return float(round(_clamp01(v) * 3.0))


def _from_enum4(w):
    return max(0.0, min(3.0, float(w))) / 3.0


class GlobalParam:
    """One classic param's Global OSC binding.

    scope: 'mix'     — /mix/in|pb/{in}/{out}/<path> (submix sends)
           'channel' — /input|playback|output/{n}/<path>
           'fx'      — fixed tree (/reverb/..., /echo/...)
    path:  address tail
    lr:    True when the RIGHT half of a pair is addressed at n+1
    to_wire/from_wire: normalized<->real transforms; to_wire=None means
           UNCALIBRATED — the transport must refuse (never guess units).
    """

    def __init__(self, scope, path, to_wire, from_wire, lr=False):
        self.scope = scope
        self.path = path
        self.to_wire = to_wire
        self.from_wire = from_wire
        self.lr = lr

    @property
    def calibrated(self):
        return self.to_wire is not None


# HW-5 wire-measured 2026-08-21 (classic 3-point sweeps, UFX II,
# TotalMix 2.1 b5): EQ bands homogeneous (band1==band3 measured)
_eq_gain = _linear(-20.0, 20.0)
_eq_freq = _log_map(20.0, 20000.0)
_eq_q = _linear(0.4, 9.9)
_lowcut_freq = _log_map(20.0, 500.0)
_fx_volume = _linear(-65.0, 6.0)
_fx_width = _linear(0.0, 1.0)
_reverb_time = _linear(0.1, 5.0)
_reverb_predelay = _linear(0.0, 999.0)
_echo_delay = _linear(0.1, 2.0)
_echo_feedback = _linear(0.0, 100.0)

_dyn_gain = _linear(-30.0, 30.0)
_comp_thresh = _linear(-60.0, 0.0)
_ratio = _linear(1.0, 10.0)
_exp_thresh = _linear(-99.0, -20.0)
_attack = _linear(0.0, 200.0)
_release = _linear(100.0, 999.0)
_alev_maxgain = _linear(0.0, 18.0)
_alev_headroom = _linear(3.0, 12.0)
_alev_risetime = _linear(0.1, 9.9)

GLOBAL_PARAM_MAP = {
    # ── page-1 domain ───────────────────────────────────────────────
    # volume rides faderlin: same 0..1 domain as classic (identity gated
    # by HW-2; fall back to fader_lin(classic->dB) if the curve differs)
    "volume": GlobalParam("mix", "faderlin", _identity, _identity),
    "pan":    GlobalParam("mix", "balpan", _to_balpan, _from_balpan),
    "mute":   GlobalParam("channel", "mute", _to_switch, _from_switch),

    # ── channel detail (classic page 2) ─────────────────────────────
    "eq_enable":     GlobalParam("channel", "eq/enable", _to_switch, _from_switch),
    "eq_gain_1":     GlobalParam("channel", "eq/band1gain", *_eq_gain),
    "eq_gain_2":     GlobalParam("channel", "eq/band2gain", *_eq_gain),
    "eq_gain_3":     GlobalParam("channel", "eq/band3gain", *_eq_gain),
    "eq_freq_1":     GlobalParam("channel", "eq/band1freq", *_eq_freq),
    "eq_freq_2":     GlobalParam("channel", "eq/band2freq", *_eq_freq),
    "eq_freq_3":     GlobalParam("channel", "eq/band3freq", *_eq_freq),
    "eq_q_1":        GlobalParam("channel", "eq/band1q", *_eq_q),
    "eq_q_2":        GlobalParam("channel", "eq/band2q", *_eq_q),
    "eq_q_3":        GlobalParam("channel", "eq/band3q", *_eq_q),
    "eq_type_1":     GlobalParam("channel", "eq/band1type", _enum4, _from_enum4),
    "eq_type_3":     GlobalParam("channel", "eq/band3type", _enum4, _from_enum4),
    "lowcut_enable": GlobalParam("channel", "lowcut/enable", _to_switch, _from_switch),
    "lowcut_freq":   GlobalParam("channel", "lowcut/freq", *_lowcut_freq),
    "lowcut_grade":  GlobalParam("channel", "lowcut/slope", _enum4, _from_enum4),
    "dyn_enable":    GlobalParam("channel", "dynamics/enable", _to_switch, _from_switch),
    "dyn_gain":      GlobalParam("channel", "dynamics/gain", *_dyn_gain),
    "comp_thresh":   GlobalParam("channel", "dynamics/compthres", *_comp_thresh),
    "comp_ratio":    GlobalParam("channel", "dynamics/compratio", *_ratio),
    "exp_thresh":    GlobalParam("channel", "dynamics/expthres", *_exp_thresh),
    "exp_ratio":     GlobalParam("channel", "dynamics/expratio", *_ratio),
    "dyn_attack":    GlobalParam("channel", "dynamics/attack", *_attack),
    "dyn_release":   GlobalParam("channel", "dynamics/release", *_release),
    "alev_enable":   GlobalParam("channel", "autolevel/enable", _to_switch, _from_switch),
    "alev_maxgain":  GlobalParam("channel", "autolevel/maxgain", *_alev_maxgain),
    "alev_headroom": GlobalParam("channel", "autolevel/headroom", *_alev_headroom),
    "alev_risetime": GlobalParam("channel", "autolevel/risetime", *_alev_risetime),
    # Gain range depends on the input's HARDWARE CLASS (both wire-measured
    # 2026-08-21): line inputs 0..+12 dB, mic preamps 0..75 dB. The base
    # map here is the line range; the transport swaps in the mic range by
    # hardware channel via input_gain_transforms() below.
    "input_gain":    GlobalParam("channel", "gain", *_linear(0.0, 12.0)),
    "input_gain_r":  GlobalParam("channel", "gain", *_linear(0.0, 12.0), lr=True),
    "phase":         GlobalParam("channel", "phase", _to_switch, _from_switch),
    "phase_r":       GlobalParam("channel", "phase", _to_switch, _from_switch, lr=True),

    # ── global FX (classic page 3) ──────────────────────────────────
    "reverb_enable":   GlobalParam("fx", "/reverb/enable", _to_switch, _from_switch),
    "reverb_time":     GlobalParam("fx", "/reverb/time", *_reverb_time),
    "reverb_volume":   GlobalParam("fx", "/reverb/volume", *_fx_volume),
    "reverb_width":    GlobalParam("fx", "/reverb/width", *_fx_width),
    "reverb_predelay": GlobalParam("fx", "/reverb/predelay", *_reverb_predelay),
    "echo_enable":     GlobalParam("fx", "/echo/enable", _to_switch, _from_switch),
    "echo_time":       GlobalParam("fx", "/echo/delay", *_echo_delay),
    "echo_feedback":   GlobalParam("fx", "/echo/feedback", *_echo_feedback),
    "echo_volume":     GlobalParam("fx", "/echo/volume", *_fx_volume),
    "echo_width":      GlobalParam("fx", "/echo/width", *_fx_width),
}


# Per-hardware-channel input-gain ranges, wire-measured 2026-08-21 on the
# UFX II (same fixed-hardware philosophy as the physical table): rear line
# inputs AN 1-8 = hw 0-7 sweep 0..+12 dB; front mic/combo inputs 9-12 =
# hw 8-11 sweep 0..75 dB (integer-stepped on the device; linked pairs gang
# both members). Digital channels (hw 12+) have no gain stage at all —
# their /sendchan dumps carry no 'gain' entry.
INPUT_GAIN_RANGES = (
    (range(0, 8), _linear(0.0, 12.0)),    # line
    (range(8, 12), _linear(0.0, 75.0)),   # mic preamp
)


def input_gain_transforms(hw):
    """(to_wire, from_wire) for an input's measured gain range, or None
    when the channel has no gain stage (digital)."""
    for chans, transforms in INPUT_GAIN_RANGES:
        if int(hw) in chans:
            return transforms
    return None


# The section switch a continuous param lives behind: a low-cut knob is
# inaudible while lowcut/enable is off, so knobs can flip it on with the
# first move ("turn on with knob move") and the UI shows the switch.
ENABLE_FOR = {}
for _p in ("eq_gain_1", "eq_gain_2", "eq_gain_3", "eq_freq_1", "eq_freq_2",
           "eq_freq_3", "eq_q_1", "eq_q_2", "eq_q_3", "eq_type_1", "eq_type_3"):
    ENABLE_FOR[_p] = "eq_enable"
ENABLE_FOR["lowcut_freq"] = "lowcut_enable"
ENABLE_FOR["lowcut_grade"] = "lowcut_enable"
for _p in ("dyn_gain", "comp_thresh", "comp_ratio", "exp_thresh", "exp_ratio",
           "dyn_attack", "dyn_release"):
    ENABLE_FOR[_p] = "dyn_enable"
for _p in ("alev_maxgain", "alev_headroom", "alev_risetime"):
    ENABLE_FOR[_p] = "alev_enable"
for _p in ("reverb_time", "reverb_volume", "reverb_width", "reverb_predelay"):
    ENABLE_FOR[_p] = "reverb_enable"
for _p in ("echo_time", "echo_feedback", "echo_volume", "echo_width"):
    ENABLE_FOR[_p] = "echo_enable"


# Companion params a knob strip should surface next to its main control:
# a low-cut knob is half the story without its slope. Values are enums
# (index/3 normalized via _enum4). Labels live here so the UI and the OSC
# strip feedback agree. A knob may also PIN companion values (operation.companions =
# {"eq_type_3": 0.667}) - re-asserted on every move and hold, so a 'high
# cut' knob on EQ band 3 keeps the band a Low Pass across snapshot recalls.
# An EQ-band knob surfaces its WHOLE band: the type (enum chip) plus the
# band's other continuous params (mini-sliders) - a knob on band-3 freq
# still lets you set the band's Q and gain. Band 2 has no type in TotalMix.
COMPANION_FOR = {
    "lowcut_freq": ["lowcut_grade"],
    "lowcut_grade": ["lowcut_freq"],
}
for _b, _type in (("1", "eq_type_1"), ("2", None), ("3", "eq_type_3")):
    _members = [f"eq_freq_{_b}", f"eq_gain_{_b}", f"eq_q_{_b}"]
    for _m in _members:
        COMPANION_FOR[_m] = ([_type] if _type else []) + [x for x in _members if x != _m]

# ALL THREE ORDERS WIRE-VERIFIED 2026-08-21 via the classic page-2 value
# strings on Main (/2/lowcutGradeVal, /2/eqType1Val, /2/eqType3Val).
ENUM_LABELS = {
    "lowcut_grade": ["6 dB/oct", "12 dB/oct", "18 dB/oct", "24 dB/oct"],
    "eq_type_1": ["Bell", "Shelf", "High Pass", "Low Pass"],
    "eq_type_3": ["Bell", "Shelf", "Low Pass", "High Pass"],
}


def companions_for(param: str):
    return COMPANION_FOR.get(str(param).lower(), [])


def enable_param_for(param: str):
    return ENABLE_FOR.get(str(param).lower())


def calibrate(param: str, to_wire, from_wire):
    """Install a measured transform (used by the calibration harness and
    by hardware-round results as they land)."""
    p = GLOBAL_PARAM_MAP[param]
    p.to_wire, p.from_wire = to_wire, from_wire
