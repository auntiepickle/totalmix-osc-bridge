"""Workspace / snapshot switching and device-confirmed belief.
Requires (facade __init__): osc_client, snapshot_map, _device_lock, _layout_epoch,
_outputs_cache, _inputs_cache, mqtt_client, osc_listener, global_listener,
device_snapshot_slot, snapshot_modified, workspace_report. Requires (other
mixins): reapply_held_knobs (knobs), broadcast_state (broadcast)."""
import logging
import re
import time

from tmosc.config import snapshot_num_to_osc_index

logger = logging.getLogger(__name__)

class SwitchingMixin:
    # ── #30: the workspace as TotalMix itself shows it ───────────────────
    # No OSC feed carries the workspace, but TotalMix puts it in its own
    # window title: "RME TotalMix FX: Fireface UFX II (1) - 48.0k - Work"
    # for a Quick Select workspace, the .tmws path for a file-loaded one.
    # The agent on the TotalMix machine sends the raw title with every
    # MIDI-owner heartbeat; the bridge parses it HERE, so a title-format
    # change never needs a tray rebuild. Verified live 2026-09-14: the title
    # follows a Quick Select load within a second; nothing on disk does
    # (preferences.xml's LastPresetSel is written on save only). A reported
    # workspace is TotalMix's displayed truth, so it confirms the belief the
    # way device feedback confirms a commanded switch.
    WORKSPACE_REPORT_TTL_S = 10.0
    _SAMPLE_RATE_RE = re.compile(r"\d+(\.\d+)?k", re.I)

    @classmethod
    def parse_workspace_title(cls, title):
        """Window title -> (name, kind) or (None, None). kind is 'quick' for a
        Quick Select name, 'file' for a loaded .tmws (name = base name, the
        directory is wherever the user keeps workspaces). The last ' - '
        segment is the workspace; a title that ends at the sample rate has
        no workspace loaded from a named slot."""
        t = (title or "").strip()
        if " - " not in t:
            return None, None
        tail = t.rsplit(" - ", 1)[1].strip()
        if not tail or cls._SAMPLE_RATE_RE.fullmatch(tail):
            return None, None
        if tail.lower().endswith(".tmws"):
            base = re.split(r"[\\/]", tail)[-1][:-5].strip()
            return (base or None), ("file" if base else None)
        return tail, "quick"

    def _resolve_workspace_name(self, name):
        """Snapshot-map key for a reported name (exact, then case-insensitive),
        or None when the map does not know it (a slot renamed in TotalMix
        after the map was scraped, or a file workspace)."""
        m = self.snapshot_map or {}
        if name in m and isinstance(m[name], dict):
            return name
        low = name.lower()
        for k, v in m.items():
            if isinstance(v, dict) and k.lower() == low:
                return k
        return None

    def report_workspace(self, title, source=None):
        """Adopt the workspace TotalMix shows in its title (sent by the agent
        with its heartbeat). Returns the report dict, or None when the title
        carries no workspace (no TotalMix window: the belief stays as it was).
        Skipped while a bridge-side switch holds the device lock, so a title
        read mid-switch cannot flip the belief back to the old workspace."""
        name, kind = self.parse_workspace_title(title)
        if name is None:
            self.workspace_report = None
            return None
        if not self._device_lock.acquire(blocking=False):
            return self.workspace_report
        try:
            key = self._resolve_workspace_name(name)
            rep = {"name": key or name, "raw": name, "kind": kind,
                   "in_map": key is not None, "source": source, "ts": time.time()}
            prev = self.workspace_report
            self.workspace_report = rep
            if (prev and prev["name"] == rep["name"]
                    and self.current_workspace == rep["name"] and self.state_confirmed):
                return rep                      # steady state: nothing to announce
            self.current_workspace = rep["name"]
            self.state_confirmed = True
            slot = self.device_snapshot_slot
            if slot is not None:
                # the device slot was named through the OLD workspace's map
                snap = self._snapshot_name_for_slot(rep["name"], slot)
                self.current_snapshot = snap.lower() if snap else f"snap_{slot}"
            logger.info(f"workspace '{rep['name']}' ({kind}"
                        f"{'' if rep['in_map'] else ', not in the snapshot map'}) "
                        f"reported by {source or 'the agent'} from the TotalMix title")
            ws_slot = (self.snapshot_map.get(key) or {}).get("slot") if key else None
            if self.mqtt_client and ws_slot is not None:
                try:
                    from tmosc.mqtt_handler import _mark_own_republish
                    _mark_own_republish(self, "totalmix/workspace", str(ws_slot))
                    self.mqtt_client.publish("totalmix/workspace", str(ws_slot), retain=True)
                except Exception:
                    pass
            self.broadcast_state()
            return rep
        finally:
            self._device_lock.release()

    def workspace_report_state(self):
        """What the header shows: the reported workspace, where it came from,
        whether the snapshot map knows it, and whether the report is fresh."""
        rep = self.workspace_report
        if not rep:
            return None
        age = time.time() - rep["ts"]
        return {"name": rep["name"], "kind": rep["kind"], "in_map": rep["in_map"],
                "source": rep["source"], "age_s": round(age, 1),
                "live": age <= self.WORKSPACE_REPORT_TTL_S}

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
        over OSC (the agent's title report, report_workspace, covers that),
        so this is belief-for-display only: state_confirmed stays as it was
        and run_macro still performs its own switch."""
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
