"""Global OSC transport (#25) — absolute-addressed writes, no aiming.

The write path in full: a macro step's name-based target resolves to a
fixed hardware channel (live name cache first, physical-table aliases as
fallback), the param maps to its Global address + unit transform
(global_units.GLOBAL_PARAM_MAP), and the writer sends one absolutely-
addressed message. No /setSubmix, no /setBankStart, no bank/row state, no
restores, no settle windows — the concepts do not exist in this namespace.

Writers satisfy the OperationRegistry client duck-type (send_message(label,
normalized_value)) so ramps/LFOs work unchanged: operations.py keeps
emitting the classic 0..1 domain and the writer converts per message.

Liveness = the cyclic status heartbeat (~1/s when 'Send status cyclic' is
enabled on the remote). Uncalibrated unit transforms REFUSE — units are
never guessed.
"""
import logging
import threading
import time

import global_units as gu
import physical_table as pt

logger = logging.getLogger(__name__)

ROW_WORDS = {"1": "input", "2": "playback", "3": "output"}
ROW_KEYS = {"1": "inputs", "2": "playbacks", "3": "outputs"}


class MixSendWriter:
    """A submix send: /mix/in|pb/{in_hw}/{out_hw}/<path>."""

    def __init__(self, client, src, in_hw, out_hw, gp):
        self._client = client
        self.address = f"/mix/{src}/{in_hw}/{out_hw}/{gp.path}"
        self._gp = gp

    def send_message(self, _label, value):
        self._client.send_message(self.address, float(self._gp.to_wire(value)))


class ChannelParamWriter:
    """A channel-scoped param: /input|playback|output/{hw}/<path>.
    L/R params (gp.lr) address the pair's RIGHT member at hw+1."""

    def __init__(self, client, row_word, hw, gp, path=None):
        self._client = client
        n = hw + 1 if gp.lr else hw
        self.address = f"/{row_word}/{n}/{path or gp.path}"
        self._gp = gp

    def send_message(self, _label, value):
        self._client.send_message(self.address, float(self._gp.to_wire(value)))


class FxWriter:
    """Global FX: fixed absolute address (/reverb/..., /echo/...)."""

    def __init__(self, client, gp):
        self._client = client
        self.address = gp.path
        self._gp = gp

    def send_message(self, _label, value):
        self._client.send_message(self.address, float(self._gp.to_wire(value)))


class GlobalTransport:
    name = "global"
    NAME_SYNC_INTERVAL_S = 2.0

    def __init__(self, client, listener, table_provider, persist_cb=None,
                 heartbeat_timeout_s=5.0):
        """client: OSC sender aimed at TotalMix's Global remote port.
        listener: GlobalOSCListener (caller starts/stops it).
        table_provider: () -> physical_table dict (bridge._physical_table).
        persist_cb: () -> None, debounced persist after alias merges."""
        self._client = client
        self.listener = listener
        self._table = table_provider
        self._persist_cb = persist_cb
        self.heartbeat_timeout_s = heartbeat_timeout_s
        self._sync_thread = None
        self._sync_stop = threading.Event()

    # ── lifecycle ───────────────────────────────────────────────────
    def start(self):
        # bootstrap: one full dump fills names/params/mix caches (the
        # remote's bandwidth limiter absorbs the burst)
        self._client.send_message("/sendall", 1.0)
        self._sync_stop.clear()
        self._sync_thread = threading.Thread(target=self._name_sync_loop,
                                             daemon=True)
        self._sync_thread.start()
        logger.info("Global transport started (bootstrap /sendall sent)")

    def stop(self):
        self._sync_stop.set()
        if self._sync_thread:
            self._sync_thread.join(timeout=3.0)
            self._sync_thread = None

    # ── resolution ──────────────────────────────────────────────────
    def _hw_for_name(self, row_key, name):
        """Live name cache first (freshest truth), physical-table aliases
        second. Names live at the LEFT pair member under Global."""
        wanted = str(name).strip().lower()
        live = self.listener.state.channel_names(row_key)
        for hw, live_name in live.items():
            if live_name.strip().lower() == wanted:
                return hw
        table = self._table()
        hw = pt.resolve_start(table, row_key, name) if table is not None else None
        if hw is None:
            return None
        # The mono-name-while-linked default: a name like "AN 2" lives at
        # the right member's offset. If the pair is CURRENTLY linked (live
        # stereo flag at the even-aligned left member), the right member is
        # not an addressable strip — default to the pair. When mono, the
        # offset is the channel itself and stands.
        if hw % 2 == 1 and self.listener.state.stereo.get(row_key, {}).get(hw - 1):
            return hw - 1
        return hw

    def resolve_step(self, target):
        """target: mappings-format dict {channel, submix?, row?, param?}.
        -> (writer|None, human_label, status)."""
        param = str(target.get("param", "volume")).strip().lower()
        row = str(target.get("row", 1))
        channel = str(target.get("channel", "")).strip()
        submix = str(target.get("submix", "")).strip()

        gp = gu.GLOBAL_PARAM_MAP.get(param)
        if gp is None:
            return None, param, "unsupported_param"
        if not gp.calibrated:
            # unit transform not wire-measured yet — never guess units
            return None, param, "uncalibrated_param"

        if gp.scope == "fx":
            return FxWriter(self._client, gp), f"FX {param}", "resolved"

        row_word, row_key = ROW_WORDS.get(row), ROW_KEYS.get(row)
        if row_word is None:
            return None, param, "unsupported_param"

        # channel-detail params do not exist on the software playback row
        # (hardware-verified in the classic era; the namespace mirrors it)
        detail = gp.scope == "channel" and param not in ("mute",)
        if detail and row == "2" and param != "volume":
            return None, param, "playback_no_detail"

        hw = self._hw_for_name(row_key, channel)
        if hw is None:
            return None, channel, "not_in_table"

        if gp.scope == "channel":
            return (ChannelParamWriter(self._client, row_word, hw, gp),
                    f"{channel} {param}", "resolved")

        # mix scope: volume/pan. Row-3 targets are the output's own fader.
        if row == "3":
            return (ChannelParamWriter(self._client, "output", hw, gp,
                                       path=gp.path),
                    f"{channel} {param} (output)", "resolved")
        if not submix:
            return None, channel, "not_in_table"
        out_hw = self._hw_for_name("outputs", submix)
        if out_hw is None:
            return None, submix, "not_in_table"
        src = "pb" if row == "2" else "in"
        return (MixSendWriter(self._client, src, hw, out_hw, gp),
                f"{channel} → {submix} {param}", "resolved")

    # ── queries ─────────────────────────────────────────────────────
    def channel_names(self, row_key):
        return self.listener.state.channel_names(row_key)

    def read_param(self, row_key, hw, param_path):
        return self.listener.state.get_param(row_key, hw, param_path)

    # ── liveness ────────────────────────────────────────────────────
    def alive(self):
        age = self.listener.state.heartbeat_age()
        if age is not None and age < self.heartbeat_timeout_s:
            return {"alive": True, "method": "heartbeat",
                    "age_s": round(age, 3), "at": time.time()}
        # one refresh attempt before the verdict
        before = self.listener.state.message_count
        self._client.send_message("/sendstate", 1.0)
        got = self.listener.wait_for(
            lambda s: s.message_count > before, 2.0)
        age = self.listener.state.heartbeat_age()
        return {"alive": bool(got), "method": "heartbeat+sendstate",
                "age_s": round(age, 3) if age is not None else None,
                "at": time.time()}

    # ── switching ───────────────────────────────────────────────────
    def load_snapshot(self, snap_num, timeout=2.0):
        """Global OSC snapshot recall: 1-BASED, no 9-N button inversion.
        Confirmed by feedback /snapshot/load/{n} == 2.0 ('active')."""
        n = int(snap_num)
        self._client.send_message(f"/snapshot/load/{n}", 1.0)
        return self.listener.wait_for(
            lambda s: s.snapshots.get(n) == 2.0, timeout)

    # ── feedback -> physical table alias sync ──────────────────────
    def _name_sync_loop(self):
        while not self._sync_stop.wait(self.NAME_SYNC_INTERVAL_S):
            try:
                self.sync_names_once()
            except Exception as e:
                logger.warning(f"global name sync error: {e}")

    def sync_names_once(self):
        changed = self.listener.state.drain_name_changes()
        if not changed:
            return 0
        table = self._table()
        if table is None:
            return 0
        merged = 0
        st = self.listener.state
        for row_key, hw in changed:
            entry = st.names.get(row_key, {}).get(hw)
            if not entry or not entry.get("name"):
                continue
            name = entry["name"]
            if pt.merge_observation(table, row_key, hw, name):
                merged += 1
            # a pair's alias list lives at BOTH member offsets — the
            # classic invariant resolve_start()/covers() depend on. Global
            # names arrive at the LEFT member only, so mirror to hw+1 when
            # the stereo flag says linked.
            if st.stereo.get(row_key, {}).get(hw):
                if pt.merge_observation(table, row_key, hw + 1, name):
                    merged += 1
        if merged and self._persist_cb:
            try:
                self._persist_cb()
            except Exception as e:
                logger.warning(f"table persist after name sync failed: {e}")
        if merged:
            logger.info(f"physical table learned {merged} alias(es) from "
                        f"Global OSC feedback")
        return merged
