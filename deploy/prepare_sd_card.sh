#!/bin/bash
# Prepare an SD card's boot partition with fleet files.
# Usage: ./prepare_sd_card.sh /Volumes/bootfs
# Run AFTER flashing Raspberry Pi OS Lite with Raspberry Pi Imager.
set -e

BOOT_MOUNT="${1:?Usage: $0 /Volumes/bootfs}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CLIENT_DIR="$SCRIPT_DIR/../client"

if [ ! -d "$BOOT_MOUNT" ]; then
  echo "ERROR: Boot partition not found at $BOOT_MOUNT"
  exit 1
fi

echo "🎬 Preparing SD card at $BOOT_MOUNT"

# 1. Enable SSH (harmless if already enabled)
touch "$BOOT_MOUNT/ssh"
echo "  ✓ SSH enabled marker written"

# 2. userconf.txt (Bookworm+)
# IMPORTANT: do NOT overwrite if it already exists (Imager likely created it).
if [ -f "$BOOT_MOUNT/userconf.txt" ]; then
  echo "  ✓ userconf.txt exists — leaving as-is"
else
  echo "  ⚠ userconf.txt missing — leaving unchanged (assumes SD was flashed with a user via Raspberry Pi Imager)"
  echo "    If needed: echo 'pi:\$(openssl passwd -6 <password>)' > $BOOT_MOUNT/userconf.txt"
fi

# 3. Create fleet directory on boot partition
FLEET_BOOT="$BOOT_MOUNT/fleet"
mkdir -p "$FLEET_BOOT/onboarding/templates"

# 4. Copy fleet client + local control
cp "$CLIENT_DIR/fleet_client.py" "$FLEET_BOOT/"
cp "$CLIENT_DIR/local_control.py" "$FLEET_BOOT/"
cp "$CLIENT_DIR/fleet-client.service" "$FLEET_BOOT/"
cp "$CLIENT_DIR/usb_sync.sh" "$FLEET_BOOT/"
cp "$SCRIPT_DIR/99-fleet-usb.rules" "$FLEET_BOOT/"
cp "$CLIENT_DIR/fleet-local-control.service" "$FLEET_BOOT/"
echo "  ✓ Fleet client + local control copied"

# 5. Copy onboarding system
cp "$CLIENT_DIR/onboarding/onboard_service.py" "$FLEET_BOOT/onboarding/"
cp "$CLIENT_DIR/onboarding/captive_portal.py" "$FLEET_BOOT/onboarding/"
cp "$CLIENT_DIR/onboarding/wifi_manager.py" "$FLEET_BOOT/onboarding/"
cp "$CLIENT_DIR/onboarding/ap_manager.py" "$FLEET_BOOT/onboarding/"
cp "$CLIENT_DIR/onboarding/hdmi_status.py" "$FLEET_BOOT/onboarding/"
cp "$CLIENT_DIR/onboarding/fleet-onboard.service" "$FLEET_BOOT/onboarding/"
cp "$CLIENT_DIR/onboarding/templates/"*.html "$FLEET_BOOT/onboarding/templates/"
echo "  ✓ Onboarding system copied"

# 6. Copy first-run installer script
cp "$SCRIPT_DIR/golden_image_firstrun.sh" "$FLEET_BOOT/"
echo "  ✓ First-run installer copied"

# 7. Write boot config (edit later if needed)
# NOTE: local_password controls the local tech UI on port 8080.
cat > "$FLEET_BOOT/fleet-boot-config.json" << 'EOF'
{
  "server_url": "http://169.254.180.14:8550",
  "group": "default",
  "device_psk": "aec-device-psk-2026",
  "local_password": "aec2026"
}
EOF
echo "  ✓ Boot config written"

# 8. Ensure fleet install runs on first boot
# Raspberry Pi Imager (Bookworm) typically generates a firstrun.sh for provisioning.
# We MUST NOT overwrite it; instead we inject our install block.
MARKER="ARS_FLEET_INSTALL"
FLEET_BLOCK=$(cat <<'BLOCK'

# ARS_FLEET_INSTALL
# Install Ars Fleet system from boot partition
if [ -x /boot/firmware/fleet/golden_image_firstrun.sh ]; then
  bash /boot/firmware/fleet/golden_image_firstrun.sh || true
elif [ -x /boot/fleet/golden_image_firstrun.sh ]; then
  bash /boot/fleet/golden_image_firstrun.sh || true
fi
BLOCK
)

if [ -f "$BOOT_MOUNT/firstrun.sh" ]; then
  if grep -q "$MARKER" "$BOOT_MOUNT/firstrun.sh"; then
    echo "  ✓ firstrun.sh already patched for fleet install"
  else
    TS=$(date '+%Y%m%d-%H%M%S')
    cp "$BOOT_MOUNT/firstrun.sh" "$BOOT_MOUNT/firstrun.sh.bak-$TS"
    echo "  ✓ Backed up existing firstrun.sh → firstrun.sh.bak-$TS"

    BOOT_MOUNT="$BOOT_MOUNT" /usr/bin/python3 - <<'PY'
import os
from pathlib import Path
import re

p = Path(os.environ["BOOT_MOUNT"]) / "firstrun.sh"
text = p.read_text(errors="ignore")
marker = "ARS_FLEET_INSTALL"
if marker in text:
    raise SystemExit(0)

block = """\n# ARS_FLEET_INSTALL\n# Install Ars Fleet system from boot partition\nif [ -x /boot/firmware/fleet/golden_image_firstrun.sh ]; then\n  bash /boot/firmware/fleet/golden_image_firstrun.sh || true\nelif [ -x /boot/fleet/golden_image_firstrun.sh ]; then\n  bash /boot/fleet/golden_image_firstrun.sh || true\nfi\n"""

lines = text.splitlines(True)
# Insert before first reboot (or before self-delete) if present; else append.
insert_at = None
for i, line in enumerate(lines):
    if re.search(r"\breboot\b", line):
        insert_at = i
        break
    if "rm -f" in line and "firstrun.sh" in line:
        insert_at = i
        break

if insert_at is None:
    insert_at = len(lines)

lines.insert(insert_at, block)
p.write_text("".join(lines))
PY

    chmod +x "$BOOT_MOUNT/firstrun.sh" || true
    echo "  ✓ Patched existing firstrun.sh to run fleet installer"
  fi
else
  # No firstrun exists; create a minimal one (may require cmdline integration depending on image).
  cat > "$BOOT_MOUNT/firstrun.sh" <<'FIRSTRUN'
#!/bin/bash
# Minimal first-run hook (if the OS image uses firstrun.sh)
# ARS_FLEET_INSTALL
if [ -x /boot/firmware/fleet/golden_image_firstrun.sh ]; then
  bash /boot/firmware/fleet/golden_image_firstrun.sh || true
elif [ -x /boot/fleet/golden_image_firstrun.sh ]; then
  bash /boot/fleet/golden_image_firstrun.sh || true
fi
FIRSTRUN
  chmod +x "$BOOT_MOUNT/firstrun.sh" || true
  echo "  ⚠ Created firstrun.sh (image may need Raspberry Pi Imager provisioning to actually execute it)"
fi

echo ""
echo "✅ SD card prepared!"
echo ""
echo "Next steps:"
echo "  1) Eject SD card and boot the Pi"
echo "  2) First boot provisions OS then installs Ars Fleet"
echo "  3) If no Wi-Fi credentials: AP onboarding starts automatically"
