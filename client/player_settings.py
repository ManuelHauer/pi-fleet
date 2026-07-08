#!/usr/bin/env python3
"""
Shared playback-settings store — /opt/fleet-media/player-settings.json.

One file, three writers, one reader:
  writes: local_control.py (technician phone UI, works offline)
          fleet_client.py  (applies `set_settings` commands from the server)
          onboard_service.py (applies [player] block from fleet-setup.toml once)
  reads:  fleet_player.py  (applies live via mpv IPC — no playback restart)

Settings are applied WITHOUT restarting mpv (rotation, image duration, volume
and mute are all runtime-settable properties), so a technician can rotate a
screen or slow down a slideshow mid-playback.

Schema (all keys optional in the file; load_settings() fills defaults):
  rotation          0 | 90 | 180 | 270   clockwise, applied to video AND images
  image_duration_s  seconds per still image in a slideshow (1–3600)
  volume_pct        0–200 (mpv scale, 100 = unity gain)
  muted             bool
  updated_at        ISO timestamp of last write (informational)
  updated_by        "local" | "server" | "setup" | "default" (informational)
"""
import json
import time
from pathlib import Path

SETTINGS_FILE = Path("/opt/fleet-media/player-settings.json")

DEFAULTS = {
    "rotation": 0,
    "image_duration_s": 10,
    "volume_pct": 100,
    "muted": False,
}

# Flip-only since v0.5: the sole real-world case is upside-down mounted
# screens, and arbitrary 90/270 rotation caused CPU-choppy playback on the
# old vo=drm path. Legacy values (90/270) coerce to 0.
VALID_ROTATIONS = (0, 180)


def _clamp(settings: dict) -> dict:
    """Coerce values into their valid ranges; drop anything unknown."""
    out = {}
    try:
        rot = int(settings.get("rotation", 0))
    except (TypeError, ValueError):
        rot = 0
    out["rotation"] = rot if rot in VALID_ROTATIONS else 0
    dur = settings.get("image_duration_s", DEFAULTS["image_duration_s"])
    try:
        out["image_duration_s"] = max(1, min(3600, int(float(dur))))
    except (TypeError, ValueError):
        out["image_duration_s"] = DEFAULTS["image_duration_s"]
    vol = settings.get("volume_pct", DEFAULTS["volume_pct"])
    try:
        out["volume_pct"] = max(0, min(200, int(float(vol))))
    except (TypeError, ValueError):
        out["volume_pct"] = DEFAULTS["volume_pct"]
    out["muted"] = bool(settings.get("muted", False))
    return out


def load_settings() -> dict:
    """Read settings with defaults filled in. Never raises."""
    merged = dict(DEFAULTS)
    if SETTINGS_FILE.exists():
        try:
            merged.update(json.loads(SETTINGS_FILE.read_text()))
        except Exception:
            pass
    return _clamp(merged)


def save_settings(patch: dict, updated_by: str = "local") -> dict:
    """Merge `patch` into the stored settings, atomically. Returns the result."""
    merged = load_settings()
    merged.update({k: v for k, v in patch.items() if k in DEFAULTS})
    merged = _clamp(merged)
    merged["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    merged["updated_by"] = updated_by
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = SETTINGS_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(merged, indent=2))
    tmp.rename(SETTINGS_FILE)
    return merged


def settings_mtime() -> float:
    """mtime of the settings file, 0.0 if absent. Cheap change detector."""
    try:
        return SETTINGS_FILE.stat().st_mtime
    except OSError:
        return 0.0
