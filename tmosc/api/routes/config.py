"""Whole-file config reads/writes: mappings, channel map, snapshot map,
uploads, init-from-example, reload."""
import json
import logging
import os
import shutil

from fastapi import APIRouter, HTTPException, Request, UploadFile, File

from tmosc.bridge import bridge
import tmosc.app_paths as app_paths
from tmosc.api import persistence
logger = logging.getLogger(__name__)

router = APIRouter(tags=["config"])


@router.get("/api/snapshot_map")
async def get_snapshot_map():
    """Return the loaded snapshot map (for client-side WS/SS validation)."""
    return bridge.snapshot_map or {}


@router.get("/api/config/mappings")
async def get_config_mappings():
    """Return full mappings.json content for the live editor."""
    return bridge.mappings


@router.post("/api/config/mappings")
async def save_config_mappings(request: Request):
    """Save JSON body directly to mappings.json and hot-reload into bridge."""
    try:
        data = await request.json()
        if "macros" not in data:
            raise HTTPException(status_code=400, detail="Invalid mappings.json: missing 'macros' key")
        data = persistence._sanitize_mappings(data)
        persistence.backup_json_files("mappings.json")
        target = app_paths.data_path("mappings.json")
        persistence._atomic_write_json(target, data)
        bridge.mappings = data
        bridge.mappings_is_example = False
        bridge.mappings_source = "mappings.json"
        logger.info(f"✅ mappings.json saved via live editor ({len(data.get('macros', {}))} macros)")
        return {"status": "success", "macros": len(data.get("macros", {}))}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Config save failed: {e}")
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/api/config/channel_map")
async def get_config_channel_map():
    """Return full channel_map content for the live editor."""
    return bridge.channel_map or {}


@router.post("/api/config/channel_map")
async def save_config_channel_map(request: Request):
    """Save JSON body directly to ufx2_channel_map.json and hot-reload into bridge."""
    try:
        data = await request.json()
        if "submixes" not in data and "physical_table" not in data:
            raise HTTPException(status_code=400, detail="Invalid channel_map: needs 'physical_table' (or legacy 'submixes')")
        persistence.backup_json_files("ufx2_channel_map.json")
        target = app_paths.data_path("ufx2_channel_map.json")
        persistence._atomic_write_json(target, data)
        bridge._load_channel_map()
        bridge.channel_map_is_example = False
        logger.info("✅ channel_map.json saved via live editor")
        return {"status": "success", "submixes": len(data.get("submixes", {}))}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Config save failed: {e}")
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/api/config/snapshot_map")
async def get_config_snapshot_map():
    """Return full snapshot_map content for the live editor."""
    return bridge.snapshot_map or {}


@router.post("/api/config/snapshot_map")
async def save_config_snapshot_map(request: Request):
    """Save snapshot_map to both local file and /app/config (SMB mount if present).
    Updates bridge.snapshot_map immediately so run_macro resolves slots correctly."""
    try:
        data = await request.json()
        if not isinstance(data, dict):
            raise HTTPException(status_code=400, detail="snapshot_map must be a JSON object")
        # Backup first (every other config write does), and only adopt the
        # new map in memory AFTER the disk write succeeds — assigning first
        # left memory and disk divergent on a failed write (review finding)
        persistence.backup_json_files("ufx2_snapshot_map.json")
        local_target = app_paths.data_path("ufx2_snapshot_map.json")
        with open(local_target, "w") as f:
            json.dump(data, f, indent=2)
        bridge.snapshot_map = data
        # Also write to SMB mount if accessible
        smb_target = "/app/config/ufx2_snapshot_map.json"
        smb_written = False
        try:
            with open(smb_target, "w") as f:
                json.dump(data, f, indent=2)
            smb_written = True
        except Exception:
            pass  # SMB mount not available in dev
        workspaces = sum(1 for k, v in data.items() if not k.startswith("_") and isinstance(v, dict))
        logger.info(f"✅ snapshot_map saved ({workspaces} workspaces, SMB={smb_written})")
        return {"status": "success", "workspaces": workspaces, "smb_written": smb_written}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"snapshot_map save failed: {e}")
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/api/upload/mappings")
async def upload_mappings(file: UploadFile = File(...)):
    try:
        data = await persistence._read_json_upload(file)
        if "macros" not in data:
            raise HTTPException(status_code=400, detail="Invalid mappings.json format")
        data = persistence._sanitize_mappings(data)
        persistence.backup_json_files("mappings.json")
        target = app_paths.data_path("mappings.json")
        persistence._atomic_write_json(target, data)
        bridge.mappings = data
        bridge.mappings_is_example = False
        bridge.mappings_source = "mappings.json"
        logger.info(f"✅ mappings.json uploaded + reloaded ({len(data.get('macros', {}))} macros)")
        return {"status": "success", "message": "mappings.json updated and reloaded"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Upload failed: {e}")
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/api/upload/channel_map")
async def upload_channel_map(file: UploadFile = File(...)):
    try:
        data = await persistence._read_json_upload(file)
        if "submixes" not in data and "physical_table" not in data:
            raise HTTPException(status_code=400, detail="Invalid ufx2_channel_map.json format")
        persistence.backup_json_files("ufx2_channel_map.json")
        target = app_paths.data_path("ufx2_channel_map.json")
        persistence._atomic_write_json(target, data)
        bridge._load_channel_map()
        logger.info("✅ ufx2_channel_map.json uploaded + reloaded")
        return {"status": "success", "message": "channel map updated and reloaded"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Upload failed: {e}")
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/api/config/channel_map/init-from-example")
async def init_channel_map_from_example():
    """Copy ufx2_channel_map.example.json → ufx2_channel_map.json and reload."""
    example = app_paths.example_path("ufx2_channel_map.example.json")
    target  = app_paths.data_path("ufx2_channel_map.json")
    try:
        if not os.path.exists(example):
            raise HTTPException(status_code=404, detail="ufx2_channel_map.example.json not found")
        shutil.copy2(example, target)
        bridge._load_channel_map()
        bridge.channel_map_is_example = False
        submixes = len((bridge.channel_map or {}).get("submixes", {}))
        logger.info(f"✅ ufx2_channel_map.json initialized from example ({submixes} submixes)")
        return {"status": "success", "submixes": submixes}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Init channel_map from example failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/config/mappings/init-from-example")
async def init_mappings_from_example():
    """Copy mappings.example.json → mappings.json and reload into the bridge.
    Called from the UI when no mappings.json exists on the server."""
    example = app_paths.example_path("mappings.example.json")
    target  = app_paths.data_path("mappings.json")
    try:
        if not os.path.exists(example):
            raise HTTPException(status_code=404, detail="mappings.example.json not found")
        shutil.copy2(example, target)
        with open(target, "r") as f:
            data = json.load(f)
        bridge.mappings = data
        bridge.mappings_is_example = False
        bridge.mappings_source = "mappings.json"
        logger.info(f"✅ mappings.json initialized from example ({len(data.get('macros', {}))} macros)")
        return {"status": "success", "macros": len(data.get("macros", {}))}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Init from example failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/reload")
async def reload_bridge():
    """Reload mappings.json from disk into the running bridge."""
    try:
        target = app_paths.data_path("mappings.json")
        with open(target, "r") as f:
            data = json.load(f)
        bridge.mappings = data
        bridge.mappings_is_example = False
        bridge.mappings_source = "mappings.json"
        logger.info(f"✅ Bridge reloaded — {len(data.get('macros', {}))} macros")
        return {"status": "success", "macros": len(data.get("macros", {}))}
    except Exception as e:
        logger.error(f"Reload failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))
