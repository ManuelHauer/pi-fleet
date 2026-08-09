#!/bin/bash
# Flash a golden fleet image onto a new SD card and stamp in THIS card's
# per-device config (venue Wi-Fi, label, group). Run on macOS.
#
#   WIFI_SSID="VenueNet" WIFI_PASSWORD="secret" \
#   DEVICE_LABEL="OK Linz ? left screen" FLEET_GROUP="ok-linz" \
#     ./flash_clone.sh aef-golden-20260708.img.gz disk4
#
# The image already has packages + fleet installed and was generalized, so the
# clone boots WITHOUT re-installing (~90 s to ready), re-derives its own unique
# identity/hostname, and onboards to the Wi-Fi you stamp here. No internet
# needed at the venue.
#
# Per-card env (all optional except the image + disk):
#   WIFI_SSID / WIFI_PASSWORD / WIFI_HIDDEN=1 / WIFI_COUNTRY   venue Wi-Fi
#   WIFI_BACKUP_SSID / WIFI_BACKUP_PASSWORD / WIFI_BACKUP_HIDDEN=1   fallback Wi-Fi
#   DEVICE_LABEL / FLEET_GROUP / DEVICE_LOCATION               dashboard fields
#   FLEET_SETUP_FILE   use a ready-made fleet-setup.toml instead of the above
set -euo pipefail

IMG="${1:?Usage: $0 <golden.img[.gz]> <diskN>}"
DISK="${2:?need target diskN (e.g. disk4) ? WILL BE ERASED}"
[ -f "$IMG" ] || { echo "ERROR: image $IMG not found"; exit 1; }

INFO=$(/usr/sbin/diskutil info "/dev/$DISK" 2>/dev/null || true)
echo "$INFO" | grep -q "Device Node" || { echo "ERROR: /dev/$DISK not found"; exit 1; }
echo "$INFO" | grep -q "Internal:.*Yes" && { echo "ERROR: refusing internal disk"; exit 1; }

echo "[!] This ERASES /dev/$DISK and writes $IMG."
read -r -p "Type the disk id to confirm (e.g. $DISK): " confirm
[ "$confirm" = "$DISK" ] || { echo "aborted"; exit 1; }

echo "== Writing image -> /dev/$DISK =="
/usr/sbin/diskutil unmountDisk "/dev/$DISK"
RDISK="/dev/r${DISK}"

echo "== Writing sparse image -> /dev/$DISK =="
# gzip -dc "$IMG" | sudo dd of="$RDISK" bs=8m 2>/dev/null
# Use sparse writes: the golden image has zeroed empty space and will write
# many times faster than a literal 29 GB copy.
if command -v bmaptool >/dev/null 2>&1 && [ -f "${IMG%.gz}.bmap" ]; then
  echo "  using bmaptool with ${IMG%.gz}.bmap"
  bmaptool copy --bmap "${IMG%.gz}.bmap" "$IMG" "$RDISK"
else
  case "$IMG" in
    *.gz) gzip -dc "$IMG" | sudo dd of="$RDISK" bs=8m conv=sparse,fsync 2>/dev/null ;;
    *)    sudo dd if="$IMG" of="$RDISK" bs=8m conv=sparse,fsync 2>/dev/null ;;
  esac
fi
sync
echo "  ok image written"

echo "== Stamping per-card config =="
/usr/sbin/diskutil mountDisk "/dev/$DISK" >/dev/null 2>&1 || true
sleep 2
BOOT=""
for m in /Volumes/bootfs /Volumes/boot; do [ -d "$m" ] && BOOT="$m" && break; done
[ -z "$BOOT" ] && { echo "ERROR: boot partition didn't mount ? clone still works but has the MASTER's fleet-setup.toml"; exit 1; }

if [ -n "${FLEET_SETUP_FILE:-}" ]; then
  cp "$FLEET_SETUP_FILE" "$BOOT/fleet-setup.toml"
  echo "  ok fleet-setup.toml from $FLEET_SETUP_FILE"
elif [ -n "${WIFI_SSID:-}" ]; then
  {
    echo "[[wifi]]"
    echo "ssid = \"${WIFI_SSID}\""
    echo "password = \"${WIFI_PASSWORD:-}\""
    [ "${WIFI_HIDDEN:-0}" = "1" ] && echo "hidden = true"
    [ -n "${WIFI_COUNTRY:-}" ] && echo "country = \"${WIFI_COUNTRY}\""
    if [ -n "${WIFI_BACKUP_SSID:-}" ]; then
      echo ""
      echo "[[wifi]]"
      echo "ssid = \"${WIFI_BACKUP_SSID}\""
      echo "password = \"${WIFI_BACKUP_PASSWORD:-}\""
      [ "${WIFI_BACKUP_HIDDEN:-0}" = "1" ] && echo "hidden = true"
    fi
    echo ""
    echo "[device]"
    [ -n "${DEVICE_LABEL:-}" ] && echo "label = \"${DEVICE_LABEL}\""
    [ -n "${FLEET_GROUP:-}" ] && echo "group = \"${FLEET_GROUP}\""
    [ -n "${DEVICE_LOCATION:-}" ] && echo "location = \"${DEVICE_LOCATION}\""
  } > "$BOOT/fleet-setup.toml"
  echo "  ok fleet-setup.toml written (primary: $WIFI_SSID, backup: ${WIFI_BACKUP_SSID:-none}, label: ${DEVICE_LABEL:-none})"
else
  echo "  ? No Wi-Fi stamped ? clone will open the AEC-PI-XXXX setup hotspot at the venue."
  echo "    (Re-run with WIFI_SSID=... to bake venue Wi-Fi in.)"
fi

sync
/usr/sbin/diskutil eject "/dev/$DISK" >/dev/null 2>&1 || true
echo ""
echo "[OK] Clone ready. Put the card in a Pi, power it on ? no install, no cable:"
echo "   boots -> re-derives its identity -> joins Wi-Fi -> registers (~90 s)."
