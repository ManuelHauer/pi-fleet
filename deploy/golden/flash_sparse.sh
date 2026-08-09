#!/bin/bash
# Flash a .img.gz golden image to an SD card as fast as possible.
#
# Usage:
#   ./flash_sparse.sh aef-golden-20260728.img.gz /dev/sdX
#
# Uses sparse writes: an image with lots of zeroed empty space writes in a
# small fraction of the time it would take to copy all 29 GB.
#
# If a .bmap file exists next to the image, it is used to skip zero blocks too.
set -euo pipefail

IMG="${1:?Usage: $0 <image.img[.gz]> <target-device>}"
DEV="${2:?need target block device e.g. /dev/sdc}"

[ -e "$IMG" ] || { echo "ERROR: image $IMG not found"; exit 1; }
if [ ! -b "$DEV" ]; then
  echo "ERROR: $DEV is not a block device"
  exit 1
fi

echo "[flash] target: $DEV"
echo "[flash] WARNING: this will erase $DEV"
read -r -p "Type YES to continue: " confirm
[ "$confirm" = "YES" ] || { echo "aborted"; exit 1; }

echo "[flash] unmounting..."
for p in "${DEV}"*; do
  umount "$p" 2>/dev/null || true
done

BMAP=""
if [ -f "${IMG%.gz}.bmap" ]; then
  BMAP="${IMG%.gz}.bmap"
fi

if command -v bmaptool >/dev/null 2>&1 && [ -n "$BMAP" ]; then
  echo "[flash] using bmaptool with $BMAP"
  bmaptool copy --bmap "$BMAP" "$IMG" "$DEV"
else
  echo "[flash] writing with sparse dd (no bmap file)"
  if [[ "$IMG" == *.gz ]]; then
    gzip -dc "$IMG" | sudo dd of="$DEV" bs=8M conv=sparse,fsync status=progress
  else
    sudo dd if="$IMG" of="$DEV" bs=8M conv=sparse,fsync status=progress
  fi
fi

sync
SIZE=$(ls -lh "$IMG" | awk '{print $5}')
echo "[flash] done. Image size: $SIZE. SD card ready."
echo "[flash] next: optionally expand FLEET-MEDIA on first boot or flash via Imager."
