"""KNOB macros (continuous MIDI control): device value, companions,
auto-enable, pins, groups, set/readback/trailing flush, device watcher, hold
reapply, MQTT knob state. Requires (facade __init__): global_transport,
global_listener, duck, mappings, mqtt_client, knob_values, the _knob_* dicts,
_mqtt_knob_published, _knob_watch_stop, _knob_enable_sent. Requires (other
mixins / facade): _global_active (facade), _record_fire (macros),
_sync_snapshot_from_device (switching), broadcast_state (broadcast)."""
import logging
import threading
import time

import tmosc.global_units as gu
from tmosc.operations import shape_value, unshape_value

logger = logging.getLogger(__name__)

ROW_KEYS_BY_WORD = {"input": "inputs", "playback": "playbacks", "output": "outputs"}


class KnobsMixin:
    # ─────────────────────────────────────────────────────────────
    # KNOB macros — continuous MIDI control (operation type "knob")
    # ─────────────────────────────────────────────────────────────
    # A knob tick is NOT a macro fire: no device lock, no start/complete
    # events, no LED/health churn per message. Resolve by name (sub-ms
    # under Global, always right for the current layout) and write. Global-
    # first: refuses cleanly under the classic transport.
    KNOB_BROADCAST_INTERVAL_S = 0.1

    @staticmethod
    def _knob_step(macro):
        for st in macro.get("steps", []):
            if (st.get("operation") or {}).get("type") == "knob" and "target" in st:
                return st
        return None

    def knob_device_value(self, step):
        """The device's CURRENT value for the knob's target, normalized
        0..1 from Global feedback — None when unknown. Lets the card show
        where the mixer actually is before the physical knob is touched."""
        if not self._global_active():
            return None
        writer, _, status = self.global_transport.resolve_step(step["target"])
        if status != "resolved":
            return None
        st = self.global_listener.state
        addr = getattr(writer, "address", "")
        try:
            if addr.startswith("/mix/"):
                _, _, src, in_hw, out_hw, path = addr.split("/")
                # Only the send LEVEL has wire-verified feedback (.../fader,
                # dB). A mix pan knob used to be answered with that fader
                # through the fader curve - wrong parameter, wrong units -
                # and the card/HA slider jumped on every level change.
                # Unknown beats wrong until pan feedback is observed.
                if path != "faderlin":
                    return None
                e = st.get_mix(src, int(in_hw), int(out_hw), "fader")
                return gu.fader_lin(e[0]) if e else None
            parts = addr.strip("/").split("/")
            row_word, hw, path = parts[0], int(parts[1]), "/".join(parts[2:])
            if path == "faderlin":
                # Feedback for a channel's own fader arrives in dB - as
                # .../fader on some rows but as .../volume on the OUTPUT row
                # (wire-observed 2026-09-10: a Main fader move reports
                # /output/0/volume). Reading only "fader" left every row-3
                # volume knob without a device value, so neither the card
                # nor the HA slider could follow TotalMix (#28). Freshest wins.
                row_key = ROW_KEYS_BY_WORD[row_word]
                cands = [c for c in (st.get_param(row_key, hw, "fader"),
                                     st.get_param(row_key, hw, "volume")) if c]
                if not cands:
                    return None
                return gu.fader_lin(max(cands, key=lambda c: c[1])[0])
            e = st.get_param(ROW_KEYS_BY_WORD[row_word], hw, path)
            if e is None:
                return None
            param = str(step["target"].get("param", "volume")).lower()
            gp = gu.GLOBAL_PARAM_MAP.get(param)
            return float(gp.from_wire(e[0])) if gp and gp.from_wire else None
        except Exception:
            return None

    @staticmethod
    def _enable_target(step):
        """Target dict for the knob param's section switch, or None."""
        en = gu.enable_param_for(step["target"].get("param", "volume"))
        return {**step["target"], "param": en} if en else None

    def _param_state(self, target):
        """Normalized (0..1) device value of any param on a target from
        Global feedback, or None when unknown/unresolvable."""
        if not self._global_active():
            return None
        writer, _, status = self.global_transport.resolve_step(target)
        if status != "resolved":
            return None
        st = self.global_listener.state
        addr = getattr(writer, "address", "")
        parts = addr.strip("/").split("/")
        try:
            if parts[0] in ROW_KEYS_BY_WORD:
                e = st.get_param(ROW_KEYS_BY_WORD[parts[0]], int(parts[1]),
                                 "/".join(parts[2:]))
            else:
                e = st.get_param("fx", 0, addr.strip("/"))
        except (ValueError, IndexError):
            return None
        if e is None:
            return None
        gp = gu.GLOBAL_PARAM_MAP.get(str(target.get("param", "")).lower())
        return float(gp.from_wire(e[0])) if gp and gp.from_wire else None

    def knob_companions(self, step):
        """Device state of the knob param's companion params (e.g. the
        low-cut slope next to a low-cut freq knob): {param: normalized}."""
        out = {}
        for p in gu.companions_for(step["target"].get("param", "volume")):
            out[p] = self._param_state({**step["target"], "param": p})
        return out

    def knob_param_set(self, macro_name, param, value, source="ui"):
        """Write any calibrated param on the knob's routing target - used
        for companion controls (low-cut slope, EQ band type)."""
        macro = self.mappings.get("macros", {}).get(macro_name)
        step = self._knob_step(macro) if macro else None
        if step is None:
            return {"status": "not_a_knob"}
        if param not in gu.GLOBAL_PARAM_MAP:
            return {"status": "unsupported_param"}
        if not self._global_active():
            return {"status": "knob_needs_global"}
        tgt = {**step["target"], "param": param}
        writer, label, status = self.global_transport.resolve_step(tgt)
        if status != "resolved":
            return {"status": status}
        try:
            value = max(0.0, min(1.0, float(value)))
        except (TypeError, ValueError):
            return {"status": "bad_value"}
        writer.send_message("param", value)
        self._schedule_knob_readback(macro_name, step, writer.address)
        self.broadcast_state(macro_event={
            "type": "knob_update", "name": macro_name, "status": "resolved",
            "value": self.knob_values.get(macro_name),
            "device_value": self.knob_device_value(step),
            "enable_value": self.knob_enable_state(step),
            "companions": {**self.knob_companions(step), param: value},
            "source": source})
        return {"status": "resolved", "param": param, "value": value}

    def knob_enable_state(self, step):
        """Device state of the knob's section switch from Global feedback:
        True/False, or None when unknown / no companion."""
        tgt = self._enable_target(step)
        if tgt is None or not self._global_active():
            return None
        writer, _, status = self.global_transport.resolve_step(tgt)
        if status != "resolved":
            return None
        st = self.global_listener.state
        addr = getattr(writer, "address", "")
        parts = addr.strip("/").split("/")
        try:
            if parts[0] in ROW_KEYS_BY_WORD:
                e = st.get_param(ROW_KEYS_BY_WORD[parts[0]], int(parts[1]),
                                 "/".join(parts[2:]))
            else:
                e = st.get_param("fx", 0, addr.strip("/"))
        except (ValueError, IndexError):
            return None
        return None if e is None else bool(float(e[0]) >= 0.5)

    def knob_enable(self, macro_name, on, source="ui"):
        """Flip the knob's section switch (EQ / low cut / dynamics / FX on)."""
        macro = self.mappings.get("macros", {}).get(macro_name)
        step = self._knob_step(macro) if macro else None
        if step is None:
            return {"status": "not_a_knob"}
        tgt = self._enable_target(step)
        if tgt is None:
            return {"status": "no_enable_param"}
        if not self._global_active():
            return {"status": "knob_needs_global"}
        writer, label, status = self.global_transport.resolve_step(tgt)
        if status != "resolved":
            return {"status": status}
        writer.send_message("enable", 1.0 if on else 0.0)
        self._knob_enable_sent[macro_name] = time.time()
        self._schedule_knob_readback(macro_name, step, writer.address)
        self.broadcast_state(macro_event={
            "type": "knob_update", "name": macro_name, "status": "resolved",
            "value": self.knob_values.get(macro_name),
            "device_value": self.knob_device_value(step),
            "enable_value": bool(on), "source": source})
        return {"status": "resolved", "enable": bool(on)}

    def _auto_enable(self, macro_name, step):
        """'Turn on with knob move': if the section switch is not known ON,
        set it (absolute set, idempotent) - at most once per 2s until the
        readback confirms, so a streaming knob never spams it."""
        if step["operation"].get("auto_enable") is False:
            return
        tgt = self._enable_target(step)
        if tgt is None:
            return
        if self.knob_enable_state(step) is True:
            return
        if time.time() - self._knob_enable_sent.get(macro_name, 0) < 2.0:
            return
        writer, _, status = self.global_transport.resolve_step(tgt)
        if status == "resolved":
            writer.send_message("enable", 1.0)
            self._knob_enable_sent[macro_name] = time.time()
            logger.info(f"knob '{macro_name}' switched its section on ({tgt['param']})")

    KNOB_OFF_AT_MIN_EPS = 0.01

    def _off_at_min(self, macro_name, step, value):
        """operation.off_at = 'min' | 'max' (legacy bool off_at_min): one
        end of the knob's travel switches its section OFF - bottom for a low
        cut, TOP for a high cut (20 kHz = no cut); leaving that end switches
        it ON again immediately (the crossing bypasses the auto-enable
        throttle). Returns True while parked at the off end."""
        op = step["operation"]
        end = op.get("off_at") or ("min" if op.get("off_at_min") else None)
        if end not in ("min", "max"):
            return False
        tgt = self._enable_target(step)
        if tgt is None:
            return False
        at_min = (value <= self.KNOB_OFF_AT_MIN_EPS) if end == "min" \
                 else (value >= 1.0 - self.KNOB_OFF_AT_MIN_EPS)
        key = f"{macro_name}:offmin"
        prev = self._knob_enable_sent.get(key)      # last crossing state
        if prev == at_min:
            return at_min
        writer, _, status = self.global_transport.resolve_step(tgt)
        if status == "resolved":
            writer.send_message("enable", 0.0 if at_min else 1.0)
            self._knob_enable_sent[macro_name] = time.time()   # counts as an enable write
            logger.info(f"knob '{macro_name}' {'OFF at end of travel' if at_min else 'back ON'} ({tgt['param']})")
        self._knob_enable_sent[key] = at_min
        return at_min

    def _assert_pins(self, macro_name, step):
        """Pinned companion values (operation.companions): if the device
        state differs (or is unknown), write them - throttled with the same
        2s guard as auto-enable so a streaming knob never spams."""
        pins = step["operation"].get("companions") or {}
        if not pins:
            return
        if time.time() - self._knob_enable_sent.get(f"{macro_name}:pins", 0) < 2.0:
            return
        for p, v in pins.items():
            if p not in gu.GLOBAL_PARAM_MAP:
                continue
            try:
                v = max(0.0, min(1.0, float(v)))
            except (TypeError, ValueError):
                continue
            tgt = {**step["target"], "param": p}
            cur = self._param_state(tgt)
            if cur is not None and abs(cur - v) < 0.02:
                continue
            writer, _, status = self.global_transport.resolve_step(tgt)
            if status == "resolved":
                writer.send_message("pin", v)
                self._knob_enable_sent[f"{macro_name}:pins"] = time.time()
                logger.info(f"knob '{macro_name}' pinned {p} -> {v:.3f}")

    def _write_group_members(self, macro_name, step, primary_norm):
        """Send groups (#user request: 'move multiple faders at once as a
        group'). VCA-style: each member carries a stored dB offset from
        the primary, so the group's internal balance survives every move.

        Native TotalMix fader groups were deliberated first and rejected:
        only 4 exist, they are GLOBAL (the same group programmed across
        submixes corrupts the other submixes - RME forum), their OSC
        exposure is an on/off toggle (/3/faderGroups) not a group fader,
        and group logic hooks the GUI fader layer - direct Global /mix
        writes bypass it. Bridge-side members are per-knob, unlimited,
        and the offsets are stored, visible, and re-capturable."""
        grp = (step.get("operation") or {}).get("group")
        if not isinstance(grp, list) or not grp:
            return
        if str(step.get("target", {}).get("param", "volume")) != "volume":
            return                      # groups ride the fader law
        base_db = gu.fader_db(primary_norm)
        # SAFETY (critical-review S1/C3): a group must never drive a member -
        # least of all an output/Main fader - into the +6 dB overgain region
        # and blow past a knob's own cap. Every member write is ceilinged at
        # unity (0 dBFS). And a member that resolves to the primary's own
        # address (stereo-alias collision, H3) is skipped, not double-written.
        member_ceil = gu.fader_lin(0.0)
        seen = set()
        try:
            pw, _, pst = self.global_transport.resolve_step(step["target"])
            if pst == "resolved":
                seen.add(getattr(pw, "address", None))
        except Exception:
            pass
        for mem in grp:
            if not isinstance(mem, dict) or not mem.get("channel"):
                continue
            tgt = {k: v for k, v in mem.items()
                   if k in ("submix", "channel", "row")}
            try:
                off = float(mem.get("offset_db", 0.0))
            except (TypeError, ValueError):
                off = 0.0
            try:
                writer, _, status = self.global_transport.resolve_step(tgt)
            except Exception:
                logger.exception(f"group member resolve failed for '{macro_name}'")
                continue
            if status != "resolved":
                continue
            addr = getattr(writer, "address", None)
            if addr in seen:
                continue                    # alias collided with primary/member
            seen.add(addr)
            # primary at hard bottom = the whole group mutes clean
            v = 0.0 if primary_norm <= 0.0005 \
                else max(0.0, min(member_ceil, gu.fader_lin(base_db + off)))
            try:
                writer.send_message("group", v)
            except Exception:
                logger.exception(f"group member write failed for '{macro_name}'")

    def knob_set(self, macro_name, value, source="midi"):
        """Write a knob's value (0..1, mapped through its range/threshold).
        Returns {"status": ..., "value": ...}; statuses mirror resolve_step
        plus 'not_a_knob' and 'knob_needs_global'."""
        macro = self.mappings.get("macros", {}).get(macro_name)
        step = self._knob_step(macro) if macro else None
        if step is None:
            return {"status": "not_a_knob"}
        try:
            value = max(0.0, min(1.0, float(value)))
        except (TypeError, ValueError):
            return {"status": "bad_value"}
        writer = None
        if not self._global_active():
            status = "knob_needs_global"
        else:
            writer, label, status = self.global_transport.resolve_step(step["target"])
        if status == "resolved":
            if not self._off_at_min(macro_name, step, value):
                self._auto_enable(macro_name, step)
            self._assert_pins(macro_name, step)
            shaped = shape_value(value, step["operation"])
            writer.send_message("knob", shaped)
            if self.duck is not None:
                # a ducked send: this is the performer's new un-ducked level
                self.duck.seed(macro_name, gu.fader_db(shaped))
            self._write_group_members(macro_name, step, shaped)
            self.knob_values[macro_name] = value
            self._schedule_knob_readback(macro_name, step, writer.address)
        if self._knob_last_status.get(macro_name) != status:
            # log on CHANGE only — a knob streams dozens of ticks a second
            self._knob_last_status[macro_name] = status
            if status == "resolved":
                logger.info(f"knob '{macro_name}' live -> {getattr(writer, 'address', '?')}")
                self._record_fire(macro_name, "ok")
            else:
                logger.error(f"knob '{macro_name}' refused: {status}")
                self._record_fire(macro_name, "skipped", status)
        now = time.time()
        if now - self._knob_last_broadcast.get(macro_name, 0) >= self.KNOB_BROADCAST_INTERVAL_S:
            prev_trailing = self._knob_trailing_timers.pop(macro_name, None)
            if prev_trailing:
                prev_trailing.cancel()
            self._knob_last_broadcast[macro_name] = now
            if status == "resolved":
                self._knob_last_pushed[macro_name] = self._knob_snapshot(step)
                self._mqtt_knob_state(macro_name)
            self.broadcast_state(macro_event={
                "type": "knob_update", "name": macro_name, "status": status,
                "value": self.knob_values.get(macro_name),
                "device_value": self.knob_device_value(step) if status == "resolved" else None,
                "enable_value": self.knob_enable_state(step) if status == "resolved" else None,
                "companions": self.knob_companions(step) if status == "resolved" else {},
                "source": source,
            })
        else:
            # Throttled — but a knob stream must NEVER drop its final value:
            # the last tick of a drag would otherwise never broadcast, and
            # every browser (including the dragger, whose stale echo then
            # lands after release) would show a value the server has already
            # moved past. Arm a trailing-edge flush that broadcasts CURRENT
            # state when the throttle window closes. (#user report: knob
            # jumps back a little after a drag.)
            self._schedule_knob_trailing(macro_name, step, source)
        return {"status": status, "value": self.knob_values.get(macro_name)}

    def _mqtt_knob_state(self, name, value=None):
        """Retained knob state for Home Assistant (#mqtt-knob): rides the
        same throttle as the WS broadcast, so an MQTT slider tracks every
        source of change (iPhone, MIDI, UI, snapshot re-assert). `value`
        overrides the bridge's own last-written value - the device watcher
        passes the knob position derived from TotalMix feedback (#28)."""
        if not self.mqtt_client:
            return
        v = self.knob_values.get(name) if value is None else value
        if v is None:
            return
        try:
            self.mqtt_client.publish(f"totalmix/knob/{name}/state",
                                     f"{float(v):.4f}", retain=True)
            self._mqtt_knob_published[name] = float(v)
        except Exception:
            pass

    def _schedule_knob_trailing(self, name, step, source):
        if self._knob_trailing_timers.get(name):
            return   # one armed already; it reads live state when it fires
        delay = max(0.01, self.KNOB_BROADCAST_INTERVAL_S
                    - (time.time() - self._knob_last_broadcast.get(name, 0)))

        def _flush():
            try:
                self._knob_trailing_timers.pop(name, None)
                self._knob_last_broadcast[name] = time.time()
                status = self._knob_last_status.get(name, "resolved")
                resolved = status == "resolved"
                if resolved:
                    self._knob_last_pushed[name] = self._knob_snapshot(step)
                    self._mqtt_knob_state(name)   # HA gets the FINAL value
                self.broadcast_state(macro_event={
                    "type": "knob_update", "name": name, "status": status,
                    "value": self.knob_values.get(name),
                    "device_value": self.knob_device_value(step) if resolved else None,
                    "enable_value": self.knob_enable_state(step) if resolved else None,
                    "companions": self.knob_companions(step) if resolved else {},
                    "source": source,
                })
            except Exception as e:
                logger.debug(f"knob trailing flush for {name} failed: {e}")

        t = threading.Timer(delay, _flush)
        t.daemon = True
        self._knob_trailing_timers[name] = t
        t.start()

    KNOB_READBACK_DELAY_S = 0.4

    def _knob_snapshot(self, step):
        """Comparable tuple of everything a knob card displays from the
        device: value, section switch, companions."""
        dv = self.knob_device_value(step)
        en = self.knob_enable_state(step)
        comps = tuple(sorted((k, None if v is None else round(v, 4))
                             for k, v in self.knob_companions(step).items()))
        return (None if dv is None else round(dv, 4), en, comps)

    # A device echo of our own write differs from the published value only
    # by quantization; below this (1% of knob travel) it is not a user move.
    KNOB_MQTT_DEVICE_EPS = 0.01

    def _knob_watch_tick(self):
        """One pass of the device -> browser/MQTT differ (see _knob_watch_loop).
        Each knob is isolated: one malformed knob config (a bad `range`, say)
        must not silently stop device sync for every other knob."""
        for name, macro in list(self.mappings.get("macros", {}).items()):
            try:
                self._knob_watch_one(name, macro)
            except Exception as e:
                if name not in self._knob_watch_failed:
                    self._knob_watch_failed.add(name)
                    logger.warning(f"knob '{name}': device sync skipped ({e}) - fix its config")
            else:
                self._knob_watch_failed.discard(name)

    def _knob_watch_one(self, name, macro):
        step = self._knob_step(macro)
        if step is None or not self._global_active():
            return
        snap = self._knob_snapshot(step)
        if snap == self._knob_last_pushed.get(name):
            return
        self._knob_last_pushed[name] = snap
        self.broadcast_state(macro_event={
            "type": "knob_update", "name": name, "status": "resolved",
            "value": self.knob_values.get(name),
            "device_value": snap[0], "enable_value": snap[1],
            "companions": dict(self.knob_companions(step)),
            "source": "device"})
        # #28: Home Assistant only ever heard our OWN writes, so a fader
        # moved in TotalMix left the HA knob stale. Publish the device's
        # value as the knob position (inverse-mapped through the range),
        # skipping mere echoes of what we last published.
        if snap[0] is not None:
            kv = unshape_value(snap[0], step["operation"])
            last = self._mqtt_knob_published.get(name)
            if last is None or abs(kv - last) > self.KNOB_MQTT_DEVICE_EPS:
                self._mqtt_knob_state(name, kv)

    def _knob_watch_loop(self):
        """Device → browser sync (#user report): someone flips EQ off IN
        TOTALMIX and the chip must follow. The Global listener already holds
        the truth (change broadcasts update its state); this 1 Hz differ
        pushes a knob_update whenever a knob's displayed state changed
        without us writing it - and, since #28, the MQTT knob state too.
        Since #30 it also follows snapshot recalls made on the device."""
        while not self._knob_watch_stop.wait(1.0):
            try:
                self._sync_snapshot_from_device()
            except Exception as e:
                logger.warning(f"snapshot sync failed: {e}")
            try:
                self._knob_watch_tick()
            except Exception as e:
                logger.warning(f"knob watch tick failed: {e}")

    def _schedule_knob_readback(self, name, step, address):
        """Own Global writes never echo (re-send OFF), so after a knob
        settles, re-dump its channel once and push the DEVICE's value to
        the card — the 'device' line stays honest instead of freezing at
        whatever the last dump said. Mix-scope knobs skip it (/sendchan
        carries no mix rows; a full /sendmix per settle is too heavy)."""
        prev = self._knob_readback_timers.get(name)
        if prev:
            prev.cancel()
        parts = address.strip("/").split("/")
        if parts[0] == "mix" or len(parts) < 3:
            return
        row_word, hw = parts[0], parts[1]

        def _go():
            try:
                self.global_transport._client.send_message(
                    f"/sendchan/{row_word}/{hw}", 1.0)
                time.sleep(0.35)
                self.broadcast_state(macro_event={
                    "type": "knob_update", "name": name, "status": "resolved",
                    "value": self.knob_values.get(name),
                    "device_value": self.knob_device_value(step),
                    "enable_value": self.knob_enable_state(step),
                    "companions": self.knob_companions(step),
                    "source": "readback"})
            except Exception as e:
                logger.debug(f"knob readback for {name} failed: {e}")

        t = threading.Timer(self.KNOB_READBACK_DELAY_S, _go)
        t.daemon = True
        self._knob_readback_timers[name] = t
        t.start()

    def reapply_held_knobs(self):
        """Snapshot-agnostic knobs: after a confirmed snapshot/workspace
        switch the device holds the SNAPSHOT's stored values — re-assert
        every knob marked hold so a recall never yanks a knob back."""
        if not self._global_active():
            return 0
        n = 0
        for name, value in list(self.knob_values.items()):
            macro = self.mappings.get("macros", {}).get(name)
            step = self._knob_step(macro) if macro else None
            if step and step["operation"].get("hold"):
                # The recall may have flipped the section switch and the
                # pinned companions too; forget the crossing memo and the
                # 2 s throttles so the OFF end / enable / pins are written
                # again, not just the value (a knob parked at its off end
                # otherwise re-wrote the freq and left the cut engaged).
                for key in (name, f"{name}:offmin", f"{name}:pins"):
                    self._knob_enable_sent.pop(key, None)
                if self.knob_set(name, value, source="hold")["status"] == "resolved":
                    n += 1
        if n:
            logger.info(f"   -> re-asserted {n} held knob(s) after switch")
        return n
