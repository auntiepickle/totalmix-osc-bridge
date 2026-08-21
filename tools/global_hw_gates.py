"""#25 hardware gates HW-3/5/6/8 (+ final snapshot wash = HW-7).

Runs on the TotalMix host. Every gate restores what it changes; the final
`wash` recalls the active snapshot, which also restores any snapshot-scoped
residue (verify it reads 2.0 = active/unmodified BEFORE the session so the
wash is a true restore).

Calibration method (HW-5): the classic remote writes the normalized 0..1
endpoints+midpoint of a param, the Global listener reads the REAL wire
units TotalMix broadcasts on change ('Send changes' is ON for the Global
remote, and classic-originated changes are 'changes'). Three points
determine lo/hi and linear-vs-log. Channel params aim classic page 2 via
/setBankStart <hw> (page 2 mirrors the BANK-START channel), self-verified
on the Global side, bank restored to 0 after.

Safety: no /setSubmix anywhere; typed floats only; FX must be DISABLED
before calfx (checked); channel gates default to unused ADAT inputs.
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pythonosc.udp_client import SimpleUDPClient

import global_units as gu
from global_listener import GlobalOSCListener

HOST = os.getenv("GLOBAL_OSC_IP", "192.168.1.61")
GLOBAL_PORT = int(os.getenv("GLOBAL_OSC_PORT", "7002"))
LISTEN_PORT = int(os.getenv("GLOBAL_OSC_LISTEN_PORT", "9002"))
CLASSIC_PORT = int(os.getenv("OSC_PORT", "7001"))


class Rig:
    def __init__(self):
        self.g = SimpleUDPClient(HOST, GLOBAL_PORT)
        self.c = SimpleUDPClient(HOST, CLASSIC_PORT)
        self.rx = GlobalOSCListener(LISTEN_PORT)
        if not self.rx.start():
            sys.exit("listener bind failed")

    def stop(self):
        self.rx.stop()

    # ── reads (feedback-driven) ────────────────────────────────────
    def wait_param(self, row_key, hw, path, t0, timeout=3.0):
        ok = self.rx.wait_for(
            lambda s: (s.get_param(row_key, hw, path) or (None, 0))[1] > t0,
            timeout)
        e = self.rx.state.get_param(row_key, hw, path)
        return (e[0] if e else None), ok

    def rechan(self, row, hw):
        """Targeted re-dump of one channel; returns after the burst."""
        t0 = time.time()
        self.g.send_message(f"/sendchan/{row}/{hw}", 1.0)
        row_key = {"input": "inputs", "playback": "playbacks",
                   "output": "outputs"}[row]
        self.rx.wait_for(
            lambda s: (s.get_param(row_key, hw, "mute") or (None, 0))[1] > t0,
            3.0)
        time.sleep(0.25)   # let the rest of the burst land

    def read_chan(self, row, hw, path):
        self.rechan(row, hw)
        row_key = {"input": "inputs", "playback": "playbacks",
                   "output": "outputs"}[row]
        e = self.rx.state.get_param(row_key, hw, path)
        return e[0] if e else None

    def read_mix(self, src, in_hw, out_hw, param, timeout=10.0):
        """/sendmix re-dump (own Global writes never echo), then read."""
        t0 = time.time()
        self.g.send_message("/sendmix", 1.0)
        self.rx.wait_for(
            lambda s: (s.get_mix(src, in_hw, out_hw, param) or (None, 0))[1] > t0,
            timeout)
        e = self.rx.state.get_mix(src, in_hw, out_hw, param)
        return e[0] if e else None

    def classic_3point(self, classic_addr, read_after):
        """Write 0 / 0.5 / 1 on the classic port, read real units after
        each via read_after(t0) -> (value, arrived)."""
        pts = []
        for v in (0.0, 0.5, 1.0):
            t0 = time.time()
            self.c.send_message(classic_addr, float(v))
            val, ok = read_after(t0)
            pts.append((v, val if ok else None))
            time.sleep(0.15)
        return pts


def curve_verdict(pts):
    (_, lo), (_, mid), (_, hi) = pts
    if None in (lo, mid, hi):
        return "NO-FEEDBACK", lo, hi
    if hi == lo:
        return "CONSTANT", lo, hi
    lin_mid = (lo + hi) / 2.0
    verdict = "linear" if abs(mid - lin_mid) <= 0.02 * abs(hi - lo) else "NONLINEAR"
    if verdict == "NONLINEAR" and lo > 0 and hi > 0:
        import math
        log_mid = math.sqrt(lo * hi)
        if abs(mid - log_mid) <= 0.05 * abs(hi - lo):
            verdict = "log"
    return verdict, lo, hi


# ── HW-3: are Global enables absolute sets? ────────────────────────
def gate_enables(rig, args):
    hw, path = args.ch, "eq/enable"
    addr = f"/input/{hw}/{path}"
    orig = rig.read_chan("input", hw, path)
    print(f"orig {path} = {orig}")
    seq = []
    for v in (1.0, 1.0, 0.0, 0.0):        # repeat-writes expose toggles
        rig.g.send_message(addr, v)
        time.sleep(0.15)
        seq.append((v, rig.read_chan("input", hw, path)))
        print(f"  wrote {v} -> read {seq[-1][1]}")
    absolute = [s[1] for s in seq] == [1.0, 1.0, 0.0, 0.0]
    rig.g.send_message(addr, float(orig))
    time.sleep(0.15)
    restored = rig.read_chan("input", hw, path)
    print(f"restored -> {restored} (orig {orig})")
    print("HW-3: ABSOLUTE SET — PASS" if absolute else
          "HW-3: NOT ABSOLUTE (momentary/toggle?) — FAIL, transport needs "
          "edge handling")


# ── HW-6: balpan write path + range ────────────────────────────────
def gate_pan(rig, args):
    src, i, o = "in", args.in_ch, args.out_ch
    addr = f"/mix/{src}/{i}/{o}/balpan"
    orig = rig.read_mix(src, i, o, "balpan")
    print(f"orig balpan {i}->{o} = {orig}")
    if orig is None:
        print("HW-6: no balpan entry in /sendmix — FAIL/investigate")
        return
    results = []
    for v in (-1.0, 1.0, 0.5, 0.0):
        rig.g.send_message(addr, v)
        time.sleep(0.15)
        rb = rig.read_mix(src, i, o, "balpan")
        results.append((v, rb))
        print(f"  wrote {v} -> readback {rb}")
    rig.g.send_message(addr, float(orig))
    time.sleep(0.15)
    print(f"restored -> {rig.read_mix(src, i, o, 'balpan')} (orig {orig})")
    ok = all(rb is not None and abs(rb - v) < 0.02 for v, rb in results)
    print("HW-6: balpan -1..+1 accepted & echoed exactly — PASS" if ok
          else f"HW-6: mismatch {results} — FAIL")


# ── HW-8: linked-pair addressing ───────────────────────────────────
def gate_stereo(rig, args):
    left = args.left
    right = left + 1
    rig.rechan("input", left)
    st = rig.rx.state
    name_l = st.channel_names("inputs").get(left)
    name_r = st.channel_names("inputs").get(right)
    linked = st.stereo.get("inputs", {}).get(left)
    print(f"left {left}: name={name_l!r} stereo={linked}; "
          f"right {right}: name={name_r!r}")
    if not linked:
        print("HW-8: channel not linked — pick a linked pair")
        return
    # faderlin is WRITE-side only — dumps report 'fader' in dB, so
    # expectations go through the RME curve
    addr_l = f"/mix/in/{left}/0/faderlin"
    addr_r = f"/mix/in/{right}/0/faderlin"
    orig_db_l = rig.read_mix("in", left, 0, "fader")
    orig_db_r = rig.rx.state.get_mix("in", right, 0, "fader")
    orig_db_r = orig_db_r[0] if orig_db_r else None
    print(f"orig fader dB: left={orig_db_l} right={orig_db_r}")
    exp1 = gu.fader_db(0.42)
    rig.g.send_message(addr_l, 0.42)
    time.sleep(0.15)
    l1 = rig.read_mix("in", left, 0, "fader")
    print(f"wrote LEFT faderlin 0.42 -> left dB {l1} (curve predicts {exp1:.2f})")
    rig.g.send_message(addr_r, 0.37)
    time.sleep(0.15)
    l2 = rig.read_mix("in", left, 0, "fader")
    r2 = rig.rx.state.get_mix("in", right, 0, "fader")
    r2 = r2[0] if r2 else None
    exp2 = gu.fader_db(0.37)
    print(f"wrote RIGHT faderlin 0.37 -> left dB {l2}, right dB {r2} "
          f"(0.37 would be {exp2:.2f} dB)")
    # restore via faderlin from the measured original dB
    restore_lin = gu.fader_lin(orig_db_l if orig_db_l is not None else -300.0)
    rig.g.send_message(addr_l, float(restore_lin))
    time.sleep(0.15)
    back = rig.read_mix("in", left, 0, "fader")
    print(f"restored -> left dB {back} (orig {orig_db_l})")
    left_ok = l1 is not None and abs(l1 - exp1) < 0.1
    right_moved_left = l2 is not None and l1 is not None and abs(l2 - l1) > 0.1
    right_own = r2 is not None and abs(r2 - exp2) < 0.1
    print(f"HW-8: left-member write {'PASS' if left_ok else 'FAIL'}; "
          f"right-member while linked: "
          f"{'MOVED THE PAIR' if right_moved_left else 'left unchanged'}"
          f" / right slot {'updated' if right_own else 'not updated'}")


# ── HW-5a: FX calibration (classic 3-point) ────────────────────────
FX_PARAMS = [
    # (name, classic_addr, global_path)
    ("reverb_time",     "/3/reverbTime",     "reverb/time"),
    ("reverb_volume",   "/3/reverbVolume",   "reverb/volume"),
    ("reverb_width",    "/3/reverbWidth",    "reverb/width"),
    ("reverb_predelay", "/3/reverbPredelay", "reverb/predelay"),
    ("echo_time",       "/3/echoDelaytime",  "echo/delay"),
    ("echo_feedback",   "/3/echoFeedback",   "echo/feedback"),
    ("echo_volume",     "/3/echoVolume",     "echo/volume"),
    ("echo_width",      "/3/echoWidth",      "echo/width"),
]


def gate_calfx(rig, args):
    # refuse if either FX is enabled (calibration sweeps would be audible)
    rig.g.send_message("/sendall", 1.0)
    time.sleep(4.0)
    for en in ("reverb/enable", "echo/enable"):
        e = rig.rx.state.get_param("fx", 0, en)
        if e and float(e[0]) >= 0.5:
            sys.exit(f"{en} is ON — refusing to sweep audible FX params")
    originals = {}
    for name, _, gpath in FX_PARAMS:
        e = rig.rx.state.get_param("fx", 0, gpath)
        originals[name] = e[0] if e else None
    print("originals:", originals)
    print(f"{'param':16} {'lo':>10} {'hi':>10}  curve   points")
    for name, caddr, gpath in FX_PARAMS:
        pts = rig.classic_3point(
            caddr, lambda t0: rig.wait_param("fx", 0, gpath, t0))
        verdict, lo, hi = curve_verdict(pts)
        print(f"{name:16} {lo!s:>10} {hi!s:>10}  {verdict:7} {pts}")
        # restore in now-known real units
        if originals[name] is not None:
            rig.g.send_message(f"/{gpath}", float(originals[name]))
            time.sleep(0.1)
    # verify restores with one fresh dump
    t0 = time.time()
    rig.g.send_message("/sendall", 1.0)
    time.sleep(4.0)
    bad = []
    for name, _, gpath in FX_PARAMS:
        e = rig.rx.state.get_param("fx", 0, gpath)
        now = e[0] if e and e[1] > t0 else None
        if originals[name] is not None and (
                now is None or abs(now - originals[name]) > 1e-3):
            bad.append((name, originals[name], now))
    print("RESTORES VERIFIED" if not bad else f"RESTORE MISMATCH: {bad}")


# ── HW-5b: channel-detail calibration via bank-start aiming ────────
CH_PARAMS = [
    ("eq_gain_1",   "/2/eqGain1",   "eq/band1gain"),
    ("eq_freq_1",   "/2/eqFreq1",   "eq/band1freq"),
    ("eq_q_1",      "/2/eqQ1",      "eq/band1q"),
    ("eq_gain_3",   "/2/eqGain3",   "eq/band3gain"),
    ("eq_freq_2",   "/2/eqFreq2",   "eq/band2freq"),
    ("lowcut_freq", "/2/lowcutFreq", "lowcut/freq"),
    ("lowcut_grade", "/2/lowcutGrade", "lowcut/slope"),
    ("eq_type_1",   "/2/eqType1",   "eq/band1type"),
    ("comp_thresh", "/2/compTrsh",  "dynamics/compthres"),
]


def gate_calchannel(rig, args):
    hw = args.ch
    rig.rechan("input", hw)
    originals = {}
    for name, _, gpath in CH_PARAMS:
        e = rig.rx.state.get_param("inputs", hw, gpath)
        originals[name] = e[0] if e else None
    print(f"channel {hw} originals:", originals)

    # aim classic page 2 at hw (page 2 mirrors the bank-start channel)
    rig.c.send_message("/1/busInput", 1.0)
    time.sleep(0.2)
    rig.c.send_message("/setBankStart", float(hw))
    time.sleep(0.3)
    try:
        # aim self-verification: nudge band1 gain to classic max and see it
        # land on THIS hw channel's global feedback
        t0 = time.time()
        rig.c.send_message("/2/eqGain1", 1.0)
        val, ok = rig.wait_param("inputs", hw, "eq/band1gain", t0)
        if not ok:
            print("AIM FAILED — /2/eqGain1 did not land on the target "
                  "channel; aborting (nothing else written)")
            return
        print(f"aim verified: /2/eqGain1 1.0 -> hw {hw} band1gain = {val}")

        print(f"{'param':13} {'lo':>9} {'hi':>9}  curve   points")
        for name, caddr, gpath in CH_PARAMS:
            pts = rig.classic_3point(
                caddr, lambda t0, g=gpath: rig.wait_param("inputs", hw, g, t0))
            verdict, lo, hi = curve_verdict(pts)
            print(f"{name:13} {lo!s:>9} {hi!s:>9}  {verdict:7} {pts}")
            if originals[name] is not None:
                rig.g.send_message(f"/input/{hw}/{gpath}",
                                   float(originals[name]))
                time.sleep(0.1)
    finally:
        rig.c.send_message("/setBankStart", 0.0)   # classic invariant
        time.sleep(0.1)

    # verify restores
    rig.rechan("input", hw)
    bad = []
    for name, _, gpath in CH_PARAMS:
        e = rig.rx.state.get_param("inputs", hw, gpath)
        now = e[0] if e else None
        if originals[name] is not None and (
                now is None or abs(now - originals[name]) > 1e-3):
            bad.append((name, originals[name], now))
    print("RESTORES VERIFIED" if not bad else f"RESTORE MISMATCH: {bad}")


# ── HW-7 + wash: recall the active snapshot ────────────────────────
def gate_wash(rig, args):
    n = args.snap
    # snapshot slot states only arrive in a full dump (or on change)
    rig.g.send_message("/sendall", 1.0)
    rig.rx.wait_for(lambda s: len(s.snapshots) >= 8, 8.0)
    pre = dict(rig.rx.state.snapshots)
    print(f"snapshot state pre-wash: {pre}")
    if pre.get(n) not in (2.0, 3.0):
        # 2=active unmodified, 3=active+changed (our session's writes)
        sys.exit(f"slot {n} is not the active snapshot ({pre}) — "
                 f"refusing to recall")
    with rig.rx.state._lock:
        rig.rx.state.snapshots.pop(n, None)   # force fresh confirmation
    rig.g.send_message(f"/snapshot/load/{n}", 1.0)
    ok = rig.rx.wait_for(lambda s: s.snapshots.get(n) == 2.0, 4.0)
    print(f"recalled /snapshot/load/{n} -> confirmed 2.0: {ok} "
          f"(state now {dict(rig.rx.state.snapshots)})")
    print("HW-7: 1-based recall + feedback confirm — "
          + ("PASS (device state washed to stored snapshot)" if ok else
             "NO CONFIRM — investigate before trusting load_snapshot"))


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("enables"); s.add_argument("--ch", type=int, default=26)
    s = sub.add_parser("pan")
    s.add_argument("--in-ch", type=int, default=26)
    s.add_argument("--out-ch", type=int, default=0)
    s = sub.add_parser("stereo"); s.add_argument("--left", type=int, default=28)
    sub.add_parser("calfx")
    s = sub.add_parser("calchannel"); s.add_argument("--ch", type=int, default=26)
    s = sub.add_parser("wash"); s.add_argument("--snap", type=int, default=4)
    args = ap.parse_args()
    rig = Rig()
    try:
        {"enables": gate_enables, "pan": gate_pan, "stereo": gate_stereo,
         "calfx": gate_calfx, "calchannel": gate_calchannel,
         "wash": gate_wash}[args.cmd](rig, args)
    finally:
        rig.stop()


if __name__ == "__main__":
    main()
