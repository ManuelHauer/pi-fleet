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
#   FLEET_HOSTNAME_PREFIX  hostname prefix; each Pi becomes <prefix>-<3-hex
#                          checksum of its device id> (default: aef-pi)
#   FLEET_TAILSCALE_AUTHKEY  per-SD mesh key (optional)
#   FLEET_SETUP_FILE       path to a filled-in fleet-setup.toml to pre-prime
#                          venue Wi-Fi / label / playback settings
#   WIFI_SSID / WIFI_PASSWORD / DEVICE_LABEL / WIFI_HIDDEN=1   quick
#                          alternative to FLEET_SETUP_FILE — generates
#                          fleet-setup.toml inline (WIFI_HIDDEN for networks
#                          that don't broadcast their SSID). When WIFI_SSID is
#                          set, the card ALSO joins that network at the OS level
#                          on first boot, so the installer is online over Wi-Fi
#                          with NO Ethernet needed.
#   WIFI_COUNTRY           Wi-Fi regulatory domain for the OS join (default: AT)
#   FLEET_PI_PASSWORD      password for the 'pi' user. Needed when the image
#                          was flashed WITHOUT Raspberry Pi Imager's user
#                          provisioning (plain rpi-imager --cli / dd) —
#                          headless Bookworm/Trixie creates NO user otherwise
#                          and the fleet services (User=pi) can't run.
#   FLEET_SSH_PUBKEY       ssh public key line to authorize for pi (optional,
#                          installed with correct ownership at first boot)
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

# 2. userconf.txt (Bookworm+) — do NOT overwrite if Imager already wrote one.
# Without it a headless image boots with NO user account: no SSH login, and
# the fleet services (User=pi) cannot start. (Hardware-test finding.)
PI_HASH=""
if [ -f "$BOOT_MOUNT/userconf.txt" ]; then
  echo "  ✓ userconf.txt exists — leaving as-is"
elif [ -n "${FLEET_PI_PASSWORD:-}" ]; then
  if PI_HASH=$(openssl passwd -6 "$FLEET_PI_PASSWORD" 2>/dev/null) && [ "${PI_HASH:0:3}" = '$6$' ]; then
    :
  elif PI_HASH=$(python3 -c 'import crypt,sys;print(crypt.crypt(sys.argv[1],crypt.mksalt(crypt.METHOD_SHA512)))' "$FLEET_PI_PASSWORD" 2>/dev/null); then
    :
  else
    echo "ERROR: cannot hash FLEET_PI_PASSWORD (need openssl with 'passwd -6' or python3 with crypt)"; exit 1
  fi
  printf 'pi:%s\n' "$PI_HASH" > "$BOOT_MOUNT/userconf.txt"
  echo "  ✓ userconf.txt created (user 'pi')"
else
  echo "  ⚠ userconf.txt missing and FLEET_PI_PASSWORD not set —"
  echo "    OK only if the image was provisioned via Raspberry Pi Imager's settings."
fi

# 3. DISABLE the Pi OS auto-expand so setup_media_partition.sh (on the Pi)
#    can carve the FLEET-MEDIA partition out of the unclaimed space.
#    Two generations of the mechanism:
#      Bookworm:  init=/usr/lib/raspi-config/init_resize.sh
#      Trixie:    a bare 'resize' kernel arg consumed by a systemd service
CMDLINE="$BOOT_MOUNT/cmdline.txt"
if [ -f "$CMDLINE" ] && grep -qE "init_resize|(^| )resize( |$)" "$CMDLINE"; then
  cp "$CMDLINE" "$CMDLINE.bak-fleet"
  # shellcheck disable=SC2016
  sed -i.tmp -E 's# init=/usr/lib/raspi-config/init_resize\.sh##; s#(^| )resize( |$)#\1#; s#  +# #g' "$CMDLINE"
  rm -f "$CMDLINE.tmp"
  echo "  ✓ Auto-expand disabled in cmdline.txt (media partition needs the space)"
else
  echo "  ✓ cmdline.txt has no auto-expand hook (already removed?)"
fi

# 4. Mirror the repo layout into /boot/firmware/fleet/
FLEET_BOOT="$BOOT_MOUNT/fleet"
rm -rf "$FLEET_BOOT/client" "$FLEET_BOOT/deploy" 2>/dev/null || true
mkdir -p "$FLEET_BOOT/client" "$FLEET_BOOT/deploy"

# rsync, not cp -a: FAT32 can't take BSD flags/xattrs (cp -a dies with
# 'chflags: Invalid argument' under set -e) and we don't want __pycache__.
rsync -r --no-perms --no-owner --no-group \
      --exclude='__pycache__' --exclude='.DS_Store' --exclude='*.pyc' \
      "$CLIENT_DIR/" "$FLEET_BOOT/client/"
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
HOSTNAME_PREFIX="${FLEET_HOSTNAME_PREFIX:-aef-pi}"
cat > "$FLEET_BOOT/fleet-boot-config.json" <<EOF
{
  "server_url": "$SERVER_URL",
  "group": "$GROUP",
  "device_psk": "$DEVICE_PSK",
  "local_password": "$LOCAL_PW",
  "rootfs_gb": $ROOTFS_GB,
  "hostname_prefix": "$HOSTNAME_PREFIX"
}
EOF
echo "  ✓ Boot config written (server: $SERVER_URL, group: $GROUP, rootfs: ${ROOTFS_GB}G, hostnames: ${HOSTNAME_PREFIX}-xxx)"
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
    [ "${WIFI_HIDDEN:-0}" = "1" ] && echo "hidden = true"
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
  # No Imager firstrun.sh → the image was flashed plain. Generate a fully
  # self-contained one AND arm it ourselves via the kernel cmdline (the same
  # systemd.run mechanism Imager uses). It creates the user (userconf.txt is
  # processed by the same helper), installs the SSH key with CORRECT pi:pi
  # ownership (root-owned ~/.ssh breaks sshd StrictModes — hardware-test
  # finding), enables ssh, hands over to the fleet installer hook, then
  # disarms itself.
  {
    echo '#!/bin/bash'
    echo '# ARS_FLEET_INSTALL — self-contained first boot (generated by prepare_sd_card.sh)'
    echo 'set +e'
    if [ -n "$PI_HASH" ]; then
      printf 'id pi >/dev/null 2>&1 || { [ -x /usr/lib/userconf-pi/userconf ] && /usr/lib/userconf-pi/userconf pi %q; }\n' "$PI_HASH"
    fi
    if [ -n "${FLEET_SSH_PUBKEY:-}" ]; then
      printf 'install -o pi -g pi -m 700 -d /home/pi/.ssh\n'
      printf 'printf "%%s\\n" %q > /home/pi/.ssh/authorized_keys\n' "$FLEET_SSH_PUBKEY"
      printf 'chown pi:pi /home/pi/.ssh/authorized_keys; chmod 600 /home/pi/.ssh/authorized_keys\n'
    fi
    echo 'systemctl enable ssh 2>/dev/null || true'
    # OS-level Wi-Fi so the INSTALLER (which downloads packages) is online on
    # first boot WITHOUT Ethernet. The fleet's own onboarding (fleet-setup.toml)
    # only runs AFTER the install — too late to provide install-time internet.
    # This creates the NetworkManager "preconfigured" profile, exactly like
    # Raspberry Pi Imager's Wi-Fi settings do (that's why the Imager-flashed
    # Pi 4 never needed a cable).
    if [ -n "${WIFI_SSID:-}" ]; then
      WCC="${WIFI_COUNTRY:-AT}"
      printf 'rfkill unblock wifi 2>/dev/null || true\n'
      printf 'raspi-config nonint do_wifi_country %q 2>/dev/null || true\n' "$WCC"
      if [ "${WIFI_HIDDEN:-0}" = "1" ]; then
        printf 'if [ -x /usr/lib/raspberrypi-sys-mods/imager_custom ]; then /usr/lib/raspberrypi-sys-mods/imager_custom set_wlan -h %q %q %q; fi\n' "$WIFI_SSID" "${WIFI_PASSWORD:-}" "$WCC"
      else
        printf 'if [ -x /usr/lib/raspberrypi-sys-mods/imager_custom ]; then /usr/lib/raspberrypi-sys-mods/imager_custom set_wlan %q %q %q; fi\n' "$WIFI_SSID" "${WIFI_PASSWORD:-}" "$WCC"
      fi
      # Fallback for images without imager_custom
      printf 'command -v raspi-config >/dev/null && raspi-config nonint do_wifi_ssid_passphrase %q %q 0 %q 2>/dev/null || true\n' "$WIFI_SSID" "${WIFI_PASSWORD:-}" "${WIFI_HIDDEN:-0}"
    fi
    echo 'if [ -f /boot/firmware/fleet/pi_firstboot_fleet.sh ]; then bash /boot/firmware/fleet/pi_firstboot_fleet.sh || true;'
    echo 'elif [ -f /boot/fleet/pi_firstboot_fleet.sh ]; then bash /boot/fleet/pi_firstboot_fleet.sh || true; fi'
    echo '# disarm: strip the systemd.run hook and remove this script'
    echo "sed -i 's| systemd.run=[^ ]*||g; s| systemd.run_success_action=[^ ]*||g; s| systemd.unit=kernel-command-line.target||g' /boot/firmware/cmdline.txt 2>/dev/null || true"
    echo "sed -i 's| systemd.run=[^ ]*||g; s| systemd.run_success_action=[^ ]*||g; s| systemd.unit=kernel-command-line.target||g' /boot/cmdline.txt 2>/dev/null || true"
    echo 'rm -f /boot/firmware/firstrun.sh /boot/firstrun.sh'
    echo 'exit 0'
  } > "$BOOT_MOUNT/firstrun.sh"
  chmod +x "$BOOT_MOUNT/firstrun.sh" || true

  # Arm the hook (idempotent: strip any previous instance first)
  python3 - "$CMDLINE" <<'PY'
import re, sys
p = sys.argv[1]
line = open(p).read().strip()
line = re.sub(r'\s*systemd\.run(_success_action)?=\S+', '', line)
line = re.sub(r'\s*systemd\.unit=kernel-command-line\.target', '', line)
line += ' systemd.run=/boot/firmware/firstrun.sh systemd.run_success_action=reboot systemd.unit=kernel-command-line.target'
open(p, 'w').write(line.strip() + '\n')
PY
  echo "  ✓ Self-contained firstrun.sh created + armed in cmdline.txt"
  [ -z "$PI_HASH" ] && [ ! -f "$BOOT_MOUNT/userconf.txt" ] && \
    echo "  ⚠ No user will exist on this image (set FLEET_PI_PASSWORD) — services need user 'pi'!"
fi

echo ""
echo "✅ SD card prepared!"
echo "   First boot (with Ethernet at HQ, or Wi-Fi preseed): installs packages,"
echo "   creates the FLEET-MEDIA partition, then reboots into fleet operation."
