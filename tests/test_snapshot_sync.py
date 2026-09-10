"""#30: the header follows snapshot recalls made in TotalMix (Global feed)."""
import pytest

import tmosc.bridge as bridge_module
from tmosc.global_listener import GlobalOSCListener


class FakeMqtt:
    def __init__(self):
        self.published = []

    def publish(self, topic, payload, retain=False, **kw):
        self.published.append((topic, payload, retain))


SNAP_MAP = {
    "Blank": {"slot": 1, "snapshots": {"1": "Default", "4": "Work", "6": "Gaming"}},
    "Music": {"slot": 2, "snapshots": {"1": "Default", "4": {"name": "cooking", "index": 4}}},
}


@pytest.fixture
def synced(make_bridge):
    b = make_bridge({}, snap_map=SNAP_MAP)
    b.global_listener = GlobalOSCListener(0)
    b.mqtt_client = FakeMqtt()
    b.current_workspace = "Blank"
    b.current_snapshot = "default"
    return b


def _feed(b, states):
    for slot, v in states.items():
        b.global_listener.state.ingest(f"/snapshot/load/{slot}", (float(v),))


def test_device_recall_updates_snapshot_and_mqtt(synced):
    b = synced
    _feed(b, {1: 0, 4: 3})                      # slot 4 active + modified, as seen on the rig
    b._sync_snapshot_from_device()
    assert b.current_snapshot == "work"
    assert b.device_snapshot_slot == 4 and b.snapshot_modified is True
    assert ("totalmix/snapshot", "4", True) in b.mqtt_client.published
    assert b.state_confirmed is None            # display only: never claims a confirmed switch
    n = len(b.mqtt_client.published)
    b._sync_snapshot_from_device()              # unchanged feed -> silent
    assert len(b.mqtt_client.published) == n


def test_dual_shape_map_and_modified_flag(synced):
    b = synced
    b.current_workspace = "Music"
    _feed(b, {4: 2})
    b._sync_snapshot_from_device()
    assert b.current_snapshot == "cooking" and b.snapshot_modified is False
    _feed(b, {4: 3})                            # someone edits after the recall
    b._sync_snapshot_from_device()
    assert b.snapshot_modified is True and b.current_snapshot == "cooking"


def test_ambiguous_or_empty_feed_changes_nothing(synced):
    b = synced
    b._sync_snapshot_from_device()              # nothing reported yet
    assert b.current_snapshot == "default" and b.device_snapshot_slot is None
    _feed(b, {2: 2, 5: 2})                      # two active slots = unreliable dump
    b._sync_snapshot_from_device()
    assert b.current_snapshot == "default" and b.device_snapshot_slot is None


def test_unknown_workspace_records_slot_but_keeps_name(synced):
    b = synced
    b.current_workspace = None
    _feed(b, {6: 2})
    b._sync_snapshot_from_device()
    assert b.device_snapshot_slot == 6
    assert b.current_snapshot == "default"     # no map to translate the slot through
    assert not any(t == "totalmix/snapshot" for t, _, _ in b.mqtt_client.published)


def test_own_switch_is_not_republished(synced):
    """After the bridge itself recalled slot 6, the feed confirming slot 6
    must not publish a second retained snapshot message."""
    b = synced
    b.current_snapshot = "gaming"
    _feed(b, {6: 2})
    b._sync_snapshot_from_device()
    assert b.device_snapshot_slot == 6
    assert not any(t == "totalmix/snapshot" for t, _, _ in b.mqtt_client.published)
