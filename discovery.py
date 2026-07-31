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

    # Bound the walk by the LIVE output row before sending any /setSubmix:
    # each output strip is one submix, and /setSubmix past the last real
    # submix CRASHES TotalMix outright (hardware root cause, controlled
    # test 2026-07-31: index 30 fatal on a 28-index layout). Knowing how
    # many submixes exist lets the walk stop BEFORE the fatal index — the
    # label-repeat backstop below only detects the injury one index late.
    osc_client.send_message("/1/busOutput", 1.0)
    time.sleep(settle_s)
    osc_client.send_message("/1/busInput", 1.0)
    time.sleep(settle_s)
    out_strips = listener.state.submix_snapshot("_outputs").get("3", {})
    out_names = {str(d.get("name", "")).strip() for d in out_strips.values()}
    out_names = {n for n in out_names if n and n.lower() not in ("n.a.", "n/a")}
    expected = len(out_names) or None
    if expected:
        logger.info(f"Output row reports {expected} strips — the walk stops "
                    f"after finding that many submixes")
    else:
        logger.warning("Output row not enumerable — walking with the "
                       "label-repeat backstop only (it aborts one index LATE; "
                       "the first past-the-end index is the fatal one)")

    prev_label = None
    dupe_run = 0
    for i in range(1, submix_count + 1):
        t_send = time.time()
        osc_client.send_message("/setSubmix", float(i))
        # CONFIRM the label is FRESH before classifying it. The old fixed
        # sleep read whatever current_submix held: one lost/late
        # labelSubmix datagram made a real submix look like a stereo-pair
        # duplicate — silently dropping it from the map AND letting the
        # walk run past the output-count stop toward the fatal index
        # (review finding). An unconfirmed label is never classified.
        confirmed = listener.wait_for(
            lambda st: (st.raw_entry("/1/labelSubmix") or {})
                       .get("last_seen", 0) >= t_send,
            max(settle_s, 1.0))
        if not confirmed:
            # SILENCE IS AMBIGUOUS (hardware-measured, v0.1.0-alpha
            # regression): a /setSubmix that selects the ALREADY-selected
            # submix — every stereo pair's second index — is a total
            # no-op: zero messages, no label. But a crashed device is
            # also silent, and walking on through a crash is the #17
            # injury. Disambiguate with the guaranteed-change row toggle
            # (the liveness probe's primitive): alive means the send was
            # a no-op and the unchanged label classifies as the pair
            # duplicate below; dead means abort.
            b0 = listener.state.message_count
            osc_client.send_message("/1/busPlayback", 1.0)
            osc_client.send_message("/1/busInput", 1.0)
            alive = listener.wait_for(
                lambda st: st.message_count > b0, max(settle_s, 1.0))
            if alive:
                logger.info(f"   index {i}: silent /setSubmix, device alive "
                            f"— no-op (still on "
                            f"'{listener.state.current_submix}')")
            else:
                entry = {"index": i, "label": None,
                         "skipped": "no feedback and device not responding",
                         "stop": (f"/setSubmix {i} was silent AND the row "
                                  f"toggle got no feedback — device dead "
                                  f"or feedback lost, aborting the walk")}
                walk_log.append(entry)
                logger.error(f"Walk aborted at index {i}: {entry['stop']}")
                if progress_cb:
                    progress_cb(i, submix_count, None)
                break
        time.sleep(settle_s)  # let the bank burst behind the label land
        name = listener.state.current_submix
        entry = {"index": i, "label": name}
        stop = None
        if not name or name.strip().lower() in EMPTY_NAMES:
            entry["skipped"] = "empty or no feedback"
            dupe_run = 0
        elif any(n == name for _, n in found):
            # One consecutive duplicate = the second half of a stereo-linked
            # output (pairs occupy two indices). A longer run means the walk
            # went past the last real submix — and the index that first went
            # past may already have crashed the device, so ABORT immediately
            # instead of walking on (crashes A/B: 31/32 never answered).
            dupe_run = dupe_run + 1 if name == prev_label else 1
            if dupe_run == 1:
                entry["skipped"] = "duplicate label (stereo pair)"
            else:
                entry["skipped"] = "past last submix (label repeating)"
                stop = (f"label repeating at index {i}: past the last submix. "
                        f"An out-of-range /setSubmix crashes TotalMix — this "
                        f"index may already have done it; probe the device.")
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
            if expected is not None and len(found) >= expected:
                stop = (f"all {expected} outputs found — stopping before any "
                        f"out-of-range /setSubmix (it would crash TotalMix)")
        prev_label = name
        walk_log.append(entry)
        if progress_cb:
            progress_cb(i, submix_count, name)
        if stop:
            entry["stop"] = stop
            logger.warning(f"Walk stopped early: {stop}")
            break

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
