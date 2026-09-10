"""Review follow-ups (2026-09-09): channel-map persist under concurrency and
the duck supervisor being joined before the Global transport is torn down."""
import json
import threading
import time

import tmosc.bridge as bridge_module


def test_channel_map_persist_is_safe_under_concurrency(monkeypatch, tmp_path):
    monkeypatch.setenv("TMOSC_DATA_DIR", str(tmp_path))
    b = bridge_module.bridge
    cms = [{"physical_table": {"n": i, "pad": "x" * 4000}} for i in range(8)]
    threads = [threading.Thread(target=b._persist_channel_map_file, args=(cm,)) for cm in cms]
    for t in threads:
        t.start()
    for t in threads:
        t.join(5)
    on_disk = json.loads((tmp_path / "ufx2_channel_map.json").read_text())
    assert on_disk in cms                                   # a whole write, never a torn one
    assert not list(tmp_path.glob(".tmp-*"))                # no temp files left behind


def test_stop_global_osc_joins_duck_supervisor_first(make_bridge):
    b = make_bridge({})
    finished = []
    b._duck_thread = threading.Thread(target=lambda: (time.sleep(0.1), finished.append(True)))
    b._duck_thread.start()
    b.stop_global_osc()
    assert finished == [True]                               # joined before returning
