"""
Ars Festival Media Server — FastAPI application.
Central server for managing Raspberry Pi media fleet.
"""
import hashlib
import shutil
import json
import uuid
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, UploadFile, File, Form, Depends, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

import database as db
from auth import verify_admin, verify_device, create_admin_token
from config import (MEDIA_DIR, HOST, PORT, DEFAULT_POLL_INTERVAL,
                    DISABLE_SHELL, HEARTBEAT_KEEP)

VERSION = "0.3.0"

app = FastAPI(title="Ars Festival Media Server", version=VERSION)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def startup():
    db.init_db()
    try:
        pruned = db.prune_heartbeats(HEARTBEAT_KEEP)
        if pruned:
            print(f"Pruned {pruned} old heartbeat rows")
    except Exception as e:
        print(f"Heartbeat prune failed: {e}")


# ──────────────────────────────────────────────
# Health / Info
# ──────────────────────────────────────────────

@app.get("/")
def root():
    return RedirectResponse("/dashboard/")


@app.api_route("/health", methods=["GET", "HEAD"])
def health():
    return {"status": "ok", "service": "ars-fleet-server", "version": VERSION}


@app.get("/info")
def info():
    return {
        "poll_interval_seconds": DEFAULT_POLL_INTERVAL,
        "media_endpoint": "/media/file/",
        "manifest_endpoint": "/manifest/",
    }


# ──────────────────────────────────────────────
# Auth
# ──────────────────────────────────────────────

@app.post("/auth/token")
def get_token(admin=Depends(verify_admin)):
    return {"token": create_admin_token(), "type": "bearer"}


# ──────────────────────────────────────────────
# Device API (Pi clients)
# ──────────────────────────────────────────────

@app.post("/device/register")
def device_register(
    device_id: str = Form(...),
    hostname: str = Form(None),
    label: str = Form(None),
    group_name: str = Form("default"),
    hw_model: str = Form(None),
    ip_address: str = Form(None),
    mac_address: str = Form(None),
    os_info: str = Form(None),
    _auth=Depends(verify_device)
):
    result = db.register_device(device_id, hostname, label, group_name,
                                hw_model, ip_address, mac_address, os_info)
    return result


_heartbeat_counter = 0


@app.post("/device/heartbeat")
def device_heartbeat(
    device_id: str = Form(...),
    manifest_version: str = Form(None),
    vlc_status: str = Form(None),
    cpu_temp: float = Form(None),
    disk_free_mb: int = Form(None),
    uptime_seconds: int = Form(None),
    pinned: str = Form(None),
    pinned_source: str = Form(None),
    pinned_at: str = Form(None),
    fleet_state: str = Form(None),
    ip_address: str = Form(None),
    settings: str = Form(None),
    _auth=Depends(verify_device)
):
    pin_bool: Optional[bool] = None
    if pinned is not None and pinned != "":
        pin_bool = pinned in ("1", "true", "True", "yes")
    result = db.record_heartbeat(
        device_id, manifest_version, vlc_status,
        cpu_temp, disk_free_mb, uptime_seconds,
        pinned=pin_bool,
        pinned_source=pinned_source or None,
        pinned_at=pinned_at or None,
        fleet_state=fleet_state or None,
        ip_address=ip_address or None,
        settings=settings or None,
    )
    # Periodic heartbeat pruning (cheap; roughly every 1000 beats)
    global _heartbeat_counter
    _heartbeat_counter += 1
    if _heartbeat_counter % 1000 == 0:
        try:
            db.prune_heartbeats(HEARTBEAT_KEEP)
        except Exception:
            pass
    # Also return pending commands
    commands = db.get_pending_commands(device_id)
    return {"heartbeat": result, "pending_commands": commands,
            "poll_interval": DEFAULT_POLL_INTERVAL}


@app.get("/device/commands/{device_id}")
def device_get_commands(device_id: str, _auth=Depends(verify_device)):
    return {"commands": db.get_pending_commands(device_id)}


@app.post("/device/commands/{cmd_id}/ack")
def device_ack_command(cmd_id: str, result: str = Form("ok"), _auth=Depends(verify_device)):
    ok = db.ack_command(cmd_id, result)
    if not ok:
        raise HTTPException(404, "Command not found")
    return {"acked": True}


# ──────────────────────────────────────────────
# Manifest API
# ──────────────────────────────────────────────

@app.get("/manifest/{group_name}")
def get_manifest(group_name: str):
    """Public endpoint — devices poll this to check for updates."""
    manifest = db.get_latest_manifest(group_name)
    if not manifest:
        return {"version": None, "files": [], "group": group_name}
    return manifest


@app.get("/manifests")
def list_manifests(group_name: str = Query(None), admin=Depends(verify_admin)):
    return {"manifests": db.list_manifests(group_name)}


@app.post("/manifest")
def create_manifest(
    group_name: str = Form("default"),
    file_ids: str = Form(...),  # comma-separated
    notes: str = Form(""),
    admin=Depends(verify_admin)
):
    ids = [f.strip() for f in file_ids.split(",") if f.strip()]
    if not ids:
        raise HTTPException(400, "No file IDs provided")
    result = db.create_manifest(group_name, ids, notes)
    return result


# ──────────────────────────────────────────────
# Media file API
# ──────────────────────────────────────────────

@app.post("/media/upload")
async def upload_media(
    file: UploadFile = File(...),
    folder_id: str = Form(None),
    admin=Depends(verify_admin)
):
    """Upload a media file (video/audio/image), optionally into a folder."""
    if folder_id and not db.folder_exists(folder_id):
        raise HTTPException(400, "Unknown folder")
    content = await file.read()
    checksum = hashlib.sha256(content).hexdigest()

    # Use checksum prefix + original name for dedup-safe storage
    ext = Path(file.filename).suffix
    safe_name = f"{checksum[:12]}_{file.filename}"
    dest = MEDIA_DIR / safe_name
    dest.write_bytes(content)

    record = db.add_media_file(
        filename=safe_name,
        original_name=file.filename,
        content_type=file.content_type or "application/octet-stream",
        size_bytes=len(content),
        checksum=checksum,
        folder_id=folder_id or None,
    )
    return record


@app.get("/media/list")
def list_media(admin=Depends(verify_admin)):
    return {"files": db.list_media_files()}


@app.delete("/media/{file_id}")
def delete_media(file_id: str, admin=Depends(verify_admin)):
    ok = db.delete_media_file(file_id)
    if not ok:
        raise HTTPException(404, "File not found")
    return {"deleted": True}


@app.get("/media/file/{filename}")
def serve_media(filename: str):
    """Serve media file to devices. No auth required (files are not secret)."""
    path = MEDIA_DIR / filename
    if not path.exists() or not path.is_file():
        raise HTTPException(404, "File not found")
    return FileResponse(path)


# ──────────────────────────────────────────────
# Folder API
# ──────────────────────────────────────────────

@app.get("/folders")
def list_folders(admin=Depends(verify_admin)):
    return {"folders": db.list_folders()}


@app.post("/folders")
def create_folder(
    name: str = Form(...),
    parent_id: str = Form(None),
    admin=Depends(verify_admin)
):
    return db.create_folder(name, parent_id)


@app.put("/folders/{folder_id}")
def rename_folder(
    folder_id: str,
    name: str = Form(...),
    admin=Depends(verify_admin)
):
    ok = db.rename_folder(folder_id, name)
    if not ok:
        raise HTTPException(404, "Folder not found")
    return {"renamed": True}


@app.delete("/folders/{folder_id}")
def delete_folder(folder_id: str, admin=Depends(verify_admin)):
    ok = db.delete_folder(folder_id)
    if not ok:
        raise HTTPException(404, "Folder not found")
    return {"deleted": True}


@app.post("/media/{file_id}/move")
def move_file(
    file_id: str,
    folder_id: str = Form(None),
    admin=Depends(verify_admin)
):
    db.move_file_to_folder(file_id, folder_id)
    return {"moved": True, "file_id": file_id, "folder_id": folder_id}


# ──────────────────────────────────────────────
# Device-Media Assignment API
# ──────────────────────────────────────────────

@app.post("/media/{media_id}/assign")
def assign_media(
    media_id: str,
    device_ids: str = Form(...),  # comma-separated device IDs
    admin=Depends(verify_admin)
):
    ids = [d.strip() for d in device_ids.split(",") if d.strip()]
    if not ids:
        raise HTTPException(400, "No device IDs provided")
    result = db.bulk_assign_media(media_id, ids)
    # Auto-generate manifests for affected devices
    for did in ids:
        db.create_manifest_from_assignments(did)
    return result


@app.post("/media/{media_id}/unassign")
def unassign_media(
    media_id: str,
    device_ids: str = Form(...),
    admin=Depends(verify_admin)
):
    ids = [d.strip() for d in device_ids.split(",") if d.strip()]
    result = db.bulk_unassign_media(media_id, ids)
    # Re-generate manifests for affected devices
    for did in ids:
        db.create_manifest_from_assignments(did)
    return result


@app.get("/media/{media_id}/assignments")
def get_media_assignments(media_id: str, admin=Depends(verify_admin)):
    return {"assignments": db.get_media_assignments(media_id)}


@app.get("/admin/devices/{device_id}/assignments")
def get_device_assignments(device_id: str, admin=Depends(verify_admin)):
    return {"assignments": db.get_device_assignments(device_id)}


# ──────────────────────────────────────────────
# Device manifest (per-device, assignment-based)
# ──────────────────────────────────────────────

@app.get("/device/manifest/{device_id}")
def get_device_manifest(device_id: str, _auth=Depends(verify_device)):
    """Device-specific manifest endpoint — returns files assigned to this device.

    If the device is pinned (USB-sourced media), return a sentinel manifest
    so the client knows to skip the swap. Client also self-skips when its
    local state.json says pinned=true, but this is the server-side belt-and-
    suspenders.
    """
    device = db.get_device(device_id)
    if device and device.get("pinned"):
        return {"version": "pinned", "skip": True, "device_id": device_id}
    manifest = db.get_device_manifest(device_id)
    if not manifest:
        return {"version": None, "files": [], "device_id": device_id}
    return manifest


# ──────────────────────────────────────────────
# Admin: Device management
# ──────────────────────────────────────────────

@app.get("/admin/devices")
def admin_list_devices(group_name: str = Query(None), admin=Depends(verify_admin)):
    return {"devices": db.list_devices(group_name)}


@app.get("/admin/devices/{device_id}")
def admin_get_device(device_id: str, admin=Depends(verify_admin)):
    device = db.get_device(device_id)
    if not device:
        raise HTTPException(404, "Device not found")
    heartbeats = db.get_device_heartbeats(device_id, limit=10)
    return {"device": device, "recent_heartbeats": heartbeats}


@app.put("/admin/devices/{device_id}")
def admin_update_device(
    device_id: str,
    label: str = Form(None),
    group_name: str = Form(None),
    location: str = Form(None),
    admin=Depends(verify_admin)
):
    fields = {}
    if label is not None:
        fields["label"] = label
    if group_name is not None:
        fields["group_name"] = group_name
    if location is not None:
        fields["location"] = location
    ok = db.update_device(device_id, **fields)
    if not ok:
        raise HTTPException(400, "No valid fields to update")
    return {"updated": True}


VALID_COMMANDS = {"reboot", "vlc_restart", "player_restart", "update_now",
                  "health_probe", "force_poll", "unpin",
                  "set_settings", "identify", "play_sd"}
if not DISABLE_SHELL:
    VALID_COMMANDS.add("shell")


@app.post("/admin/devices/{device_id}/command")
def admin_send_command(
    device_id: str,
    command: str = Form(...),
    params: str = Form("{}"),
    admin=Depends(verify_admin)
):
    """Send a command to a device."""
    if command not in VALID_COMMANDS:
        raise HTTPException(400, f"Invalid command. Valid: {sorted(VALID_COMMANDS)}")
    try:
        params_dict = json.loads(params)
    except json.JSONDecodeError:
        params_dict = {}
    result = db.create_command(device_id, command, params_dict)
    return result


def _to_bool(v):
    return v in ("1", "true", "True", "yes", "on")


@app.put("/admin/devices/{device_id}/settings")
def admin_set_device_settings(
    device_id: str,
    rotation: int = Form(None),
    flip_h: str = Form(None),
    flip_v: str = Form(None),
    image_duration_s: int = Form(None),
    volume_pct: int = Form(None),
    muted: str = Form(None),
    admin=Depends(verify_admin)
):
    """Push playback settings (rotation / mirror / slide duration / volume /
    mute) to a device. Queues a `set_settings` command; the client applies it
    live via mpv IPC and reports the applied values back in its next heartbeat."""
    if not db.get_device(device_id):
        raise HTTPException(404, "Device not found")
    patch = {}
    if rotation is not None:
        if rotation not in (0, 90, 180, 270):
            raise HTTPException(400, "rotation must be 0, 90, 180 or 270")
        patch["rotation"] = rotation
    if flip_h is not None and flip_h != "":
        patch["flip_h"] = _to_bool(flip_h)
    if flip_v is not None and flip_v != "":
        patch["flip_v"] = _to_bool(flip_v)
    if image_duration_s is not None:
        patch["image_duration_s"] = max(1, min(3600, image_duration_s))
    if volume_pct is not None:
        patch["volume_pct"] = max(0, min(200, volume_pct))
    if muted is not None and muted != "":
        patch["muted"] = muted in ("1", "true", "True", "yes")
    if not patch:
        raise HTTPException(400, "No settings provided")
    cmd = db.create_command(device_id, "set_settings", patch)
    return {"queued": True, "settings": patch, "command": cmd}


@app.get("/admin/summary")
def admin_summary(admin=Depends(verify_admin)):
    return db.fleet_summary()


@app.post("/admin/devices/{device_id}/unpin")
def admin_unpin_device(device_id: str, admin=Depends(verify_admin)):
    """Release a USB-pinned device: clear server pin state and queue a
    force_poll command so the device clears its local pin on next heartbeat
    and immediately re-polls the manifest."""
    if not db.set_device_pin(device_id, pinned=False):
        raise HTTPException(404, "Device not found")
    cmd = db.create_command(device_id, "force_poll", {})
    return {"unpinned": True, "device_id": device_id, "queued_command": cmd}


@app.get("/admin/devices/{device_id}/heartbeats")
def admin_device_heartbeats(device_id: str, limit: int = Query(50),
                             admin=Depends(verify_admin)):
    return {"heartbeats": db.get_device_heartbeats(device_id, limit)}


# ──────────────────────────────────────────────
# Dashboard static files (served from ../dashboard/dist)
# ──────────────────────────────────────────────

DASHBOARD_DIR = Path(__file__).resolve().parent.parent / "dashboard" / "dist"

if DASHBOARD_DIR.exists():
    app.mount("/dashboard", StaticFiles(directory=str(DASHBOARD_DIR), html=True), name="dashboard")


# ──────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    print(f"🎬 Ars Festival Media Server starting on {HOST}:{PORT}")
    uvicorn.run("main:app", host=HOST, port=int(PORT), reload=True)
