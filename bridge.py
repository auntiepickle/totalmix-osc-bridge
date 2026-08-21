import os
import time
import logging
import logging.handlers
import json
import threading
import paho.mqtt.client as mqtt
import re
import asyncio
from config import *
from osc import get_client
from mqtt_handler import setup_mqtt
from osc_monitor import osc_monitor
from operations import OperationRegistry
import physical_table as pt

# === CENTRAL LOGGING ===
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s',
    datefmt='%H:%M:%S',
    handlers=[
        logging.StreamHandler(),
        logging.handlers.RotatingFileHandler(
            BRIDGE_LOG_FILE,
            maxBytes=LOG_MAX_BYTES,
            backupCount=LOG_BACKUP_COUNT,
            encoding='utf-8'
        )
    ]
)
logger = logging.getLogger(__name__)

# Load snapshot map — prefer the SMB-mounted path (same source as mqtt_handler.py),
# fall back to local file for dev environments without the mount.
_SNAPSHOT_MAP_PATHS = [
    "/app/config/ufx2_snapshot_map.json",  # Docker: SMB mount (authoritative)
    "ufx2_snapshot_map.json",              # Local dev fallback
]
SNAPSHOT_MAP = {}
for _p in _SNAPSHOT_MAP_PATHS:
    try:
        with open(_p, "r", encoding="utf-8-sig") as f:
            SNAPSHOT_MAP = json.load(f)
        logger.info(f"Loaded snapshot map from {_p} — workspaces: {list(SNAPSHOT_MAP.keys())}")
        break
    except FileNotFoundError:
        continue
    except Exception as e:
        logger.error(f"Failed to load snapshot map from {_p}: {e}")
        break
if not SNAPSHOT_MAP:
    logger.warning("No snapshot map loaded — WS/SS switching will be disabled until map is available")

# Load mappings — prefer mappings.json (user config), fall back to example
_MAPPINGS_PATHS = ["mappings.json", "mappings.example.json"]
MAPPINGS = {"macros": {}}
MAPPINGS_SOURCE = None
MAPPINGS_IS_EXAMPLE = False

for _mp in _MAPPINGS_PATHS:
    try:
        with open(_mp, "r", encoding="utf-8") as f:
            MAPPINGS = json.load(f)
        MAPPINGS_SOURCE = _mp
        MAPPINGS_IS_EXAMPLE = _mp != "mappings.json"
        if MAPPINGS_IS_EXAMPLE:
            logger.warning(
                f"mappings.json not found — loaded fallback {_mp}. "
                "Create mappings.json to override."
            )
        else:
            logger.info(f"Loaded mappings.json — {len(MAPPINGS.get('macros', {}))} macros")
        break
    except FileNotFoundError:
        continue
    except Exception as e:
        logger.error(f"Failed to load {_mp}: {e}")
        break

# OSC client — shared per-(ip, port) socket cache in osc.py, same one
# mqtt_handler's send_osc() uses.
if OSC_IP and OSC_PORT:
    osc_client = get_client(OSC_IP, OSC_PORT)
    logger.info(f"OSC Client ready → {OSC_IP}:{OSC_PORT}")
else:
    osc_client = None
    logger.warning("OSC_IP not set — OSC disabled, macros will be skipped")

class _EdgeToggleClient:
    """OSC-client shim for momentary-button addresses: turns the absolute
    0/1 stream an operation emits into edge-triggered 1.0 presses (the
    device toggles on 1.0 and ignores 0.0). Non-button addresses pass
    through untouched."""

    def __init__(self, real, addr, initial_on):
        self._real = real
        self._addr = addr
        self._on = bool(initial_on)

    def send_message(self, addr, value):
        if addr != self._addr:
            self._real.send_message(addr, value)
            return
        want = float(value) >= 0.5
        if want != self._on:
            self._real.send_message(addr, 1.0)
            self._on = want


# === WEBSOCKET CLIENTS (shared between bridge.py and web_client.py) ===
ws_clients = []  # list of active FastAPI WebSocket connections

class TotalMixOSCBridge:
    def __init__(self, osc_client, mappings, snapshot_map):
        self._suppress_count = 0    # >0 while any macro runs (see property)
        self._last_macro_end_time = 0.0
        # Serializes every sender of device-global aim state (/setSubmix,
        # /setBankStart, /1/busX) TOGETHER WITH the writes that depend on
        # it. Page-1/page-2 addresses are relative to submix+row+bank, so
        # a concurrent macro re-aiming mid-ramp silently retargets the
        # other macro's writes (review finding: the confirm-then-act
        # guarantee is void without this). RLock: resolution helpers
        # re-enter from within a locked step.
        self._device_lock = threading.RLock()
        # Bumped whenever the BRIDGE commands a layout change (workspace/
        # snapshot). Consumers refuse device state older than this.
        self._layout_epoch = 0.0
        self.osc_client = osc_client
        self.mappings = mappings
        self.mappings_is_example = MAPPINGS_IS_EXAMPLE
        self.mappings_source = MAPPINGS_SOURCE
        self.snapshot_map = snapshot_map
        self.current_workspace = None
        self.current_snapshot = None
        self.mqtt_client = None
        self.macro_live_state = {}
        self.channel_map = None
        self.channel_map_is_example = False
        self._running_macros = set()        # names of macros currently executing
        self._cancel_events = {}            # macro_name → threading.Event (for restart mode)
        self._queued_params = {}            # macro_name → float (for queue/restart modes)
        self._macro_lock = threading.Lock() # guards the three structures above (web + MQTT + queued threads)
        self.mqtt_connected = False         # set True/False by mqtt_handler callbacks
        self.osc_listener = None            # OSCListener — set by start_osc_listener()
        self.global_listener = None         # GlobalOSCListener (#25) — start_global_osc()
        self.global_transport = None        # GlobalTransport (#25) — active or shadow
        self.state_confirmed = None         # last commanded switch confirmed by device feedback?
        self.last_probe = None              # result of the last device liveness probe
        self.sweep_state = {"status": "idle"}      # physical-table sweep job state (#24)
        # Live-vs-map freshness verdict (None = unknown). A stale map after
        # a snapshot change refused correctly but looked like a dead server
        # to the user (field report) — this drives the UI drift banner.
        self._load_channel_map()

        # === SAFE THREAD-AWARE BROADCAST (MQTT + FastAPI) ===
        self.broadcast_state = self._safe_broadcast_state

    @property
    def _suppress_handler(self):
        """True while ANY macro is executing. A plain boolean was cleared
        by whichever concurrent macro finished FIRST, dropping feedback
        suppression mid-ramp for the still-running one (review finding) —
        so this is a counter now."""
        return self._suppress_count > 0

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
        """Actual broadcast logic (always runs inside asyncio)."""
        for client in list(ws_clients):
            try:
                state = {
                    "current_snapshot": getattr(self, "current_snapshot", "unknown"),
                    "current_workspace": getattr(self, "current_workspace", "unknown"),
                    "macro_update": macro_update,
                    "macro_event": macro_event,
                }
                await client.send_json(state)
            except Exception:
                if client in ws_clients:
                    ws_clients.remove(client)

   
    def _load_channel_map(self):
        """Load ufx2_channel_map.json, falling back to the example file if missing.

        Sets self.channel_map_is_example = True when the fallback is used so the
        web UI can surface a setup prompt to the user.
        """
        for path in ("ufx2_channel_map.json", "ufx2_channel_map.example.json"):
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

    def _get_macro_duration_ms(self, macro: dict, clock_bpm: float = None) -> int:
        """Return ramp/LFO duration in ms, or 400 for instant macros (used for WS progress events).

        If the operation config has ``"bpm": "clock"`` and clock_bpm is provided,
        the detected MIDI clock BPM is used for the duration calculation.
        """
        for step in macro.get("steps", []):
            if "operation" in step:
                op = step["operation"]
                if "duration" in op:   # explicit seconds override bars/bpm
                    try:
                        return int(float(op["duration"]) * 1000)
                    except (TypeError, ValueError):
                        pass
                bars = op.get("bars", 2)
                bpm  = op.get("bpm", 140)
                if bpm == "clock":
                    bpm = clock_bpm if clock_bpm else 140
                return int(bars * (240000 / bpm))
        return 400  # instant macro — brief visual feedback

    def get_routing_label(self, macro_name: str) -> str:
        """Return human-readable routing line for UI cards"""
        steps = self.mappings.get("macros", {}).get(macro_name, {}).get("steps", [])
        # Name-based targets are authoritative — they never go stale
        for step in steps:
            t = step.get("target")
            if (t and t.get("channel")
                    and str(t.get("param", "")).lower() in self.CHANNEL_DETAIL_PARAMS):
                pretty = str(t["param"]).replace("_", " ").title()
                return f"{t['channel']} ({pretty})"
            if t and str(t.get("param", "")).lower() in self.GLOBAL_FX_PARAMS:
                pretty = str(t["param"]).replace("_", " ").title()
                return f"FX: {pretty}"
            if t and t.get("channel"):
                param = str(t.get("param", "volume")).lower()
                if param == "mute":
                    # Mute is global-per-channel — naming a submix would
                    # imply a scope that does not exist (#10)
                    return f"{t['channel']} (mute)"
                if t.get("submix"):
                    suffix = f" ({param})" if param != "volume" else ""
                    return f"{t['channel']} → {t['submix']}{suffix}"
        if not self.channel_map:
            self._load_channel_map()
        # Legacy: match raw addresses against the channel map
        for submix_name, submix_data in self.channel_map.get("submixes", {}).items():
            for send_name, send_data in submix_data.get("sends", {}).items():
                if any(step.get("osc") == send_data.get("osc_address")
                       for step in steps):
                    return f"{send_name} → {submix_name}"
        return "—"
    
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
                osc_addr = f"/3/snapshots/{snapshot_num_to_osc_index(snap_num)}/1"
                with self._device_lock:
                    if self._global_active():
                        # #25: 1-based Global recall, feedback-confirmed
                        self.global_transport.load_snapshot(int(snap_num))
                    else:
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

        self.broadcast_state()
        return True

    def run_macro(self, macro_name: str, param: float = 0.5, clock_bpm: float = None):
        if macro_name not in self.mappings.get("macros", {}):
            logger.error(f"Macro '{macro_name}' not found")
            return

        if self.osc_client is None:
            logger.warning(f"Macro '{macro_name}' skipped — OSC not configured (set OSC_IP)")
            self.broadcast_state(macro_event={
                "type": "macro_skipped",
                "name": macro_name,
                "reason": "osc_not_configured",
            })
            return

        macro = self.mappings["macros"][macro_name]
        value = max(macro.get("param_range", [0.0, 1.0])[0],
                    min(macro.get("param_range", [0.0, 1.0])[1], float(param)))

        # === ROBUST NAME EXTRACTION ===
        ws_name = macro.get("workspace")
        snap_name = macro.get("snapshot")
        if isinstance(snap_name, dict):
            snap_name = snap_name.get("name") or list(snap_name.values())[0] if snap_name else None
        if isinstance(ws_name, dict):
            ws_name = ws_name.get("name") or list(ws_name.values())[0] if ws_name else None

        if snap_name:
            # Normalize to lowercase-stripped so comparisons are case-insensitive.
            # Previously used .title() which caused mismatches when mqtt_handler
            # stored snapshot names in lowercase from the snapshot map.
            snap_name = re.sub(r'^\d+\s*-\s*', '', str(snap_name)).strip().lower()

        force_switch = macro.get("force_switch", False)

        logger.info(f"Running macro '{macro_name}' → {ws_name}/{snap_name} param={value:.4f} (force_switch={force_switch})")

        # === FIRE MODE GUARD ===
        # fire_mode in mappings.json controls behaviour when macro is already running:
        #   "ignore"  (default) — drop the new trigger
        #   "queue"             — run once more after current completes
        #   "restart"           — cancel current immediately, re-run with new param
        fire_mode = macro.get("fire_mode", "ignore")
        # Guard + registration are atomic: triggers arrive concurrently from the
        # web thread, the MQTT thread, and queued re-fire threads.
        with self._macro_lock:
            if macro_name in self._running_macros:
                if fire_mode == "ignore":
                    logger.info(f"   → '{macro_name}' running, mode=ignore — dropped")
                    return
                elif fire_mode == "queue":
                    self._queued_params[macro_name] = (param, clock_bpm)
                    logger.info(f"   → '{macro_name}' running, mode=queue — queued (param={param:.3f})")
                    return
                elif fire_mode == "restart":
                    self._queued_params[macro_name] = (param, clock_bpm)
                    ev = self._cancel_events.get(macro_name)
                    if ev:
                        ev.set()
                    logger.info(f"   → '{macro_name}' running, mode=restart — cancelling and re-queuing")
                    return

            # Optional post-completion cooldown (ms). Set "debounce_ms" in macro config.
            debounce_ms = macro.get("debounce_ms", 0)
            if debounce_ms > 0:
                elapsed = (time.time() - self.macro_live_state.get(macro_name, {}).get("last_trigger", 0)) * 1000
                if elapsed < debounce_ms:
                    logger.info(f"   → '{macro_name}' in debounce window ({elapsed:.0f}/{debounce_ms}ms) — ignored")
                    return

            self._suppress_count += 1
            cancel_event = threading.Event()
            self._cancel_events[macro_name] = cancel_event
            self._running_macros.add(macro_name)

        try:
          # All device-global aim state (/setSubmix, /setBankStart, /1/busX)
          # and every write relative to it happens under the device lock —
          # one macro's step sequence at a time. Without it a concurrent
          # macro's re-aim silently retargets this macro's in-flight writes.
          with self._device_lock:
            # === ALWAYS RESOLVE SLOTS/INDICES ===
            ws_slot = None
            snap_num = None

            if ws_name and ws_name in self.snapshot_map:
                ws_slot = self.snapshot_map[ws_name].get("slot")

            if snap_name and ws_name in self.snapshot_map:
                snapshots = self.snapshot_map[ws_name].get("snapshots", {})
                for snap_key, snap_data in snapshots.items():
                    if isinstance(snap_data, dict):
                        candidate_name = snap_data.get("name") or snap_key
                        candidate_index = snap_data.get("index")
                    else:
                        candidate_name = snap_data
                        candidate_index = snap_key
                    if str(candidate_name).strip().lower() == snap_name:
                        snap_num = candidate_index or snap_key
                        break

            # === STATE-AWARE SWITCH ===
            # state_confirmed gate: an absorbed retained belief can be stale
            # (device moved while the bridge was down). Now that startup
            # resolves it to a REAL name it can match the target — skipping
            # the switch would leave the device on the wrong snapshot. One
            # redundant recall after a restart is the price of correctness.
            already_on_target = (
                bool(self.state_confirmed) and
                self.current_workspace == ws_name and
                self.current_snapshot == snap_name and
                ws_name is not None and snap_name is not None
            )

            # Block WS/SS switch if another macro is mid-execution and force_switch is off.
            # Avoids tearing the mixer state while a ramp is running.
            with self._macro_lock:
                other_running = len(self._running_macros) > 1  # this macro already in set
            if not already_on_target and not force_switch and other_running:
                logger.info(
                    f"   → '{macro_name}' skipped: WS/SS switch needed but "
                    f"{len(self._running_macros)-1} other macro(s) running (force_switch=False)"
                )
                self.broadcast_state(macro_event={
                    "type": "macro_skipped",
                    "name": macro_name,
                    "reason": "ws_ss_blocked",
                })
                return

            if force_switch or not already_on_target:
                logger.info(f"   → Need to switch (force={force_switch} or state mismatch)")

                if ws_name and ws_slot is not None:
                    t0 = time.time()
                    self.osc_client.send_message("/loadQuickWorkspace", float(ws_slot))
                    # The layout just changed: cached output rows and any
                    # state captured before this instant are void (a stale
                    # cache here once could approve a crashing /setSubmix)
                    self._outputs_cache = None
                    self._inputs_cache = None
                    self._layout_epoch = time.time()
                    logger.info(f"   → Switched workspace to '{ws_name}' (slot {ws_slot})")
                    self.current_workspace = ws_name
                    if self.mqtt_client:
                        self.mqtt_client.publish("totalmix/workspace", str(ws_slot), retain=True)
                        logger.info(f"   → Published to HA → totalmix/workspace = {ws_slot}")
                    # A workspace load triggers a full state dump (always
                    # including /1/labelSubmix) — its arrival confirms the
                    # switch, typically well under the old fixed 1.0s sleep
                    self.state_confirmed = self._wait_device(
                        lambda st: st.raw.get("/1/labelSubmix", {}).get("last_seen", 0) >= t0,
                        timeout=2.0, fallback_sleep=1.0,
                        what=f"workspace '{ws_name}' switch")
                    # TASK-6 race hardening: dumps that raced in BETWEEN the
                    # command and its confirmation can carry pre-switch
                    # content with fresh stamps — re-stamp the epoch so only
                    # post-confirmation banks are trusted for matching
                    if self.state_confirmed:
                        self._layout_epoch = time.time()

                if snap_name and snap_num is not None and self._global_active():
                    # #25: Global snapshot recall — 1-BASED address, no 9-N
                    # button inversion, confirmed by feedback value 2.0
                    ok = self.global_transport.load_snapshot(int(snap_num))
                    self._outputs_cache = None
                    self._inputs_cache = None
                    self._layout_epoch = time.time()
                    self.current_snapshot = snap_name
                    self.state_confirmed = bool(ok)
                    logger.info(f"   → Switched snapshot to '{snap_name}' via "
                                f"Global OSC (/snapshot/load/{int(snap_num)}) "
                                f"confirmed={ok}")
                    if self.mqtt_client:
                        self.mqtt_client.publish("totalmix/snapshot", str(snap_num), retain=True)
                        logger.info(f"   → Published to HA → totalmix/snapshot = {snap_num}")
                elif snap_name and snap_num is not None:
                    osc_addr = f"/3/snapshots/{snapshot_num_to_osc_index(snap_num)}/1"
                    t0 = time.time()
                    self.osc_client.send_message(osc_addr, 1.0)
                    # Snapshots re-pair strips and can change layouts too
                    self._outputs_cache = None
                    self._inputs_cache = None
                    self._layout_epoch = time.time()
                    logger.info(f"   → Switched snapshot to '{snap_name}' (OSC {osc_addr} = 1.0)")
                    self.current_snapshot = snap_name
                    if self.mqtt_client:
                        self.mqtt_client.publish("totalmix/snapshot", str(snap_num), retain=True)
                        logger.info(f"   → Published to HA → totalmix/snapshot = {snap_num}")
                    # TotalMix echoes the active snapshot's button state back
                    self.state_confirmed = self._wait_device(
                        lambda st, addr=osc_addr: (
                            st.raw.get(addr, {}).get("args") == [1.0]
                            and st.raw.get(addr, {}).get("last_seen", 0) >= t0),
                        timeout=1.0, fallback_sleep=0.3,
                        what=f"snapshot '{snap_name}' recall")
                    if self.state_confirmed:
                        self._layout_epoch = time.time()  # TASK-6 race hardening
            else:
                logger.info(f"   → Already on target {ws_name}/{snap_name} — skipping ws/ss switch (force_switch=False)")

            # (#24: snapshot switches are non-events for resolution — the
            # physical table is layout-invariant and per-write confirmations
            # carry the correctness claim. No map work here, by design.)

            # === EMIT macro_start SO BROWSER CAN SYNC PROGRESS BAR ===
            duration_ms = self._get_macro_duration_ms(macro, clock_bpm=clock_bpm)
            self.broadcast_state(macro_event={
                "type": "macro_start",
                "name": macro_name,
                "duration_ms": duration_ms,
            })

            # === MACRO STEPS WITH OPERATION LIBRARY ===
            # Restores are GUARANTEED via finally: an exception (or refusal
            # mid-sequence) must never leave the bank scrolled or a non-
            # input row selected — both persist on the device and silently
            # mis-target every later macro (review finding).
            _bank_dirty = False
            # target steps never touch classic row state under the global
            # transport, so they cannot dirty it
            _row_dirty = (not self._global_active() and
                          any(str(s.get("target", {}).get("row", 1)) in ("2", "3")
                              for s in macro.get("steps", []) if "target" in s))
            try:
             for step in macro.get("steps", []):
                osc_addr = step.get("osc")

                # #25: name-targeted steps route through the Global
                # transport when selected — absolute addressing, no aiming,
                # no bank/row state to dirty or restore. Raw-OSC steps
                # (classic namespace) fall through to the classic path.
                if "target" in step and self._global_active():
                    self._run_step_global(step, macro_name, value,
                                          cancel_event, clock_bpm)
                    continue

                # CRASH GUARD for RAW steps: /setSubmix past the last real
                # output crashes TotalMix (hardware root cause). A raw index
                # cannot be name-verified, so it is allowed ONLY when it is
                # exactly a known submix index from the map AND the live
                # output row still matches that map. No arithmetic bounds:
                # the first version used 2×strips, which assumes every
                # output is stereo — one mono output (live layout: Main)
                # made the bound land exactly ON the fatal index. And no
                # index+1 pair-half allowance either: that re-assumes width
                # at the tail, where a mono LAST submix would make +1 the
                # fatal index. Widths have now bitten three times — exact
                # match or refuse. The whole macro stops on refusal — later
                # raw steps assume the switch happened.
                if "target" not in step and osc_addr == "/setSubmix":
                    # CRASH GUARD: /setSubmix past the hardware end crashes
                    # TotalMix (root cause). #24: membership in the MEASURED
                    # physical outputs table is the guard — every measured
                    # key is a real hardware start, bounded by the sweep's
                    # verified boundary. No arithmetic, no live enumeration.
                    table = self._physical_table()
                    known = {int(k) for k in
                             ((table or {}).get("rows", {})
                              .get("outputs", {}) or {})}
                    try:
                        raw_idx = float(value if step.get("value") == "{{param}}"
                                        else step.get("value"))
                    except (TypeError, ValueError):
                        raw_idx = None
                    if (raw_idx is None or raw_idx != int(raw_idx)
                            or int(raw_idx) not in known):
                        why = (f"index {raw_idx} is not a measured output "
                               f"channel (table knows {sorted(known)})"
                               if known else
                               "no measured outputs table — run "
                               "POST /api/device/sweep")
                        logger.error(f"   → raw /setSubmix REFUSED ({why}) — "
                                     f"an out-of-range /setSubmix crashes "
                                     f"TotalMix; use a name-based target. "
                                     f"Macro aborted.")
                        self.broadcast_state(macro_event={
                            "type": "macro_skipped",
                            "name": macro_name,
                            "reason": "setsubmix_unverifiable",
                        })
                        break

                # Name-based target: live-resolve strip index via OSC feedback.
                # Stored-address fallback ONLY when feedback is unavailable —
                # if the live bank is visible and the channel is absent, the
                # stored address may point at a different channel (snapshots
                # re-pair strips), so the step is skipped instead.
                if "target" in step:
                    _, live_addr, status = self._resolve_target(step["target"])
                    if status == "resolved":
                        osc_addr = live_addr
                    elif status == "no_feedback" and osc_addr:
                        logger.warning(f"   → using stored address {osc_addr} (no feedback)")
                    else:
                        logger.error(f"   → step skipped: target "
                                     f"{step['target'].get('channel')}@"
                                     f"{step['target'].get('submix')} unresolved ({status})")
                        self.broadcast_state(macro_event={
                            "type": "macro_skipped",
                            "name": macro_name,
                            "reason": f"target_{status}",
                        })
                        continue

                if not osc_addr:
                    logger.error(f"   → step skipped: no osc address")
                    continue

                # Channel-detail steps shift the bank window to aim page 2 —
                # restore it right after the step so every bank-0 assumption
                # elsewhere (volume/pan/mute resolution) stays true
                _restore_bank = ("target" in step and
                                 str(step["target"].get("param", "")).lower()
                                 in self.CHANNEL_DETAIL_PARAMS)

                if _restore_bank:
                    _bank_dirty = True

                _is_button = ("target" in step and
                              str(step["target"].get("param", "")).lower()
                              in self.BUTTON_PARAMS)
                _button_row = (str(step["target"].get("row", 1))
                               if _is_button else None)

                if "operation" in step and step.get("value") == "{{param}}":
                    op_config = step["operation"]
                    # Substitute live MIDI clock BPM when the mapping uses "bpm": "clock"
                    if op_config.get("bpm") == "clock":
                        resolved_bpm = clock_bpm if clock_bpm else 140
                        op_config = {**op_config, "bpm": resolved_bpm}
                        logger.info(f"   → BPM clock sync: using {resolved_bpm} BPM")
                    op_client = self.osc_client
                    if _is_button:
                        # Momentary button under modulation: sync the real
                        # state once, then press only on 0/1 edges
                        initial = self._read_button_state(osc_addr, _button_row)
                        if initial is None:
                            logger.error(f"   → step skipped: {osc_addr} state "
                                         f"unknowable, cannot modulate a "
                                         f"momentary button blind")
                            continue
                        op_client = _EdgeToggleClient(self.osc_client,
                                                      osc_addr, initial)
                    OperationRegistry.execute(
                        op_config["type"],
                        op_client,
                        osc_addr,
                        value,
                        op_config,
                        cancel_event=cancel_event,
                    )
                    if _restore_bank:
                        self.osc_client.send_message("/setBankStart", 0.0)
                        _bank_dirty = False
                        logger.info("   → bank window restored to 0 after channel-detail step")
                    continue

                # === NORMAL STATIC STEP ===
                step_val = value if step.get("value") == "{{param}}" else step.get("value")
                try:
                    if _is_button:
                        # A value write cannot set a momentary button —
                        # read fresh state, press only if it differs
                        self._set_button_state(osc_addr,
                                               float(step_val) >= 0.5,
                                               _button_row)
                    else:
                        self.osc_client.send_message(osc_addr, float(step_val))
                        logger.info(f"   → {osc_addr} = {step_val}")
                except Exception as e:
                    logger.error(f"OSC send failed: {e}")
                if _restore_bank:
                    self.osc_client.send_message("/setBankStart", 0.0)
                    _bank_dirty = False
                    logger.info("   → bank window restored to 0 after channel-detail step")
            finally:
                if _bank_dirty:
                    self.osc_client.send_message("/setBankStart", 0.0)
                    logger.info("   → bank window restored to 0 (finally)")
                # Restore the input row if any step drove the playback or
                # output row — page-1 addresses are row-relative, so leaving
                # another row selected would mis-route the next macro
                if _row_dirty:
                    self.osc_client.send_message("/1/busInput", 1.0)
                    logger.info("   → input row restored after playback/output-row step")

            # === GUARANTEED HA SYNC ===
            if self.mqtt_client:
                if ws_slot is not None:
                    self.mqtt_client.publish("totalmix/workspace", str(ws_slot), retain=True)
                if snap_num is not None:
                    self.mqtt_client.publish("totalmix/snapshot", str(snap_num), retain=True)
                    self.mqtt_client.publish("totalmix/snapshot/status", f"loaded_{snap_num}", retain=True)

            logger.info(f"Macro '{macro_name}' complete")

            # === RICH MACRO UPDATE + macro_complete EVENT ===
            macro_data = self.mappings["macros"][macro_name]
            live_data = {
                "name": macro_name,
                "description": macro_data.get("description", ""),
                "value": float(value),
                "progress": 100,
                "lfo_active": False,
                "last_trigger": time.time(),
                "osc_preview": f"{(macro_data.get('steps') or [{}])[0].get('osc', '')} = {value:.3f}",
                "routing_label": self.get_routing_label(macro_name),
                "midi_trigger": macro_data.get("midi_triggers", [{}])[0] if macro_data.get("midi_triggers") else None,
            }
            self.macro_live_state[macro_name] = live_data
            self.broadcast_state(
                macro_update=live_data,
                macro_event={"type": "macro_complete", "name": macro_name},
            )

        finally:
            self._last_macro_end_time = time.time()
            with self._macro_lock:
                self._suppress_count = max(0, self._suppress_count - 1)
                self._cancel_events.pop(macro_name, None)
                self._running_macros.discard(macro_name)
                # Fire any queued trigger (queue mode or restart mode)
                queued = self._queued_params.pop(macro_name, None)
            if queued is not None:
                q_param, q_bpm = queued
                logger.info(f"   → '{macro_name}' firing queued trigger (param={q_param:.3f})")
                threading.Thread(target=self.run_macro, args=(macro_name, q_param),
                                 kwargs={"clock_bpm": q_bpm}, daemon=True).start()

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

    # Global FX-section parameters (#5 phase 1). These addresses are FIXED —
    # captured from live feedback, page 3, no channel/submix/row scope — so
    # resolution is deterministic and needs no feedback at all. Channel EQ is
    # NOT here: its scope needs a hardware measurement first (design law).
    GLOBAL_FX_PARAMS = {
        "reverb_enable":   "/3/reverbEnable",
        "reverb_time":     "/3/reverbTime",
        "reverb_volume":   "/3/reverbVolume",
        "reverb_width":    "/3/reverbWidth",
        "reverb_predelay": "/3/reverbPredelay",
        "echo_enable":     "/3/echoEnable",
        "echo_time":       "/3/echoDelaytime",
        "echo_feedback":   "/3/echoFeedback",
        "echo_volume":     "/3/echoVolume",
        "echo_width":      "/3/echoWidth",
    }

    # Channel-detail (page 2) parameters (#5 phase 2). Page 2 mirrors the
    # channel at the BANK-START position (hardware-verified with fixture
    # EQs), so resolution aims the page: pin bank 0 → resolve the strip by
    # name → compute the strip's HARDWARE-CHANNEL offset (stereo pairs
    # occupy two positions) → /setBankStart offset → write /2/... → restore
    # bank 0 after the step (bankStart is shared global state every other
    # resolve assumes is 0).
    # Momentary TOGGLE buttons (hardware-verified: /3/ FX enables
    # 2026-08-01, ALL /2/ enables 2026-08-02 — 6/6 unanimous, both rows):
    # writing 1.0 FLIPS the state and 0.0 does NOTHING. A plain value
    # write therefore "sets" nothing — set = read fresh state, press only
    # on difference; modulate = press on 0/1 edges. /2/recordEnable is
    # deliberately unexposed and untested (DURec record-arm — real-world
    # consequence); if ever exposed, assume momentary and verify first.
    BUTTON_PARAMS = {"reverb_enable", "echo_enable",
                     "eq_enable", "dyn_enable", "lowcut_enable",
                     "alev_enable", "phase", "phase_r"}

    CHANNEL_DETAIL_PARAMS = {
        "eq_enable":   "/2/eqEnable",
        "eq_gain_1":   "/2/eqGain1",
        "eq_freq_1":   "/2/eqFreq1",
        "eq_q_1":      "/2/eqQ1",
        "eq_gain_2":   "/2/eqGain2",
        "eq_freq_2":   "/2/eqFreq2",
        "eq_q_2":      "/2/eqQ2",
        "eq_gain_3":   "/2/eqGain3",
        "eq_freq_3":   "/2/eqFreq3",
        "eq_q_3":      "/2/eqQ3",
        "lowcut_freq": "/2/lowcutFreq",
        # Dynamics / Auto-Level / input stage (#20) — same page-2 aiming.
        # First tranche: addresses confirmed in the original 90-address
        # probe. The full Dynamics inventory (comp/exp threshold, ratio,
        # attack, release, enable) lands after the device inventory round.
        "dyn_gain":       "/2/compexpGain",
        "dyn_enable":     "/2/compexpEnable",
        "comp_thresh":    "/2/compTrsh",
        "comp_ratio":     "/2/compRatio",
        "exp_thresh":     "/2/expTrsh",
        "exp_ratio":      "/2/expRatio",
        "dyn_attack":     "/2/compexpAttack",
        "dyn_release":    "/2/compexpRelease",
        "alev_risetime":  "/2/alevRisetime",
        "lowcut_enable":  "/2/lowcutEnable",
        "lowcut_grade":   "/2/lowcutGrade",
        "eq_type_1":      "/2/eqType1",
        "eq_type_3":      "/2/eqType3",
        "alev_enable":    "/2/alevEnable",
        "alev_headroom":  "/2/alevHeadroom",
        "alev_maxgain":   "/2/alevMaxgain",
        "input_gain":     "/2/gain",
        "input_gain_r":   "/2/gainRight",
        "phase":          "/2/phase",
        "phase_r":        "/2/phaseRight",
    }

    @staticmethod
    def _param_address(param: str, strip) -> str:
        """OSC address for a parameter on a live-resolved strip. All are
        page-1, row-relative (the bus selection picks the row):
        volume /1/volume{n} · mute /1/mute/1/{n} · pan /1/pan{n}."""
        if param == "mute":
            return f"/1/mute/1/{strip}"
        if param == "pan":
            return f"/1/pan{strip}"
        return f"/1/volume{strip}"

    @staticmethod
    def _names_cover(strip_name: str, wanted: str) -> bool:
        """True when a strip label covers the wanted channel name across
        stereo-link changes. Snapshots re-pair strips: 'AN 2' disappears when
        AN 1+2 link into one 'AN 1/2' strip (whose fader controls both), and
        vice versa. Pair labels look like '<prefix> <a>/<b>'."""
        s = strip_name.strip().lower()
        w = wanted.strip().lower()
        if s == w:
            return True
        pair = re.match(r"^(.*?)\s*(\d+)/(\d+)$", s)
        if pair and w in (f"{pair.group(1).strip()} {pair.group(2)}",
                          f"{pair.group(1).strip()} {pair.group(3)}"):
            return True  # strip is the linked pair containing wanted
        pair = re.match(r"^(.*?)\s*(\d+)/(\d+)$", w)
        if pair and s in (f"{pair.group(1).strip()} {pair.group(2)}",
                          f"{pair.group(1).strip()} {pair.group(3)}"):
            return True  # wanted was a pair, strip is one (unlinked) half
        return False

    def _resolve_target(self, target: dict, timeout: float = 1.5):
        """Resolve {"submix": name, "channel": name} to
        (setsubmix_index, live_osc_address, status).

        status:
          "resolved"    — live match (exact or stereo-pair covering)
          "no_feedback" — no listener / no confirmation; caller MAY fall
                          back to the stored address (we know nothing)
          "not_in_bank" — the live bank was seen and the channel is NOT in
                          it; caller MUST NOT write to the stored address
                          (snapshots re-pair strips, so it now points at a
                          different channel — hardware-observed)
        """
        submix_name = str(target.get("submix", "")).strip()
        channel_name = str(target.get("channel", "")).strip()
        row = str(target.get("row", 1))
        param = str(target.get("param", "volume")).strip().lower()
        if param in self.GLOBAL_FX_PARAMS:
            # Fixed global address — no submix, no channel, no feedback needed
            addr = self.GLOBAL_FX_PARAMS[param]
            logger.info(f"   → target: global FX '{param}' → {addr}")
            return None, addr, "resolved"
        channel_scoped = (param == "mute")  # channel-detail handled below
        if param in self.CHANNEL_DETAIL_PARAMS and row == "2":
            # EQ/channel-detail exists on hardware inputs and outputs but
            # NOT on software playback (user-reported, device has no such
            # page) — an aimed write would land on a real channel instead
            logger.error(f"   → '{param}' does not exist on the playback "
                         f"row — refusing (EQ lives on hardware inputs "
                         f"and outputs only)")
            return None, None, "not_in_bank"
        if param in self.CHANNEL_DETAIL_PARAMS:
            # Page-2 aiming (#24): /setBankStart takes FIXED hardware-mono
            # offsets (RME-documented, trackname-sweep-proven invariant
            # across snapshots) — the physical table resolves the name to
            # its start directly. No widths, no strip resolution, no
            # live-layout gates: the per-write /2/trackname confirmation is
            # the correctness claim, made at the only moment it matters.
            table = self._physical_table()
            row_key = "outputs" if row == "3" else "inputs"
            offset = (pt.resolve_start(table, row_key, channel_name)
                      if table else None)
            if offset is None:
                logger.error(f"   → '{channel_name}' is not in the physical "
                             f"{row_key} table — cannot aim page 2; run "
                             f"POST /api/device/sweep to (re)measure it")
                return None, None, "not_in_bank"
            listener = self.osc_listener
            if listener is None or not listener.running:
                logger.error(f"   → no OSC listener — cannot confirm the "
                             f"page-2 aim for '{param}', refusing")
                return None, None, "not_in_bank"
            bus_addr = "/1/busOutput" if row == "3" else "/1/busInput"
            self.osc_client.send_message(bus_addr, 1.0)
            self.osc_client.send_message("/setBankStart", float(offset))
            # compute, CONFIRM, then act — every wrong-channel write this
            # project has produced would have been caught by this check
            if not self._confirm_page2_aim(channel_name, row, offset=offset):
                # restore what the failed aim changed: the scrolled bank
                # and the row selection both persist on the device
                self.osc_client.send_message("/setBankStart", 0.0)
                self.osc_client.send_message("/1/busInput", 1.0)
                return None, None, "not_in_bank"
            addr = self.CHANNEL_DETAIL_PARAMS[param]
            logger.info(f"   → resolved '{channel_name}' {param} → hw offset "
                        f"{offset} (physical table, {row_key}) → aimed "
                        f"page 2 ({addr})")
            return offset, addr, "resolved"
        if channel_scoped:
            # Mute is GLOBAL-per-channel (hardware-verified, #4/#10) and
            # channel-detail params (EQ etc.) address the CHANNEL, not a
            # submix — no /setSubmix is sent for either. Row still matters.
            index = None
        else:
            # /setSubmix takes the output's FIXED hardware start channel,
            # 0-based mono (RME-documented, sweep-proven). Membership in the
            # MEASURED table is the crash guard: every key is < the measured
            # hardware end, so a resolved start can never be the fatal
            # out-of-range send. The per-switch labelSubmix confirmation
            # below is the staleness defense — no live-layout equality gate.
            table = self._physical_table()
            index = (pt.resolve_start(table, "outputs", submix_name)
                     if table else None)
            if index is None:
                logger.warning(f"   → target submix '{submix_name}' not in "
                               f"the physical outputs table — run "
                               f"POST /api/device/sweep")
                return None, None, "not_in_bank"

        # Normalize the bank so strip indices are absolute — a bank left
        # scrolled (e.g. by TouchOSC) would shift every /1/volume{N}.
        # /setBankStart is 0-BASED (hardware-verified on a UFX II: 1.0
        # starts the bank at the SECOND channel and the shift persists as
        # global device state, silently mis-targeting hardcoded macros).
        self.osc_client.send_message("/setBankStart", 0.0)
        # Page-1 feedback AND writes follow the selected ROW — select it
        # explicitly so a stray bus selection can't mis-route the write
        bus_addr = {"1": "/1/busInput", "2": "/1/busPlayback",
                    "3": "/1/busOutput"}.get(row, "/1/busInput")
        self.osc_client.send_message(bus_addr, 1.0)
        _t_switch = time.time()
        if index is not None:
            self.osc_client.send_message("/setSubmix", float(index))
            logger.info(f"   → target: /setSubmix {index} ('{submix_name}') row {row}")
        else:
            logger.info(f"   → target: {param} '{channel_name}' row {row} (no submix switch)")

        listener = self.osc_listener
        if listener is None or not listener.running:
            logger.warning("   → no OSC listener — cannot live-resolve strip, using stored address")
            return index, None, "no_feedback"

        # Event-driven waits: the listener wakes us the instant the matching
        # OSC message arrives — no sleeps, no polling. The deadline is only
        # an error bound (UDP is lossy; the channel may not exist), never a
        # pacing mechanism.
        deadline = time.time() + timeout
        wanted = submix_name.lower()
        wanted_ch = channel_name.lower()

        def _submix_label_ok(label: str) -> bool:
            """Accept the confirmed submix label when it IS the wanted name,
            default-pair-covers it, or is a known alias of the same hw
            output (#24: 'ADAT 2' targeted while the label shows 'RE-150 In'
            covering 14-15)."""
            label = str(label or "").strip()
            if not label:
                return False
            if label.lower() == wanted or self._names_cover(label, submix_name):
                return True
            tbl = self._physical_table()
            return bool(tbl is not None and index is not None
                        and pt.covers(tbl, "outputs", index, submix_name, label))

        # Freshness watermark: DeviceState accumulates banks across layout
        # changes, and a STALE bank winning name resolution writes another
        # channel's strip index (review finding — mute had no page-2
        # confirmation to catch it). #24 TASK-6 hardware finding: the epoch
        # alone is NOT enough — a pre-switch dump can land AFTER the switch
        # command with fresh stamps (the wrong-fader race, step 5). Strips
        # must postdate THIS resolution's own sends: anything older may
        # describe the previous snapshot's numbering.
        _fresh_floor = max(self._layout_epoch, _t_switch)

        def _match_in(strips):
            # Exact name first, stereo-pair cover second — a pair strip's
            # fader controls both halves, so it is a correct target
            fresh = {s: d for s, d in strips.items()
                     if d.get("_seen", 0) >= _fresh_floor}
            for strip, data in sorted(fresh.items()):
                if str(data.get("name", "")).strip().lower() == wanted_ch:
                    return strip
            for strip, data in sorted(fresh.items()):
                if self._names_cover(str(data.get("name", "")), channel_name):
                    return strip
            # Learned-alias third priority (#24): the wanted name and the
            # shown name are aliases of the SAME hw channel — handles
            # custom-renamed pairs the a/b grammar cannot parse ('Mic 10'
            # while the strip shows 'Pill Out'). Co-occurrence required,
            # so unrelated strips can never cross-match.
            tbl = self._physical_table()
            in_key = {"1": "inputs", "3": "outputs"}.get(row)
            if tbl is not None and in_key:
                start = pt.resolve_start(tbl, in_key, channel_name)
                if start is not None:
                    for strip, data in sorted(fresh.items()):
                        shown = str(data.get("name", "")).strip()
                        if shown and pt.covers(tbl, in_key, start,
                                               channel_name, shown):
                            return strip
            return None

        def find_strip(state):
            # No logging in here — this runs as a wait predicate on every
            # incoming message, so it would log once per evaluation
            if channel_scoped:
                # Channel-scoped param: any visible bank yields the same strip
                names = ([state.current_submix] if state.current_submix else [])
                names += [s for s in list(state.submixes.keys()) if s not in names]
                for sub in names:
                    strip = _match_in(state.submix_snapshot(sub).get(row, {}))
                    if strip is not None:
                        return strip
                return None
            if not _submix_label_ok(state.current_submix):
                return None
            return _match_in(state.submix_snapshot(state.current_submix).get(row, {}))

        if not channel_scoped and not listener.wait_for(
                lambda st: _submix_label_ok(st.current_submix), timeout):
            # Distinguish SILENCE (no label followed the switch — feedback
            # loss, stored-address fallback stays legitimate) from a
            # CONFIRMED DIFFERENT label (the device answered and it is not
            # our submix under any known alias — writing anywhere now would
            # land on the wrong bus, refuse)
            lbl = listener.state.raw_entry("/1/labelSubmix") or {}
            if lbl.get("last_seen", 0) >= _t_switch and not _submix_label_ok(
                    (lbl.get("args") or [""])[0]):
                logger.error(f"   → device confirmed submix "
                             f"'{(lbl.get('args') or [''])[0]}', wanted "
                             f"'{submix_name}' — REFUSING (wrong bus)")
                return None, None, "not_in_bank"
            logger.warning(f"   → no labelSubmix confirmation for '{submix_name}' "
                           f"within {timeout}s — using stored address")
            return index, None, "no_feedback"

        # If nothing already matches post-floor, provoke a dump with the
        # probe's row-toggle trick (guaranteed change). This runs for BOTH
        # paths now: a /setSubmix to the already-selected submix is a total
        # no-op (zero messages, hardware fact), so a fresh-strips floor
        # would otherwise starve — and after a snapshot switch the only
        # candidates may be stale-content dumps (the TASK-6 race).
        if find_strip(listener.state) is None:
            other = "/1/busPlayback" if bus_addr == "/1/busInput" else "/1/busInput"
            logger.info(f"   → no fresh post-switch match — provoking a dump "
                        f"({other} → {bus_addr})")
            self.osc_client.send_message(other, 1.0)
            self.osc_client.send_message(bus_addr, 1.0)

        # TASK-7 hardware finding: a candidate match in a bank that is STILL
        # STREAMING can be the outgoing snapshot's content — the first fire
        # after a switch exact-matched 'Mic 10' at its OLD strip while the
        # true dump was in flight. Never match mid-burst: once a candidate
        # appears, wait for message-flow quiescence (no new messages for
        # SETTLE_S), then re-match on the SETTLED bank. Fire-2 on hardware
        # proved settled banks match correctly; this makes every fire a
        # fire-2. Costs ~SETTLE_S per page-1 resolution.
        SETTLE_S = 0.15
        strip = None
        while time.time() < deadline:
            if not listener.wait_for(lambda st: find_strip(st) is not None,
                                     max(0.0, deadline - time.time())):
                break
            while time.time() < deadline:
                _before = listener.state.message_count
                if not listener.wait_for(
                        lambda st: st.message_count > _before, SETTLE_S):
                    break  # quiescent — the burst has ended
            strip = find_strip(listener.state)
            if strip is not None:
                break  # settled AND matching — trustworthy
        if strip is not None:
            strip_name = str(listener.state.submix_snapshot(
                listener.state.current_submix).get(row, {})
                .get(strip, {}).get("name", ""))
            if strip_name.strip().lower() != wanted_ch:
                if self._names_cover(strip_name, channel_name):
                    logger.info(f"   → pair-matched '{channel_name}' to strip "
                                f"'{strip_name}' (stereo link changed)")
                else:
                    # Distinct tag (TASK-6 reporting gap): the learned-
                    # alias branch must be tellable from the log alone
                    logger.info(f"   → ALIAS-resolved '{channel_name}' via "
                                f"covering channel '{strip_name}' (same hw "
                                f"channel, physical table)")
            # Write address is always page 1 — the bus selection above
            # decides which row the write lands on
            addr = self._param_address(param, strip)
            logger.info(f"   → live-resolved '{channel_name}' {param} → strip {strip} "
                        f"({addr}, row {row})")
            return index, addr, "resolved"

        strips = listener.state.submix_snapshot(listener.state.current_submix).get(row, {})
        # Filter the placeholder strips past the hardware channel count —
        # a 48-wide bank would otherwise bury the real names in 30x 'n.a.'
        # — and judge 'bank seen' by FRESH entries only (stale banks must
        # not turn a refusal into a stored-address fallback)
        real = [d.get('name') for d in strips.values()
                if str(d.get('name', '')).strip().lower() not in ('n.a.', 'n/a')
                and d.get('_seen', 0) >= _fresh_floor]
        logger.error(f"   → channel '{channel_name}' is NOT in the live bank for "
                     f"'{submix_name}' (live strips: {real}) — refusing the "
                     f"stored address, it may point at a different channel now")
        return index, None, "not_in_bank"

    def _live_input_names(self, timeout: float = 1.5):
        """Fresh input-row names via a provoked dump (row toggle), settled
        to quiescence. TASK-8 hardware findings baked in: dumps stream
        VALUES FIRST and TRACKNAMES LAST (~200ms apart), so freshness is
        judged by post-provoke arrival AND a settle window, never by raw
        strip timestamps alone; and the picker must never serve the
        outgoing snapshot's row as 'live'. Ordered list of real names.
        Cached ~2s. None = cannot tell."""
        cached = getattr(self, "_inputs_cache", None)
        if cached and time.time() - cached[0] < 2.0:
            return cached[1]
        listener = self.osc_listener
        if listener is None or not listener.running or self.osc_client is None:
            return None
        t0 = time.time()
        before = listener.state.message_count
        with self._device_lock:
            # exactly one of the two is a guaranteed state change
            self.osc_client.send_message("/1/busPlayback", 1.0)
            self.osc_client.send_message("/1/busInput", 1.0)
        if not listener.wait_for(lambda s: s.message_count > before, timeout):
            return None
        deadline = t0 + timeout
        while time.time() < deadline:      # settle: outlast the whole dump
            b = listener.state.message_count
            if not listener.wait_for(lambda s: s.message_count > b, 0.15):
                break
        st = listener.state
        strips = (st.submix_snapshot(st.current_submix).get("1", {})
                  if st.current_submix else {})
        names = []
        for _, d in sorted(strips.items()):
            if d.get("_seen", 0) < t0:
                continue                    # pre-provoke content: not live
            n = str(d.get("name", "")).strip()
            if n and n.lower() not in ("n.a.", "n/a") and n not in names:
                names.append(n)
        if names:
            self._inputs_cache = (time.time(), names)
            return names
        return None

    def _live_output_names(self, timeout: float = 1.5):
        """Names of the live output strips (row 3), from a fresh busOutput
        dump. Each output strip IS one submix, so this enumerates the
        submixes that exist RIGHT NOW without sending /setSubmix — which
        matters because an out-of-range /setSubmix CRASHES TotalMix
        (hardware root cause, controlled test 2026-07-31). Cached ~2s so
        one macro run pays for the row toggle once. None = cannot tell."""
        cached = getattr(self, "_outputs_cache", None)
        if cached and time.time() - cached[0] < 2.0:
            return cached[1]
        listener = self.osc_listener
        if listener is None or not listener.running or self.osc_client is None:
            return None

        def _names(st, floor):
            # Only entries refreshed by THIS dump count: _outputs keeps
            # ghost strips from wider/older layouts forever, and a ghost
            # that matches the map would defeat the /setSubmix crash
            # guard after a layout shrink (review finding)
            strips = st.submix_snapshot("_outputs").get("3", {})
            names = {str(d.get("name", "")).strip() for d in strips.values()
                     if d.get("_seen", 0) >= floor}
            return {n for n in names
                    if n and n.lower() not in ("n.a.", "n/a")}

        with self._device_lock:
            t0 = time.time()
            before = listener.state.message_count
            self.osc_client.send_message("/1/busOutput", 1.0)
            if listener.wait_for(lambda st: st.message_count > before, timeout):
                # A row dump has no end-of-burst marker — short bounded
                # settle so stale names get overwritten first
                time.sleep(0.15)
            self.osc_client.send_message("/1/busInput", 1.0)
        names = _names(listener.state, t0) or None
        if names:
            self._outputs_cache = (time.time(), names)
        return names

    def _persist_channel_map_file(self, cm):
        """Atomic write of the channel map (temp + replace — a crash mid-
        write must not corrupt the only copy of the layout library)."""
        target = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "ufx2_channel_map.json")
        tmp = target + ".tmp"
        with open(tmp, "w") as f:
            json.dump(cm, f, indent=2)
        os.replace(tmp, target)

    def _read_button_state(self, addr: str, row=None, timeout: float = 1.0):
        """Fresh state of a momentary button. The refresh is picked BY
        PAGE: /3/ addresses use the page-3 no-op; /2/ addresses use the
        page-2 row mirror MATCHING THE COMMANDED ROW — which must be
        threaded in by the caller, never derived from listener belief
        (the mirror is a WRITE: only the matching one is a no-op, a stale
        row belief would silently switch rows — same hazard as
        _confirm_page2_aim). For /2/ addresses the bank must already be
        aimed at the target channel (the step path aims before writing;
        the mirror does not move the bank). None = unknowable — pressing
        blind toggles RANDOMLY, so callers must refuse on None."""
        listener = self.osc_listener
        if listener is None or not listener.running or self.osc_client is None:
            return None
        if addr.startswith("/2/"):
            if row is None:
                logger.error(f"   → cannot read {addr}: page-2 button reads "
                             f"need the commanded row threaded in — refusing")
                return None
            nudge = ({"1": "/2/busInput", "2": "/2/busPlayback",
                      "3": "/2/busOutput"}.get(str(row), "/2/busInput"), 1.0)
        else:
            nudge = ("/3/faderGroups/1/1", 0.0)
        with self._device_lock:
            before = (listener.state.raw_entry(addr) or {}).get("count", 0)
            self.osc_client.send_message(nudge[0], nudge[1])
            fresh = listener.wait_for(
                lambda st: (st.raw_entry(addr) or {}).get("count", 0) > before,
                timeout)
        if not fresh:
            return None
        args = (listener.state.raw_entry(addr) or {}).get("args") or []
        try:
            return float(args[0]) >= 0.5
        except (TypeError, ValueError, IndexError):
            return None

    def _set_button_state(self, addr: str, desired: bool, row=None) -> bool:
        """Set a momentary toggle button to a target state: read fresh,
        press (1.0) only if it differs. Returns False when the state is
        unknowable — a blind press is a coin flip, refuse instead."""
        current = self._read_button_state(addr, row)
        if current is None:
            logger.error(f"   → cannot read {addr} state (no page-3 dump) — "
                         f"REFUSING the button press (a blind press toggles "
                         f"randomly)")
            return False
        if current != desired:
            with self._device_lock:
                self.osc_client.send_message(addr, 1.0)
            logger.info(f"   → {addr}: pressed (now {'On' if desired else 'Off'})")
        else:
            logger.info(f"   → {addr}: already {'On' if desired else 'Off'} — no press")
        return True

    def _confirm_page2_aim(self, channel_name: str, row: str,
                           timeout: float = 0.8, offset=None) -> bool:
        """Confirm the page-2 window shows the INTENDED channel before a
        write (#20: /2/trackname names the aimed channel, and the row-
        mirror no-op reliably triggers a fresh 90-message page-2 dump —
        both hardware-verified, idempotent across repeats).

        `row` MUST be the row the caller just commanded — the mirror is a
        WRITE (/2/busX sets the row exactly like /1/busX; only the
        matching one is a no-op), so deriving it from listener belief
        would silently SWITCH rows whenever that belief was stale
        (hardware-observed hazard, same class as the stale-map aim).

        Returns True only on a confirmed match. A mismatch refuses, and
        SILENCE refuses too: the dump primitive is verified reliable, so
        no confirmation means something is genuinely wrong."""
        shown = self._read_page2_trackname(row, timeout)
        if shown is None:
            logger.error(f"   → no page-2 dump followed the row-mirror nudge "
                         f"within {timeout}s — the confirmation primitive is "
                         f"verified reliable, so REFUSING the write")
            return False
        row_key = {"1": "inputs", "2": "playbacks", "3": "outputs"}.get(str(row))
        if shown == channel_name.strip() or self._names_cover(shown, channel_name):
            logger.info(f"   → page-2 aim CONFIRMED by /2/trackname ('{shown}')")
            self._record_table_observation(row_key, offset, shown)
            return True
        # Alias-default rule (#24): the targeted name and the shown name are
        # known aliases of the SAME hardware channel ("Mic 10" targeted while
        # the device shows "Pill Out" covering 8-9) — measured co-occurrence,
        # so this cannot cross-match unrelated strips
        table = self._physical_table()
        if (table is not None and offset is not None and row_key
                and pt.covers(table, row_key, offset, channel_name, shown)):
            logger.info(f"   → page-2 aim CONFIRMED via alias: '{channel_name}' "
                        f"is covered by '{shown}' at hw {offset}")
            return True
        logger.error(f"   → page-2 window shows '{shown}', intended "
                     f"'{channel_name}' — aim landed WRONG, refusing the write")
        return False

    def _read_page2_trackname(self, row: str, timeout: float = 0.8):
        """Read which channel the page-2 window currently shows, by nudging
        the COMMANDED row's /2/ mirror (a verified-idempotent no-op that
        triggers a fresh page-2 dump) and reading /2/trackname from it.
        None = no dump followed (refuse-worthy: the primitive is reliable)."""
        listener = self.osc_listener
        if listener is None or not listener.running or self.osc_client is None:
            return None
        st = listener.state
        entry = st.raw_entry("/2/trackname")
        before = entry["count"] if entry else 0
        row_addr = {"1": "/2/busInput", "2": "/2/busPlayback",
                    "3": "/2/busOutput"}.get(str(row), "/2/busInput")
        self.osc_client.send_message(row_addr, 1.0)
        fresh = listener.wait_for(
            lambda s: (s.raw_entry("/2/trackname") or {}).get("count", 0) > before,
            timeout)
        if not fresh:
            return None
        entry = st.raw_entry("/2/trackname") or {}
        args = entry.get("args") or []
        return str(args[0]).strip() if args else ""

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

    def probe_device(self, timeout: float = 2.5):
        """Liveness probe: send a state-CHANGING command and confirm a
        feedback dump follows.

        Silence is NOT evidence of a freeze — TotalMix emits feedback only on
        change, so an idle mixer is indistinguishable from a dead one by
        listening alone. This is the only sound aliveness check.

        The probe toggles the fader ROW (/1/busPlayback then /1/busInput):
        whatever row is currently selected, exactly one of the two is a
        guaranteed state change, so a dump must follow. The submix is never
        touched (a cold listener has no prior submix to restore — the old
        /setSubmix probe left the device moved after a restart), and the
        probe ends on the input row, the bridge's canonical state. Bonus: a
        cold-booted listener comes out primed with the current bank.
        """
        listener = self.osc_listener
        if listener is None or not listener.running or self.osc_client is None:
            return {"alive": None, "reason": "no OSC listener or client"}

        state = listener.state
        before = state.message_count
        t0 = time.time()
        with self._device_lock:
            self.osc_client.send_message("/1/busPlayback", 1.0)
            self.osc_client.send_message("/1/busInput", 1.0)
        alive = listener.wait_for(lambda st: st.message_count > before, timeout)
        elapsed = time.time() - t0

        result = {"alive": bool(alive), "elapsed_s": round(elapsed, 3),
                  "method": "bus row toggle (submix untouched, ends on input row)",
                  "at": time.time()}
        if alive:
            logger.info(f"Device probe OK — feedback in {elapsed:.2f}s")
        else:
            # Auto-capture evidence while the failure is fresh
            result["evidence"] = {
                "last_message_at": state.last_message_at,
                "message_count": state.message_count,
                "current_submix": state.current_submix,
                "bank_width": state.bank_width,
                "real_strip_count": state.real_strip_count,
            }
            logger.error(f"DEVICE PROBE FAILED — TotalMix is not responding to OSC. "
                         f"Check Options → Settings → OSC → 'In Use' on the device "
                         f"(ticked = OSC thread wedged; unticked = remote dropped). "
                         f"Evidence: {result['evidence']}")
        self.last_probe = result
        self.broadcast_state(macro_event={"type": "device_probe",
                                          "alive": result["alive"]})
        return result

    def start_osc_listener(self):
        """Start the structured OSC feedback listener (device state + discovery)."""
        if not ENABLE_OSC_LISTENER:
            logger.info("OSC listener disabled (ENABLE_OSC_LISTENER=false)")
            return
        from osc_listener import OSCListener
        listener = OSCListener(
            OSC_LISTEN_PORT,
            broadcast_cb=lambda: self.broadcast_state(
                macro_event={"type": "device_update"}
            ),
        )
        if listener.start():
            self.osc_listener = listener

            def _startup_summary():
                # #24: no freshness checking — the physical table is layout-
                # invariant. Just log what the bridge knows on startup.
                time.sleep(5.0)
                try:
                    table = self._physical_table()
                    logger.info(f"physical_table on startup: "
                                f"{pt.summarize(table) if table else 'ABSENT — run POST /api/device/sweep'}")
                except Exception as e:
                    logger.debug(f"startup summary failed: {e}")
            threading.Thread(target=_startup_summary, daemon=True).start()

    # ─────────────────────────────────────────────────────────────
    # GLOBAL OSC (#25): second remote, absolute addressing
    # ─────────────────────────────────────────────────────────────
    def _global_active(self):
        """True when macro writes route through the Global transport.
        Workspace switching stays classic regardless (no Global equivalent)."""
        return OSC_TRANSPORT == "global" and self.global_transport is not None

    def start_global_osc(self):
        """Start the Global OSC listener (+ transport). In shadow mode
        (ENABLE_GLOBAL_OSC_LISTENER=true, OSC_TRANSPORT=classic) the
        listener observes and learns names while classic keeps writing."""
        if not ENABLE_GLOBAL_OSC_LISTENER:
            return
        from global_listener import GlobalOSCListener
        from global_transport import GlobalTransport
        listener = GlobalOSCListener(GLOBAL_OSC_LISTEN_PORT)
        if not listener.start():
            logger.error("Global OSC listener failed to start — "
                         "global transport unavailable")
            return
        self.global_listener = listener
        client = get_client(GLOBAL_OSC_IP, GLOBAL_OSC_PORT)
        self.global_transport = GlobalTransport(
            client, listener, self._physical_table,
            persist_cb=self._persist_after_global_names,
            heartbeat_timeout_s=GLOBAL_HEARTBEAT_TIMEOUT_S)
        self.global_transport.start()
        mode = ("TRANSPORT ACTIVE" if OSC_TRANSPORT == "global"
                else "shadow mode (observing only)")
        logger.info(f"Global OSC {mode} → {GLOBAL_OSC_IP}:{GLOBAL_OSC_PORT} "
                    f"(listening on {listener.port})")

    def stop_global_osc(self):
        if self.global_transport:
            self.global_transport.stop()
            self.global_transport = None
        if self.global_listener:
            self.global_listener.stop()
            self.global_listener = None

    def _persist_after_global_names(self):
        if self.channel_map and not self.channel_map_is_example:
            self._persist_channel_map_file(self.channel_map)

    def _run_step_global(self, step, macro_name, value, cancel_event,
                         clock_bpm):
        """One name-targeted step over the Global transport. Handles its
        own refusal events; raw-OSC steps never reach here (they stay on
        the classic client — their addresses are classic-namespace)."""
        writer, label, status = self.global_transport.resolve_step(
            step["target"])
        if status != "resolved":
            logger.error(f"   → step skipped (global): {label!r} "
                         f"unresolved ({status})")
            self.broadcast_state(macro_event={
                "type": "macro_skipped",
                "name": macro_name,
                "reason": f"target_{status}",
            })
            return
        if "operation" in step and step.get("value") == "{{param}}":
            op_config = step["operation"]
            if op_config.get("bpm") == "clock":
                resolved_bpm = clock_bpm if clock_bpm else 140
                op_config = {**op_config, "bpm": resolved_bpm}
                logger.info(f"   → BPM clock sync: using {resolved_bpm} BPM")
            # Global switches are absolute sets (no edge-toggle shim
            # needed): the writer's to_wire threshold turns the 0..1
            # stream into clean 0/1 writes.
            OperationRegistry.execute(
                op_config["type"], writer, writer.address, value,
                op_config, cancel_event=cancel_event)
        else:
            step_val = value if step.get("value") == "{{param}}" else step.get("value")
            try:
                writer.send_message(writer.address, float(step_val))
                logger.info(f"   → {writer.address} = {step_val} (global)")
            except Exception as e:
                logger.error(f"Global OSC send failed: {e}")

    def start_mqtt(self):
        """Connect MQTT and start the client loop (web and standalone modes)."""
        logger.info("=== TOTALMIX OSC BRIDGE STARTING MQTT (web or standalone mode) ===")
        logger.info(f"OSC target → {OSC_IP}:{OSC_PORT}")
        logger.info("MQTT macro namespace → totalmix/macro/<name>")

        client = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
        setup_mqtt(client, MQTT_BROKER, MQTT_PORT, MQTT_USER, MQTT_PASS, OSC_IP, OSC_PORT, self)
        self.mqtt_client = client

        if ENABLE_OSC_MONITOR:
            osc_monitor.start()

        client.loop_start()
        logger.info("MQTT client loop started — macro subscriptions ACTIVE")


bridge = TotalMixOSCBridge(osc_client, MAPPINGS, SNAPSHOT_MAP)

logger.info("=== TOTALMIX OSC BRIDGE LOADED ===")
logger.info("State-aware workspace/snapshot switching (NO force) + OperationRegistry + WebSocket live updates for Web Client v1")

# === BRIDGE STARTUP — CENTRALIZED MODE (for python bridge.py) ===
if __name__ == "__main__":
    bridge.start_mqtt()   # re-uses the same function
    bridge.start_osc_listener()
    bridge.start_global_osc()   # #25: no-op unless enabled via env

    try:
        while True:
            time.sleep(30)
    except KeyboardInterrupt:
        logger.info("\nShutting down bridge...")
        if ENABLE_OSC_MONITOR:
            osc_monitor.stop()
        if bridge.mqtt_client:
            bridge.mqtt_client.loop_stop()
        logger.info("Bridge stopped cleanly.")