#!/bin/bash
# Prepare a freshly-flashed SD card's boot partition with fleet files.
# Runs on the Mac/laptop AFTER flashing Raspberry Pi OS Lite.
#
# Usage:
#   ./prepare_sd_card.sh /Volumes/bootfs
#
# Optional env vars:
#   FLEET_SERVER_URL       fleet server base URL (e.g. https://fleet.example.org)
#   FLEET_DEVICE_PSK       device pre-shared key  (WARN if left on default)
#   FLEET_GROUP            default group          (default: "default")
#   FLEET_LOCAL_PASSWORD   local :8080 UI password
#   FLEET_ROOTFS_GB        rootfs size; the rest of the card becomes the
#                          FLEET-MEDIA partition (default: 8)
#   FLEET_TAILSCALE_AUTHKEY  per-SD mesh key (optional)
#   FLEET_SETUP_FILE       path to a filled-in fleet-setup.toml to pre-prime
#                          venue Wi-Fi / label / playback settings
#   WIFI_SSID / WIFI_PASSWORD / DEVICE_LABEL   quick alternative to
#                          FLEET_SETUP_FILE — generates fleet-setup.toml inline
set -euo pipefail

BOOT_MOUNT="${1:?Usage: $0 /Volumes/bootfs}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
CLIENT_DIR="$REPO_ROOT/client"

if [ ! -d "$BOOT_MOUNT" ]; then
  echo "ERROR: Boot partition not found at $BOOT_MOUNT"
  exit 1
fi

echo "🎬 Preparing SD card at $BOOT_MOUNT"

# 1. Enable SSH (harmless if already enabled)
touch "$BOOT_MOUNT/ssh"
echo "  ✓ SSH enabled marker written"

# 2. userconf.txt (Bookworm+) — do NOT overwrite if Imager already wrote one
if [ -f "$BOOT_MOUNT/userconf.txt" ]; then
  echo "  ✓ userconf.txt exists — leaving as-is"
else
  echo "  ⚠ userconf.txt missing — assumes Imager provisioning created a user"
fi

# 3. DISABLE the Pi OS auto-expand so setup_media_partition.sh (on the Pi)
#    can carve the FLEET-MEDIA partition out of the unclaimed space.
CMDLINE="$BOOT_MOUNT/cmdline.txt"
if [ -f "$CMDLINE" ] && grep -q "init_resize" "$CMDLINE"; then
  cp "$CMDLINE" "$CMDLINE.bak-fleet"
  # shellcheck disable=SC2016
  sed -i.tmp -E 's# init=/usr/lib/raspi-config/init_resize\.sh##' "$CMDLINE"
  rm -f "$CMDLINE.tmp"
  echo "  ✓ Auto-expand disabled in cmdline.txt (media partition needs the space)"
else
  echo "  ✓ cmdline.txt has no auto-expand hook (already removed?)"
fi

# 4. Mirror the repo layout into /boot/firmware/fleet/
FLEET_BOOT="$BOOT_MOUNT/fleet"
rm -rf "$FLEET_BOOT/client" "$FLEET_BOOT/deploy" 2>/dev/null || true
mkdir -p "$FLEET_BOOT/client" "$FLEET_BOOT/deploy"

cp -a "$CLIENT_DIR/." "$FLEET_BOOT/client/"
echo "  ✓ client/ tree copied"

cp "$SCRIPT_DIR/99-fleet-usb.rules"        "$FLEET_BOOT/deploy/"
cp "$SCRIPT_DIR/setup_media_partition.sh"  "$FLEET_BOOT/deploy/"
cp "$SCRIPT_DIR/golden_image_firstrun.sh"  "$FLEET_BOOT/"
cp "$SCRIPT_DIR/pi_firstboot_fleet.sh"     "$FLEET_BOOT/"
chmod +x "$FLEET_BOOT/golden_image_firstrun.sh" "$FLEET_BOOT/pi_firstboot_fleet.sh" \
         "$FLEET_BOOT/deploy/setup_media_partition.sh"
echo "  ✓ deploy scripts copied"

# 5. Boot config
SERVER_URL="${FLEET_SERVER_URL:-https://fleet.example.org}"
DEVICE_PSK="${FLEET_DEVICE_PSK:-change-me}"
GROUP="${FLEET_GROUP:-default}"
LOCAL_PW="${FLEET_LOCAL_PASSWORD:-aec2026}"
ROOTFS_GB="${FLEET_ROOTFS_GB:-8}"
cat > "$FLEET_BOOT/fleet-boot-config.json" <<EOF
{
  "server_url": "$SERVER_URL",
  "group": "$GROUP",
  "device_psk": "$DEVICE_PSK",
  "local_password": "$LOCAL_PW",
  "rootfs_gb": $ROOTFS_GB
}
EOF
echo "  ✓ Boot config written (server: $SERVER_URL, group: $GROUP, rootfs: ${ROOTFS_GB}G)"
if [ "$DEVICE_PSK" = "change-me" ]; then
  echo "  ⚠ FLEET_DEVICE_PSK not set — devices will use the placeholder PSK."
  echo "    Set it: FLEET_DEVICE_PSK=... ./prepare_sd_card.sh $BOOT_MOUNT"
fi

# 6. Pre-primed setup (venue Wi-Fi etc.)
if [ -n "${FLEET_SETUP_FILE:-}" ]; then
  cp "$FLEET_SETUP_FILE" "$BOOT_MOUNT/fleet-setup.toml"
  echo "  ✓ fleet-setup.toml copied from $FLEET_SETUP_FILE"
elif [ -n "${WIFI_SSID:-}" ]; then
  {
    echo "[wifi]"
    echo "ssid = \"${WIFI_SSID}\""
    echo "password = \"${WIFI_PASSWORD:-}\""
    if [ -n "${DEVICE_LABEL:-}" ] || [ -n "${FLEET_GROUP:-}" ]; then
      echo ""
      echo "[device]"
      [ -n "${DEVICE_LABEL:-}" ] && echo "label = \"${DEVICE_LABEL}\""
      echo "group = \"${GROUP}\""
    fi
  } > "$BOOT_MOUNT/fleet-setup.toml"
  echo "  ✓ fleet-setup.toml generated (SSID: $WIFI_SSID) — zero-touch onboarding"
else
  echo "  ℹ No Wi-Fi preseed (set WIFI_SSID/WIFI_PASSWORD or FLEET_SETUP_FILE)."
  echo "    Device will open the AEC-PI-XXXX setup hotspot at the venue."
fi

# 7. Tailscale authkey (per-SD; minted by flash_and_prepare_sd.sh)
if [ -n "${FLEET_TAILSCALE_AUTHKEY:-}" ]; then
  printf "%s" "$FLEET_TAILSCALE_AUTHKEY" > "$FLEET_BOOT/tailscale-authkey"
  echo "  ✓ Tailscale authkey baked"
fi

# 8. Hook into first boot: patch Imager's firstrun.sh (or create one) to run
#    pi_firstboot_fleet.sh, which installs the retrying installer unit.
MARKER="ARS_FLEET_INSTALL"
HOOK_BLOCK='
# ARS_FLEET_INSTALL
if [ -f /boot/firmware/fleet/pi_firstboot_fleet.sh ]; then
  bash /boot/firmware/fleet/pi_firstboot_fleet.sh || true
elif [ -f /boot/fleet/pi_firstboot_fleet.sh ]; then
  bash /boot/fleet/pi_firstboot_fleet.sh || true
fi
'
if [ -f "$BOOT_MOUNT/firstrun.sh" ]; then
  if grep -q "$MARKER" "$BOOT_MOUNT/firstrun.sh"; then
    echo "  ✓ firstrun.sh already patched"
  else
    cp "$BOOT_MOUNT/firstrun.sh" "$BOOT_MOUNT/firstrun.sh.bak-$(date '+%Y%m%d-%H%M%S')"
    BOOT_MOUNT="$BOOT_MOUNT" HOOK_BLOCK="$HOOK_BLOCK" /usr/bin/python3 - <<'PY'
import os, re
from pathlib import Path
p = Path(os.environ["BOOT_MOUNT"]) / "firstrun.sh"
text = p.read_text(errors="ignore")
block = os.environ["HOOK_BLOCK"]
lines = text.splitlines(True)
insert_at = None
for i, line in enumerate(lines):
    if re.search(r"\breboot\b", line) or ("rm -f" in line and "firstrun.sh" in line):
        insert_at = i
        break
if insert_at is None:
    insert_at = len(lines)
lines.insert(insert_at, block)
p.write_text("".join(lines))
PY
    echo "  ✓ Patched firstrun.sh"
  fi
else
  printf '#!/bin/bash\n%s\n' "$HOOK_BLOCK" > "$BOOT_MOUNT/firstrun.sh"
  chmod +x "$BOOT_MOUNT/firstrun.sh" || true
  echo "  ⚠ Created firstrun.sh (needs Imager provisioning to actually execute)"
fi

echo ""
echo "✅ SD card prepared!"
echo "   First boot (with Ethernet at HQ, or Wi-Fi preseed): installs packages,"
echo "   creates the FLEET-MEDIA partition, then reboots into fleet operation."
