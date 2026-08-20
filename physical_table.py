"""Fixed per-device hardware-channel table (issue #24).

The device's I/O is physically fixed: 30 mono channels per row on a UFX II.
Stereo-linking only changes how strips are DISPLAYED — a pair collapses to
one strip carrying one display name at BOTH member offsets — and hardware
starts never move (trackname-sweep-CONFIRMED.md, 4/4 sweeps across
differently-paired snapshots). /setSubmix and /setBankStart are hardware-
mono indexed (RME staff / sweep-proven), so one measured table per device
replaces every per-layout width map and walk.

Keys are string hw-mono offsets "0".."29", 0-based — the identical indices
TotalMix FX 2.1 Global OSC uses (#25): keep ALL table access behind this
module so a GlobalOSC transport can consume it unchanged.

Alias lists accumulate every display name ever observed at an offset
("AN 1" and "AN 1/2" both live at 0). A name at several offsets (a pair
name at both members) resolves to the LOWEST — that is the start. Aliases
are never removed by observation; a reset sweep clears one row first.
"""

CHANNELS_PER_ROW = 30
ROW_KEYS = ("inputs", "playbacks", "outputs")
SCHEMA_VERSION = 1


def empty_table(channels_per_row: int = CHANNELS_PER_ROW) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "channels_per_row": channels_per_row,
        "rows": {},
        "last_sweep": {},
        "source": {},
    }


def _aliases(table: dict, row: str, offset) -> list:
    return ((table or {}).get("rows", {}).get(row, {}) or {}).get(str(offset), [])


def _alias_has(aliases, name: str) -> bool:
    low = str(name).strip().lower()
    return any(str(a).strip().lower() == low for a in aliases)


def resolve_start(table: dict, row: str, name: str):
    """Lowest hw offset whose alias set contains `name` — i.e. the channel's
    START (starts never move; the pair name at both members makes min() the
    left/first channel). Exact match wins over case-insensitive. None when
    the name has never been observed in this row."""
    rows = (table or {}).get("rows", {}).get(row, {}) or {}
    exact = [int(k) for k, aliases in rows.items() if name in aliases]
    if exact:
        return min(exact)
    ci = [int(k) for k, aliases in rows.items() if _alias_has(aliases, name)]
    return min(ci) if ci else None


def covers(table: dict, row: str, offset, wanted: str, shown: str) -> bool:
    """The alias-default rule: both the targeted name and the currently
    displayed name are known aliases of the SAME offset ("AN 2" targeted
    while the device shows "AN 1/2"; "Mic 10" while it shows "Pill Out").
    Co-occurrence at one offset is required, so unrelated strips can never
    cross-match."""
    aliases = _aliases(table, row, offset)
    return _alias_has(aliases, wanted) and _alias_has(aliases, shown)


def merge_observation(table: dict, row: str, offset, name: str) -> bool:
    """Accumulate a display-name observation. Returns True when the table
    changed (caller persists, debounced)."""
    name = str(name).strip()
    if not name or name.lower() == "n.a.":
        return False
    offset = int(offset)
    if offset < 0 or offset >= table.get("channels_per_row", CHANNELS_PER_ROW):
        return False
    rows = table.setdefault("rows", {})
    aliases = rows.setdefault(row, {}).setdefault(str(offset), [])
    if _alias_has(aliases, name):
        return False
    aliases.append(name)
    aliases.sort()
    return True


def row_present(table: dict, row: str) -> bool:
    return bool((table or {}).get("rows", {}).get(row))


def display_names(table: dict, row: str) -> list:
    """[{hw, name, aliases}] — one entry per distinct start, most recent
    observation last in `aliases` is NOT guaranteed (sorted), so `name` is
    just the first alias; live feeds override this for current names."""
    rows = (table or {}).get("rows", {}).get(row, {}) or {}
    out = []
    seen = set()
    for k in sorted(rows, key=int):
        aliases = rows[k]
        key = tuple(aliases)
        # a pair's alias list appears at both member offsets — only the
        # start (first occurrence) becomes a picker entry
        if key in seen and out and set(aliases) & set(out[-1]["aliases"]):
            continue
        seen.add(key)
        out.append({"hw": int(k), "name": aliases[0] if aliases else "?",
                    "aliases": list(aliases)})
    return out


def build_outputs_from_legacy(channel_map: dict) -> dict:
    """Migration: legacy walked submix indices ARE hw starts (sweep-proven)
    — except the legacy walk stored the first output as index 1 while its
    hw start is 0 (the clamp the old row-3 EQ path encoded). Build the
    outputs row from the active submixes plus every layout_library entry,
    accumulating names as aliases. Inputs are NOT derivable from legacy
    data (width maps lost ordering); that row waits for the first sweep."""
    outputs = {}

    def _absorb(submixes: dict):
        entries = [(name, sub.get("index")) for name, sub in submixes.items()
                   if isinstance(sub, dict) and sub.get("index") is not None]
        if not entries:
            return
        lowest = min(idx for _, idx in entries)
        for name, idx in entries:
            hw = 0 if idx == lowest else int(idx)
            aliases = outputs.setdefault(str(hw), [])
            if not _alias_has(aliases, name):
                aliases.append(str(name))
                aliases.sort()

    _absorb((channel_map or {}).get("submixes", {}) or {})
    for entry in ((channel_map or {}).get("layout_library", {}) or {}).values():
        if isinstance(entry, dict):
            _absorb(entry)
    return outputs


def summarize(table: dict) -> dict:
    return {
        "present": {row: row_present(table, row) for row in ("inputs", "outputs")},
        "channels_per_row": (table or {}).get("channels_per_row"),
        "last_sweep": (table or {}).get("last_sweep", {}),
        "source": (table or {}).get("source", {}),
    }
