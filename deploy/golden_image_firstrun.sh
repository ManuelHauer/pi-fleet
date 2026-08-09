#!/bin/bash
# Golden Image First-Run Installer (v0.3)
#
# Runs on the Pi via the fleet-firstrun systemd unit (installed by
# pi_firstboot_fleet.sh) on every boot UNTIL it completes successfully —
# a failed run (e.g. no network for apt) simply retries on the next boot.
#
# Does, in order:
#   1. machine-id hygiene for cloned images
#   2. apt packages (NetworkManager stack, mpv, PIL/Flask, exfat tools, Tailscale)
#   3. FLEET-MEDIA partition creation (setup_media_partition.sh)
#   4. fleet code install to /opt/fleet-client + config + systemd units
#
# FIRST BOOT NEEDS INTERNET (Ethernet at HQ, or Wi-Fi preseeded via
# fleet-setup.toml → NetworkManager can only be configured after this
# installer ran once, so use Ethernet for the very first boot of a fresh
# image, OR build one golden card and clone it).

set -euo pipefail
exec > >(tee -a /var/log/fleet-firstrun.log) 2>&1
echo "$(date): Fleet golden image first-run starting…"

# ── Root-cause guard: refuse to layer fleet on top of legacy media-loop images ──
# The only supported starting point is a freshly-flashed Raspberry Pi OS Lite
# (arm64) card. If someone runs this on a reused Debian/VLC looper image, the
# old autostart hooks will fight the fleet stack and create the exact VLC-shell
# bug we have seen. Catch it early instead of patching symptoms later.
if command -v cvlc >/dev/null 2>&1 || command -v vlc >/dev/null 2>&1; then
  if [ -f /home/pi/.bash_profile ] || [ -f /home/pi/loopvideos.sh ] || \
     [ -d /etc/systemd/system/getty@tty1.service.d ]; then
    echo "ERROR: This SD card contains a legacy VLC media-looper setup."
    echo "       Do NOT layer the fleet stack on top of an old exhibition image."
    echo "       Flash a fresh Raspberry Pi OS Lite (arm64) card and try again."
    exit 1
  fi
fi

DONE_MARKER="/etc/fleet-client/.firstrun-done"
if [ -f "$DONE_MARKER" ]; then
  echo "first-run already completed — nothing to do"
  exit 0
fi

# ── 1. machine-id hygiene (cloned images share it; breaks journald/dbus) ──
truncate -s 0 /etc/machine-id || true
rm -f /var/lib/dbus/machine-id
systemd-machine-id-setup || true

# Locate fleet folder (Bookworm mounts the boot partition at /boot/firmware)
FLEET_DIR=""
if [ -d "/boot/firmware/fleet" ]; then
  FLEET_DIR="/boot/firmware/fleet"
elif [ -d "/boot/fleet" ]; then
  FLEET_DIR="/boot/fleet"
fi
if [ -z "$FLEET_DIR" ]; then
  echo "ERROR: fleet dir not found in /boot/firmware/fleet or /boot/fleet — aborting"
  exit 1
fi

# Read boot config (server URL, group, PSK, local password, rootfs size)
FLEET_SERVER="https://fleet.example.org"
DEVICE_GROUP="default"
DEVICE_PSK="change-me"
LOCAL_PASSWORD="aec2026"
ROOTFS_GB="8"
HOSTNAME_PREFIX="aef-pi"
BOOTCFG="$FLEET_DIR/fleet-boot-config.json"
cfgget() { python3 -c "import json,sys; print(json.load(open('$BOOTCFG')).get('$1',''))" 2>/dev/null || true; }
if [ -f "$BOOTCFG" ]; then
  v=$(cfgget server_url);     [ -n "$v" ] && FLEET_SERVER="$v"
  v=$(cfgget group);          [ -n "$v" ] && DEVICE_GROUP="$v"
  v=$(cfgget device_psk);     [ -n "$v" ] && DEVICE_PSK="$v"
  v=$(cfgget local_password); [ -n "$v" ] && LOCAL_PASSWORD="$v"
  v=$(cfgget rootfs_gb);      [ -n "$v" ] && ROOTFS_GB="$v"
  v=$(cfgget hostname_prefix); [ -n "$v" ] && HOSTNAME_PREFIX="$v"
fi
echo "Server: $FLEET_SERVER   Group: $DEVICE_GROUP   rootfs: ${ROOTFS_GB}G"

# ── 2. Packages ──
echo "Waiting for network…"
NET_OK=0
for i in $(seq 1 30); do
  if ping -c1 -W2 1.1.1.1 &>/dev/null || ping -c1 -W2 8.8.8.8 &>/dev/null; then
    NET_OK=1; echo "Network available"; break
  fi
  sleep 2
done
if [ "$NET_OK" != "1" ]; then
  echo "ERROR: no network — first boot needs internet (Ethernet at HQ)."
  echo "Will retry automatically on next boot."
  exit 1
fi

export DEBIAN_FRONTEND=noninteractive

# Some Trixie mirrors return 404 for i18n Translation files during apt update,
# which does not stop the install but produces noisy warnings. Disable language
# index downloads — this is a headless appliance, translations are not needed.
mkdir -p /etc/apt/apt.conf.d
echo 'Acquire::Languages "none";' > /etc/apt/apt.conf.d/99disable-languages

# ── FLEET-MEDIA partition FIRST — this GROWS the root filesystem (auto-expand
# was disabled at flash time so we could carve out the media partition). The
# stock image root is only ~2 GB; apt would run out of space downloading mpv &
# co. before we ever got here. setup_media_partition.sh grows rootfs to
# ROOTFS_GB, then formats the rest as FLEET-MEDIA (installing exfatprogs itself
# once the rootfs has room). MUST precede the package install. ──
if [ -x "$FLEET_DIR/deploy/setup_media_partition.sh" ]; then
  ROOTFS_GB="$ROOTFS_GB" bash "$FLEET_DIR/deploy/setup_media_partition.sh" || {
    echo "WARNING: media partition / rootfs grow failed — continuing (apt may be tight)"
  }
fi

echo "Installing packages…"
apt-get update -qq
# NetworkManager is the default netstack on Bookworm/Trixie — we standardize
# on it (nmcli) for venue Wi-Fi AND the onboarding hotspot. No hostapd, no
# standalone dnsmasq, no wpa_supplicant.conf juggling. dnsmasq-base backs
# NM's shared mode; exfatprogs+parted for the FLEET-MEDIA partition.
apt-get install -y --no-install-recommends \
    network-manager dnsmasq-base \
    python3 python3-pip python3-pil python3-flask python3-requests python3-evdev \
    mpv \
    exfatprogs parted \
    rfkill iw \
    curl ca-certificates gnupg

# Captive-portal DNS wildcard for NM's shared-mode dnsmasq: every hostname
# resolves to the Pi while the onboarding hotspot is up → phones pop the portal.
mkdir -p /etc/NetworkManager/dnsmasq-shared.d
cat > /etc/NetworkManager/dnsmasq-shared.d/00-fleet-captive.conf <<'EOF'
# Fleet onboarding: answer every DNS query with the hotspot gateway
address=/#/10.42.0.1
EOF

# ── Tailscale (OPTIONAL mesh; non-fatal — a public-HTTPS deployment needs no
# mesh, and a repo/keyring hiccup must never abort the whole provisioning) ──
echo "Installing Tailscale…"
if ! command -v tailscale >/dev/null 2>&1; then
  if curl -fsSL https://pkgs.tailscale.com/stable/raspbian/bookworm.noarmor.gpg \
        | tee /usr/share/keyrings/tailscale-archive-keyring.gpg >/dev/null \
     && curl -fsSL https://pkgs.tailscale.com/stable/raspbian/bookworm.tailscale-keyring.list \
        | tee /etc/apt/sources.list.d/tailscale.list >/dev/null; then
    apt-get update -qq && apt-get install -y tailscale \
      || echo "WARNING: tailscale install failed — continuing without mesh"
  else
    echo "WARNING: tailscale repo setup failed — continuing without mesh"
  fi
fi

# ── 4. Fleet code, config, services ──
mkdir -p /opt/fleet-client/onboarding/templates
mkdir -p /opt/fleet-media/releases
mkdir -p /etc/fleet-client

echo "Installing fleet client…"
cp -r "$FLEET_DIR/client/." /opt/fleet-client/
chmod +x /opt/fleet-client/*.py /opt/fleet-client/*.sh 2>/dev/null || true

# Udev rule for USB sync
cp "$FLEET_DIR/deploy/99-fleet-usb.rules" /etc/udev/rules.d/
udevadm control --reload 2>/dev/null || true

# Per-SD Tailscale authkey (single-use, ephemeral; baked at flash time)
if [ -f "$FLEET_DIR/tailscale-authkey" ]; then
    cp "$FLEET_DIR/tailscale-authkey" /etc/fleet-client/tailscale-authkey
    chmod 600 /etc/fleet-client/tailscale-authkey
fi

# Runtime config (fleet-setup.toml can override parts of this later)
cat > /etc/fleet-client/config.json << EOF
{
    "server_url": "$FLEET_SERVER",
    "device_psk": "$DEVICE_PSK",
    "local_password": "$LOCAL_PASSWORD",
    "group": "$DEVICE_GROUP",
    "poll_interval": 30,
    "jitter_max": 5,
    "media_base": "/opt/fleet-media",
    "hostname_prefix": "$HOSTNAME_PREFIX",
    "label": ""
}
EOF
# The daemons run as User=pi, so pi must OWN the config (600 root-only made
# fleet-client fall back to built-in defaults — wrong server/group — and broke
# the local UI password). Owned by pi, mode 600 keeps the PSK/password private.
chown pi:pi /etc/fleet-client/config.json
chmod 600 /etc/fleet-client/config.json

# Media dir + client tree + config dir ownership (daemons run as pi)
chown -R pi:pi /opt/fleet-media /opt/fleet-client 2>/dev/null || true
chown pi:pi /etc/fleet-client 2>/dev/null || true

# The fleet daemons run as User=pi but need to reboot the device from command
# queue / local UI without an interactive password prompt.
cat > /etc/sudoers.d/fleet-pi <<'EOF'
pi ALL=(ALL) NOPASSWD: /sbin/reboot, /sbin/shutdown, /usr/sbin/reboot, /usr/sbin/shutdown
EOF
chmod 440 /etc/sudoers.d/fleet-pi

# ── systemd units ──
cp /opt/fleet-client/fleet-player.service        /etc/systemd/system/
cp /opt/fleet-client/fleet-client.service        /etc/systemd/system/
cp /opt/fleet-client/fleet-local-control.service /etc/systemd/system/
cp /opt/fleet-client/fleet-keyboard.service      /etc/systemd/system/
cp /opt/fleet-client/fleet-hostname.service      /etc/systemd/system/
cp /opt/fleet-client/onboarding/fleet-onboard.service /etc/systemd/system/

systemctl daemon-reload
systemctl enable fleet-onboard.service \
                 fleet-player.service \
                 fleet-client.service \
                 fleet-local-control.service \
                 fleet-keyboard.service \
                 fleet-hostname.service

# Ensure SSH host keys exist on every boot. They are removed during
# golden-image generalization; some Bookworm/Trixie paths fail to recreate
# them automatically, which leaves sshd dead on first boot of a clone.
# The primary fix is fleet-regenerate-hostkeys.service (installed below);
# this drop-in is a fallback for images that were already generalized
# before that service existed.
mkdir -p /etc/systemd/system/ssh.service.d
cat > /etc/systemd/system/ssh.service.d/10-fleet-keys.conf <<'EOF'
[Service]
ExecStartPre=-/usr/bin/ssh-keygen -A
EOF
systemctl daemon-reload
systemctl enable ssh 2>/dev/null || true
ssh-keygen -A || true

# Ensure cloned images regenerate SSH host keys even when first-run is skipped
# (clones inherit .firstrun-done, so this installer never runs on them — the
# enabled unit below ships inside the golden image and self-activates on a
# clone's first boot because generalization removed the host keys).
FLEET_REGEN_KEYS_UNIT=/etc/systemd/system/fleet-regenerate-hostkeys.service
cp "$FLEET_DIR/deploy/fleet-regenerate-hostkeys.service" "$FLEET_REGEN_KEYS_UNIT"
chmod 644 "$FLEET_REGEN_KEYS_UNIT"
systemctl daemon-reload
systemctl enable fleet-regenerate-hostkeys.service

# Set the fleet hostname right away (also runs every boot via the unit)
python3 /opt/fleet-client/set_hostname.py || true

# ── Boot-firmware guard (Trixie raspi-firmware bug, GitHub firmware#2034) ──
# raspi-firmware 1:1.20260521-2 pruned cross-board boot files from
# /boot/firmware (kernel8.img, start4.elf, ...) without syncing the FAT,
# leaving images unbootable. Verify the full set is still here; if anything
# is missing, reinstall the (fixed) package and hard-sync the FAT.
BOOTFW_MISSING=""
for f in kernel8.img kernel_2712.img start.elf start4.elf start4cd.elf \
         initramfs8 initramfs_2712; do
  [ -f "/boot/firmware/$f" ] || BOOTFW_MISSING="$BOOTFW_MISSING $f"
done
if [ -n "$BOOTFW_MISSING" ]; then
  echo "WARNING: /boot/firmware missing:$BOOTFW_MISSING — reinstalling raspi-firmware"
  apt-get install --reinstall -y raspi-firmware || true
  sync -f /boot/firmware 2>/dev/null || true
  BOOTFW_STILL=""
  for f in $BOOTFW_MISSING; do
    [ -f "/boot/firmware/$f" ] || BOOTFW_STILL="$BOOTFW_STILL $f"
  done
  if [ -n "$BOOTFW_STILL" ]; then
    echo "ERROR: boot firmware still incomplete:$BOOTFW_STILL"
    echo "       image would be unbootable on some Pi models — investigate before capture"
  fi
fi

# Free tty1 for mpv DRM (no autologin needed; mpv talks to KMS directly)
systemctl disable getty@tty1.service 2>/dev/null || true

# ── done ──
mkdir -p /etc/fleet-client
date > "$DONE_MARKER"
systemctl disable fleet-firstrun.service 2>/dev/null || true

echo "$(date): Fleet first-run complete!"
echo "Rebooting into normal fleet operation…"
reboot
