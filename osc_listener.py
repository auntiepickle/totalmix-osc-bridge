"""Structured OSC feedback listener.

TotalMix FX pushes its state over OSC to a configured outgoing port whenever
the visible bank changes (submix select, bank paging, fader moves, snapshot
recalls). osc_monitor.py only logged that traffic for humans to read; this
module parses it into structured, queryable state so the API and the mapping
UI can consume real device data.

Feedback addresses parsed (row is 1=input, 2=playback, 3=output):
    /1/labelSubmix        str    name of the currently selected submix
    /{row}/trackname{n}   str    channel name for fader n of the visible bank
    /{row}/volume{n}      float  fader position 0.0-1.0
    /{row}/volume{n}Val   str    display value ("-6.0 dB")
    /{row}/pan{n}         float  pan position

Everything else still lands in the raw address store, so unknown feedback is
visible via /api/device/state instead of lost.
"""
import re
import threading
import time
import logging

from pythonosc.dispatcher import Dispatcher
from pythonosc.osc_server import ThreadingOSCUDPServer

logger = logging.getLogger(__name__)

_CHANNEL_RE = re.compile(r"^/([123])/(trackname|volume|pan)(\d+)(Val)?$")

UNKNOWN_SUBMIX = "_unselected"


class DeviceState:
    """Thread-safe accumulator for TotalMix OSC feedback."""

    def __init__(self):
        self._lock = threading.Lock()
        self.raw = {}              # address -> {"args", "count", "last_seen"}
        self.current_submix = None # name from /1/labelSubmix
        # submix name -> row -> channel -> {"name","volume","volume_db","pan"}
        self.submixes = {}
        self.last_message_at = None

    # ── ingestion ─────────────────────────────────────────────────────────

    def ingest(self, address, args):
        now = time.time()
        with self._lock:
            self.last_message_at = now
            entry = self.raw.setdefault(address, {"count": 0})
            entry["args"] = list(args)
            entry["count"] += 1
            entry["last_seen"] = now

            if address == "/1/labelSubmix" and args:
                name = str(args[0]).strip()
                if name:
                    self.current_submix = name
                    self.submixes.setdefault(name, {})
                return True  # structural change

            m = _CHANNEL_RE.match(address)
            if not m:
                return False
            row, field, ch, is_val = m.group(1), m.group(2), int(m.group(3)), m.group(4)

            if row == "3":
                submix_key = "_outputs"  # output faders are not submix-scoped
            else:
                submix_key = self.current_submix or UNKNOWN_SUBMIX
            channels = (self.submixes.setdefault(submix_key, {})
                        .setdefault(row, {})
                        .setdefault(ch, {}))

            if field == "trackname" and args:
                channels["name"] = str(args[0])
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
                "submixes": {
                    name: {row: dict(chs) for row, chs in rows.items()}
                    for name, rows in self.submixes.items()
                },
                "raw_addresses": {
                    addr: dict(entry) for addr, entry in self.raw.items()
                },
                "last_message_at": self.last_message_at,
            }

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
        if self.broadcast_cb is None:
            return
        now = time.time()
        if structural or (now - self._last_broadcast) >= self.THROTTLE_S:
            self._last_broadcast = now
            try:
                self.broadcast_cb()
            except Exception as e:
                logger.debug(f"device_update broadcast failed: {e}")
