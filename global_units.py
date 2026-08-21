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
    "eq_gain_1":     GlobalParam("channel", "eq/band1gain", None, None),
    "eq_gain_2":     GlobalParam("channel", "eq/band2gain", None, None),
    "eq_gain_3":     GlobalParam("channel", "eq/band3gain", None, None),
    "eq_freq_1":     GlobalParam("channel", "eq/band1freq", None, None),
    "eq_freq_2":     GlobalParam("channel", "eq/band2freq", None, None),
    "eq_freq_3":     GlobalParam("channel", "eq/band3freq", None, None),
    "eq_q_1":        GlobalParam("channel", "eq/band1q", None, None),
    "eq_q_2":        GlobalParam("channel", "eq/band2q", None, None),
    "eq_q_3":        GlobalParam("channel", "eq/band3q", None, None),
    "eq_type_1":     GlobalParam("channel", "eq/band1type", _enum4, _from_enum4),
    "eq_type_3":     GlobalParam("channel", "eq/band3type", _enum4, _from_enum4),
    "lowcut_enable": GlobalParam("channel", "lowcut/enable", _to_switch, _from_switch),
    "lowcut_freq":   GlobalParam("channel", "lowcut/freq", None, None),
    "lowcut_grade":  GlobalParam("channel", "lowcut/slope", None, None),
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
    "input_gain":    GlobalParam("channel", "gain", None, None),
    "input_gain_r":  GlobalParam("channel", "gain", None, None, lr=True),
    "phase":         GlobalParam("channel", "phase", _to_switch, _from_switch),
    "phase_r":       GlobalParam("channel", "phase", _to_switch, _from_switch, lr=True),

    # ── global FX (classic page 3) ──────────────────────────────────
    "reverb_enable":   GlobalParam("fx", "/reverb/enable", _to_switch, _from_switch),
    "reverb_time":     GlobalParam("fx", "/reverb/time", None, None),
    "reverb_volume":   GlobalParam("fx", "/reverb/volume", None, None),
    "reverb_width":    GlobalParam("fx", "/reverb/width", None, None),
    "reverb_predelay": GlobalParam("fx", "/reverb/predelay", None, None),
    "echo_enable":     GlobalParam("fx", "/echo/enable", _to_switch, _from_switch),
    "echo_time":       GlobalParam("fx", "/echo/delay", None, None),
    "echo_feedback":   GlobalParam("fx", "/echo/feedback", None, None),
    "echo_volume":     GlobalParam("fx", "/echo/volume", None, None),
    "echo_width":      GlobalParam("fx", "/echo/width", None, None),
}


def calibrate(param: str, to_wire, from_wire):
    """Install a measured transform (used by the calibration harness and
    by hardware-round results as they land)."""
    p = GLOBAL_PARAM_MAP[param]
    p.to_wire, p.from_wire = to_wire, from_wire
