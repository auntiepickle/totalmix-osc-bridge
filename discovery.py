"""Channel-map discovery — interrogate TotalMix instead of hand-editing JSON.

Walks the output submixes by sending /setSubmix 1..N, waits for TotalMix to
push the new bank's feedback (labelSubmix, tracknames, volumes), and builds a
ufx2_channel_map.json-compatible draft from what actually came back.

This replaces the old manual flow: enable osc_monitor, click every submix in
TotalMix by hand, grep the log, run parse_submix_log.py, hand-edit the result.
"""
import time
import logging

logger = logging.getLogger(__name__)

EMPTY_NAMES = {"", "<empty>"}


def discover_channel_map(osc_client, listener, submix_count=16, settle_s=1.0,
                         progress_cb=None):
    """Walk submixes 1..submix_count and return (channel_map, walk_log).

    channel_map matches the ufx2_channel_map.json schema. walk_log records
    what each index resolved to, including skipped/empty slots, so the UI can
    show exactly what the device reported.
    """
    found = []   # (index, submix_name)
    walk_log = []

    for i in range(1, submix_count + 1):
        osc_client.send_message("/setSubmix", float(i))
        time.sleep(settle_s)
        name = listener.state.current_submix
        entry = {"index": i, "label": name}
        if not name or name.strip().lower() in EMPTY_NAMES:
            entry["skipped"] = "empty or no feedback"
        elif any(n == name for _, n in found):
            # TotalMix clamps out-of-range indices to the last submix — once a
            # name repeats we have walked off the end
            entry["skipped"] = "duplicate label (end of submixes)"
        else:
            found.append((i, name))
        walk_log.append(entry)
        if progress_cb:
            progress_cb(i, submix_count, name)

    channel_map = {"submixes": {}}
    for index, name in found:
        sends = {}
        rows = listener.state.submix_snapshot(name)
        for row in ("1", "2"):
            for ch, data in sorted(rows.get(row, {}).items()):
                send_name = (data.get("name") or "").strip() or f"Row {row} Ch {ch}"
                key = send_name
                if key in sends:  # same trackname on both rows
                    key = f"{send_name} (row {row})"
                sends[key] = {
                    "row": int(row),
                    "channel": ch,
                    "osc_address": f"/{row}/volume{ch}",
                    "description": f"{send_name} send to {name}",
                }
        channel_map["submixes"][name] = {
            "index": index,
            "name": name,
            "sends": sends,
        }

    logger.info(
        f"Discovery complete — {len(found)} submixes, "
        f"{sum(len(s['sends']) for s in channel_map['submixes'].values())} sends"
    )
    return channel_map, walk_log
