#!/bin/bash
# Triggered by udev when a USB drive is inserted.
# Scans the USB drive for media files, copies them to the fleet media dir,
# updates the local manifest, and tells the local daemon to restart playback.

LOG_FILE="/var/log/fleet-usb-sync.log"
exec >> "$LOG_FILE" 2>&1

echo "[$(date)] USB Drive inserted/triggered."

# Allow some time for mount to stabilize
sleep 3

# Find where the drive is mounted
# Depending on OS config, usbmount or udisks might mount it under /media or /run/media
MOUNT_PATH=$(findmnt -n -o TARGET -S "$1" 2>/dev/null || echo "")

if [ -z "$MOUNT_PATH" ]; then
    echo "Could not determine mount point for $1. Looking in /media..."
    # Fallback heuristic
    MOUNT_PATH=$(ls -d /media/*/* 2>/dev/null | head -n 1)
fi

if [ -z "$MOUNT_PATH" ] || [ ! -d "$MOUNT_PATH" ]; then
    echo "No valid mount point found. Exiting."
    exit 1
fi

echo "Scanning mount point: $MOUNT_PATH"

MEDIA_DIR="/opt/fleet-media"
mkdir -p "$MEDIA_DIR"

# File extensions to look for
EXTENSIONS="mp4|mkv|mov|avi|webm|m4v|mp3|wav|flac|aac|m4a|ogg|jpg|jpeg|png|webp|gif"

# Find media files on the USB stick (case insensitive)
# Use find to list files matching extensions
FOUND_FILES=$(find "$MOUNT_PATH" -type f -regextype posix-extended -iregex ".*\.($EXTENSIONS)$")

if [ -z "$FOUND_FILES" ]; then
    echo "No media files found on USB. Exiting."
    exit 0
fi

echo "Found media files:"
echo "$FOUND_FILES"

# Clean out old media (copy mode REPLACE behavior)
echo "Wiping old media from $MEDIA_DIR..."
rm -f "$MEDIA_DIR"/*

# Copy new files over
echo "Copying files to $MEDIA_DIR..."
while IFS= read -r file; do
    cp -v "$file" "$MEDIA_DIR/"
done <<< "$FOUND_FILES"

echo "Copy complete. Rebuilding local manifest..."

# Generate a synthetic offline manifest so the client doesn't delete the files next time it polls
MANIFEST_FILE="/var/lib/fleet-client/manifest.json"
mkdir -p /var/lib/fleet-client

echo '{"version": "usb-offline-sync", "files": [' > "$MANIFEST_FILE"
FIRST=1
for f in "$MEDIA_DIR"/*; do
    if [ "$FIRST" -eq 1 ]; then
        FIRST=0
    else
        echo ',' >> "$MANIFEST_FILE"
    fi
    FILENAME=$(basename "$f")
    # Python one-liner to get sha256 checksum safely
    CHECKSUM=$(python3 -c "import hashlib; print(hashlib.sha256(open('$f','rb').read()).hexdigest())")
    SIZE=$(stat -c%s "$f")
    
    echo "  {\"id\": \"usb-$CHECKSUM\", \"filename\": \"$FILENAME\", \"checksum\": \"$CHECKSUM\", \"size\": $SIZE}" >> "$MANIFEST_FILE"
done
echo ']}' >> "$MANIFEST_FILE"

echo "Manifest rebuilt."

# Restart the fleet client service to pick up the new local manifest and restart mpv
echo "Restarting fleet-client service..."
systemctl restart fleet-client.service

echo "[$(date)] USB Sync complete!"
