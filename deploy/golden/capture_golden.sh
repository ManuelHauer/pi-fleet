#!/bin/bash
# Capture a golden fleet image from a fully-provisioned master Pi (run on macOS).
#
#   ./capture_golden.sh <master-host-or-ip> <diskN> [out.img.gz]
#
# Flow:
#   1. SSH to the master, run generalize.sh (sysprep), power it off.
#   2. You move the master's SD card into this Mac's reader.
#   3. dd the card -> gzip -> aef-golden-<date>.img.gz.
#
# The result flashes onto same-or-larger cards with flash_clone.sh. Every clone
# boots with packages pre-installed and re-derives its own identity + Wi-Fi.
#
# SSH note: pass the same key/user you provisioned with, via env:
#   SSH_OPTS="-i ~/.ssh/pitest_key -o IdentitiesOnly=yes" ./capture_golden.sh ...
set -euo pipefail

HOST="${1:?Usage: $0 <master-host> <diskN> [out.img.gz]}"
DISK="${2:?need the card diskN, e.g. disk4, AFTER you move the card over}"
OUT="${3:-aef-golden-$(date +%Y%m%d).img.gz}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SSH_USER="${SSH_USER:-pi}"
SSH_OPTS="${SSH_OPTS:--o StrictHostKeyChecking=accept-new}"

echo "== Step 1/3: generalize the master ($HOST) =="
echo "This stops fleet, strips per-device identity, and powers the Pi off."
read -r -p "Master is up and reachable? [enter to continue, Ctrl-C to abort] " _
# shellcheck disable=SC2086
ssh $SSH_OPTS "${SSH_USER}@${HOST}" 'sudo bash -s' < "$SCRIPT_DIR/generalize.sh"

echo
echo "== Step 2/3: move the card =="
echo "Wait for the Pi's LEDs to go dark, remove its SD card, put it in this Mac."
read -r -p "Card is in the Mac as /dev/$DISK? [enter to continue] " _

INFO=$(/usr/sbin/diskutil info "/dev/$DISK" 2>/dev/null || true)
echo "$INFO" | grep -q "Device Node" || { echo "ERROR: /dev/$DISK not found"; exit 1; }
echo "$INFO" | grep -q "Internal:.*Yes" && { echo "ERROR: refusing internal disk"; exit 1; }

echo "== Step 3/3: image /dev/$DISK -> $OUT =="
/usr/sbin/diskutil unmountDisk "/dev/$DISK"
# rdiskN = raw device = much faster
RDISK="/dev/r${DISK}"
echo "Reading card (this takes a while; gzip keeps the empty FLEET-MEDIA space small)..."
sudo dd if="$RDISK" bs=8m 2>/dev/null | gzip -1 > "$OUT"
sync
SIZE=$(du -h "$OUT" | cut -f1)
echo
echo "[OK] Golden image captured: $OUT ($SIZE)"
echo "   Flash clones with:  ./flash_clone.sh $OUT diskN  (+ WIFI_SSID etc. per card)"
