"""Per-client WebSocket send queues (#32): broadcasts only enqueue, one
sender task per connection drains in order, a full queue drops its oldest
frame, and a dead client detaches itself without touching the others."""
import asyncio

import pytest

import tmosc.bridge as bridge_module
from tmosc.bridge import ws_attach, ws_detach, ws_enqueue_all, ws_clients


class FakeWS:
    def __init__(self, fail_after=None):
        self.sent = []
        self.fail_after = fail_after

    async def send_json(self, payload):
        if self.fail_after is not None and len(self.sent) >= self.fail_after:
            raise RuntimeError("transport closed")
        self.sent.append(payload)


@pytest.fixture(autouse=True)
def _clean_registry():
    saved = (list(ws_clients), dict(bridge_module._ws_queues))
    ws_clients.clear()
    bridge_module._ws_queues.clear()
    yield
    ws_clients[:] = saved[0]
    bridge_module._ws_queues.clear()
    bridge_module._ws_queues.update(saved[1])


def test_frames_arrive_in_order_per_client():
    async def run():
        a, b = FakeWS(), FakeWS()
        ta, tb = ws_attach(a), ws_attach(b)
        for i in range(5):
            ws_enqueue_all({"n": i})
        await asyncio.sleep(0.05)
        ta.cancel(); tb.cancel()
        return a.sent, b.sent
    sa, sb = asyncio.run(run())
    assert [f["n"] for f in sa] == [0, 1, 2, 3, 4]
    assert sb == sa


def test_full_queue_drops_oldest_never_blocks(monkeypatch):
    monkeypatch.setattr(bridge_module, "WS_QUEUE_MAX", 3)

    async def run():
        stalled = FakeWS()
        # register without a sender: nothing drains, the queue fills
        q = asyncio.Queue(maxsize=bridge_module.WS_QUEUE_MAX)
        bridge_module._ws_queues[stalled] = q
        for i in range(6):
            ws_enqueue_all({"n": i})           # must never raise or await
        return [q.get_nowait()["n"] for _ in range(q.qsize())]
    assert asyncio.run(run()) == [3, 4, 5]


def test_dead_client_detaches_itself_only():
    async def run():
        dead, live = FakeWS(fail_after=1), FakeWS()
        td, tl = ws_attach(dead), ws_attach(live)
        for i in range(3):
            ws_enqueue_all({"n": i})
        await asyncio.sleep(0.05)
        still = (dead in ws_clients, live in ws_clients,
                 dead in bridge_module._ws_queues)
        tl.cancel(); td.cancel()
        return still, live.sent
    (dead_listed, live_listed, dead_queued), live_sent = asyncio.run(run())
    assert not dead_listed and not dead_queued
    assert live_listed and [f["n"] for f in live_sent] == [0, 1, 2]


def test_detach_is_idempotent():
    async def run():
        ws = FakeWS()
        t = ws_attach(ws)
        t.cancel()
        ws_detach(ws)
        ws_detach(ws)
        return ws in ws_clients, ws in bridge_module._ws_queues
    assert asyncio.run(run()) == (False, False)
