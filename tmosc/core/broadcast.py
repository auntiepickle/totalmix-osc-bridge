"""Thread-safe WebSocket dispatch and MIDI-owner presence (what the
broadcast carries). Requires (created by TotalMixOSCBridge.__init__): _midi_owner,
_midi_owner_lock, current_*, state_confirmed, device_snapshot_slot,
snapshot_modified; main_loop is optional. Requires from the facade:
_ws_enqueue_all (the WS registry lives in tmosc.bridge, shared with the API)."""
import asyncio
import logging
import time

logger = logging.getLogger(__name__)

class BroadcastMixin:
    # ─────────────────────────────────────────────────────────────
    # SAFE WEBSOCKET BROADCAST (FINAL VERSION — MQTT thread safe)
    # ─────────────────────────────────────────────────────────────
    def _safe_broadcast_state(self, macro_update=None, macro_event=None):
        """Thread-safe broadcast that works from ANY thread (MQTT callbacks OR FastAPI)."""
        try:
            asyncio.get_running_loop()
            asyncio.create_task(self._do_broadcast(macro_update, macro_event))
        except RuntimeError:
            try:
                if hasattr(self, 'main_loop') and self.main_loop is not None:
                    asyncio.run_coroutine_threadsafe(self._do_broadcast(macro_update, macro_event), self.main_loop)
                else:
                    logger.debug("Broadcast skipped (no main_loop yet)")
            except Exception as e:
                logger.debug(f"Broadcast failed safely: {e}")

    async def _do_broadcast(self, macro_update=None, macro_event=None):
        """Actual broadcast logic (always runs inside asyncio): build the
        state frame once and enqueue it per client (#32)."""
        self._ws_enqueue_all({
            "current_snapshot": getattr(self, "current_snapshot", "unknown"),
            "current_workspace": getattr(self, "current_workspace", "unknown"),
            "state_confirmed": getattr(self, "state_confirmed", None),
            "device_snapshot_slot": getattr(self, "device_snapshot_slot", None),
            "snapshot_modified": getattr(self, "snapshot_modified", None),
            "workspace_report": self.workspace_report_state(),
            "macro_update": macro_update,
            "macro_event": macro_event,
        })

    # ─────────────────────────────────────────────────────────────
    # MIDI OWNERSHIP (coexistence): a tray/agent announces it holds the
    # physical MIDI port via a heartbeat. The browser yields Web MIDI
    # while an agent owns it (WinMM inputs are exclusive) and reclaims
    # when the agent leaves. Presence is advisory and TTL-expired.
    # ─────────────────────────────────────────────────────────────
    MIDI_OWNER_TTL_S = 6.0

    def midi_owner_state(self):
        """Current MIDI owner, or None if no heartbeat within the TTL."""
        with self._midi_owner_lock:
            o = self._midi_owner
            if not o:
                return None
            age = time.time() - o["last_seen"]
            if age > self.MIDI_OWNER_TTL_S:
                return None
            return {"id": o["id"], "host": o.get("host"), "age_s": round(age, 2)}

    def midi_owner_heartbeat(self, owner_id, host=None, title=None):
        """An agent announces it is handling MIDI. Refreshes presence; on a
        NEW claim (none/expired -> owned, or a different owner) broadcasts a
        midi_owner event so browsers yield promptly. `title` (#30) is the
        TotalMix main-window title as the agent sees it on that machine ("" =
        no TotalMix window there); None = an agent that does not report it."""
        now = time.time()
        with self._midi_owner_lock:
            prev = self._midi_owner
            was_active = bool(prev) and (now - prev["last_seen"] <= self.MIDI_OWNER_TTL_S)
            new_claim = (not was_active) or (prev is not None and prev["id"] != owner_id)
            self._midi_owner = {"id": owner_id, "host": host, "last_seen": now}
        if new_claim:
            self.broadcast_midi_owner(self.midi_owner_state())
        if title is not None:
            self.report_workspace(title, source=host or owner_id)
        return self.midi_owner_state()

    def midi_owner_release(self, owner_id):
        """An agent cleanly releases the port (shutdown). Clears presence and
        broadcasts so browsers reclaim MIDI immediately."""
        with self._midi_owner_lock:
            released = bool(self._midi_owner) and self._midi_owner["id"] == owner_id
            if released:
                self._midi_owner = None
        if released:
            self.broadcast_midi_owner(None)
        return released

    def broadcast_midi_owner(self, owner):
        """Push a typed midi_owner event to all WS clients (thread-safe)."""
        event = {"type": "midi_owner", "owner": owner}
        try:
            asyncio.get_running_loop()
            asyncio.create_task(self._do_broadcast_event(event))
        except RuntimeError:
            if getattr(self, "main_loop", None) is not None:
                try:
                    asyncio.run_coroutine_threadsafe(self._do_broadcast_event(event), self.main_loop)
                except Exception as e:
                    logger.debug(f"midi_owner broadcast skipped: {e}")

    async def _do_broadcast_event(self, event):
        """Broadcast an arbitrary typed event dict (has a top-level 'type')."""
        self._ws_enqueue_all(event)
