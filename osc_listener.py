"""Structured OSC feedback listener.

TotalMix FX pushes its state over OSC to a configured outgoing port whenever
the visible bank changes (submix select, bank paging, fader moves, snapshot
recalls). osc_monitor.py only logged that traffic for humans to read; this
module parses it into structured, queryable state so the API and the mapping
UI can consume real device data.

Feedback addresses parsed:
    /1/labelSubmix        str    name of the currently selected submix
    /1/busInput           1.0    input row selected    -> row 1
    /1/busPlayback        1.0    playback row selected -> row 2
    /1/busOutput          1.0    output row selected   -> row 3
    /1/trackname{n}       str    channel name for fader n of the visible bank
    /1/volume{n}          float  fader position 0.0-1.0
    /1/volume{n}Val       str    display value ("-6.0 dB")
    /1/pan{n}             float  pan position

Page-1 channel messages refer to whichever ROW is currently selected via the
bus* toggles — the page number is not the row. Channel data is filed under
the active row (default: input). Legacy /2/... and /3/... page messages keep
their page number as the row.

Everything else still lands in the raw address store, so unknown feedback is
visible via /api/device/state instead of lost.
"""
import re
import threading
import time
import logging
from collections import deque

from pythonosc.dispatcher import Dispatcher
from pythonosc.osc_server import ThreadingOSCUDPServer

logger = logging.getLogger(__name__)

_CHANNEL_RE = re.compile(r"^/([123])/(trackname|volume|pan)(\d+)(Val)?$")
_MUTE_RE = re.compile(r"^/1/mute/1/(\d+)$")

_BUS_ROW = {"/1/busInput": "1", "/1/busPlayback": "2", "/1/busOutput": "3"}

UNKNOWN_SUBMIX = "_unselected"


class DeviceState:
    """Thread-safe accumulator for TotalMix OSC feedback."""

    def __init__(self):
        self._lock = threading.Lock()
        self.raw = {}              # address -> {"args", "count", "last_seen"}
        self.message_count = 0     # total ingested messages (liveness probes)
        self.current_submix = None # name from /1/labelSubmix
        self.current_row = "1"     # from /1/busInput|busPlayback|busOutput
        # submix name -> row -> channel -> {"name","volume","volume_db","pan"}
        self.submixes = {}
        self.last_message_at = None
        # Bank width estimate: highest trackname index in the most recent
        # dump burst. TotalMix's per-WORKSPACE 'Number of Faders per Bank'
        # caps what OSC can see — the UI warns when it is too narrow.
        self.bank_width = None
        self._burst_max_strip = 0
        # Real strips (trackname not 'n.a.') — the UI compares this against
        # the channel map to catch a STALE map (map under-covering the
        # device went undetected once). High-water mark over the last few
        # bursts: OSC is UDP, and a single dropped trackname packet in one
        # burst must not undercount (an undercount SUPPRESSES the stale-map
        # banner — fail-silent). A genuine layout shrink propagates once a
        # few complete smaller bursts confirm it.
        self.real_strip_count = None
        self._burst_real_strips = set()
        self._burst_history = deque(maxlen=3)

    # ── ingestion ─────────────────────────────────────────────────────────

    def ingest(self, address, args):
        now = time.time()
        with self._lock:
            self.last_message_at = now
            self.message_count += 1
            entry = self.raw.setdefault(address, {"count": 0})
            entry["args"] = list(args)
            entry["count"] += 1
            entry["last_seen"] = now

            if address == "/1/labelSubmix" and args:
                name = str(args[0]).strip()
                if name:
                    self.current_submix = name
                    self.submixes.setdefault(name, {})
                # A label marks a new dump burst — the finished burst's max
                # strip is the authoritative bank width (may shrink after a
                # workspace load reverts the faders-per-bank setting)
                if self._burst_max_strip:
                    self.bank_width = self._burst_max_strip
                self._burst_max_strip = 0
                if self._burst_real_strips:
                    self._burst_history.append(len(self._burst_real_strips))
                    self.real_strip_count = max(self._burst_history)
                self._burst_real_strips = set()
                return True  # structural change

            if address in _BUS_ROW and args and float(args[0]) == 1.0:
                self.current_row = _BUS_ROW[address]
                return True  # row switch rescopes subsequent channel data

            # Mute grid: /1/mute/1/{strip} — page-1, follows the selected row
            mm = _MUTE_RE.match(address)
            if mm and args:
                ch = int(mm.group(1))
                row_key = self.current_row
                submix_key = ("_outputs" if row_key == "3"
                              else self.current_submix or UNKNOWN_SUBMIX)
                (self.submixes.setdefault(submix_key, {})
                     .setdefault(row_key, {})
                     .setdefault(ch, {}))["mute"] = float(args[0])
                return False

            m = _CHANNEL_RE.match(address)
            if not m:
                return False
            page, field, ch, is_val = m.group(1), m.group(2), int(m.group(3)), m.group(4)
            # Page 1 shows whichever row the bus* toggles selected; legacy
            # /2 and /3 pages keep their page number as the row.
            row = self.current_row if page == "1" else page

            if row == "3":
                submix_key = "_outputs"  # output faders are not submix-scoped
            else:
                submix_key = self.current_submix or UNKNOWN_SUBMIX
            channels = (self.submixes.setdefault(submix_key, {})
                        .setdefault(row, {})
                        .setdefault(ch, {}))

            if field == "trackname" and args:
                channels["name"] = str(args[0])
                if page == "1":
                    self._burst_max_strip = max(self._burst_max_strip, ch)
                    # Grow immediately (shrink only at burst boundaries)
                    if ch > (self.bank_width or 0):
                        self.bank_width = ch
                    if str(args[0]).strip().lower() not in ("n.a.", "n/a"):
                        self._burst_real_strips.add(ch)
                        if len(self._burst_real_strips) > (self.real_strip_count or 0):
                            self.real_strip_count = len(self._burst_real_strips)
                return True
            if field == "volume" and args:
                if is_val:
                    channels["volume_db"] = str(args[0])
                else:
                    channels["volume"] = float(args[0])
                return False
            if field == "pan" and args:
                channels["pan"] = float(args[0])
                return False
            return False

    # ── queries ───────────────────────────────────────────────────────────

    def to_dict(self):
        with self._lock:
            return {
                "current_submix": self.current_submix,
                "current_row": self.current_row,
                "bank_width": self.bank_width,
                "real_strip_count": self.real_strip_count,
                "message_count": self.message_count,
                "submixes": {
                    name: {row: dict(chs) for row, chs in rows.items()}
                    for name, rows in self.submixes.items()
                },
                "raw_addresses": {
                    addr: dict(entry) for addr, entry in self.raw.items()
                },
                "last_message_at": self.last_message_at,
            }

    def raw_entry(self, address):
        """Thread-safe copy of one raw-address entry ({args, count, ...})."""
        with self._lock:
            e = self.raw.get(address)
            return dict(e) if e else None

    def submix_snapshot(self, name):
        with self._lock:
            rows = self.submixes.get(name, {})
            return {row: {ch: dict(data) for ch, data in chs.items()}
                    for row, chs in rows.items()}


class OSCListener:
    """UDP OSC server feeding DeviceState. broadcast_cb is called (throttled)
    so the bridge can push device updates to WebSocket clients."""

    THROTTLE_S = 0.25

    def __init__(self, port, broadcast_cb=None):
        self.port = port
        self.state = DeviceState()
        self.broadcast_cb = broadcast_cb
        self._server = None
        self._thread = None
        self._last_broadcast = 0.0
        self._waiters = []            # (predicate, threading.Event)
        self._waiters_lock = threading.Lock()

    def wait_for(self, predicate, timeout):
        """Block until predicate(state) holds or timeout expires.

        Event-driven, not polled: the predicate is re-evaluated on every
        incoming OSC message and the waiter wakes the moment it turns true.
        The timeout is an error bound only (UDP is lossy and the protocol
        has no end-of-dump marker), not a pacing mechanism.
        Returns True if the predicate was satisfied.
        """
        if predicate(self.state):
            return True
        ev = threading.Event()
        entry = (predicate, ev)
        with self._waiters_lock:
            self._waiters.append(entry)
        try:
            # Close the race: a message may have landed between the first
            # check and registration
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
            self._server = ThreadingOSCUDPServer(("0.0.0.0", self.port), dispatcher)
        except OSError as e:
            logger.error(
                f"OSC listener could not bind UDP port {self.port} ({e}) — "
                "is ENABLE_OSC_MONITOR using the same port? Device capture disabled."
            )
            self._server = None
            return False
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        logger.info(f"OSC listener started → UDP port {self.port} (TotalMix feedback)")
        return True

    def stop(self):
        if self._server:
            self._server.shutdown()
            self._server = None
            logger.info("OSC listener stopped")

    @property
    def running(self):
        return self._server is not None

    def _handle(self, address, *args):
        # TotalMix sends "/" heartbeats — record nothing, they are just noise
        if address == "/":
            return
        structural = self.state.ingest(address, args)

        # Wake any wait_for() callers whose condition this message satisfied
        if self._waiters:
            with self._waiters_lock:
                waiters = list(self._waiters)
            for predicate, ev in waiters:
                if not ev.is_set():
                    try:
                        if predicate(self.state):
                            ev.set()
                    except Exception:
                        ev.set()  # broken predicate must not strand its waiter

        if self.broadcast_cb is None:
            return
        now = time.time()
        if structural or (now - self._last_broadcast) >= self.THROTTLE_S:
            self._last_broadcast = now
            try:
                self.broadcast_cb()
            except Exception as e:
                logger.debug(f"device_update broadcast failed: {e}")
