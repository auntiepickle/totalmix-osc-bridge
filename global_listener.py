"""Global OSC feedback listener (#25).

Deliberately a SEPARATE implementation from osc_listener.py: DeviceState is
bank/strip/submix-scoped with burst heuristics that are meaningless under
absolute addressing. The proven scaffolding PATTERNS are mirrored —
blocking single-threaded server (ordering), event-driven wait_for waiters,
per-message exception containment, stop() that closes the socket — but the
state model is (row, hw_channel, param) with REAL units and timestamps.

Wire facts baked in (verified 2026-08-20/21): feedback arrives as OSC
bundles (python-osc unframes them before dispatch — verified live, HW-1);
cyclic status heartbeat ~1/s on /status/device|connection|dsp; channel
numbers are 0-based hardware-mono — identical to physical_table keys;
names arrive at the LEFT member only (unlike page-2 sweeps).
"""
import logging
import threading
import time

from pythonosc.dispatcher import Dispatcher
from pythonosc.osc_server import BlockingOSCUDPServer

logger = logging.getLogger(__name__)

ROW_KEYS = {"input": "inputs", "playback": "playbacks", "output": "outputs"}


class GlobalDeviceState:
    """Thread-safe store of everything Global OSC has told us."""

    def __init__(self):
        self._lock = threading.Lock()
        self.params = {}      # (row_key, hw, param_path) -> {"value", "ts"}
        self.names = {}       # row_key -> {hw: {"name", "ts"}}
        self.stereo = {}      # row_key -> {hw: bool}
        self.mix = {}         # (src 'in'|'pb', in_hw, out_hw, param) -> {"value","ts"}
        self.status = {}      # status name -> value
        self.snapshots = {}   # snap_num -> feedback value (0 off / 2 active / 3 changed)
        self.last_heartbeat = 0.0
        self.message_count = 0
        self.pending_name_changes = set()   # (row_key, hw) — drained by the sync

    def heartbeat_age(self):
        with self._lock:
            if not self.last_heartbeat:
                return None
            return time.time() - self.last_heartbeat

    def get_param(self, row_key, hw, param_path):
        with self._lock:
            e = self.params.get((row_key, int(hw), param_path))
            return (e["value"], e["ts"]) if e else None

    def get_mix(self, src, in_hw, out_hw, param):
        with self._lock:
            e = self.mix.get((src, int(in_hw), int(out_hw), param))
            return (e["value"], e["ts"]) if e else None

    def channel_names(self, row_key):
        with self._lock:
            return {hw: e["name"] for hw, e in self.names.get(row_key, {}).items()}

    def ingest(self, address, args):
        now = time.time()
        arg0 = args[0] if args else None
        parts = address.strip("/").split("/")
        if not parts:
            return
        head = parts[0]
        with self._lock:
            self.message_count += 1
            if head == "status":
                if len(parts) >= 2:
                    self.status[parts[1]] = arg0
                self.last_heartbeat = now
                return
            if head == "level":
                return  # high-rate meters — deliberately not stored
            if head in ("in", "input", "playback", "output") and head in ROW_KEYS:
                if len(parts) < 3:
                    return
                row_key = ROW_KEYS[head]
                try:
                    hw = int(parts[1])
                except ValueError:
                    return
                path = "/".join(parts[2:])
                if path == "name":
                    name = str(arg0).strip() if arg0 is not None else ""
                    prev = self.names.setdefault(row_key, {}).get(hw, {}).get("name")
                    self.names[row_key][hw] = {"name": name, "ts": now}
                    if name and name != prev:
                        self.pending_name_changes.add((row_key, hw))
                    return
                if path == "stereo":
                    self.stereo.setdefault(row_key, {})[hw] = bool(
                        arg0 is not None and float(arg0) >= 0.5)
                    # a link change re-scopes the pair's alias merge
                    self.pending_name_changes.add((row_key, hw))
                    return
                self.params[(row_key, hw, path)] = {"value": arg0, "ts": now}
                return
            if head == "mix" and len(parts) >= 5:
                src = parts[1]              # 'in' | 'pb'
                try:
                    in_hw, out_hw = int(parts[2]), int(parts[3])
                except ValueError:
                    return
                param = "/".join(parts[4:])
                self.mix[(src, in_hw, out_hw, param)] = {"value": arg0, "ts": now}
                return
            if head == "snapshot" and len(parts) >= 3 and parts[1] == "load":
                try:
                    self.snapshots[int(parts[2])] = float(arg0 or 0)
                except (ValueError, TypeError):
                    pass
                return
            # everything else (reverb/echo/controlroom/...) → flat store
            self.params[("fx", 0, address.strip("/"))] = {"value": arg0, "ts": now}

    def drain_name_changes(self):
        with self._lock:
            changed = list(self.pending_name_changes)
            self.pending_name_changes.clear()
            return changed


class GlobalOSCListener:
    """UDP server feeding GlobalDeviceState. Same waiter/containment
    patterns as the classic OSCListener."""

    def __init__(self, port):
        self.port = port
        self.state = GlobalDeviceState()
        self._server = None
        self._thread = None
        self._waiters = []
        self._waiters_lock = threading.Lock()

    def wait_for(self, predicate, timeout):
        if predicate(self.state):
            return True
        ev = threading.Event()
        entry = (predicate, ev)
        with self._waiters_lock:
            self._waiters.append(entry)
        try:
            if predicate(self.state):
                return True
            return ev.wait(timeout)
        finally:
            with self._waiters_lock:
                if entry in self._waiters:
                    self._waiters.remove(entry)

    def start(self):
        dispatcher = Dispatcher()
        dispatcher.set_default_handler(self._handle, needs_reply_address=False)
        try:
            # Blocking single-threaded: ordering within a bundle burst is
            # preserved; handlers are microseconds of dict work
            self._server = BlockingOSCUDPServer(("0.0.0.0", self.port), dispatcher)
        except OSError as e:
            logger.error(f"Global OSC listener could not bind UDP {self.port}: {e}")
            self._server = None
            return False
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        logger.info(f"Global OSC listener started → UDP {self.port}")
        return True

    def stop(self):
        if self._server:
            self._server.shutdown()
            self._server.server_close()   # else EADDRINUSE on restart
            self._server = None
            if self._thread:
                self._thread.join(timeout=2.0)
            logger.info("Global OSC listener stopped")

    @property
    def running(self):
        return self._server is not None

    def _handle(self, address, *args):
        try:
            self.state.ingest(address, args)
        except Exception as e:
            # one malformed element must never drop the rest of the packet
            logger.warning(f"Global OSC ingest error on {address} {args!r}: {e}")
            return
        if self._waiters:
            with self._waiters_lock:
                waiters = list(self._waiters)
            for predicate, ev in waiters:
                if not ev.is_set():
                    try:
                        if predicate(self.state):
                            ev.set()
                    except Exception:
                        ev.set()
