import os
import time
import logging
import json
import threading
import paho.mqtt.client as mqtt
import asyncio
# Tests monkeypatch OSC_TRANSPORT / ENABLE_MQTT / ENABLE_OSC_MONITOR on THIS
# module; the methods that read them (_global_active, start_*) must therefore
# live here and read the module global at call time.
from tmosc.config import (
    OSC_IP, OSC_PORT, ENABLE_MQTT, MQTT_BROKER, MQTT_PORT, MQTT_USER, MQTT_PASS,
    ENABLE_OSC_MONITOR, ENABLE_OSC_LISTENER, OSC_LISTEN_PORT,
    OSC_TRANSPORT, GLOBAL_OSC_IP, GLOBAL_OSC_PORT, GLOBAL_OSC_LISTEN_PORT,
    ENABLE_GLOBAL_OSC_LISTENER, GLOBAL_HEARTBEAT_TIMEOUT_S,
)
import tmosc.app_paths as app_paths
from tmosc.osc import get_client
from tmosc.mqtt_handler import setup_mqtt
from tmosc.osc_monitor import osc_monitor
import tmosc.physical_table as pt
from tmosc.core.broadcast import BroadcastMixin
from tmosc.core.switching import SwitchingMixin
from tmosc.core.macros import MacrosMixin
from tmosc.core.knobs import KnobsMixin
from tmosc.core.channel_map import ChannelMapMixin
from tmosc.core.transport import ClassicTransportMixin

logger = logging.getLogger(__name__)

# Config loaders. They used to run at import (module-level SNAPSHOT_MAP /
# MAPPINGS / osc_client); build_bridge() calls them now (#27 phase 4b), so
# importing this module has no side effects beyond defining names.


def load_snapshot_map(paths=None):
    """First readable snapshot map on the search path, {} when none. Prefers
    the SMB-mounted path (same source as mqtt_handler.py), falls back to the
    local file for dev environments without the mount."""
    paths = paths or [
        "/app/config/ufx2_snapshot_map.json",           # Docker: SMB mount (authoritative)
        app_paths.data_path("ufx2_snapshot_map.json"),  # local copy (repo root; %APPDATA% when frozen)
    ]
    for _p in paths:
        try:
            with open(_p, "r", encoding="utf-8-sig") as f:
                snapshot_map = json.load(f)
            logger.info(f"Loaded snapshot map from {_p} — workspaces: {list(snapshot_map.keys())}")
            return snapshot_map
        except FileNotFoundError:
            continue
        except Exception as e:
            logger.error(f"Failed to load snapshot map from {_p}: {e}")
            break
    logger.warning("No snapshot map loaded — WS/SS switching will be disabled until map is available")
    return {}


def load_mappings():
    """mappings.json (user config) or the bundled example as a fallback.
    Returns (mappings, source_basename, is_example)."""
    paths = [app_paths.data_path("mappings.json"),
             app_paths.example_path("mappings.example.json")]
    for _mp in paths:
        try:
            with open(_mp, "r", encoding="utf-8") as f:
                mappings = json.load(f)
        except FileNotFoundError:
            continue
        except Exception as e:
            logger.error(f"Failed to load {_mp}: {e}")
            break
        source = os.path.basename(_mp)
        is_example = source != "mappings.json"
        if is_example:
            logger.warning(
                f"mappings.json not found — loaded fallback {_mp}. "
                "Create mappings.json to override."
            )
        else:
            logger.info(f"Loaded mappings.json — {len(mappings.get('macros', {}))} macros")
        return mappings, source, is_example
    return {"macros": {}}, None, False


def make_osc_client():
    """Classic OSC client (shared per-(ip, port) socket cache in osc.py, the
    same one mqtt_handler's send_osc() uses), or None when OSC_IP is unset."""
    if OSC_IP and OSC_PORT:
        client = get_client(OSC_IP, OSC_PORT)
        logger.info(f"OSC Client ready → {OSC_IP}:{OSC_PORT}")
        return client
    logger.warning("OSC_IP not set — OSC disabled, macros will be skipped")
    return None


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

class TotalMixOSCBridge(BroadcastMixin, SwitchingMixin, MacrosMixin, KnobsMixin, ChannelMapMixin, ClassicTransportMixin):
    """Facade: one object, one namespace. The mixins in tmosc/core/ hold the
    behaviour by responsibility; this class owns __init__ (ALL instance
    state), process lifecycle (start_*/stop_*), and everything that reads
    env config (tests monkeypatch those constants on THIS module)."""

    # The broadcast mixin fans frames out through the WS registry that lives
    # in this module (it is shared with tmosc/api/app.py).
    _ws_enqueue_all = staticmethod(ws_enqueue_all)

    def __init__(self, osc_client, mappings, snapshot_map,
                 mappings_source=None, mappings_is_example=False):
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
        self.mappings_is_example = mappings_is_example  # bundled example, not the user's file
        self.mappings_source = mappings_source          # basename of what was loaded
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
        self.workspace_report = None        # #30: workspace from the TotalMix title, via the agent heartbeat
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


def build_bridge():
    """The bridge factory: load mappings, snapshot map and the classic OSC
    client from the data dir / env and return a fresh, not-yet-started
    TotalMixOSCBridge. create_app() calls this once per app (the object
    lives at app.state.bridge); the headless mode below calls it directly.
    Nothing starts here - start_mqtt/start_osc_listener/start_global_osc
    are the lifespan's job."""
    mappings, source, is_example = load_mappings()
    b = TotalMixOSCBridge(make_osc_client(), mappings, load_snapshot_map(),
                          mappings_source=source, mappings_is_example=is_example)
    logger.info("=== TOTALMIX OSC BRIDGE LOADED ===")
    logger.info("State-aware workspace/snapshot switching (NO force) + OperationRegistry + WebSocket live updates for Web Client v1")
    return b


# === BRIDGE STARTUP — HEADLESS MODE (python -m tmosc.bridge: MQTT/OSC only, no web UI) ===
if __name__ == "__main__":
    from tmosc.logsetup import configure_logging
    configure_logging()
    bridge = build_bridge()
    bridge.start_mqtt()   # re-uses the same function
    bridge.start_osc_listener()
    bridge.start_global_osc()   # #25: no-op unless enabled via env

    try:
        while True:
            time.sleep(30)
    except KeyboardInterrupt:
        logger.info("\nShutting down bridge...")
        bridge.stop_global_osc()
        if ENABLE_OSC_MONITOR:
            osc_monitor.stop()
        if bridge.mqtt_client:
            bridge.mqtt_client.loop_stop()
