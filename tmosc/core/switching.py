"""Workspace / snapshot switching and device-confirmed belief.
Requires (facade __init__): osc_client, snapshot_map, _device_lock, _layout_epoch,
_outputs_cache, _inputs_cache, mqtt_client, osc_listener, global_listener,
device_snapshot_slot, snapshot_modified. Requires (other mixins):
reapply_held_knobs (knobs), broadcast_state (broadcast)."""
import logging
import time

from tmosc.config import snapshot_num_to_osc_index

logger = logging.getLogger(__name__)

class SwitchingMixin:
    def update_workspace(self, name: str = None, slot: int = None):
        if name:
            self.current_workspace = name
        elif slot is not None and self.snapshot_map:
            for ws_name, data in self.snapshot_map.items():
                if data.get("slot") == slot:
                    self.current_workspace = ws_name
                    break
        logger.info(f"BRIDGE STATE → workspace = {self.current_workspace or 'None'}")
        self.broadcast_state()  # ← live web update

    def update_snapshot(self, name: str = None, index: int = None, workspace: str = None):
        if name:
            self.current_snapshot = str(name).strip().lower()  # normalize: match run_macro
        elif index is not None and (workspace or self.current_workspace):
            ws = workspace or self.current_workspace
            if ws and ws in self.snapshot_map:
                snapshots = self.snapshot_map[ws].get("snapshots", {})
                for snap_key, snap_value in snapshots.items():
                    if str(snap_key) == str(index) or snap_value == name:
                        self.current_snapshot = str(snap_value).strip().lower()
                        break
        logger.info(f"BRIDGE STATE → snapshot = {self.current_snapshot or 'None'}")
        self.broadcast_state()  # ← live web update

    def switch_to(self, workspace: str, snapshot: str = None) -> bool:
        """Switch workspace and optionally a snapshot without running a macro.

        Used by the click-to-switch UI buttons. Runs the same OSC sequence as
        run_macro's switch block but with no steps and no suppress guard.
        Returns True on success, False if the workspace is not in the snapshot map.
        """
        if not self.osc_client:
            logger.warning("switch_to: no OSC client")
            return False

        ws_entry = self.snapshot_map.get(workspace)
        if ws_entry is None:
            logger.warning(f"switch_to: workspace '{workspace}' not in snapshot map")
            return False

        ws_slot = ws_entry.get("slot")
        if ws_slot is None:
            logger.warning(f"switch_to: workspace '{workspace}' has no slot")
            return False

        t0 = time.time()
        with self._device_lock:
            self.osc_client.send_message("/loadQuickWorkspace", float(ws_slot))
        self._outputs_cache = None
        self._inputs_cache = None
        self._layout_epoch = time.time()
        self.current_workspace = workspace
        if self.mqtt_client:
            # keep the retained belief current — run_macro publishes these,
            # switch_to did not, so a restart absorbed a stale workspace
            # (hardware: bridge booted believing Work/snap_1)
            self.mqtt_client.publish("totalmix/workspace", str(ws_slot),
                                     retain=True)
        logger.info(f"switch_to: workspace '{workspace}' (slot {ws_slot})")

        if snapshot:
            self._wait_device(
                lambda st: st.raw.get("/1/labelSubmix", {}).get("last_seen", 0) >= t0,
                timeout=2.0, fallback_sleep=1.0,
                what=f"workspace '{workspace}' switch")
            snapshots = ws_entry.get("snapshots", {})
            snap_num  = None
            for snap_key, snap_val in snapshots.items():
                # snapshot maps come in two shapes: {"2": "Live"} and
                # {"2": {"name": "Live", "index": 2}} — handle both
                cand = (snap_val.get("name") or snap_key)                     if isinstance(snap_val, dict) else snap_val
                if str(cand).strip().lower() == snapshot.strip().lower():
                    snap_num = (snap_val.get("index") or snap_key)                         if isinstance(snap_val, dict) else snap_key
                    break
            if snap_num is not None:
                # recall stays classic under every transport (#25 TASK 11:
                # Global snapshot feedback unreliable; classic confirm isn't)
                osc_addr = f"/3/snapshots/{snapshot_num_to_osc_index(snap_num)}/1"
                with self._device_lock:
                    self.osc_client.send_message(osc_addr, 1.0)
                self._outputs_cache = None
                self._inputs_cache = None
                self._layout_epoch = time.time()
                self.current_snapshot = snapshot.strip().lower()
                if self.mqtt_client:
                    self.mqtt_client.publish("totalmix/snapshot",
                                             str(snap_num), retain=True)
                logger.info(f"switch_to: snapshot '{snapshot}' ({osc_addr})")
            else:
                logger.warning(f"switch_to: snapshot '{snapshot}' not found in '{workspace}'")

        self.reapply_held_knobs()   # snapshot-agnostic knobs
        self.broadcast_state()
        return True

    # ─────────────────────────────────────────────────────────────
    # NAME-BASED TARGET RESOLUTION (live, via OSC feedback)
    # ─────────────────────────────────────────────────────────────
    # /1/volume{N} indexes visible fader STRIPS, not hardware channels —
    # stereo-linked pairs collapse into one strip, so indices shift with
    # link state (which is snapshot-dependent). A statically captured
    # channel map goes stale the moment the mixer state differs.
    # Steps may therefore carry {"target": {"submix": name, "channel": name}}:
    # at fire time we select the submix, wait for TotalMix's feedback burst,
    # and match the channel NAME to a live strip index.

    def _wait_device(self, predicate, timeout, fallback_sleep, what="device feedback"):
        """Wait for device confirmation via OSC feedback (event-driven).

        Falls back to the historical fixed sleep when no listener is running
        (feedback-less deployments). On timeout, proceeds anyway — the
        timeout is a worst-case bound, matching the old sleep behavior, but
        confirmation typically arrives far sooner.
        """
        listener = self.osc_listener
        if listener is None or not listener.running:
            time.sleep(fallback_sleep)
            return False
        t0 = time.time()
        if listener.wait_for(predicate, timeout):
            logger.info(f"   → {what} confirmed in {time.time() - t0:.2f}s")
            return True
        logger.warning(f"   → {what} not confirmed within {timeout}s — proceeding")
        return False

    def _snapshot_name_for_slot(self, workspace, slot):
        """Snapshot name for a slot in a workspace of the snapshot map (both
        map shapes), or None."""
        entry = (self.snapshot_map or {}).get(workspace)
        if not isinstance(entry, dict):
            return None
        snaps = entry.get("snapshots", {}) or {}
        val = snaps.get(str(slot))
        if val is None:
            for k, v in snaps.items():
                if isinstance(v, dict) and str(v.get("index")) == str(slot):
                    val = v
                    break
        if isinstance(val, dict):
            val = val.get("name")
        return str(val).strip() if val else None

    def _sync_snapshot_from_device(self):
        """#30: follow snapshot recalls made IN TOTALMIX. The Global feed
        reports /snapshot/load/N per slot (0 off, 2 active, 3 active but
        modified). When exactly one slot is active, map it through the
        believed workspace's snapshot map and adopt the name for the header
        and the retained MQTT state. The workspace itself is never reported
        over OSC, so this is belief-for-display only: state_confirmed stays
        as it was and run_macro still performs its own switch."""
        listener = self.global_listener
        if listener is None:
            return
        snaps = dict(listener.state.snapshots)
        active = [int(n) for n, v in snaps.items() if v is not None and float(v) >= 2.0]
        if len(active) != 1:
            return
        slot = active[0]
        modified = float(snaps[slot]) >= 3.0
        changed = slot != self.device_snapshot_slot or modified != self.snapshot_modified
        if not changed:
            return
        self.device_snapshot_slot = slot
        self.snapshot_modified = modified
        name = self._snapshot_name_for_slot(self.current_workspace, slot)
        if name and name.lower() != (self.current_snapshot or ""):
            logger.info(f"device snapshot -> slot {slot} '{name}'"
                        f"{' (modified)' if modified else ''} (Global feedback)")
            self.current_snapshot = name.lower()
            if self.mqtt_client:
                try:
                    self.mqtt_client.publish("totalmix/snapshot", str(slot), retain=True)
                except Exception:
                    pass
        self.broadcast_state()
