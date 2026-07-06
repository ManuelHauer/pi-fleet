"""
SQLite database layer for device registry, media manifests, and commands.
"""
import sqlite3
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from contextlib import contextmanager

from config import DB_PATH


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def get_db():
    conn = sqlite3.connect(str(DB_PATH), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _ensure_column(db, table: str, col: str, ddl: str):
    """Add a column to an existing table if it doesn't exist.

    SQLite has no ADD COLUMN IF NOT EXISTS, so we PRAGMA-check first.
    Used for migrations on long-lived DBs.
    """
    existing = [r[1] for r in db.execute(f"PRAGMA table_info({table})").fetchall()]
    if col not in existing:
        db.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")


def init_db():
    """Create tables if not exist."""
    with get_db() as db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS devices (
                id TEXT PRIMARY KEY,
                hostname TEXT,
                label TEXT,
                group_name TEXT DEFAULT 'default',
                hw_model TEXT,
                ip_address TEXT,
                mac_address TEXT,
                os_info TEXT,
                last_seen TEXT,
                current_manifest_version TEXT,
                status TEXT DEFAULT 'unknown',
                registered_at TEXT,
                pinned INTEGER DEFAULT 0,
                pinned_source TEXT,
                pinned_at TEXT,
                extra JSON DEFAULT '{}'
            );

            CREATE TABLE IF NOT EXISTS media_folders (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                parent_id TEXT,
                path TEXT NOT NULL DEFAULT '/',
                created_at TEXT,
                FOREIGN KEY (parent_id) REFERENCES media_folders(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS media_files (
                id TEXT PRIMARY KEY,
                filename TEXT NOT NULL,
                original_name TEXT,
                content_type TEXT,
                size_bytes INTEGER,
                checksum_sha256 TEXT NOT NULL,
                folder_id TEXT,
                uploaded_at TEXT,
                uploaded_by TEXT DEFAULT 'admin',
                FOREIGN KEY (folder_id) REFERENCES media_folders(id) ON DELETE SET NULL
            );

            CREATE TABLE IF NOT EXISTS device_media (
                device_id TEXT NOT NULL,
                media_id TEXT NOT NULL,
                assigned_at TEXT,
                assigned_by TEXT DEFAULT 'admin',
                PRIMARY KEY (device_id, media_id),
                FOREIGN KEY (device_id) REFERENCES devices(id) ON DELETE CASCADE,
                FOREIGN KEY (media_id) REFERENCES media_files(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS manifests (
                version TEXT PRIMARY KEY,
                group_name TEXT NOT NULL DEFAULT 'default',
                device_id TEXT,
                files JSON NOT NULL,
                created_at TEXT,
                created_by TEXT DEFAULT 'admin',
                notes TEXT DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS commands (
                id TEXT PRIMARY KEY,
                device_id TEXT NOT NULL,
                command TEXT NOT NULL,
                params JSON DEFAULT '{}',
                status TEXT DEFAULT 'pending',
                created_at TEXT,
                acked_at TEXT,
                result TEXT,
                FOREIGN KEY (device_id) REFERENCES devices(id)
            );

            CREATE TABLE IF NOT EXISTS heartbeats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                device_id TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                manifest_version TEXT,
                vlc_status TEXT,
                cpu_temp REAL,
                disk_free_mb INTEGER,
                uptime_seconds INTEGER,
                extra JSON DEFAULT '{}',
                FOREIGN KEY (device_id) REFERENCES devices(id)
            );

            CREATE INDEX IF NOT EXISTS idx_commands_device_status ON commands(device_id, status);
            CREATE INDEX IF NOT EXISTS idx_heartbeats_device ON heartbeats(device_id, timestamp);
            CREATE INDEX IF NOT EXISTS idx_manifests_group ON manifests(group_name, created_at);
            CREATE INDEX IF NOT EXISTS idx_manifests_device ON manifests(device_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_device_media_device ON device_media(device_id);
            CREATE INDEX IF NOT EXISTS idx_device_media_media ON device_media(media_id);
            CREATE INDEX IF NOT EXISTS idx_media_files_folder ON media_files(folder_id);
        """)
        # Migrations for existing DBs (v0.2 USB-pin model)
        _ensure_column(db, "devices", "pinned", "pinned INTEGER DEFAULT 0")
        _ensure_column(db, "devices", "pinned_source", "pinned_source TEXT")
        _ensure_column(db, "devices", "pinned_at", "pinned_at TEXT")
        # v0.3: venue field, reported playback settings, derived client state
        _ensure_column(db, "devices", "location", "location TEXT")
        _ensure_column(db, "devices", "settings", "settings JSON DEFAULT '{}'")
        _ensure_column(db, "devices", "fleet_state", "fleet_state TEXT")


# --- Device operations ---

def register_device(device_id: str, hostname: str = None, label: str = None,
                    group_name: str = "default", hw_model: str = None,
                    ip_address: str = None, mac_address: str = None,
                    os_info: str = None) -> dict:
    with get_db() as db:
        existing = db.execute("SELECT id FROM devices WHERE id=?", (device_id,)).fetchone()
        now = utcnow()
        if existing:
            db.execute("""UPDATE devices SET hostname=COALESCE(?,hostname),
                          label=COALESCE(?,label), group_name=COALESCE(?,group_name),
                          hw_model=COALESCE(?,hw_model), ip_address=COALESCE(?,ip_address),
                          mac_address=COALESCE(?,mac_address), os_info=COALESCE(?,os_info),
                          last_seen=?, status='online'
                          WHERE id=?""",
                       (hostname, label, group_name, hw_model, ip_address,
                        mac_address, os_info, now, device_id))
            return {"action": "updated", "device_id": device_id}
        else:
            db.execute("""INSERT INTO devices (id,hostname,label,group_name,hw_model,
                          ip_address,mac_address,os_info,last_seen,registered_at,status)
                          VALUES (?,?,?,?,?,?,?,?,?,?,'online')""",
                       (device_id, hostname, label, group_name, hw_model,
                        ip_address, mac_address, os_info, now, now))
            return {"action": "registered", "device_id": device_id}


def list_devices(group_name: str = None) -> list:
    with get_db() as db:
        if group_name:
            rows = db.execute("SELECT * FROM devices WHERE group_name=? ORDER BY label",
                              (group_name,)).fetchall()
        else:
            rows = db.execute("SELECT * FROM devices ORDER BY group_name, label").fetchall()
        return [dict(r) for r in rows]


from typing import Optional


def get_device(device_id: str) -> Optional[dict]:
    with get_db() as db:
        row = db.execute("SELECT * FROM devices WHERE id=?", (device_id,)).fetchone()
        return dict(row) if row else None


def update_device(device_id: str, **fields) -> bool:
    allowed = {"label", "group_name", "status", "extra", "location"}
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return False
    set_clause = ", ".join(f"{k}=?" for k in updates)
    with get_db() as db:
        db.execute(f"UPDATE devices SET {set_clause} WHERE id=?",
                   (*updates.values(), device_id))
        return True


# --- Media file operations ---

def add_media_file(filename: str, original_name: str, content_type: str,
                   size_bytes: int, checksum: str) -> dict:
    file_id = str(uuid.uuid4())[:8]
    with get_db() as db:
        db.execute("""INSERT INTO media_files (id,filename,original_name,content_type,
                      size_bytes,checksum_sha256,uploaded_at)
                      VALUES (?,?,?,?,?,?,?)""",
                   (file_id, filename, original_name, content_type,
                    size_bytes, checksum, utcnow()))
        return {"id": file_id, "filename": filename, "checksum": checksum}


def list_media_files() -> list:
    with get_db() as db:
        rows = db.execute("SELECT * FROM media_files ORDER BY uploaded_at DESC").fetchall()
        return [dict(r) for r in rows]


def delete_media_file(file_id: str) -> bool:
    with get_db() as db:
        cur = db.execute("DELETE FROM media_files WHERE id=?", (file_id,))
        return cur.rowcount > 0


# --- Manifest operations ---

def create_manifest(group_name: str, file_ids: list, notes: str = "") -> dict:
    """Create a new manifest version for a group from a list of media file IDs."""
    version = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S") + "-" + str(uuid.uuid4())[:4]
    with get_db() as db:
        # Resolve file details
        placeholders = ",".join("?" * len(file_ids))
        rows = db.execute(
            f"SELECT id, filename, checksum_sha256, size_bytes FROM media_files WHERE id IN ({placeholders})",
            file_ids
        ).fetchall()
        files = [{"id": r["id"], "filename": r["filename"],
                  "checksum": r["checksum_sha256"], "size": r["size_bytes"]} for r in rows]
        db.execute("""INSERT INTO manifests (version, group_name, files, created_at, notes)
                      VALUES (?,?,?,?,?)""",
                   (version, group_name, json.dumps(files), utcnow(), notes))
        return {"version": version, "group": group_name, "file_count": len(files)}


def get_latest_manifest(group_name: str) -> Optional[dict]:
    with get_db() as db:
        row = db.execute(
            "SELECT * FROM manifests WHERE group_name=? ORDER BY created_at DESC LIMIT 1",
            (group_name,)
        ).fetchone()
        if row:
            d = dict(row)
            d["files"] = json.loads(d["files"])
            return d
        return None


def list_manifests(group_name: str = None) -> list:
    with get_db() as db:
        if group_name:
            rows = db.execute("SELECT version, group_name, created_at, notes FROM manifests WHERE group_name=? ORDER BY created_at DESC",
                              (group_name,)).fetchall()
        else:
            rows = db.execute("SELECT version, group_name, created_at, notes FROM manifests ORDER BY created_at DESC").fetchall()
        return [dict(r) for r in rows]


# --- Command operations ---

def create_command(device_id: str, command: str, params: dict = None) -> dict:
    cmd_id = str(uuid.uuid4())[:8]
    with get_db() as db:
        db.execute("""INSERT INTO commands (id, device_id, command, params, created_at)
                      VALUES (?,?,?,?,?)""",
                   (cmd_id, device_id, command, json.dumps(params or {}), utcnow()))
        return {"id": cmd_id, "device_id": device_id, "command": command}


def get_pending_commands(device_id: str) -> list:
    with get_db() as db:
        rows = db.execute(
            "SELECT * FROM commands WHERE device_id=? AND status='pending' ORDER BY created_at",
            (device_id,)
        ).fetchall()
        return [dict(r) for r in rows]


def ack_command(cmd_id: str, result: str = "ok") -> bool:
    with get_db() as db:
        cur = db.execute("UPDATE commands SET status='done', acked_at=?, result=? WHERE id=?",
                         (utcnow(), result, cmd_id))
        return cur.rowcount > 0


# --- Heartbeat operations ---

def record_heartbeat(device_id: str, manifest_version: str = None,
                     vlc_status: str = None, cpu_temp: float = None,
                     disk_free_mb: int = None, uptime_seconds: int = None,
                     pinned: bool = None, pinned_source: str = None,
                     pinned_at: str = None,
                     fleet_state: str = None, ip_address: str = None,
                     settings: str = None,
                     extra: dict = None) -> dict:
    with get_db() as db:
        db.execute("""INSERT INTO heartbeats (device_id, timestamp, manifest_version,
                      vlc_status, cpu_temp, disk_free_mb, uptime_seconds, extra)
                      VALUES (?,?,?,?,?,?,?,?)""",
                   (device_id, utcnow(), manifest_version, vlc_status,
                    cpu_temp, disk_free_mb, uptime_seconds,
                    json.dumps(extra or {})))
        # Validate reported settings JSON before storing
        settings_json = None
        if settings:
            try:
                settings_json = json.dumps(json.loads(settings))
            except (ValueError, TypeError):
                settings_json = None
        # Update device last_seen + pin state + v0.3 reported fields
        db.execute(
            """UPDATE devices SET
                last_seen=?,
                status='online',
                current_manifest_version=COALESCE(?,current_manifest_version),
                pinned=COALESCE(?, pinned),
                pinned_source=COALESCE(?, pinned_source),
                pinned_at=COALESCE(?, pinned_at),
                fleet_state=COALESCE(?, fleet_state),
                ip_address=COALESCE(?, ip_address),
                settings=COALESCE(?, settings)
               WHERE id=?""",
            (utcnow(), manifest_version,
             1 if pinned is True else (0 if pinned is False else None),
             pinned_source if pinned_source else None,
             pinned_at if pinned_at else None,
             fleet_state if fleet_state else None,
             ip_address if ip_address else None,
             settings_json,
             device_id))
        return {"recorded": True}


def prune_heartbeats(keep_per_device: int = 500) -> int:
    """Drop old heartbeat rows so a festival's 150 Pis × 2/min don't grow the
    DB unbounded. Keeps the newest N rows per device. Returns rows deleted."""
    with get_db() as db:
        cur = db.execute("""
            DELETE FROM heartbeats WHERE id IN (
                SELECT id FROM (
                    SELECT id, ROW_NUMBER() OVER (
                        PARTITION BY device_id ORDER BY timestamp DESC
                    ) AS rn FROM heartbeats
                ) WHERE rn > ?
            )""", (keep_per_device,))
        return cur.rowcount


def fleet_summary() -> dict:
    """Aggregate counts for the dashboard header."""
    from datetime import timedelta
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=90)).isoformat()
    with get_db() as db:
        total = db.execute("SELECT COUNT(*) c FROM devices").fetchone()["c"]
        online = db.execute("SELECT COUNT(*) c FROM devices WHERE last_seen >= ?",
                            (cutoff,)).fetchone()["c"]
        pinned = db.execute("SELECT COUNT(*) c FROM devices WHERE pinned=1").fetchone()["c"]
        media = db.execute("SELECT COUNT(*) c, COALESCE(SUM(size_bytes),0) s "
                           "FROM media_files").fetchone()
        groups = [r["group_name"] for r in db.execute(
            "SELECT DISTINCT group_name FROM devices ORDER BY group_name").fetchall()]
        return {"devices_total": total, "devices_online": online,
                "devices_offline": total - online, "devices_pinned": pinned,
                "media_files": media["c"], "media_bytes": media["s"],
                "groups": groups}


def set_device_pin(device_id: str, pinned: bool,
                   pinned_source: str = None, pinned_at: str = None) -> bool:
    """Server-side pin toggle (admin override). Returns True if a row matched."""
    with get_db() as db:
        cur = db.execute(
            """UPDATE devices SET pinned=?, pinned_source=?, pinned_at=?
               WHERE id=?""",
            (1 if pinned else 0,
             pinned_source if pinned else None,
             pinned_at if pinned else None,
             device_id))
        return cur.rowcount > 0


def get_device_heartbeats(device_id: str, limit: int = 50) -> list:
    with get_db() as db:
        rows = db.execute(
            "SELECT * FROM heartbeats WHERE device_id=? ORDER BY timestamp DESC LIMIT ?",
            (device_id, limit)
        ).fetchall()
        return [dict(r) for r in rows]


# --- Folder operations ---

def create_folder(name: str, parent_id: str = None) -> dict:
    folder_id = str(uuid.uuid4())[:8]
    with get_db() as db:
        # Build path
        if parent_id:
            parent = db.execute("SELECT path FROM media_folders WHERE id=?", (parent_id,)).fetchone()
            path = (parent["path"].rstrip("/") + "/" + name) if parent else ("/" + name)
        else:
            path = "/" + name
        db.execute("INSERT INTO media_folders (id, name, parent_id, path, created_at) VALUES (?,?,?,?,?)",
                   (folder_id, name, parent_id, path, utcnow()))
        return {"id": folder_id, "name": name, "parent_id": parent_id, "path": path}


def list_folders() -> list:
    with get_db() as db:
        rows = db.execute("SELECT * FROM media_folders ORDER BY path").fetchall()
        return [dict(r) for r in rows]


def rename_folder(folder_id: str, new_name: str) -> bool:
    with get_db() as db:
        folder = db.execute("SELECT * FROM media_folders WHERE id=?", (folder_id,)).fetchone()
        if not folder:
            return False
        old_path = folder["path"]
        parent_path = "/".join(old_path.rstrip("/").split("/")[:-1]) or ""
        new_path = parent_path + "/" + new_name
        # Update this folder
        db.execute("UPDATE media_folders SET name=?, path=? WHERE id=?",
                   (new_name, new_path, folder_id))
        # Update descendants
        db.execute("UPDATE media_folders SET path = ? || substr(path, ?) WHERE path LIKE ?",
                   (new_path, len(old_path) + 1, old_path + "/%"))
        return True


def delete_folder(folder_id: str) -> bool:
    with get_db() as db:
        # Move files in this folder (and subfolders) to root (folder_id=NULL)
        db.execute("UPDATE media_files SET folder_id=NULL WHERE folder_id=?", (folder_id,))
        # Get all subfolder ids
        sub_ids = [r["id"] for r in db.execute(
            "SELECT id FROM media_folders WHERE id=? OR parent_id=?", (folder_id, folder_id)
        ).fetchall()]
        for sid in sub_ids:
            db.execute("UPDATE media_files SET folder_id=NULL WHERE folder_id=?", (sid,))
        # Delete folder and subfolders (CASCADE should handle children)
        db.execute("DELETE FROM media_folders WHERE id=?", (folder_id,))
        return True


def move_file_to_folder(file_id: str, folder_id: str = None) -> bool:
    with get_db() as db:
        db.execute("UPDATE media_files SET folder_id=? WHERE id=?", (folder_id, file_id))
        return True


# --- Device-media assignment operations ---

def assign_media_to_device(media_id: str, device_id: str) -> dict:
    with get_db() as db:
        existing = db.execute("SELECT 1 FROM device_media WHERE device_id=? AND media_id=?",
                              (device_id, media_id)).fetchone()
        if existing:
            return {"action": "already_assigned", "device_id": device_id, "media_id": media_id}
        db.execute("INSERT INTO device_media (device_id, media_id, assigned_at) VALUES (?,?,?)",
                   (device_id, media_id, utcnow()))
        return {"action": "assigned", "device_id": device_id, "media_id": media_id}


def unassign_media_from_device(media_id: str, device_id: str) -> bool:
    with get_db() as db:
        cur = db.execute("DELETE FROM device_media WHERE device_id=? AND media_id=?",
                         (device_id, media_id))
        return cur.rowcount > 0


def get_device_assignments(device_id: str) -> list:
    """Get all media files assigned to a device."""
    with get_db() as db:
        rows = db.execute("""
            SELECT mf.*, dm.assigned_at FROM device_media dm
            JOIN media_files mf ON dm.media_id = mf.id
            WHERE dm.device_id=?
            ORDER BY mf.original_name
        """, (device_id,)).fetchall()
        return [dict(r) for r in rows]


def get_media_assignments(media_id: str) -> list:
    """Get all devices a media file is assigned to."""
    with get_db() as db:
        rows = db.execute("""
            SELECT d.id, d.label, d.hostname, d.group_name, d.status, dm.assigned_at
            FROM device_media dm
            JOIN devices d ON dm.device_id = d.id
            WHERE dm.media_id=?
            ORDER BY d.label
        """, (media_id,)).fetchall()
        return [dict(r) for r in rows]


def bulk_assign_media(media_id: str, device_ids: list) -> dict:
    """Assign a media file to multiple devices at once."""
    assigned = 0
    skipped = 0
    with get_db() as db:
        for did in device_ids:
            existing = db.execute("SELECT 1 FROM device_media WHERE device_id=? AND media_id=?",
                                  (did, media_id)).fetchone()
            if existing:
                skipped += 1
                continue
            db.execute("INSERT INTO device_media (device_id, media_id, assigned_at) VALUES (?,?,?)",
                       (did, media_id, utcnow()))
            assigned += 1
    return {"assigned": assigned, "skipped": skipped, "media_id": media_id}


def bulk_unassign_media(media_id: str, device_ids: list) -> dict:
    removed = 0
    with get_db() as db:
        for did in device_ids:
            cur = db.execute("DELETE FROM device_media WHERE device_id=? AND media_id=?",
                             (did, media_id))
            removed += cur.rowcount
    return {"removed": removed, "media_id": media_id}


def create_manifest_from_assignments(device_id: str) -> Optional[dict]:
    """Generate a manifest for a device based on its current assignments."""
    files = get_device_assignments(device_id)
    if not files:
        return None
    version = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S") + "-" + str(uuid.uuid4())[:4]
    files_json = [{"id": f["id"], "filename": f["filename"],
                   "checksum": f["checksum_sha256"], "size": f["size_bytes"]}
                  for f in files]
    with get_db() as db:
        db.execute("""INSERT INTO manifests (version, group_name, device_id, files, created_at, notes)
                      VALUES (?,?,?,?,?,?)""",
                   (version, "device", device_id, json.dumps(files_json), utcnow(),
                    f"Auto-generated from device assignments"))
    return {"version": version, "device_id": device_id, "file_count": len(files_json)}


def get_device_manifest(device_id: str) -> Optional[dict]:
    """Get latest manifest for a specific device (from assignments)."""
    with get_db() as db:
        # First try device-specific manifest
        row = db.execute(
            "SELECT * FROM manifests WHERE device_id=? ORDER BY created_at DESC LIMIT 1",
            (device_id,)
        ).fetchone()
        if row:
            d = dict(row)
            d["files"] = json.loads(d["files"])
            return d
        # Fall back to group manifest
        device = db.execute("SELECT group_name FROM devices WHERE id=?", (device_id,)).fetchone()
        if device:
            return get_latest_manifest(device["group_name"])
        return None
