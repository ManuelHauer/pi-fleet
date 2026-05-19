#!/bin/bash
# Triggered by udev when a USB filesystem appears.
# Mounts the stick, verifies it has fleet media, copies into a versioned release dir,
# atomically swaps the `current` symlink, pins the device, signals fleet-player.
# Idempotent: rerun = swap content, no leftover state.

set -euo pipefail

DEVICE="${1:-}"
[[ -z "$DEVICE" ]] && { echo "usage: $0 /dev/sdX1" >&2; exit 2; }

MEDIA_BASE="/opt/fleet-media"
STATE_FILE="$MEDIA_BASE/state.json"
PLAYLIST_FILE="$MEDIA_BASE/playlist.current"
RESTART_TRIGGER="$MEDIA_BASE/.restart-player"
LOG="/var/log/fleet-usb-sync.log"

log() { echo "[$(date -Is)] $*" | tee -a "$LOG"; }

# Mount the stick
MNT=$(mktemp -d /tmp/fleet-usb.XXXX)
trap "umount -l '$MNT' 2>/dev/null || true; rmdir '$MNT' 2>/dev/null || true" EXIT

if ! mount -o ro "$DEVICE" "$MNT" 2>>"$LOG"; then
    log "ERROR: mount $DEVICE failed"; exit 3
fi

# Find a fleet/ directory or fall back to root if it has media files
SRC=""
if [[ -d "$MNT/fleet" ]]; then
    SRC="$MNT/fleet"
elif compgen -G "$MNT/*.mp4" > /dev/null \
  || compgen -G "$MNT/*.mkv" > /dev/null \
  || compgen -G "$MNT/*.mov" > /dev/null \
  || compgen -G "$MNT/*.webm" > /dev/null \
  || compgen -G "$MNT/*.mp3" > /dev/null \
  || compgen -G "$MNT/*.wav" > /dev/null \
  || compgen -G "$MNT/*.png" > /dev/null \
  || compgen -G "$MNT/*.jpg" > /dev/null \
  || compgen -G "$MNT/*.jpeg" > /dev/null; then
    SRC="$MNT"
fi

if [[ -z "$SRC" ]]; then
    log "No fleet/ dir or media files on $DEVICE — ignoring (not a fleet stick)"
    exit 0
fi

log "USB media found at $SRC"

# Hash the content for the release ID (just names + sizes; cheap, stable)
HASH=$(find "$SRC" -maxdepth 1 -type f -printf '%f %s\n' | sort | sha256sum | cut -c1-12)
RELEASE_DIR="$MEDIA_BASE/releases/usb-$HASH"

if [[ -d "$RELEASE_DIR" ]]; then
    log "Release usb-$HASH already exists — re-swapping to it"
else
    log "Creating release usb-$HASH"
    rm -rf "$RELEASE_DIR.tmp"
    mkdir -p "$RELEASE_DIR.tmp"
    cp -a "$SRC"/. "$RELEASE_DIR.tmp/"
    mv "$RELEASE_DIR.tmp" "$RELEASE_DIR"
fi

# Atomic symlink swap (-T treats target as plain file, not dir-into)
ln -sfn "$RELEASE_DIR" "$MEDIA_BASE/current.new"
mv -T "$MEDIA_BASE/current.new" "$MEDIA_BASE/current"

# Update state.json (preserve other fields, set pinned + current_version)
python3 - <<PYEOF
import json, time
from pathlib import Path
sf = Path("$STATE_FILE")
state = {}
if sf.exists():
    try:
        state = json.loads(sf.read_text())
    except Exception:
        state = {}
state["current_version"] = "usb-$HASH"
state["pinned"] = True
state["pinned_source"] = "usb"
state["pinned_at"] = time.time()
sf.parent.mkdir(parents=True, exist_ok=True)
sf.write_text(json.dumps(state, indent=2))
PYEOF

# Write playlist for fleet-player
python3 - <<PYEOF
from pathlib import Path
cur = Path("$MEDIA_BASE/current")
out = Path("$PLAYLIST_FILE")
files = sorted(p for p in cur.iterdir() if p.is_file() and not p.name.startswith("."))
out.write_text("\n".join(str(p) for p in files) + "\n")
PYEOF

# Signal fleet-player
touch "$RESTART_TRIGGER"

# Cleanup: keep only the 3 newest releases, never delete the one currently in use
CURRENT_LINK=$(readlink "$MEDIA_BASE/current")
find "$MEDIA_BASE/releases" -maxdepth 1 -mindepth 1 -type d \
    -not -path "$CURRENT_LINK" -printf "%T@ %p\n" | sort -n | head -n -2 \
    | cut -d' ' -f2- | while read -r d; do
    log "Cleaning old release: $d"
    rm -rf "$d"
done

log "USB sync complete: release usb-$HASH, pinned"
