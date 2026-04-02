import json

# ========================================================
# LEGACY / FALLBACK ONLY — fully dynamic workspaces now live in mqtt_handler.py
# ========================================================

def publish_workspaces(client):
    """DEPRECATED static publisher — kept only as a safe fallback on first connect.
    Real workspace list is now published dynamically from ufx2_snapshot_map.json"""
    print("ℹ️  publish_workspaces called (static fallback) — dynamic publishing is active in mqtt_handler")
    # No-op because we now use publish_dynamic_workspaces instead
    # (this prevents the ImportError and keeps old code happy)