"""physical_table.py — the fixed per-device hardware-channel table (#24).

Ground truth: trackname-sweep-CONFIRMED.md — starts are invariant across
snapshots; a pair's display name appears at BOTH member offsets."""
import physical_table as pt


def _table(rows):
    t = pt.empty_table()
    t["rows"] = rows
    return t


INPUTS = {
    "0": ["AN 1", "AN 1/2"], "1": ["AN 1/2", "AN 2"],
    "2": ["RE-101"], "3": ["RE-!50 Out"],
    "4": ["Mavis"], "5": ["Mavis"],
    "8": ["Mic 10", "Pill Out"], "9": ["Mic 10", "Pill Out"],
}


def test_resolve_start_is_lowest_member_offset():
    t = _table({"inputs": INPUTS})
    assert pt.resolve_start(t, "inputs", "AN 1/2") == 0   # pair name at 0 and 1
    assert pt.resolve_start(t, "inputs", "AN 2") == 1     # right half's own name
    assert pt.resolve_start(t, "inputs", "Mavis") == 4
    assert pt.resolve_start(t, "inputs", "RE-101") == 2
    assert pt.resolve_start(t, "inputs", "nope") is None
    assert pt.resolve_start(t, "inputs", "re-101") == 2   # case-insensitive fallback


def test_covers_requires_co_occurrence_at_the_offset():
    t = _table({"inputs": INPUTS})
    # "Mic 10" targeted while the device shows "Pill Out" — same channel
    assert pt.covers(t, "inputs", 9, "Mic 10", "Pill Out") is True
    assert pt.covers(t, "inputs", 8, "Mic 10", "Pill Out") is True
    # unrelated names never cross-match
    assert pt.covers(t, "inputs", 2, "Mavis", "RE-101") is False
    assert pt.covers(t, "inputs", 4, "Mavis", "Pill Out") is False


def test_merge_observation_accumulates_and_dedupes():
    t = pt.empty_table()
    assert pt.merge_observation(t, "inputs", 0, "AN 1") is True
    assert pt.merge_observation(t, "inputs", 0, "AN 1/2") is True
    assert pt.merge_observation(t, "inputs", 0, "an 1") is False   # dupe (ci)
    assert pt.merge_observation(t, "inputs", 0, "n.a.") is False   # filtered
    assert pt.merge_observation(t, "inputs", 99, "ghost") is False  # out of range
    assert t["rows"]["inputs"]["0"] == ["AN 1", "AN 1/2"]


def test_build_outputs_from_legacy_first_index_clamps_to_zero():
    """Legacy walks stored the first output as index 1; its hw start is 0
    (the clamp the old row-3 EQ path encoded). Later indices ARE starts."""
    legacy = {
        "submixes": {"Main": {"index": 1}, "AN 3/4": {"index": 2},
                     "RE-150 In": {"index": 14}},
        "layout_library": {
            "somekey": {"Main": {"index": 1}, "ADAT 2": {"index": 15},
                        "RE-150 In": {"index": 14}},
        },
    }
    out = pt.build_outputs_from_legacy(legacy)
    assert out["0"] == ["Main"]
    assert out["2"] == ["AN 3/4"]
    assert out["14"] == ["RE-150 In"]
    assert out["15"] == ["ADAT 2"]      # from the library entry
    assert "1" not in out               # no output ever started at hw 1


def test_display_names_one_entry_per_pair():
    t = _table({"inputs": {"0": ["AN 1/2"], "1": ["AN 1/2"],
                           "2": ["RE-101"]}})
    names = pt.display_names(t, "inputs")
    assert [(e["hw"], e["name"]) for e in names] == [(0, "AN 1/2"), (2, "RE-101")]
