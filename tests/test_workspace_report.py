"""#30, workspace half: the agent on the TotalMix machine sends TotalMix's
window title with its heartbeat; the bridge parses the workspace out of it
and adopts it as confirmed. Title shapes observed live 2026-09-14:
  'RME TotalMix FX: Fireface UFX II (1) - 48.0k - Work'          quick select
  '... - 48.0k - Z:\\totalMixFX\\workspaces\\dub_final.tmws'      file workspace
"""
import threading

import pytest
from fastapi.testclient import TestClient

from tmosc.api.app import app, create_app
from tmosc.core.switching import SwitchingMixin

TITLE = "RME TotalMix FX: Fireface UFX II (1) - 48.0k - "
SNAP_MAP = {
    "Work": {"slot": 3, "snapshots": {"1": "Work", "2": "Gaming", "4": "Chillin"}},
    "Blank": {"slot": 1, "snapshots": {"1": "Default", "3": "Messin around", "4": "Work"}},
    "_comment": "not a workspace",
}


@pytest.mark.parametrize("title, expect", [
    (TITLE + "Work", ("Work", "quick")),
    (TITLE + "dub_music", ("dub_music", "quick")),
    (TITLE + r"Z:\totalMixFX\workspaces\dub_final.tmws", ("dub_final", "file")),
    (TITLE + "/home/me/ws/live set.TMWS", ("live set", "file")),
    ("RME TotalMix FX: Fireface UFX II (1) - 48.0k", (None, None)),     # nothing loaded by name
    ("RME TotalMix FX: Fireface UFX II (1) - 44.1k", (None, None)),
    ("RME TotalMix FX: Fireface UFX II (1)", (None, None)),
    ("", (None, None)),
    (None, (None, None)),
    (TITLE + "C:\\x\\.tmws", (None, None)),                                # empty base name
])
def test_parse_workspace_title(title, expect):
    assert SwitchingMixin.parse_workspace_title(title) == expect


def _bridge(make_bridge):
    b = make_bridge({}, snap_map=SNAP_MAP)
    b.current_workspace = "Blank"
    b.current_snapshot = "work"          # slot 4 named through the WRONG workspace
    b.state_confirmed = False
    b.device_snapshot_slot = 4
    return b


def test_report_adopts_workspace_and_renames_snapshot(make_bridge):
    b = _bridge(make_bridge)
    rep = b.report_workspace(TITLE + "Work", source="BONE")
    assert rep["name"] == "Work" and rep["kind"] == "quick" and rep["in_map"] is True
    assert b.current_workspace == "Work"
    assert b.state_confirmed is True
    assert b.current_snapshot == "chillin"       # slot 4 through Work's map now
    assert b.events                               # broadcast once
    st = b.workspace_report_state()
    assert st["name"] == "Work" and st["source"] == "BONE" and st["live"] is True
    # steady state: same title again announces nothing
    n = len(b.events)
    b.report_workspace(TITLE + "Work", source="BONE")
    assert len(b.events) == n


def test_report_matches_map_case_insensitively(make_bridge):
    b = _bridge(make_bridge)
    rep = b.report_workspace(TITLE + "work")
    assert rep["name"] == "Work" and rep["in_map"] is True
    assert b.current_workspace == "Work"


def test_report_unknown_workspace_keeps_name_and_slot(make_bridge):
    """A slot renamed in TotalMix after the map was scraped, or a file
    workspace: the header shows what TotalMix shows; snapshot names are
    unavailable, so the slot number stands in."""
    b = _bridge(make_bridge)
    rep = b.report_workspace(TITLE + r"Z:\ws\dub_final.tmws", source="BONE")
    assert rep == {**rep, "name": "dub_final", "kind": "file", "in_map": False}
    assert b.current_workspace == "dub_final"
    assert b.state_confirmed is True
    assert b.current_snapshot == "snap_4"


def test_report_without_workspace_clears_report_only(make_bridge):
    b = _bridge(make_bridge)
    b.report_workspace(TITLE + "Work")
    assert b.report_workspace("RME TotalMix FX: Fireface UFX II (1) - 48.0k") is None
    assert b.workspace_report_state() is None
    assert b.current_workspace == "Work"        # belief stays, just unrefreshed
    assert b.report_workspace("") is None


def test_report_skipped_while_a_switch_holds_the_device_lock(make_bridge):
    b = _bridge(make_bridge)
    held = threading.Event()
    release = threading.Event()

    def holder():
        with b._device_lock:
            held.set()
            release.wait(5)
    t = threading.Thread(target=holder, daemon=True)
    t.start()
    held.wait(5)
    try:
        assert b.report_workspace(TITLE + "Work") is None     # no report adopted
        assert b.current_workspace == "Blank"
    finally:
        release.set()
        t.join(5)
    assert b.report_workspace(TITLE + "Work")["name"] == "Work"


def test_report_publishes_retained_workspace_slot_marked_as_own(make_bridge):
    b = _bridge(make_bridge)
    published = []

    class FakeMqtt:
        def publish(self, topic, payload, retain=False, **kw):
            published.append((topic, payload, retain))
    b.mqtt_client = FakeMqtt()
    b.report_workspace(TITLE + "Work")
    assert published == [("totalmix/workspace", "3", True)]
    assert b._own_retained_republish["totalmix/workspace"][0] == "3"
    # an unmapped workspace has no slot to publish
    published.clear()
    b.report_workspace(TITLE + "nowhere")
    assert published == []


def test_report_expires_for_display(make_bridge, monkeypatch):
    b = _bridge(make_bridge)
    b.report_workspace(TITLE + "Work")
    monkeypatch.setattr(b, "WORKSPACE_REPORT_TTL_S", -1.0)
    assert b.workspace_report_state()["live"] is False


def test_heartbeat_carries_the_title_through_the_api(make_bridge):
    b = _bridge(make_bridge)
    client = TestClient(create_app(bridge=b))
    b._midi_owner = None
    # an agent that does not know about titles changes nothing
    r = client.post("/api/midi/owner/heartbeat", json={"id": "old-agent", "host": "BOX"})
    assert r.status_code == 200 and r.json()["workspace_report"] is None
    assert client.get("/api/status").json()["workspace"] == "Blank"
    # one that does: the belief follows TotalMix
    r = client.post("/api/midi/owner/heartbeat",
                    json={"id": "BONE-tmosc-agent", "host": "BONE", "title": TITLE + "Work"})
    assert r.json()["owner"]["id"] == "BONE-tmosc-agent"
    assert r.json()["workspace_report"]["name"] == "Work"
    s = client.get("/api/status").json()
    assert s["workspace"] == "Work" and s["state_confirmed"] is True
    assert s["workspace_report"]["source"] == "BONE" and s["workspace_report"]["in_map"] is True
    assert s["snapshot"] == "chillin"
    # no TotalMix window on the agent machine: report gone, belief kept
    client.post("/api/midi/owner/heartbeat",
                json={"id": "BONE-tmosc-agent", "host": "BONE", "title": ""})
    s = client.get("/api/status").json()
    assert s["workspace_report"] is None and s["workspace"] == "Work"


def test_shared_app_status_has_the_field():
    assert "workspace_report" in TestClient(app).get("/api/status").json()
