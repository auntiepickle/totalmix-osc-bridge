import threading
import time


STATIC_MACRO = {
    "steps": [{"osc": "/1/volume1", "value": "{{param}}"}],
}


def macro_events(bridge, event_type):
    return [e["event"] for e in bridge.events
            if e["event"] and e["event"]["type"] == event_type]


def test_static_step_sends_param(make_bridge, fake_osc):
    b = make_bridge({"vol": STATIC_MACRO})
    b.run_macro("vol", 0.7)
    assert ("/1/volume1", 0.7) in fake_osc.sent
    assert len(macro_events(b, "macro_complete")) == 1


def test_param_clamped_to_range(make_bridge, fake_osc):
    b = make_bridge({"vol": {**STATIC_MACRO, "param_range": [0.2, 0.8]}})
    b.run_macro("vol", 1.5)
    assert ("/1/volume1", 0.8) in fake_osc.sent
    fake_osc.clear()
    b.run_macro("vol", -1.0)
    assert ("/1/volume1", 0.2) in fake_osc.sent


def test_unknown_macro_is_noop(make_bridge, fake_osc):
    b = make_bridge({})
    b.run_macro("nope", 0.5)
    assert fake_osc.sent == []


def test_no_osc_client_skips_with_event(make_bridge):
    b = make_bridge({"vol": STATIC_MACRO})
    b.osc_client = None
    b.run_macro("vol", 0.5)
    skipped = macro_events(b, "macro_skipped")
    assert len(skipped) == 1
    assert skipped[0]["reason"] == "osc_not_configured"


def test_fixed_value_step_ignores_param(make_bridge, fake_osc):
    b = make_bridge({"mute": {"steps": [{"osc": "/1/mute1", "value": 1.0}]}})
    b.run_macro("mute", 0.123)
    assert ("/1/mute1", 1.0) in fake_osc.sent


def test_debounce_drops_second_trigger(make_bridge, fake_osc):
    b = make_bridge({"vol": {**STATIC_MACRO, "debounce_ms": 60000}})
    b.run_macro("vol", 0.5)
    first_count = len(fake_osc.sent)
    b.run_macro("vol", 0.9)
    assert len(fake_osc.sent) == first_count


def test_workspace_snapshot_switch_and_state_awareness(make_bridge, fake_osc):
    macro = {**STATIC_MACRO, "workspace": "Pill_setup", "snapshot": "Reset"}
    b = make_bridge({"vol": macro})
    b.run_macro("vol", 0.5)

    # Slot 2 workspace, snapshot 1 → OSC button index 8 (bottom-to-top)
    assert ("/loadQuickWorkspace", 2.0) in fake_osc.sent
    assert ("/3/snapshots/8/1", 1.0) in fake_osc.sent
    assert b.current_workspace == "Pill_setup"
    assert b.current_snapshot == "reset"

    # Second run on the same target must not re-send the switch sequence
    fake_osc.clear()
    b.run_macro("vol", 0.6)
    assert "/loadQuickWorkspace" not in fake_osc.addresses()
    assert "/3/snapshots/8/1" not in fake_osc.addresses()
    assert ("/1/volume1", 0.6) in fake_osc.sent


def test_switch_to_known_and_unknown_snapshot(make_bridge, fake_osc):
    b = make_bridge({})
    assert b.switch_to("Pill_setup", snapshot="Live") is True
    assert ("/loadQuickWorkspace", 2.0) in fake_osc.sent
    assert ("/3/snapshots/7/1", 1.0) in fake_osc.sent

    # Unknown snapshot: workspace switches, no snapshot OSC, no exception
    fake_osc.clear()
    assert b.switch_to("Pill_setup", snapshot="does-not-exist") is True
    assert "/loadQuickWorkspace" in fake_osc.addresses()
    assert not [a for a in fake_osc.addresses() if a.startswith("/3/snapshots")]

    assert b.switch_to("unknown-workspace") is False


RAMP_MACRO = {
    "steps": [{
        "osc": "/1/volume1",
        "value": "{{param}}",
        "operation": {"type": "ramp", "duration": 0.4, "steps_per_sec": 20},
    }],
}


def test_fire_mode_ignore_drops_concurrent_triggers(make_bridge, fake_osc):
    b = make_bridge({"ramp": {**RAMP_MACRO, "fire_mode": "ignore"}})
    threads = [threading.Thread(target=b.run_macro, args=("ramp", 0.5))
               for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(macro_events(b, "macro_start")) == 1
    assert len(macro_events(b, "macro_complete")) == 1


def test_fire_mode_queue_runs_again_after_completion(make_bridge, fake_osc):
    b = make_bridge({"ramp": {**RAMP_MACRO, "fire_mode": "queue"}})
    runner = threading.Thread(target=b.run_macro, args=("ramp", 0.5))
    runner.start()
    time.sleep(0.1)  # first run is mid-ramp
    b.run_macro("ramp", 0.9)  # returns immediately, queues
    runner.join()
    deadline = time.time() + 3
    while len(macro_events(b, "macro_complete")) < 2 and time.time() < deadline:
        time.sleep(0.05)
    assert len(macro_events(b, "macro_complete")) == 2


def test_fire_mode_restart_cancels_and_reruns(make_bridge, fake_osc):
    b = make_bridge({"ramp": {**RAMP_MACRO, "fire_mode": "restart"}})
    runner = threading.Thread(target=b.run_macro, args=("ramp", 0.5))
    runner.start()
    time.sleep(0.1)
    started = time.time()
    b.run_macro("ramp", 0.9)  # cancels the in-flight ramp, re-queues
    runner.join()
    # First run ended early via cancel_event — a full ramp would still have
    # ~0.3s to go; a cancelled one exits within one step (0.05s)
    assert time.time() - started < 0.2
    deadline = time.time() + 3
    while len(macro_events(b, "macro_complete")) < 2 and time.time() < deadline:
        time.sleep(0.05)
    assert len(macro_events(b, "macro_complete")) == 2


def test_macro_complete_update_payload(make_bridge, fake_osc):
    b = make_bridge({"vol": {**STATIC_MACRO, "description": "test macro"}})
    b.run_macro("vol", 0.5)
    updates = [e["update"] for e in b.events if e["update"]]
    assert len(updates) == 1
    u = updates[0]
    assert u["name"] == "vol"
    assert u["value"] == 0.5
    assert u["progress"] == 100
    assert u["last_trigger"] > 0
