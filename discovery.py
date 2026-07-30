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


def discover_channel_map(osc_client, listener, submix_count=32, settle_s=1.0,
                         progress_cb=None, include_playback=True):
    """Walk submixes 1..submix_count and return (channel_map, walk_log).

    channel_map matches the ufx2_channel_map.json schema. walk_log records
    what each index resolved to, including skipped/empty slots, so the UI can
    show exactly what the device reported.

    include_playback: additionally select the software-playback row
    (/1/busPlayback) per discovered submix to capture its sends — page-1
    feedback follows the selected row, so the playback bank is invisible
    unless explicitly selected. The input row is restored afterwards.
    """
    found = []   # (index, submix_name)
    walk_log = []
    initial_submix = listener.state.current_submix  # restore after the walk

    # Normalize the bank to the first channel so strip indices are absolute.
    # /setBankStart is 0-BASED (hardware-verified: 1.0 shifts the bank one
    # strip left). The bank only spans "Number of Faders per Bank" strips
    # (TotalMix Options → Settings → OSC, default 8) — set it high enough to
    # cover every channel or discovery will only see the first bank.
    osc_client.send_message("/setBankStart", 0.0)

    prev_label = None
    dupe_run = 0
    for i in range(1, submix_count + 1):
        osc_client.send_message("/setSubmix", float(i))
        time.sleep(settle_s)
        name = listener.state.current_submix
        entry = {"index": i, "label": name}
        if not name or name.strip().lower() in EMPTY_NAMES:
            entry["skipped"] = "empty or no feedback"
            dupe_run = 0
        elif any(n == name for _, n in found):
            # One consecutive duplicate = the second half of a stereo-linked
            # output (pairs occupy two indices). A longer run means the walk
            # has saturated past the last real submix — TotalMix clamps
            # out-of-range indices and keeps reporting the final label.
            dupe_run = dupe_run + 1 if name == prev_label else 1
            entry["skipped"] = ("duplicate label (stereo pair)" if dupe_run == 1
                                else "past last submix (label repeating)")
        else:
            found.append((i, name))
            dupe_run = 0
            if include_playback:
                # Capture the playback row for this submix, then restore the
                # input row before the next /setSubmix dumps row-1 data
                osc_client.send_message("/1/busPlayback", 1.0)
                time.sleep(settle_s)
                osc_client.send_message("/1/busInput", 1.0)
                time.sleep(settle_s)
        prev_label = name
        walk_log.append(entry)
        if progress_cb:
            progress_cb(i, submix_count, name)

    # Put TotalMix back on the submix that was selected before the walk
    restore = next((i for i, n in found if n == initial_submix), None)
    if restore is not None and initial_submix != listener.state.current_submix:
        osc_client.send_message("/setSubmix", float(restore))
        logger.info(f"Restored pre-walk submix '{initial_submix}' (index {restore})")

    channel_map = {"submixes": {}}
    for index, name in found:
        sends = {}
        rows = listener.state.submix_snapshot(name)
        for row in ("1", "2"):
            for ch, data in sorted(rows.get(row, {}).items()):
                raw_name = (data.get("name") or "").strip()
                # Strips past the device's channel count report "n.a." when
                # the OSC bank is wider than the hardware — not real sends
                if raw_name.lower() in ("n.a.", "n/a"):
                    continue
                send_name = raw_name or f"Row {row} Ch {ch}"
                # Playback strips often share names with inputs — suffix them
                key = send_name if row == "1" else f"{send_name} (playback)"
                if key in sends:
                    key = f"{send_name} (row {row})"
                sends[key] = {
                    "row": int(row),
                    "name": send_name,  # raw trackname, for live matching
                    "channel": ch,
                    # The write address is always page 1 — the ROW is chosen
                    # by /1/busInput / /1/busPlayback before writing
                    "osc_address": f"/1/volume{ch}",
                    "description": (f"{send_name} send to {name}" if row == "1"
                                    else f"{send_name} (playback) send to {name}"),
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
