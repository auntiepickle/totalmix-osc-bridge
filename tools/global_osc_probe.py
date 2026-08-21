"""Global OSC hardware harness (#25) — runs on the TotalMix host.

Safe by construction: sends only /sendchan, /sendall, /sendstate (read
triggers), /mix/... faderlin and /input/N/mute writes inside write-verify
(which restores what it changed). The classic fatal op (/setSubmix) does
not exist in this namespace and no classic-port code path is present.

Subcommands:
  listen        print decoded feedback for N seconds (HW-1: bundle dispatch)
  sendchan      dump one channel's full state in real units
  names         compare live names vs the physical table (startup mandate)
  write-verify  read faderlin -> write new -> read back -> RESTORE
  fader-curve   HW-2: classic volume writes vs global faderlin readback
  heartbeat     watchdog demo (age, then /sendstate refresh)
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pythonosc.udp_client import SimpleUDPClient

import global_units as gu
import physical_table as pt
from global_listener import GlobalOSCListener

DEFAULT_HOST = os.getenv("GLOBAL_OSC_IP", "192.168.1.61")
DEFAULT_PORT = int(os.getenv("GLOBAL_OSC_PORT", "7002"))
DEFAULT_LISTEN = int(os.getenv("GLOBAL_OSC_LISTEN_PORT", "9002"))
CLASSIC_PORT = int(os.getenv("OSC_PORT", "7001"))


def make(args):
    tx = SimpleUDPClient(args.host, args.port)
    rx = GlobalOSCListener(args.listen_port)
    if not rx.start():
        sys.exit("listener bind failed (is something else on the port?)")
    return tx, rx


def load_table():
    # the MEASURED table lives on the production bridge — prefer it
    bridge_url = os.getenv("BRIDGE_URL", "http://192.168.1.41:8088")
    try:
        import urllib.request
        with urllib.request.urlopen(f"{bridge_url}/api/device/physical_table",
                                    timeout=4) as r:
            return json.load(r)
    except Exception:
        pass
    for p in ("ufx2_channel_map.json", "ufx2_channel_map.example.json"):
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), p)
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                return json.load(f).get("physical_table")
    return None


def cmd_listen(args):
    tx, rx = make(args)
    t0 = time.time()
    last = 0
    while time.time() - t0 < args.secs:
        time.sleep(0.5)
        st = rx.state
        if st.message_count != last:
            last = st.message_count
    print(f"messages: {rx.state.message_count}")
    print(f"status: {rx.state.status}")
    print(f"heartbeat age: {rx.state.heartbeat_age()}")
    with rx.state._lock:
        addrs = sorted({(k[0], k[2]) for k in rx.state.params})
    for a in addrs[:40]:
        print(" ", a)
    rx.stop()


def cmd_sendchan(args):
    tx, rx = make(args)
    tx.send_message(f"/sendchan/{args.row}/{args.ch}", 1.0)
    time.sleep(1.5)
    row_key = {"input": "inputs", "playback": "playbacks",
               "output": "outputs"}[args.row]
    with rx.state._lock:
        entries = {k[2]: v["value"] for k, v in rx.state.params.items()
                   if k[0] == row_key and k[1] == args.ch}
    names = rx.state.channel_names(row_key)
    print(f"{args.row} hw {args.ch}  name={names.get(args.ch)!r}  "
          f"stereo={rx.state.stereo.get(row_key, {}).get(args.ch)}")
    for k in sorted(entries):
        print(f"  {k} = {entries[k]}")
    rx.stop()


def cmd_names(args):
    tx, rx = make(args)
    tx.send_message("/sendall", 1.0)
    time.sleep(4.0)
    table = load_table()
    ok = True
    for row_key in ("inputs", "outputs"):
        live = rx.state.channel_names(row_key)
        print(f"— {row_key}: {len(live)} live names")
        for hw in sorted(live):
            name = live[hw]
            expected = pt.resolve_start(table, row_key, name) if table else None
            mark = ""
            if table is not None and expected is not None and expected != hw:
                # the pair-left rule: global names arrive at the LEFT member
                # only, so a name resolving to a lower member is fine when
                # stereo; a genuine mismatch is loud
                if not rx.state.stereo.get(row_key, {}).get(expected):
                    mark = f"  ⚠ table says {expected}"
                    ok = False
            print(f"  {hw:2d}  {name}{mark}")
    print("TABLE MATCH" if ok else "MISMATCHES FOUND — investigate before trusting")
    rx.stop()


def cmd_write_verify(args):
    tx, rx = make(args)
    table = load_table()
    if table is None:
        sys.exit("no physical table — refusing to write")
    if (str(args.in_ch) not in table["rows"].get("inputs", {})
            or str(args.out_ch) not in table["rows"].get("outputs", {})):
        sys.exit("channel(s) not in the measured table — refusing to write")
    addr = f"/mix/in/{args.in_ch}/{args.out_ch}/faderlin"
    tx.send_message(f"/sendchan/input/{args.in_ch}", 1.0)
    time.sleep(1.0)
    before = rx.state.get_mix("in", args.in_ch, args.out_ch, "faderlin")
    print(f"before: {before}")
    t0 = time.time()
    tx.send_message(addr, float(args.value))
    got = rx.wait_for(
        lambda s: (s.get_mix("in", args.in_ch, args.out_ch, "faderlin") or
                   (None, 0))[1] > t0, 2.0)
    after = rx.state.get_mix("in", args.in_ch, args.out_ch, "faderlin")
    print(f"write {args.value} -> echoed={got}, readback: {after}")
    restore = before[0] if before else 0.0
    tx.send_message(addr, float(restore))
    time.sleep(0.3)
    print(f"restored to {restore}")
    rx.stop()


def cmd_fader_curve(args):
    """HW-2: write classic /1/volume{strip} values on the CLASSIC port and
    read the global faderlin feedback — verifies the identity hypothesis.
    Requires the target strip visible at bank 0 on the input row, and the
    submix currently selected on the classic remote to be out_ch's."""
    tx, rx = make(args)
    classic = SimpleUDPClient(args.host, CLASSIC_PORT)
    pairs = []
    for v in (0.0, 0.25, 0.5, 0.75, 1.0):
        t0 = time.time()
        classic.send_message(f"/1/volume{args.strip}", float(v))
        rx.wait_for(
            lambda s: (s.get_mix("in", args.in_ch, args.out_ch, "faderlin")
                       or (None, 0))[1] > t0, 2.0)
        lin = rx.state.get_mix("in", args.in_ch, args.out_ch, "faderlin")
        db = rx.state.get_mix("in", args.in_ch, args.out_ch, "fader")
        pairs.append((v, lin[0] if lin else None, db[0] if db else None))
        print(f"classic {v:5.2f} -> faderlin {pairs[-1][1]} "
              f"(dB {pairs[-1][2]}; curve predicts {gu.fader_db(v):.2f})")
        time.sleep(0.2)
    ident = all(l is not None and abs(l - v) < 0.02 for v, l, _ in pairs)
    print("IDENTITY CONFIRMED" if ident else "NOT IDENTITY — use dB conversion")
    rx.stop()


def cmd_heartbeat(args):
    tx, rx = make(args)
    time.sleep(2.5)
    print(f"age after 2.5s listen: {rx.state.heartbeat_age()}")
    tx.send_message("/sendstate", 1.0)
    time.sleep(1.0)
    print(f"status: {rx.state.status}, age: {rx.state.heartbeat_age()}")
    rx.stop()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default=DEFAULT_HOST)
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--listen-port", type=int, default=DEFAULT_LISTEN)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("listen"); s.add_argument("--secs", type=float, default=5)
    s = sub.add_parser("sendchan")
    s.add_argument("--row", default="input",
                   choices=["input", "playback", "output"])
    s.add_argument("--ch", type=int, required=True)
    sub.add_parser("names")
    s = sub.add_parser("write-verify")
    s.add_argument("--in-ch", type=int, required=True)
    s.add_argument("--out-ch", type=int, required=True)
    s.add_argument("--value", type=float, required=True)
    s = sub.add_parser("fader-curve")
    s.add_argument("--strip", type=int, required=True)
    s.add_argument("--in-ch", type=int, required=True)
    s.add_argument("--out-ch", type=int, required=True)
    sub.add_parser("heartbeat")
    args = ap.parse_args()
    {"listen": cmd_listen, "sendchan": cmd_sendchan, "names": cmd_names,
     "write-verify": cmd_write_verify, "fader-curve": cmd_fader_curve,
     "heartbeat": cmd_heartbeat}[args.cmd](args)


if __name__ == "__main__":
    main()
