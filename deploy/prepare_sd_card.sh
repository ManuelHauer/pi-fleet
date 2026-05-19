#!/bin/bash
# Prepare an SD card's boot partition with fleet files.
# Usage: ./prepare_sd_card.sh /Volumes/bootfs
# Run AFTER flashing Raspberry Pi OS Lite with Raspberry Pi Imager.
set -e

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
  echo "  ⚠ userconf.txt missing — assumes Imager provisioning created one"
fi

# 3. Mirror the repo layout into /boot/firmware/fleet/
FLEET_BOOT="$BOOT_MOUNT/fleet"
rm -rf "$FLEET_BOOT/client" "$FLEET_BOOT/deploy" 2>/dev/null || true
mkdir -p "$FLEET_BOOT/client" "$FLEET_BOOT/deploy"

# Copy the entire client/ tree (fleet_client.py, fleet_player.py, identity.py,
# usb_sync.sh, local_control.py, *.service, onboarding/*).
cp -a "$CLIENT_DIR/." "$FLEET_BOOT/client/"
echo "  ✓ client/ tree copied"

# Copy deploy files the first-run script needs (udev rule)
cp "$SCRIPT_DIR/99-fleet-usb.rules" "$FLEET_BOOT/deploy/"
echo "  ✓ udev rule copied"

# Copy first-run installer at the top of the fleet dir
cp "$SCRIPT_DIR/golden_image_firstrun.sh" "$FLEET_BOOT/"
chmod +x "$FLEET_BOOT/golden_image_firstrun.sh"
echo "  ✓ First-run installer copied"

# 4. Boot config — edit later if needed
if [ ! -f "$FLEET_BOOT/fleet-boot-config.json" ]; then
  cat > "$FLEET_BOOT/fleet-boot-config.json" << 'EOF'
{
  "server_url": "http://169.254.180.14:8550",
  "group": "default",
  "device_psk": "aec-device-psk-2026",
  "local_password": "aec2026"
}
EOF
  echo "  ✓ Boot config written"
else
  echo "  ✓ Boot config already present — leaving as-is"
fi

# 5. Tailscale authkey (per-SD, baked at flash time by flash_and_prepare_sd.sh)
# If FLEET_TAILSCALE_AUTHKEY is set, write it; otherwise leave whatever's already there.
if [ -n "${FLEET_TAILSCALE_AUTHKEY:-}" ]; then
  printf "%s" "$FLEET_TAILSCALE_AUTHKEY" > "$FLEET_BOOT/tailscale-authkey"
  chmod 600 "$FLEET_BOOT/tailscale-authkey"
  echo "  ✓ Tailscale authkey baked"
fi

# 6. Patch firstrun.sh (Imager) to invoke our installer once
MARKER="ARS_FLEET_INSTALL"
if [ -f "$BOOT_MOUNT/firstrun.sh" ]; then
  if grep -q "$MARKER" "$BOOT_MOUNT/firstrun.sh"; then
    echo "  ✓ firstrun.sh already patched"
  else
    TS=$(date '+%Y%m%d-%H%M%S')
    cp "$BOOT_MOUNT/firstrun.sh" "$BOOT_MOUNT/firstrun.sh.bak-$TS"

    BOOT_MOUNT="$BOOT_MOUNT" /usr/bin/python3 - <<'PY'
import os, re
from pathlib import Path

p = Path(os.environ["BOOT_MOUNT"]) / "firstrun.sh"
text = p.read_text(errors="ignore")
marker = "ARS_FLEET_INSTALL"
if marker in text:
    raise SystemExit(0)

block = (
    "\n# ARS_FLEET_INSTALL\n"
    "# Install Ars Fleet system from boot partition\n"
    "if [ -x /boot/firmware/fleet/golden_image_firstrun.sh ]; then\n"
    "  bash /boot/firmware/fleet/golden_image_firstrun.sh || true\n"
    "elif [ -x /boot/fleet/golden_image_firstrun.sh ]; then\n"
    "  bash /boot/fleet/golden_image_firstrun.sh || true\n"
    "fi\n"
)

lines = text.splitlines(True)
insert_at = None
for i, line in enumerate(lines):
    if re.search(r"\breboot\b", line):
        insert_at = i; break
    if "rm -f" in line and "firstrun.sh" in line:
        insert_at = i; break
if insert_at is None:
    insert_at = len(lines)
lines.insert(insert_at, block)
p.write_text("".join(lines))
PY
    chmod +x "$BOOT_MOUNT/firstrun.sh" || true
    echo "  ✓ Patched firstrun.sh"
  fi
else
  cat > "$BOOT_MOUNT/firstrun.sh" <<'FIRSTRUN'
#!/bin/bash
# ARS_FLEET_INSTALL
if [ -x /boot/firmware/fleet/golden_image_firstrun.sh ]; then
  bash /boot/firmware/fleet/golden_image_firstrun.sh || true
elif [ -x /boot/fleet/golden_image_firstrun.sh ]; then
  bash /boot/fleet/golden_image_firstrun.sh || true
fi
FIRSTRUN
  chmod +x "$BOOT_MOUNT/firstrun.sh" || true
  echo "  ⚠ Created firstrun.sh (image may need Imager provisioning to actually execute it)"
fi

echo ""
echo "✅ SD card prepared!"
