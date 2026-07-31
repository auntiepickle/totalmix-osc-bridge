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

# === WEBSOCKET CLIENTS (shared between bridge.py and web_client.py) ===
ws_clients = []  # list of active FastAPI WebSocket connections

class TotalMixOSCBridge:
    def __init__(self, osc_client, mappings, snapshot_map):
        self._suppress_handler = False
        self._last_macro_end_time = 0.0
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
        self.state_confirmed = None         # last commanded switch confirmed by device feedback?
        self.last_probe = None              # result of the last device liveness probe
        self.discovery_state = {"status": "idle"}  # channel-map discovery job state
        self._load_channel_map()

        # === SAFE THREAD-AWARE BROADCAST (MQTT + FastAPI) ===
        self.broadcast_state = self._safe_broadcast_state

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
                return
            except FileNotFoundError:
                continue
            except Exception as e:
                logger.warning(f"Could not load {path}: {e}")
                break
        self.channel_map = {}
        self.channel_map_is_example = False

    def _get_macro_duration_ms(self, macro: dict, clock_bpm: float = None) -> int:
        """Return ramp/LFO duration in ms, or 400 for instant macros (used for WS progress events).

        If the operation config has ``"bpm": "clock"`` and clock_bpm is provided,
        the detected MIDI clock BPM is used for the duration calculation.
        """
        for step in macro.get("steps", []):
            if "operation" in step:
                op = step["operation"]
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
        self.osc_client.send_message("/loadQuickWorkspace", float(ws_slot))
        self.current_workspace = workspace
        logger.info(f"switch_to: workspace '{workspace}' (slot {ws_slot})")

        if snapshot:
            self._wait_device(
                lambda st: st.raw.get("/1/labelSubmix", {}).get("last_seen", 0) >= t0,
                timeout=2.0, fallback_sleep=1.0,
                what=f"workspace '{workspace}' switch")
            snapshots = ws_entry.get("snapshots", {})
            snap_num  = None
            for snap_key, snap_val in snapshots.items():
                if str(snap_val).strip().lower() == snapshot.strip().lower():
                    snap_num = snap_key
                    break
            if snap_num is not None:
                osc_addr = f"/3/snapshots/{snapshot_num_to_osc_index(snap_num)}/1"
                self.osc_client.send_message(osc_addr, 1.0)
                self.current_snapshot = snapshot.strip().lower()
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
                    self._queued_params[macro_name] = param
                    logger.info(f"   → '{macro_name}' running, mode=queue — queued (param={param:.3f})")
                    return
                elif fire_mode == "restart":
                    self._queued_params[macro_name] = param
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

            self._suppress_handler = True
            cancel_event = threading.Event()
            self._cancel_events[macro_name] = cancel_event
            self._running_macros.add(macro_name)

        try:
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
            already_on_target = (
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

                if snap_name and snap_num is not None:
                    osc_addr = f"/3/snapshots/{snapshot_num_to_osc_index(snap_num)}/1"
                    t0 = time.time()
                    self.osc_client.send_message(osc_addr, 1.0)
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
            else:
                logger.info(f"   → Already on target {ws_name}/{snap_name} — skipping ws/ss switch (force_switch=False)")

            # === EMIT macro_start SO BROWSER CAN SYNC PROGRESS BAR ===
            duration_ms = self._get_macro_duration_ms(macro, clock_bpm=clock_bpm)
            self.broadcast_state(macro_event={
                "type": "macro_start",
                "name": macro_name,
                "duration_ms": duration_ms,
            })

            # === MACRO STEPS WITH OPERATION LIBRARY ===
            for step in macro.get("steps", []):
                osc_addr = step.get("osc")

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
                    outs = self._live_output_names()
                    map_subs = (self.channel_map or {}).get("submixes", {})
                    known = {int(s["index"]) for s in map_subs.values()
                             if isinstance(s.get("index"), (int, float))}
                    map_names = {str(n).strip() for n in map_subs}
                    try:
                        raw_idx = float(value if step.get("value") == "{{param}}"
                                        else step.get("value"))
                    except (TypeError, ValueError):
                        raw_idx = None
                    if (outs is None or raw_idx is None
                            or raw_idx != int(raw_idx)
                            or int(raw_idx) not in known
                            or outs != map_names):
                        why = ("live outputs not enumerable" if outs is None
                               else "output layout changed since the map"
                               if outs != map_names
                               else f"index {raw_idx} is not a known submix "
                                    f"index (map knows {sorted(known)})")
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

                if "operation" in step and step.get("value") == "{{param}}":
                    op_config = step["operation"]
                    # Substitute live MIDI clock BPM when the mapping uses "bpm": "clock"
                    if op_config.get("bpm") == "clock":
                        resolved_bpm = clock_bpm if clock_bpm else 140
                        op_config = {**op_config, "bpm": resolved_bpm}
                        logger.info(f"   → BPM clock sync: using {resolved_bpm} BPM")
                    OperationRegistry.execute(
                        op_config["type"],
                        self.osc_client,
                        osc_addr,
                        value,
                        op_config,
                        cancel_event=cancel_event,
                    )
                    if _restore_bank:
                        self.osc_client.send_message("/setBankStart", 0.0)
                        logger.info("   → bank window restored to 0 after channel-detail step")
                    continue

                # === NORMAL STATIC STEP ===
                step_val = value if step.get("value") == "{{param}}" else step.get("value")
                try:
                    self.osc_client.send_message(osc_addr, float(step_val))
                    logger.info(f"   → {osc_addr} = {step_val}")
                except Exception as e:
                    logger.error(f"OSC send failed: {e}")
                if _restore_bank:
                    self.osc_client.send_message("/setBankStart", 0.0)
                    logger.info("   → bank window restored to 0 after channel-detail step")

            # Restore the input row if any step drove the playback or
            # output row — page-1 addresses are row-relative, so leaving
            # another row selected would mis-route the next macro
            if any(str(s.get("target", {}).get("row", 1)) in ("2", "3")
                   for s in macro.get("steps", []) if "target" in s):
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
                "osc_preview": f"{macro_data.get('steps', [{}])[0].get('osc', '')} = {value:.3f}",
                "routing_label": self.get_routing_label(macro_name),
                "midi_trigger": macro_data.get("midi_triggers", [{}])[0] if macro_data.get("midi_triggers") else None,
            }
            self.macro_live_state[macro_name] = live_data
            self.broadcast_state(
                macro_update=live_data,
                macro_event={"type": "macro_complete", "name": macro_name},
            )

        finally:
            self._suppress_handler = False
            self._last_macro_end_time = time.time()
            with self._macro_lock:
                self._cancel_events.pop(macro_name, None)
                self._running_macros.discard(macro_name)
                # Fire any queued trigger (queue mode or restart mode)
                queued = self._queued_params.pop(macro_name, None)
            if queued is not None:
                logger.info(f"   → '{macro_name}' firing queued trigger (param={queued:.3f})")
                threading.Thread(target=self.run_macro, args=(macro_name, queued), daemon=True).start()

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
        "alev_enable":    "/2/alevEnable",
        "alev_headroom":  "/2/alevHeadroom",
        "alev_maxgain":   "/2/alevMaxgain",
        "input_gain":     "/2/gain",
        "input_gain_r":   "/2/gainRight",
        "phase":          "/2/phase",
        "phase_r":        "/2/phaseRight",
    }

    @staticmethod
    def _layout_key_from_names(names) -> str:
        """Stable key for a strip layout: the sorted real strip names.
        Snapshots re-pair strips, so the SAME name can carry DIFFERENT
        widths in different layouts (hardware-proven: RE-101 is width 2
        in the 17-strip layout, width 1 in the 23-strip one) — widths
        must be stored and looked up per layout, never in one flat map."""
        real = {str(n).strip() for n in names}
        return "|".join(sorted(
            n for n in real if n and n.lower() not in ("n.a.", "n/a")))

    def _layout_key(self, strips: dict) -> str:
        return self._layout_key_from_names(
            d.get("name", "") for d in strips.values())

    def _widths_for_layout(self, strips: dict) -> dict:
        """The verified width map for THIS layout:
        channel_map['width_maps'][layout_key] when the layout has been
        measured, else the legacy flat channel_widths (whose per-strip
        check still refuses when it does not cover)."""
        cm = self.channel_map or {}
        return (cm.get("width_maps", {}).get(self._layout_key(strips))
                or cm.get("channel_widths", {}))

    def _hw_offset_of_strip(self, strips: dict, strip):
        """Hardware-channel offset of a strip: the summed widths of the
        strips before it, from the VERIFIED width map only.

        Label inference is banned here: hardware testing proved a stereo
        pair can carry a slash-free name ('RE-101'), which silently mis-
        aimed every subsequent strip and wrote to the wrong channel's EQ
        (#5 write-test failure). Widths come from the layout-keyed
        width_maps (or legacy channel_widths), produced by hardware
        measurement or hand-entry. Returns None (→ refusal) if ANY
        preceding strip's width is unknown — one unknown poisons the
        whole offset.
        """
        widths = self._widths_for_layout(strips)
        offset = 0
        for s in sorted(strips):
            if s >= strip:
                break
            name = str(strips[s].get("name", "")).strip()
            w = widths.get(name)
            if w not in (1, 2):
                logger.error(f"   → no verified width for strip '{name}' — "
                             f"cannot aim page 2 (POST /api/device/widths "
                             f"with this layout's widths)")
                return None
            offset += w
        return offset

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

    def _submix_index_by_name(self, submix_name: str):
        """Look up a submix's /setSubmix index by name (channel map, then
        live listener state is not consulted — indices are stable per device)."""
        subs = (self.channel_map or {}).get("submixes", {})
        entry = subs.get(submix_name)
        if entry is None:  # case-insensitive fallback
            wanted = submix_name.strip().lower()
            entry = next((v for k, v in subs.items()
                          if k.strip().lower() == wanted), None)
        return entry.get("index") if entry else None

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
        channel_scoped = (param == "mute"
                          or param in self.CHANNEL_DETAIL_PARAMS)
        if param in self.CHANNEL_DETAIL_PARAMS and row == "2":
            # EQ/channel-detail exists on hardware inputs and outputs but
            # NOT on software playback (user-reported, device has no such
            # page) — an aimed write would land on a real channel instead
            logger.error(f"   → '{param}' does not exist on the playback "
                         f"row — refusing (EQ lives on hardware inputs "
                         f"and outputs only)")
            return None, None, "not_in_bank"
        if param in self.CHANNEL_DETAIL_PARAMS and row == "3":
            # OUTPUT EQ (hardware-measured on BOTH layout types): page 2
            # follows the selected row, and the page-2 offset is the
            # output's first hw channel — the walked submix index, except
            # the first output whose index is clamped to 1 (aims 0).
            # Verified on an all-stereo layout (Main 0-1, RE-150 In 14-15)
            # AND on a mono-containing one (RE-150 In index 14 at offset
            # 14 ONLY, mono ADAT 2 owning 15; Main 0-1; stereo Phones 1 at
            # 8-9). Mono and stereo alike: offset = index. Index sanity
            # (first == 1, deltas 1 or 2) still gates against a malformed
            # map; the live-outputs match gates against a stale one.
            subs = (self.channel_map or {}).get("submixes", {})
            entries = sorted(
                ((int(s["index"]), str(s.get("name", "")).strip())
                 for s in subs.values()
                 if isinstance(s.get("index"), (int, float))),
                key=lambda t: t[0])
            idx_of = {n: i for i, n in entries}
            if channel_name not in idx_of:
                logger.error(f"   → output '{channel_name}' not in the "
                             f"channel map — cannot aim page 2, refusing")
                return None, None, "not_in_bank"
            indices = [i for i, _ in entries]
            deltas = [b - a for a, b in zip(indices, indices[1:])]
            sane = (indices[0] == 1
                    and all(d in (1, 2) for d in deltas))
            if not sane:
                logger.error(f"   → output index spacing looks malformed "
                             f"(first {indices[0]}, deltas {deltas}) — "
                             f"refusing rather than mis-aim; re-run "
                             f"discovery")
                return None, None, "not_in_bank"
            live_outputs = self._live_output_names()
            map_outputs = {n for _, n in entries}
            if live_outputs is None or live_outputs != map_outputs:
                logger.error(f"   → live output row unknown or changed "
                             f"since the map was captured — refusing "
                             f"page-2 aim for output '{channel_name}'")
                return None, None, "not_in_bank"
            offset = 0 if channel_name == entries[0][1] else idx_of[channel_name]
            self.osc_client.send_message("/1/busOutput", 1.0)
            self.osc_client.send_message("/setBankStart", float(offset))
            addr = self.CHANNEL_DETAIL_PARAMS[param]
            logger.info(f"   → resolved OUTPUT '{channel_name}' {param} → "
                        f"hw offset {offset} (offset = walked index, "
                        f"first output = 0) → aimed page 2 ({addr})")
            return idx_of[channel_name], addr, "resolved"
        if channel_scoped:
            # Mute is GLOBAL-per-channel (hardware-verified, #4/#10) and
            # channel-detail params (EQ etc.) address the CHANNEL, not a
            # submix — no /setSubmix is sent for either. Row still matters.
            index = None
        else:
            index = self._submix_index_by_name(submix_name)
            if index is None:
                logger.warning(f"   → target submix '{submix_name}' not in channel map")
                return None, None, "not_in_bank"
            # CRASH GUARD (hardware root cause, 2026-07-31): /setSubmix past
            # the device's last real output crashes TotalMix outright, and
            # stale maps are routine — the index is only trusted when the
            # LIVE output row (one strip per submix, enumerated WITHOUT
            # /setSubmix) still matches the map the index came from.
            live_outputs = self._live_output_names()
            if live_outputs is None:
                logger.error(f"   → cannot enumerate the live output row — "
                             f"REFUSING /setSubmix {index} ('{submix_name}'): "
                             f"an out-of-range index crashes TotalMix")
                return None, None, "not_in_bank"
            map_outputs = {str(n).strip() for n in
                           (self.channel_map or {}).get("submixes", {})}
            if live_outputs != map_outputs:
                logger.error(f"   → output layout changed since the map was "
                             f"captured (gone: "
                             f"{sorted(map_outputs - live_outputs)}, new: "
                             f"{sorted(live_outputs - map_outputs)}) — "
                             f"REFUSING /setSubmix {index}; re-run discovery")
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
        if index is not None:
            self.osc_client.send_message("/setSubmix", float(index))
            logger.info(f"   → target: /setSubmix {index} ('{submix_name}') row {row}")
        else:
            logger.info(f"   → target: {param} '{channel_name}' row {row} (no submix switch)")

        listener = self.osc_listener
        if listener is None or not listener.running:
            if param in self.CHANNEL_DETAIL_PARAMS:
                # An unaimed page-2 write lands on whatever channel the bank
                # happens to show — refuse outright, never fall back
                logger.error(f"   → no OSC listener — cannot aim the channel-detail "
                             f"page for '{param}', refusing")
                return None, None, "not_in_bank"
            logger.warning("   → no OSC listener — cannot live-resolve strip, using stored address")
            return index, None, "no_feedback"

        # Event-driven waits: the listener wakes us the instant the matching
        # OSC message arrives — no sleeps, no polling. The deadline is only
        # an error bound (UDP is lossy; the channel may not exist), never a
        # pacing mechanism.
        deadline = time.time() + timeout
        wanted = submix_name.lower()
        wanted_ch = channel_name.lower()

        def _match_in(strips):
            # Exact name first, stereo-pair cover second — a pair strip's
            # fader controls both halves, so it is a correct target
            for strip, data in sorted(strips.items()):
                if str(data.get("name", "")).strip().lower() == wanted_ch:
                    return strip
            for strip, data in sorted(strips.items()):
                if self._names_cover(str(data.get("name", "")), channel_name):
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
            current = (state.current_submix or "").strip().lower()
            if current != wanted:
                return None
            return _match_in(state.submix_snapshot(state.current_submix).get(row, {}))

        if channel_scoped:
            # No submix switch happened, so there may be no fresh dump to
            # wait on. If nothing matches the already-known state, provoke a
            # dump with the probe's row-toggle trick (guaranteed change).
            if find_strip(listener.state) is None:
                other = "/1/busPlayback" if bus_addr == "/1/busInput" else "/1/busInput"
                self.osc_client.send_message(other, 1.0)
                self.osc_client.send_message(bus_addr, 1.0)
        elif not listener.wait_for(
                lambda st: (st.current_submix or "").strip().lower() == wanted,
                timeout):
            logger.warning(f"   → no labelSubmix confirmation for '{submix_name}' "
                           f"within {timeout}s — using stored address")
            return index, None, "no_feedback"

        remaining = max(0.0, deadline - time.time())
        if listener.wait_for(lambda st: find_strip(st) is not None, remaining):
            strip = find_strip(listener.state)
            if strip is not None:
                strip_name = str(listener.state.submix_snapshot(
                    listener.state.current_submix).get(row, {})
                    .get(strip, {}).get("name", ""))
                if strip_name.strip().lower() != wanted_ch:
                    logger.info(f"   → pair-matched '{channel_name}' to strip "
                                f"'{strip_name}' (stereo link changed)")
                if param in self.CHANNEL_DETAIL_PARAMS:
                    # Aim page 2 at this channel: /setBankStart takes a
                    # HARDWARE-CHANNEL offset (stereo pairs = two positions),
                    # computed from the live bank the strip was found in.
                    # run_macro restores bank 0 after the step executes.
                    st = listener.state
                    banks = ([st.current_submix] if st.current_submix else [])
                    banks += [s for s in list(st.submixes.keys()) if s not in banks]
                    strips = next((st.submix_snapshot(b).get(row, {})
                                   for b in banks
                                   if strip in st.submix_snapshot(b).get(row, {})), {})
                    offset = self._hw_offset_of_strip(strips, strip)
                    if offset is None:
                        # Wrong-channel EQ writes are silent and destructive —
                        # refuse rather than aim on a guess (#5 write-test
                        # failure: a slash-free stereo pair broke label math)
                        return None, None, "not_in_bank"
                    self.osc_client.send_message("/setBankStart", float(offset))
                    addr = self.CHANNEL_DETAIL_PARAMS[param]
                    logger.info(f"   → live-resolved '{channel_name}' {param} → strip "
                                f"{strip}, hw offset {offset} (verified widths) → "
                                f"aimed page 2 ({addr})")
                    return index, addr, "resolved"
                # Write address is always page 1 — the bus selection above
                # decides which row the write lands on
                addr = self._param_address(param, strip)
                logger.info(f"   → live-resolved '{channel_name}' {param} → strip {strip} "
                            f"({addr}, row {row})")
                return index, addr, "resolved"

        strips = listener.state.submix_snapshot(listener.state.current_submix).get(row, {})
        # Filter the placeholder strips past the hardware channel count —
        # a 48-wide bank would otherwise bury the real names in 30x 'n.a.'
        real = [d.get('name') for d in strips.values()
                if str(d.get('name', '')).strip().lower() not in ('n.a.', 'n/a')]
        logger.error(f"   → channel '{channel_name}' is NOT in the live bank for "
                     f"'{submix_name}' (live strips: {real}) — refusing the "
                     f"stored address, it may point at a different channel now")
        return index, None, "not_in_bank"

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

        def _names(st):
            strips = st.submix_snapshot("_outputs").get("3", {})
            names = {str(d.get("name", "")).strip() for d in strips.values()}
            return {n for n in names
                    if n and n.lower() not in ("n.a.", "n/a")}

        before = listener.state.message_count
        self.osc_client.send_message("/1/busOutput", 1.0)
        if listener.wait_for(lambda st: st.message_count > before, timeout):
            # A row dump has no end-of-burst marker — short bounded settle
            # so stale names from a previous layout get overwritten first
            time.sleep(0.15)
        self.osc_client.send_message("/1/busInput", 1.0)
        names = _names(listener.state) or None
        if names:
            self._outputs_cache = (time.time(), names)
        return names

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