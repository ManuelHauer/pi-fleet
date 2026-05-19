#!/bin/bash
# Flash Raspberry Pi OS Lite to an SD card and inject Ars Fleet files.
#
# Usage:
#   ./flash_and_prepare_sd.sh disk2
#
# WARNING: DESTRUCTIVE. This will erase the target disk.

set -euo pipefail

DISK="${1:?Usage: $0 <diskN> (e.g. disk2)}"
RPI_IMAGER="/Applications/Raspberry Pi Imager.app/Contents/MacOS/rpi-imager"
OS_URL="https://downloads.raspberrypi.com/raspios_lite_armhf_latest"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PREP_SCRIPT="$SCRIPT_DIR/prepare_sd_card.sh"
FIRST_RUN="$SCRIPT_DIR/pi_firstboot_fleet.sh"

if [ ! -x "$RPI_IMAGER" ]; then
  echo "ERROR: Raspberry Pi Imager CLI not found at: $RPI_IMAGER"
  exit 1
fi

INFO=$(/usr/sbin/diskutil info "/dev/$DISK" 2>/dev/null || true)
if ! echo "$INFO" | grep -q "Device Node"; then
  echo "ERROR: /dev/$DISK not found"
  exit 1
fi

if echo "$INFO" | grep -q "Internal:.*Yes"; then
  echo "ERROR: Refusing to flash internal disk: $DISK"
  exit 1
fi

echo "🎬 Flashing Raspberry Pi OS Lite to /dev/$DISK"
/usr/sbin/diskutil unmountDisk "/dev/$DISK" >/dev/null || true

# Flash image (downloads .xz via redirect)
"$RPI_IMAGER" --cli --disable-eject --first-run-script "$FIRST_RUN" "$OS_URL" "/dev/$DISK"

echo "✅ Flash complete. Mounting boot partition…"
# Boot partition is typically s1
/usr/sbin/diskutil mount "/dev/${DISK}s1" >/dev/null || true

# Determine mountpoint
MOUNTPOINT=$(/usr/sbin/diskutil info -plist "/dev/${DISK}s1" | /usr/bin/python3 - <<'PY'
import sys, plistlib
p = plistlib.load(sys.stdin.buffer)
print(p.get('MountPoint',''))
PY
)

if [ -z "$MOUNTPOINT" ] || [ ! -d "$MOUNTPOINT" ]; then
  echo "ERROR: Could not determine boot mountpoint for /dev/${DISK}s1"
  /usr/sbin/diskutil list "/dev/$DISK" || true
  exit 1
fi

echo "Boot mounted at: $MOUNTPOINT"

# Inject fleet files + patch firstrun (safe)
bash "$PREP_SCRIPT" "$MOUNTPOINT"

echo "✅ Ejecting SD card"
/usr/sbin/diskutil eject "/dev/$DISK" >/dev/null || true

echo "DONE"
