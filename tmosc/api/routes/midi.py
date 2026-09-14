"""Agent coexistence: MIDI owner heartbeat/release, activity relay, the
bindings TSV the native agent reads."""
from typing import Optional

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from tmosc.api.deps import Bridge

router = APIRouter(tags=["midi"])


class MidiOwnerBody(BaseModel):
    id: str
    host: Optional[str] = None
    title: Optional[str] = None   # #30: TotalMix main-window title on the agent's machine ("" = none)


@router.post("/api/midi/owner/heartbeat")
async def midi_owner_heartbeat(body: MidiOwnerBody, bridge: Bridge):
    """A tray/agent announces (every couple seconds) that it is handling MIDI.
    Refreshes presence; browsers yield Web MIDI while an agent owns the port.
    With `title` the bridge also adopts the workspace TotalMix shows (#30)."""
    return {"owner": bridge.midi_owner_heartbeat(body.id, body.host, title=body.title),
            "workspace_report": bridge.workspace_report_state()}


@router.post("/api/midi/owner/release")
async def midi_owner_release(body: MidiOwnerBody, bridge: Bridge):
    """A tray/agent cleanly releases the MIDI port (on shutdown) so browsers
    reclaim it immediately instead of waiting for the heartbeat to expire."""
    return {"released": bridge.midi_owner_release(body.id)}


@router.post("/api/midi/activity")
async def midi_activity(body: dict, bridge: Bridge):
    """A tray/agent relays each raw MIDI message it reads (throttled) so a
    browser that has yielded the port can still run MIDI-learn and show live
    activity. Pure fan-out: broadcast to WS clients, store nothing. The browser
    uses this for monitor + learn ONLY — it never fires macros from it (the
    agent already did), so there's no double-trigger."""
    m = body.get("m")
    if (isinstance(m, (list, tuple)) and 1 <= len(m) <= 3
            and all(isinstance(x, int) for x in m)):
        await bridge._do_broadcast_event(
            {"type": "midi_activity", "m": list(m), "src": "agent"})
    return {"ok": True}


@router.get("/api/midi/bindings", response_class=PlainTextResponse)
def get_midi_bindings(bridge: Bridge):
    """MIDI trigger table as TSV, for the native background agent (and a
    future microcontroller) to read without a JSON parser. One line per
    trigger, tab-separated:

        name  is_knob  type  number  note  channel  use_value_as_param

    number/note are -1 when not applicable. Read-only; the agent matches
    incoming MIDI against this and drives /ws (knob) or /api/trigger (fire),
    exactly as the browser does."""
    lines = []
    for name, m in list(bridge.mappings.get("macros", {}).items()):   # snapshot: other threads resize it
        is_knob = 1 if bridge._knob_step(m) else 0
        for t in (m.get("midi_triggers") or []):
            typ = str(t.get("type", "control_change"))
            number = t.get("number", -1)
            note = t.get("note", -1)
            channel = t.get("channel", 1)
            uvap = 1 if t.get("use_value_as_param") else 0
            number = -1 if number is None else number
            note = -1 if note is None else note
            # name is MACRO_NAME_RE-constrained ([A-Za-z0-9_-]) so no tabs/newlines
            lines.append(f"{name}\t{is_knob}\t{typ}\t{number}\t{note}\t{channel}\t{uvap}")
    return "\n".join(lines) + ("\n" if lines else "")
