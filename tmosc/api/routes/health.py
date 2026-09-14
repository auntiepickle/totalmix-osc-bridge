"""Root redirects, /api/health, /api/status."""
from fastapi import APIRouter
from fastapi.responses import RedirectResponse

from tmosc.api.deps import Bridge
import tmosc.physical_table as pt

router = APIRouter(tags=["health"])


@router.get("/")
async def root():
    return RedirectResponse(url="/static/index.html")


@router.get("/index.html")
async def index_fallback():
    return RedirectResponse(url="/static/index.html")


@router.get("/api/health")
async def get_health(bridge: Bridge):
    """Return connection health for MQTT and OSC, plus the current MIDI owner
    (an external tray/agent holding the port) so the browser can yield/reclaim
    Web MIDI. Polled by the header — keep it light."""
    return {
        "mqtt_connected": getattr(bridge, "mqtt_connected", False),
        "osc_configured": bridge.osc_client is not None,
        "midi_owner": bridge.midi_owner_state(),
    }


@router.get("/api/status")
async def get_status(bridge: Bridge):
    """Return currently-loaded config summary for the gear menu."""
    channel_map = bridge.channel_map or {}
    snap_map = bridge.snapshot_map or {}
    listener = bridge.osc_listener
    listening = listener is not None and listener.running
    bank_width = listener.state.bank_width if listening else None
    live_strips = listener.state.real_strip_count if listening else None
    # Highest channel the map expects — if the live bank is narrower, part
    # of the rig is invisible to routing (per-workspace TotalMix setting)
    map_max_channel = max(
        (s.get("channel", 0)
         for sub in channel_map.get("submixes", {}).values()
         for s in sub.get("sends", {}).values()),
        default=0,
    )
    # How many INPUT-row channels the map knows per submix — compared against
    # live_strip_count, which only ever reflects the currently selected row
    # (input in practice). Counting playback sends here masked a real stale
    # map once: 17 live vs '39 total' stayed silent while 16 mapped input
    # channels didn't exist on the device.
    map_strip_count = max(
        (sum(1 for s in sub.get("sends", {}).values() if s.get("row", 1) == 1)
         for sub in channel_map.get("submixes", {}).values()),
        default=0,
    )
    # NOTE (2026-08-20 architecture review): input strip counts change with
    # EVERY snapshot (pairing is per-snapshot) — that is normal operation,
    # not drift. An earlier version auto-walked here and made snapshot
    # switches trigger 90s walks in a loop. Strip counts are reported for
    # telemetry only; nothing acts on them.
    return {
        "osc_bank_width": bank_width,
        "live_strip_count": live_strips,
        "channel_map_max_channel": map_max_channel,
        "channel_map_strip_count": map_strip_count,
        # workspace/snapshot below are the bridge's commanded belief;
        # state_confirmed says whether the device confirmed the last switch
        "state_confirmed": getattr(bridge, "state_confirmed", None),
        # #30: what the device reports about snapshots (Global feed); the
        # workspace is never reported, so it stays belief
        "device_snapshot_slot": getattr(bridge, "device_snapshot_slot", None),
        "snapshot_modified": getattr(bridge, "snapshot_modified", None),
        # #30: the workspace as TotalMix shows it, reported by the agent on
        # that machine (None without such an agent)
        "workspace_report": bridge.workspace_report_state(),
        # Live-vs-map drift (output side): False drives the UI banner
        # #24: no drift concept — per-write confirmations carry correctness.
        # The physical table summary + sweep status are the honest surface.
        "physical_table": pt.summarize(
            (bridge.channel_map or {}).get("physical_table") or {}),
        "sweep_status": bridge.sweep_state.get("status"),
        "device_probe": getattr(bridge, "last_probe", None),
        "macros": len(bridge.mappings.get("macros", {})),
        "channel_map_submixes": len(channel_map.get("submixes", {})),
        "snapshot_map_workspaces": len(snap_map),
        "workspace": bridge.current_workspace,
        "snapshot": bridge.current_snapshot,
        "mappings_is_example": bridge.mappings_is_example,
        "mappings_source": bridge.mappings_source,
        "channel_map_is_example": getattr(bridge, "channel_map_is_example", False),
    }
