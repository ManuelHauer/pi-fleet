#!/bin/bash
# Golden Image First-Run Script
# Runs once on first boot. Installs packages, copies the fleet code from the
# boot partition into /opt/fleet-client, drops in systemd units + udev rules,
# enables services. Self-disables on success.

set -e
exec > /var/log/fleet-firstrun.log 2>&1
echo "$(date): Fleet golden image first-run starting…"

# Wipe inherited machine-id so a cloned image regenerates its own. We don't
# use machine-id for identity (see client/identity.py), but duplicates across
# the fleet break journald/dbus subtly.
truncate -s 0 /etc/machine-id || true
rm -f /var/lib/dbus/machine-id
systemd-machine-id-setup || true

# Locate fleet folder (Bookworm mounts boot partition at /boot/firmware; early boot may use /boot)
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

# Read boot config (server URL, group, PSK, local password)
FLEET_SERVER="http://169.254.180.14:8550"
DEVICE_GROUP="default"
DEVICE_PSK="aec-device-psk-2026"
LOCAL_PASSWORD="aec2026"
if [ -f "$FLEET_DIR/fleet-boot-config.json" ]; then
  FLEET_SERVER=$(python3 -c "import json; print(json.load(open('$FLEET_DIR/fleet-boot-config.json')).get('server_url','$FLEET_SERVER'))" 2>/dev/null || echo "$FLEET_SERVER")
  DEVICE_GROUP=$(python3 -c "import json; print(json.load(open('$FLEET_DIR/fleet-boot-config.json')).get('group','$DEVICE_GROUP'))" 2>/dev/null || echo "$DEVICE_GROUP")
  DEVICE_PSK=$(python3 -c "import json; print(json.load(open('$FLEET_DIR/fleet-boot-config.json')).get('device_psk','$DEVICE_PSK'))" 2>/dev/null || echo "$DEVICE_PSK")
  LOCAL_PASSWORD=$(python3 -c "import json; print(json.load(open('$FLEET_DIR/fleet-boot-config.json')).get('local_password','$LOCAL_PASSWORD'))" 2>/dev/null || echo "$LOCAL_PASSWORD")
fi

echo "Server: $FLEET_SERVER"
echo "Group:  $DEVICE_GROUP"

# Best-effort network wait so apt-get has a chance
echo "Waiting for network…"
for i in $(seq 1 15); do
    if ping -c1 -W2 8.8.8.8 &>/dev/null; then
        echo "Network available"
        break
    fi
    sleep 2
done

# ── Package install (mpv only, no VLC — VLC has a DRM bug on Trixie) ──
echo "Installing packages…"
apt-get update -qq
apt-get install -y --no-install-recommends \
    python3 python3-pip python3-pil python3-flask python3-requests \
    mpv \
    hostapd dnsmasq wireless-tools wpasupplicant \
    iptables curl ca-certificates gnupg

# ── Tailscale via official apt repo (works on Bookworm + Trixie) ──
echo "Installing Tailscale…"
curl -fsSL https://pkgs.tailscale.com/stable/raspbian/bookworm.noarmor.gpg \
    | tee /usr/share/keyrings/tailscale-archive-keyring.gpg >/dev/null
curl -fsSL https://pkgs.tailscale.com/stable/raspbian/bookworm.tailscale-keyring.list \
    | tee /etc/apt/sources.list.d/tailscale.list
apt-get update -qq && apt-get install -y tailscale

# Don't auto-start hostapd/dnsmasq (the onboarding daemon owns them)
systemctl disable hostapd 2>/dev/null || true
systemctl stop hostapd 2>/dev/null || true
systemctl disable dnsmasq 2>/dev/null || true
systemctl stop dnsmasq 2>/dev/null || true

# ── Directories ──
mkdir -p /opt/fleet-client/onboarding/templates
mkdir -p /opt/fleet-media/releases
mkdir -p /etc/fleet-client

# ── Install fleet code (entire client/ tree) ──
echo "Installing fleet client…"
cp -r "$FLEET_DIR/client/." /opt/fleet-client/
chmod +x /opt/fleet-client/*.py /opt/fleet-client/*.sh 2>/dev/null || true

# Udev rule for USB sync
cp "$FLEET_DIR/deploy/99-fleet-usb.rules" /etc/udev/rules.d/

# Per-SD Tailscale authkey (single-use, ephemeral; baked at flash time)
if [ -f "$FLEET_DIR/tailscale-authkey" ]; then
    cp "$FLEET_DIR/tailscale-authkey" /etc/fleet-client/tailscale-authkey
    chmod 600 /etc/fleet-client/tailscale-authkey
fi

# Write runtime config
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

# ── Systemd units (player + client + local control + onboarding) ──
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

# Self-disable first-run trigger
systemctl disable fleet-firstrun 2>/dev/null || true
rm -f /etc/systemd/system/fleet-firstrun.service

echo "$(date): Fleet first-run complete!"
echo "On next boot: machine-id regen → onboarding → mesh join → fleet-player + fleet-client come up."
