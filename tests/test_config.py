from config import snapshot_num_to_osc_index


def test_snapshot_index_is_inverted():
    # TotalMix orders snapshot OSC buttons bottom-to-top: slot 1 ↔ index 8
    assert snapshot_num_to_osc_index(1) == 8
    assert snapshot_num_to_osc_index(8) == 1
    assert snapshot_num_to_osc_index(4) == 5


def test_snapshot_index_round_trips():
    for slot in range(1, 9):
        assert snapshot_num_to_osc_index(snapshot_num_to_osc_index(slot)) == slot
