"""Workspace/snapshot switches wait on device confirmation, not fixed sleeps.

The old code slept 1.0s after /loadQuickWorkspace and 0.3s after a snapshot
recall on every switching fire. With a listener, the bridge now waits for the
feedback that marks each transition and proceeds the moment it arrives.
"""
import time

from osc_listener import OSCListener


SWITCH_MACRO = {
    "workspace": "Pill_setup",
    "snapshot": "Reset",
    "steps": [{"osc": "/1/volume1", "value": "{{param}}"}],
}


class EchoingTotalMix:
    """Echoes the feedback a real device sends for workspace loads (full
    state dump incl. labelSubmix) and snapshot recalls (button state)."""

    def __init__(self, listener, fake_osc, delay=0.0):
        self.listener = listener
        self.fake_osc = fake_osc
        self.delay = delay

    def send_message(self, address, value):
        self.fake_osc.send_message(address, value)
        if self.delay:
            time.sleep(self.delay)
        if address == "/loadQuickWorkspace":
            self.listener._handle("/1/labelSubmix", "Main")
        elif address.startswith("/3/snapshots/"):
            self.listener._handle(address, 1.0)


def test_switch_completes_on_confirmation_not_fixed_sleeps(make_bridge, fake_osc):
    listener = OSCListener(0)
    listener._server = object()
    b = make_bridge({"m": SWITCH_MACRO})
    b.osc_listener = listener
    b.osc_client = EchoingTotalMix(listener, fake_osc)

    t0 = time.time()
    b.run_macro("m", 0.5)
    elapsed = time.time() - t0

    # Old behavior: >= 1.3s of unconditional sleeping. With instant
    # confirmation the whole macro must finish far quicker.
    assert elapsed < 0.5, f"switch took {elapsed:.2f}s — fixed sleeps still present?"
    assert ("/loadQuickWorkspace", 2.0) in fake_osc.sent
    assert ("/3/snapshots/8/1", 1.0) in fake_osc.sent
    assert ("/1/volume1", 0.5) in fake_osc.sent


def test_switch_still_ordered_with_slow_device(make_bridge, fake_osc):
    """Confirmation arriving late (0.2s) must still gate the next command —
    ordering is preserved, just without over-waiting."""
    listener = OSCListener(0)
    listener._server = object()
    b = make_bridge({"m": SWITCH_MACRO})
    b.osc_listener = listener
    b.osc_client = EchoingTotalMix(listener, fake_osc, delay=0.2)

    b.run_macro("m", 0.5)
    order = fake_osc.addresses()
    assert order.index("/loadQuickWorkspace") < order.index("/3/snapshots/8/1") \
        < order.index("/1/volume1")


def test_no_listener_keeps_fallback_sleeps(make_bridge, fake_osc):
    b = make_bridge({"m": SWITCH_MACRO})
    assert b.osc_listener is None
    t0 = time.time()
    b.run_macro("m", 0.5)
    # Fallback path preserves the historical worst-case pacing (1.0 + 0.3)
    assert time.time() - t0 >= 1.2
    assert ("/1/volume1", 0.5) in fake_osc.sent
