#!/bin/bash
# Golden Image First-Run Script
# This script is placed on the boot partition of a fresh Raspberry Pi OS image.
# It runs once on first boot, installs the fleet system, and self-destructs.
#
# To use: copy this + fleet files to /boot/firmware/fleet/ on the SD card.
# The systemd service triggers it on first boot.

set -e
exec > /var/log/fleet-firstrun.log 2>&1
echo "$(date): Fleet golden image first-run starting…"

# Locate fleet folder (Bookworm mounts boot partition at /boot/firmware; early boot may use /boot)
FLEET_DIR=""
if [ -d "/boot/firmware/fleet" ]; then
  FLEET_DIR="/boot/firmware/fleet"
elif [ -d "/boot/fleet" ]; then
  FLEET_DIR="/boot/fleet"
fi

if [ -z "$FLEET_DIR" ]; then
  echo "ERROR: fleet dir not found in /boot/firmware/fleet or /boot/fleet — skipping"
  exit 1
fi

# Read config from boot partition
FLEET_SERVER="http://169.254.180.14:8550"
DEVICE_GROUP="default"
DEVICE_PSK="aec-device-psk-2026"
LOCAL_PASSWORD="aec2026"
if [ -f "$FLEET_DIR/fleet-boot-config.json" ]; then
  FLEET_SERVER=$(python3 -c "import json; print(json.load(open('$FLEET_DIR/fleet-boot-config.json')).get('server_url','http://169.254.180.14:8550'))" 2>/dev/null || echo "$FLEET_SERVER")
  DEVICE_GROUP=$(python3 -c "import json; print(json.load(open('$FLEET_DIR/fleet-boot-config.json')).get('group','default'))" 2>/dev/null || echo "$DEVICE_GROUP")
  DEVICE_PSK=$(python3 -c "import json; print(json.load(open('$FLEET_DIR/fleet-boot-config.json')).get('device_psk','aec-device-psk-2026'))" 2>/dev/null || echo "$DEVICE_PSK")
  LOCAL_PASSWORD=$(python3 -c "import json; print(json.load(open('$FLEET_DIR/fleet-boot-config.json')).get('local_password','aec2026'))" 2>/dev/null || echo "$LOCAL_PASSWORD")
fi

echo "Server: $FLEET_SERVER"
echo "Group:  $DEVICE_GROUP"

# Wait for network (best-effort)
echo "Waiting for network…"
for i in $(seq 1 15); do
    if ping -c1 -W2 8.8.8.8 &>/dev/null; then
        echo "Network available"
        break
    fi
    sleep 2
done

# Install packages
echo "Installing packages…"
apt-get update -qq
apt-get install -y -qq mpv python3 python3-flask \
    hostapd dnsmasq wireless-tools wpasupplicant || {
    echo "WARN: Some packages may have failed — continuing"
}

# Disable hostapd/dnsmasq auto-start
systemctl disable hostapd 2>/dev/null || true
systemctl stop hostapd 2>/dev/null || true
systemctl disable dnsmasq 2>/dev/null || true
systemctl stop dnsmasq 2>/dev/null || true

# Create directories
mkdir -p /opt/fleet-client/onboarding/templates
mkdir -p /opt/fleet-media/releases
mkdir -p /etc/fleet-client

# Copy fleet files from boot partition
echo "Installing fleet client…"
cp "$FLEET_DIR/fleet_client.py" /opt/fleet-client/
chmod +x /opt/fleet-client/fleet_client.py

echo "Installing local control UI…"
cp "$FLEET_DIR/local_control.py" /opt/fleet-client/
cp "$FLEET_DIR/usb_sync.sh" /opt/fleet-client/
chmod +x /opt/fleet-client/usb_sync.sh
cp "$FLEET_DIR/99-fleet-usb.rules" /etc/udev/rules.d/
chmod +x /opt/fleet-client/local_control.py || true

echo "Installing onboarding system…"
cp -r "$FLEET_DIR/onboarding/"* /opt/fleet-client/onboarding/

# Write config
cat > /etc/fleet-client/config.json << EOF
{
    "server_url": "$FLEET_SERVER",
    "device_psk": "$DEVICE_PSK",
    "local_password": "$LOCAL_PASSWORD",
    "group": "$DEVICE_GROUP",
    "poll_interval": 30,
    "jitter_max": 5,
    "media_base": "/opt/fleet-media",
    "label": "",
    "vlc_extra_args": []
}
EOF

# Install systemd services
cp "$FLEET_DIR/fleet-client.service" /etc/systemd/system/
cp "$FLEET_DIR/fleet-local-control.service" /etc/systemd/system/
cp "$FLEET_DIR/onboarding/fleet-onboard.service" /etc/systemd/system/fleet-onboard.service
systemctl daemon-reload
systemctl enable fleet-onboard
systemctl enable fleet-client
systemctl enable fleet-local-control

# Self-destruct: remove first-run trigger
systemctl disable fleet-firstrun 2>/dev/null || true
rm -f /etc/systemd/system/fleet-firstrun.service

echo "$(date): Fleet first-run complete!"
echo "System will proceed to onboarding on next boot cycle."

# Optionally reboot to start clean
# reboot
