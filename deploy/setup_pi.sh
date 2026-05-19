#!/bin/bash
# Ars Festival Fleet Client — Pi deployment script
# Run on the Raspberry Pi (as root or with sudo)
set -e

FLEET_SERVER="${1:-http://169.254.180.14:8550}"
DEVICE_GROUP="${2:-default}"
DEVICE_LABEL="${3:-}"

echo "🎬 Ars Festival Fleet Client Setup"
echo "  Server: $FLEET_SERVER"
echo "  Group:  $DEVICE_GROUP"
echo ""

# Install system dependencies
echo "→ Installing system packages…"
apt-get update -qq
apt-get install -y -qq vlc vlc-plugin-base python3 python3-flask \
    hostapd dnsmasq wireless-tools wpasupplicant

# Prevent hostapd/dnsmasq from auto-starting (we manage them)
systemctl disable hostapd 2>/dev/null || true
systemctl stop hostapd 2>/dev/null || true
systemctl disable dnsmasq 2>/dev/null || true
systemctl stop dnsmasq 2>/dev/null || true

# Create directories
echo "→ Creating directories…"
mkdir -p /opt/fleet-client/onboarding/templates
mkdir -p /opt/fleet-media/releases
mkdir -p /etc/fleet-client
mkdir -p /var/log

# Copy client
echo "→ Installing fleet client…"
cp fleet_client.py /opt/fleet-client/fleet_client.py
chmod +x /opt/fleet-client/fleet_client.py

# Copy onboarding system
echo "→ Installing onboarding system…"
cp -r onboarding/* /opt/fleet-client/onboarding/

# Write config
echo "→ Writing config…"
cat > /etc/fleet-client/config.json << EOF
{
    "server_url": "$FLEET_SERVER",
    "device_psk": "aec-device-psk-2026",
    "group": "$DEVICE_GROUP",
    "poll_interval": 43200,
    "jitter_max": 600,
    "media_base": "/opt/fleet-media",
    "label": "$DEVICE_LABEL",
    "vlc_extra_args": []
}
EOF

# Copy local control
echo "→ Installing local control UI…"
cp local_control.py /opt/fleet-client/local_control.py

# Install systemd services
echo "→ Installing systemd services…"
cp fleet-client.service /etc/systemd/system/fleet-client.service
cp fleet-local-control.service /etc/systemd/system/fleet-local-control.service
cp onboarding/fleet-onboard.service /etc/systemd/system/fleet-onboard.service
systemctl daemon-reload
systemctl enable fleet-onboard
systemctl enable fleet-client
systemctl enable fleet-local-control

# Remove onboard-done marker to trigger onboarding on next boot
rm -f /etc/fleet-client/onboard-done

echo ""
echo "✅ Fleet client installed!"
echo "   On next boot: onboarding → Wi-Fi setup → fleet client starts"
echo "   Device ID: $(cat /etc/fleet-client/device-id 2>/dev/null || echo 'will be generated on first run')"
echo ""
echo "   Services:"
echo "     fleet-onboard:  systemctl status fleet-onboard"
echo "     fleet-client:   systemctl status fleet-client"
echo "   Logs:   journalctl -u fleet-onboard -f"
echo "           journalctl -u fleet-client -f"
