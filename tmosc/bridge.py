import os
import time
import logging
import logging.handlers
import json
import tempfile
import threading
import paho.mqtt.client as mqtt
import re
import asyncio
# Tests monkeypatch OSC_TRANSPORT / ENABLE_MQTT / ENABLE_OSC_MONITOR on THIS
# module; the methods that read them (_global_active, start_*) must therefore
# live here and read the module global at call time.
from tmosc.config import (
    OSC_IP, OSC_PORT, ENABLE_MQTT, MQTT_BROKER, MQTT_PORT, MQTT_USER, MQTT_PASS,
    ENABLE_OSC_MONITOR, ENABLE_OSC_LISTENER, OSC_LISTEN_PORT,
    OSC_TRANSPORT, GLOBAL_OSC_IP, GLOBAL_OSC_PORT, GLOBAL_OSC_LISTEN_PORT,
    ENABLE_GLOBAL_OSC_LISTENER, GLOBAL_HEARTBEAT_TIMEOUT_S,
    BRIDGE_LOG_FILE, LOG_MAX_BYTES, LOG_BACKUP_COUNT,
    snapshot_num_to_osc_index,
)
import tmosc.app_paths as app_paths
from tmosc.osc import get_client
from tmosc.mqtt_handler import setup_mqtt
from tmosc.osc_monitor import osc_monitor
from tmosc.operations import OperationRegistry, shape_value, unshape_value
import tmosc.physical_table as pt
import tmosc.global_units as gu
from tmosc.core.broadcast import BroadcastMixin
from tmosc.core.switching import SwitchingMixin
from tmosc.core.knobs import KnobsMixin
from tmosc.core.channel_map import ChannelMapMixin
from tmosc.core.transport import ClassicTransportMixin

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
    "/app/config/ufx2_snapshot_map.json",           # Docker: SMB mount (authoritative)
    app_paths.data_path("ufx2_snapshot_map.json"),  # local copy (repo root; %APPDATA% when frozen)
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
_MAPPINGS_PATHS = [app_paths.data_path("mappings.json"),
                   app_paths.example_path("mappings.example.json")]
MAPPINGS = {"macros": {}}
MAPPINGS_SOURCE = None
MAPPINGS_IS_EXAMPLE = False

for _mp in _MAPPINGS_PATHS:
    try:
        with open(_mp, "r", encoding="utf-8") as f:
            MAPPINGS = json.load(f)
        MAPPINGS_SOURCE = os.path.basename(_mp)
        MAPPINGS_IS_EXAMPLE = MAPPINGS_SOURCE != "mappings.json"
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


# === WEBSOCKET CLIENTS (shared between bridge.py and tmosc/api/app.py) ===
ws_clients = []  # list of active FastAPI WebSocket connections
# Per-client send queues (#32): a broadcast only ENQUEUES; one sender task
# per connection drains its queue in order. A client whose transport is
# paused (phone on flaky Wi-Fi) therefore stalls only itself, and every
# client sees knob_update frames in the order they were produced - the old
# one-task-per-broadcast fan-out could suspend on a slow client's drain()
# while later broadcasts completed, reordering frames for everyone.
_ws_queues = {}       # WebSocket -> asyncio.Queue
WS_QUEUE_MAX = 64     # frames a stalled client may fall behind before it loses the oldest


def ws_attach(websocket):
    """Register a connection and start its sender task. Call on the event
    loop (the /ws endpoint). Returns the task so the endpoint can cancel it."""
    q = asyncio.Queue(maxsize=WS_QUEUE_MAX)
    _ws_queues[websocket] = q
    ws_clients.append(websocket)

    async def _sender():
        try:
            while True:
                await websocket.send_json(await q.get())
        except asyncio.CancelledError:
            raise
        except Exception:
            ws_detach(websocket)

    return asyncio.get_running_loop().create_task(_sender())


def ws_detach(websocket):
    _ws_queues.pop(websocket, None)
    if websocket in ws_clients:
        ws_clients.remove(websocket)


def _ws_enqueue(q, payload):
    if q.full():
        try:
            q.get_nowait()          # drop the OLDEST frame, never block the loop
        except asyncio.QueueEmpty:
            pass
    q.put_nowait(payload)


def ws_enqueue_all(payload):
    """Fan one payload out to every client queue (loop thread only)."""
    for q in list(_ws_queues.values()):
        _ws_enqueue(q, payload)

class TotalMixOSCBridge(BroadcastMixin, SwitchingMixin, KnobsMixin, ChannelMapMixin, ClassicTransportMixin):
    """Facade: one object, one namespace. The mixins in tmosc/core/ hold the
    behaviour by responsibility; this class owns __init__ (ALL instance
    state), process lifecycle (start_*/stop_*), and everything that reads
    env config (tests monkeypatch those constants on THIS module)."""

    # The broadcast mixin fans frames out through the WS registry that lives
    # in this module (it is shared with tmosc/api/app.py).
    _ws_enqueue_all = staticmethod(ws_enqueue_all)

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
        # #22: persistent per-macro last-fire outcome (survives page loads,
        # unlike the transient flash/event). name -> {status: ok|partial|
        # skipped, reason, skipped_steps, at}
        self.macro_health = {}
        # KNOB macros (continuous MIDI control): last value per knob, for the
        # live card and for 'hold' re-assertion after snapshot switches
        self.knob_values = {}
        self._knob_last_status = {}     # name -> last resolve status (log on change)
        self._knob_last_broadcast = {}  # name -> ts (UI updates throttled ~10Hz)
        self._knob_trailing_timers = {}  # name -> Timer (trailing-edge flush)
        self._knob_readback_timers = {} # name -> Timer (settle readback)
        self._knob_last_pushed = {}     # name -> snapshot tuple (device-sync differ)
        self._mqtt_knob_published = {}  # name -> last value sent to totalmix/knob/<name>/state (#28)
        self._knob_watch_failed = set()  # knobs whose device sync raised (warned once)
        self._knob_watch_stop = threading.Event()
        self._persist_lock = threading.Lock()   # channel-map file writes (3 threads can race)
        self._knob_enable_sent = {}     # name -> ts of last auto-enable write
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
        self.duck = None                    # DuckSupervisor — sidechain engine
        self.state_confirmed = None         # last commanded switch confirmed by device feedback?
        # #30: what the DEVICE says about snapshots (Global /snapshot/load/N feed):
        # the active slot and whether it is modified. Display + MQTT only - never
        # a reason to skip a commanded switch (the workspace is unobservable).
        self.device_snapshot_slot = None
        self.snapshot_modified = None
        self.last_probe = None              # result of the last device liveness probe
        self._midi_owner = None             # {"id","host","last_seen"} — external agent (tray) holding the MIDI port
        self._midi_owner_lock = threading.Lock()  # coexistence: browser yields Web MIDI while an agent owns it
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
    

    def _record_fire(self, name, status, reason=None, skipped=None):
        """#22 card health: remember how the last fire of a macro went."""
        self.macro_health[name] = {
            "status": status,               # "ok" | "partial" | "skipped"
            "reason": reason,               # whole-macro skip reason
            "skipped_steps": skipped or [], # per-step skip reasons (partial)
            "at": time.time(),
        }

    def run_macro(self, macro_name: str, param: float = 0.5, clock_bpm: float = None):
        if macro_name not in self.mappings.get("macros", {}):
            logger.error(f"Macro '{macro_name}' not found")
            return

        if self.osc_client is None:
            logger.warning(f"Macro '{macro_name}' skipped — OSC not configured (set OSC_IP)")
            self._record_fire(macro_name, "skipped", "osc_not_configured")
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
                self._record_fire(macro_name, "skipped", "ws_ss_blocked")
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

                # #25 NOTE: snapshot recall deliberately stays CLASSIC even
                # under the global transport. TASK 11 (2026-08-21) measured
                # Global /snapshot/load feedback as unreliable — load/4
                # never confirmed (2s penalty each) and the snapshots dict
                # went stale vs reality — while classic button-echo confirm
                # is 0.02–0.08s and consistent. The classic remote stays
                # configured regardless (workspace switching has no Global
                # equivalent), so this costs nothing.
                if snap_name and snap_num is not None:
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
            if (force_switch or not already_on_target) and (ws_slot is not None or snap_num is not None):
                self.reapply_held_knobs()   # snapshot-agnostic knobs

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
            skip_reasons = []   # #22 health: per-step skips → "partial"
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
                if (step.get("operation") or {}).get("type") == "knob":
                    # a KNOB step fired as a macro (FIRE button, MQTT, PC
                    # trigger) is just a set to the param value
                    r = self.knob_set(macro_name, value, source="fire")
                    if r["status"] != "resolved":
                        skip_reasons.append(r["status"])
                        self.broadcast_state(macro_event={
                            "type": "macro_skipped", "name": macro_name,
                            "reason": r["status"]})
                    continue
                if "target" in step and self._global_active():
                    _fail = self._run_step_global(step, macro_name, value,
                                                  cancel_event, clock_bpm)
                    if _fail:
                        skip_reasons.append(_fail)
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
                        skip_reasons.append("setsubmix_unverifiable")
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
                        skip_reasons.append(f"target_{status}")
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
                            skip_reasons.append("button_state_unknown")
                            continue
                        op_client = _EdgeToggleClient(self.osc_client,
                                                      osc_addr, initial)
                    try:
                        OperationRegistry.execute(
                            op_config["type"],
                            op_client,
                            osc_addr,
                            value,
                            op_config,
                            cancel_event=cancel_event,
                        )
                    except Exception:
                        # an operation bug must not kill the trigger thread
                        # before the fire is recorded (#22 health)
                        logger.exception(f"   → operation {op_config.get('type')!r} "
                                         f"failed on {osc_addr}")
                        skip_reasons.append("operation_error")
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
            self._record_fire(macro_name,
                              "partial" if skip_reasons else "ok",
                              skipped=skip_reasons)
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
                "last_fire": self.macro_health.get(macro_name),
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


    def start_osc_listener(self):
        """Start the structured OSC feedback listener (device state + discovery)."""
        if not ENABLE_OSC_LISTENER:
            logger.info("OSC listener disabled (ENABLE_OSC_LISTENER=false)")
            return
        from tmosc.osc_listener import OSCListener
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
        from tmosc.global_listener import GlobalOSCListener
        from tmosc.global_transport import GlobalTransport
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
        self._knob_watch_stop.clear()
        threading.Thread(target=self._knob_watch_loop, daemon=True).start()
        # Sidechain duck engine (#user idea): key-channel meters drive gain
        # reduction on duck-enabled knob targets. Shares the knob-watch stop.
        from tmosc.duck_engine import DuckSupervisor
        self.duck = DuckSupervisor(self)
        self._duck_thread = threading.Thread(target=self.duck.run,
                                             args=(self._knob_watch_stop,),
                                             name="duck-supervisor", daemon=True)
        self._duck_thread.start()
        mode = ("TRANSPORT ACTIVE" if OSC_TRANSPORT == "global"
                else "shadow mode (observing only)")
        logger.info(f"Global OSC {mode} → {GLOBAL_OSC_IP}:{GLOBAL_OSC_PORT} "
                    f"(listening on {listener.port})")

    def stop_global_osc(self):
        self._knob_watch_stop.set()
        # Let the duck supervisor run its restore-on-exit BEFORE the transport
        # is torn down - otherwise a ducked send stays ducked across a restart
        # (its restore write needs the transport; review finding 2026-09-09).
        t = getattr(self, "_duck_thread", None)
        if t is not None and t.is_alive():
            t.join(timeout=1.5)
        if self.global_transport:
            self.global_transport.stop()
            self.global_transport = None
        if self.global_listener:
            self.global_listener.stop()
            self.global_listener = None


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
            return f"target_{status}"   # #22 health: caller records it
        if "operation" in step and step.get("value") == "{{param}}":
            op_config = step["operation"]
            if op_config.get("bpm") == "clock":
                resolved_bpm = clock_bpm if clock_bpm else 140
                op_config = {**op_config, "bpm": resolved_bpm}
                logger.info(f"   → BPM clock sync: using {resolved_bpm} BPM")
            # Global switches are absolute sets (no edge-toggle shim
            # needed): the writer's to_wire threshold turns the 0..1
            # stream into clean 0/1 writes.
            try:
                OperationRegistry.execute(
                    op_config["type"], writer, writer.address, value,
                    op_config, cancel_event=cancel_event)
            except Exception:
                logger.exception(f"   → operation {op_config.get('type')!r} "
                                 f"failed on {writer.address}")
                return "operation_error"   # fire recorded as partial, not lost
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

        client = None
        if ENABLE_MQTT:
            logger.info("MQTT macro namespace → totalmix/macro/<name>")
            client = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
            setup_mqtt(client, MQTT_BROKER, MQTT_PORT, MQTT_USER, MQTT_PASS, OSC_IP, OSC_PORT, self)
            self.mqtt_client = client
        else:
            logger.info("MQTT disabled (ENABLE_MQTT=False) — macros via web UI / MIDI / REST only")

        if ENABLE_OSC_MONITOR:
            osc_monitor.start()

        if client is not None:
            client.loop_start()
            logger.info("MQTT client loop started — macro subscriptions ACTIVE")


bridge = TotalMixOSCBridge(osc_client, MAPPINGS, SNAPSHOT_MAP)

logger.info("=== TOTALMIX OSC BRIDGE LOADED ===")
logger.info("State-aware workspace/snapshot switching (NO force) + OperationRegistry + WebSocket live updates for Web Client v1")

# === BRIDGE STARTUP — HEADLESS MODE (python -m tmosc.bridge: MQTT/OSC only, no web UI) ===
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