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
BOOTCFG="$FLEET_DIR/fleet-boot-config.json"
cfgget() { python3 -c "import json,sys; print(json.load(open('$BOOTCFG')).get('$1',''))" 2>/dev/null || true; }
if [ -f "$BOOTCFG" ]; then
  v=$(cfgget server_url);     [ -n "$v" ] && FLEET_SERVER="$v"
  v=$(cfgget group);          [ -n "$v" ] && DEVICE_GROUP="$v"
  v=$(cfgget device_psk);     [ -n "$v" ] && DEVICE_PSK="$v"
  v=$(cfgget local_password); [ -n "$v" ] && LOCAL_PASSWORD="$v"
  v=$(cfgget rootfs_gb);      [ -n "$v" ] && ROOTFS_GB="$v"
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
    python3 python3-pip python3-pil python3-flask python3-requests \
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
    "label": ""
}
EOF
chmod 600 /etc/fleet-client/config.json

# Media dir ownership (daemons run as pi)
chown -R pi:pi /opt/fleet-media /opt/fleet-client 2>/dev/null || true

# ── systemd units ──
cp /opt/fleet-client/fleet-player.service        /etc/systemd/system/
cp /opt/fleet-client/fleet-client.service        /etc/systemd/system/
cp /opt/fleet-client/fleet-local-control.service /etc/systemd/system/
cp /opt/fleet-client/onboarding/fleet-onboard.service /etc/systemd/system/

systemctl daemon-reload
systemctl enable fleet-onboard.service \
                 fleet-player.service \
                 fleet-client.service \
                 fleet-local-control.service

# Free tty1 for mpv DRM (no autologin needed; mpv talks to KMS directly)
systemctl disable getty@tty1.service 2>/dev/null || true

# ── done ──
mkdir -p /etc/fleet-client
date > "$DONE_MARKER"
systemctl disable fleet-firstrun.service 2>/dev/null || true

echo "$(date): Fleet first-run complete!"
echo "Rebooting into normal fleet operation…"
reboot
