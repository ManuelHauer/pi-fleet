#!/bin/bash
# Flash Raspberry Pi OS Lite to an SD card and inject Ars Fleet files +
# a single-use Tailscale authkey minted from Headscale.
#
# Usage:
#   HEADSCALE_URL=https://hs.example.com HEADSCALE_TOKEN=... \
#     ./flash_and_prepare_sd.sh disk2
#
# WARNING: DESTRUCTIVE. This will erase the target disk.

set -euo pipefail

DISK="${1:?Usage: $0 <diskN> (e.g. disk2)}"
RPI_IMAGER="/Applications/Raspberry Pi Imager.app/Contents/MacOS/rpi-imager"
OS_URL="https://downloads.raspberrypi.com/raspios_lite_armhf_latest"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PREP_SCRIPT="$SCRIPT_DIR/prepare_sd_card.sh"
FIRST_RUN="$SCRIPT_DIR/pi_firstboot_fleet.sh"

# ── Headscale: mint a per-SD single-use authkey ──
# Optional but recommended. If HEADSCALE_URL is unset we skip and the Pi will
# come up without mesh; it will still work as a USB-only kiosk.
TAILSCALE_AUTHKEY=""
if [ -n "${HEADSCALE_URL:-}" ] && [ -n "${HEADSCALE_TOKEN:-}" ]; then
  HS_USER="${HEADSCALE_USER:-fleet}"
  HS_EXPIRY="${HEADSCALE_EXPIRY:-24h}"
  echo "🔑 Requesting Tailscale preauthkey from Headscale ($HEADSCALE_URL, user=$HS_USER)"
  body=$(printf '{"user":"%s","reusable":false,"ephemeral":true,"expiration":"%s"}' \
                "$HS_USER" "$HS_EXPIRY")
  resp=$(curl -fsSL -X POST \
    -H "Authorization: Bearer $HEADSCALE_TOKEN" \
    -H "Content-Type: application/json" \
    --data "$body" \
    "$HEADSCALE_URL/api/v1/preauthkey")
  TAILSCALE_AUTHKEY=$(printf "%s" "$resp" | /usr/bin/python3 -c \
    'import sys, json; d=json.load(sys.stdin); k=d.get("preAuthKey",{}); print(k.get("key",""))')
  if [ -z "$TAILSCALE_AUTHKEY" ]; then
    echo "⚠ Headscale returned no key; response was:"; echo "$resp"
    exit 2
  fi
  echo "  ✓ Got single-use, ephemeral authkey ($HS_EXPIRY validity)"
else
  echo "ℹ HEADSCALE_URL/HEADSCALE_TOKEN unset — skipping mesh-key provisioning."
  echo "  Pi will boot but will not auto-join the tailnet."
fi

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

"$RPI_IMAGER" --cli --disable-eject --first-run-script "$FIRST_RUN" "$OS_URL" "/dev/$DISK"

echo "✅ Flash complete. Mounting boot partition…"
/usr/sbin/diskutil mount "/dev/${DISK}s1" >/dev/null || true

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

# prepare_sd_card.sh consumes FLEET_TAILSCALE_AUTHKEY from the env
FLEET_TAILSCALE_AUTHKEY="$TAILSCALE_AUTHKEY" bash "$PREP_SCRIPT" "$MOUNTPOINT"

echo "✅ Ejecting SD card"
/usr/sbin/diskutil eject "/dev/$DISK" >/dev/null || true

echo "DONE"
