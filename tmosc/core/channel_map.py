"""Channel map + physical table: load, migrate, persist, sweep.
Requires (facade __init__): channel_map, channel_map_is_example, _persist_lock,
_device_lock, osc_client, osc_listener, sweep_state. Requires (other mixins):
_read_page2_trackname (transport), broadcast_state (broadcast)."""
import json
import logging
import os
import re
import tempfile
import time

import tmosc.app_paths as app_paths
import tmosc.physical_table as pt

logger = logging.getLogger(__name__)

class ChannelMapMixin:
    def _load_channel_map(self):
        """Load ufx2_channel_map.json, falling back to the example file if missing.

        Sets self.channel_map_is_example = True when the fallback is used so the
        web UI can surface a setup prompt to the user.
        """
        for path in (app_paths.data_path("ufx2_channel_map.json"),
                     app_paths.example_path("ufx2_channel_map.example.json")):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    self.channel_map = json.load(f)
                self.channel_map_is_example = path.endswith(".example.json")
                if self.channel_map_is_example:
                    logger.warning(
                        "ufx2_channel_map.json not found — loaded example fallback. "
                        "Routing labels on macro cards may not match your setup."
                    )
                else:
                    logger.info("✅ Loaded ufx2_channel_map.json")
                self._purge_placeholder_layout_keys()
                self._migrate_physical_table()
                return
            except FileNotFoundError:
                continue
            except Exception as e:
                logger.warning(f"Could not load {path}: {e}")
                break
        self.channel_map = {}
        self.channel_map_is_example = False

    def _purge_placeholder_layout_keys(self):
        """Drop snapshot_layouts keys minted from unresolved placeholder
        beliefs (ws|snap_N or slot_N|snap) — wrong data that accumulated
        before startup absorption resolved names; minting is now guarded
        but persisted phantoms need healing once."""
        assoc = (self.channel_map or {}).get("snapshot_layouts")
        if not assoc:
            return
        def _is_phantom(k):
            ws, _, snap = str(k).partition("|")
            return bool(re.fullmatch(r"slot_\d+", ws)
                        or re.fullmatch(r"snap_\d+", snap))
        bad = [k for k in assoc if _is_phantom(k)]
        for k in bad:
            logger.warning(f"🧹 dropping phantom snapshot-layout key '{k}' "
                           f"(minted from an unresolved placeholder name)")
            del assoc[k]
        if bad:
            try:
                self._persist_channel_map_file(self.channel_map)
            except Exception as e:
                logger.warning(f"could not persist phantom-key purge: {e}")

    def _migrate_physical_table(self):
        """#24: seed the fixed hardware-channel table from legacy walked data.

        Legacy walked submix indices ARE hw starts (trackname-sweep-proven)
        except the first output, stored as index 1 while its start is 0.
        In-memory only — nothing is persisted until the first sweep
        completes, so the legacy file stays intact for rollback. Inputs are
        NOT derivable from legacy data (width maps lost channel ordering);
        that row waits for the sweep."""
        cm = self.channel_map or {}
        if cm.get("physical_table") or not cm.get("submixes"):
            return
        outputs = pt.build_outputs_from_legacy(cm)
        if not outputs:
            return
        table = pt.empty_table()
        table["rows"]["outputs"] = outputs
        table["source"]["outputs"] = "legacy_migration"
        cm["physical_table"] = table
        logger.info(f"🗺 physical_table seeded from legacy walk data — "
                    f"{len(outputs)} output channels (in-memory; run "
                    f"POST /api/device/sweep to measure and persist)")

    def _physical_table(self):
        return (self.channel_map or {}).get("physical_table")

    def _persist_channel_map_file(self, cm):
        """Atomic write of the channel map (temp + replace — a crash mid-
        write must not corrupt the only copy of the layout library)."""
        target = app_paths.data_path("ufx2_channel_map.json")
        # Private temp file + lock: the Global name-sync thread, a sweep and a
        # web save can all persist at once, and one shared .tmp path let them
        # truncate each other mid-write (review finding 2026-09-09).
        with self._persist_lock:
            fd, tmp = tempfile.mkstemp(dir=os.path.dirname(target) or ".",
                                       prefix=".tmp-channel-map-", suffix=".json")
            try:
                with os.fdopen(fd, "w") as f:
                    json.dump(cm, f, indent=2)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp, target)
            except Exception:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise

    def _record_table_observation(self, row_key, offset, shown):
        """Incremental alias learning: every confirmed aim teaches the table.
        Persisted immediately (cheap, infrequent) unless running from the
        example map."""
        table = self._physical_table()
        if table is None or offset is None or not row_key or not shown:
            return
        if pt.merge_observation(table, row_key, offset, shown):
            if not self.channel_map_is_example:
                try:
                    self._persist_channel_map_file(self.channel_map)
                except Exception as e:
                    logger.warning(f"could not persist table observation: {e}")

    SWEEP_BOUNDARY_EXTRA = 4  # probe past the hw end to verify saturation

    def run_sweep(self, rows=("inputs", "outputs"), settle_s: float = 0.3,
                  reset: bool = False):
        """Measure the physical table from the device's own mouth (#24):
        for each hw offset 0..N+3, /setBankStart → row-mirror nudge →
        read /2/trackname. Read-only w.r.t. mixer state — the only state
        touched is bank position and row selection, both restored. NEVER
        sends /setSubmix (the sole fatal operation).

        Offsets past the hardware end must SATURATE at the last channel's
        name (sweep-proven); a NEW name there means channels_per_row is
        wrong for this device — abort without persisting."""
        listener = self.osc_listener
        if listener is None or not listener.running or self.osc_client is None:
            self.sweep_state = {"status": "error",
                                "error": "no OSC client/listener"}
            return self.sweep_state
        row_defs = {"inputs": ("/1/busInput", "1"),
                    "outputs": ("/1/busOutput", "3")}
        rows = [r for r in rows if r in row_defs]
        table = self._physical_table()
        if table is None:
            table = pt.empty_table()
            self.channel_map.setdefault("physical_table", table)
            self.channel_map["physical_table"] = table
        n = table.get("channels_per_row", pt.CHANNELS_PER_ROW)
        total = len(rows) * (n + self.SWEEP_BOUNDARY_EXTRA)
        self.sweep_state = {"status": "running", "progress": 0, "total": total,
                            "rows": rows}
        observed_all = {}
        try:
            with self._device_lock:
                try:
                    for row in rows:
                        bus_addr, row_num = row_defs[row]
                        self.osc_client.send_message(bus_addr, 1.0)
                        observed = {}
                        for offset in range(0, n + self.SWEEP_BOUNDARY_EXTRA):
                            self.osc_client.send_message("/setBankStart",
                                                         float(offset))
                            if settle_s:
                                time.sleep(settle_s)
                            name = self._read_page2_trackname(row_num)
                            if name is None:
                                raise RuntimeError(
                                    f"{row} sweep: no page-2 dump at offset "
                                    f"{offset} — device unresponsive, aborting")
                            observed[offset] = name
                            self.sweep_state["progress"] += 1
                            self.broadcast_state(macro_event={
                                "type": "sweep_progress",
                                "row": row,
                                "progress": self.sweep_state["progress"],
                                "total": total})
                        last_real = observed.get(n - 1)
                        for b in range(n, n + self.SWEEP_BOUNDARY_EXTRA):
                            if observed.get(b) and observed[b] != last_real:
                                raise RuntimeError(
                                    f"{row} sweep: offset {b} shows "
                                    f"'{observed[b]}' past the assumed "
                                    f"hardware end ({n}) — channels_per_row "
                                    f"is wrong for this device, aborting "
                                    f"without persisting")
                        observed_all[row] = observed
                finally:
                    self.osc_client.send_message("/setBankStart", 0.0)
                    self.osc_client.send_message("/1/busInput", 1.0)
            # The device section takes ~35 s and a channel-map save from the
            # web editor meanwhile REPLACES self.channel_map with a new dict;
            # merging into the table captured above would persist the new
            # map without a single observation (#38). Re-resolve now.
            cm = self.channel_map if isinstance(self.channel_map, dict) else {}
            self.channel_map = cm
            table = cm.get("physical_table")
            if not isinstance(table, dict):
                table = pt.empty_table()
                cm["physical_table"] = table
            row_map = {"inputs": "inputs", "outputs": "outputs"}
            for row, observed in observed_all.items():
                key = row_map[row]
                if reset:
                    table.setdefault("rows", {})[key] = {}
                for offset in range(0, n):
                    name = observed.get(offset)
                    if name:
                        pt.merge_observation(table, key, offset, name)
                table.setdefault("last_sweep", {})[key] = time.time()
                table.setdefault("source", {})[key] = "sweep"
            # Legacy structures are superseded once BOTH rows are measured —
            # prune them from the persisted file (backup is the .tmp+replace
            # atomic write plus the config backups on the web side)
            pruned = []
            if all(table.get("source", {}).get(r) == "sweep"
                   for r in ("inputs", "outputs")):
                for legacy in ("width_maps", "channel_widths",
                               "layout_library", "snapshot_layouts"):
                    if legacy in (self.channel_map or {}):
                        del self.channel_map[legacy]
                        pruned.append(legacy)
            # A sweep measures the REAL device — always persist. This is how
            # a fresh install bootstraps its channel map (no walk needed).
            self._persist_channel_map_file(self.channel_map)
            self.channel_map_is_example = False
            self.sweep_state = {"status": "done", "rows": rows,
                                "pruned_legacy": pruned,
                                "table": pt.summarize(table)}
            logger.info(f"🗺 sweep complete — rows {rows}, legacy pruned: "
                        f"{pruned or 'none'}")
            self.broadcast_state(macro_event={"type": "sweep_complete",
                                              "rows": rows})
        except Exception as e:
            logger.error(f"sweep failed: {e}")
            self.sweep_state = {"status": "error", "error": str(e)}
            self.broadcast_state(macro_event={"type": "sweep_error",
                                              "error": str(e)})
        return self.sweep_state

    def _persist_after_global_names(self):
        if self.channel_map and not self.channel_map_is_example:
            self._persist_channel_map_file(self.channel_map)
