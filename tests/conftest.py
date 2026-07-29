import os
import sys
import threading

import pytest

# Repo root on sys.path so tests import modules the same way uvicorn does
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Never let the suite talk to a real interface, even if a .env leaked into the
# environment. Must happen before any module under test is imported.
os.environ.pop("OSC_IP", None)
os.environ["ENABLE_OSC_MONITOR"] = "False"


class FakeOSCClient:
    """Records every send_message call instead of hitting the network."""

    def __init__(self):
        self.sent = []  # list of (address, value) tuples
        self._lock = threading.Lock()

    def send_message(self, address, value):
        with self._lock:
            self.sent.append((address, value))

    def addresses(self):
        return [a for a, _ in self.sent]

    def clear(self):
        with self._lock:
            self.sent.clear()


@pytest.fixture
def fake_osc():
    return FakeOSCClient()


@pytest.fixture
def snapshot_map():
    return {
        "Pill_setup": {
            "slot": 2,
            "snapshots": {"1": "Reset", "2": "Live"},
        },
    }


@pytest.fixture
def make_bridge(fake_osc, snapshot_map):
    """Build an isolated TotalMixOSCBridge wired to the fake OSC client.

    broadcast_state is replaced with a recorder so tests can assert on
    macro_start / macro_complete / macro_skipped events without an event loop.
    """
    from bridge import TotalMixOSCBridge

    def _make(macros, snap_map=None):
        b = TotalMixOSCBridge(
            fake_osc,
            {"macros": macros},
            snap_map if snap_map is not None else snapshot_map,
        )
        b.events = []
        b.broadcast_state = lambda macro_update=None, macro_event=None: (
            b.events.append({"update": macro_update, "event": macro_event})
        )
        return b

    return _make
